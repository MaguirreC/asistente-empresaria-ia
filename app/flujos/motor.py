"""Motor de los flujos guiados de compra.

Un flujo es una secuencia de preguntas que termina en un **formulario** listo
para que el front lo mapee en la pantalla del producto. En vez de mandar al
usuario a la pantalla y dejarlo solo, se le arma la compra desde el chat.

Todo se resuelve en código: un flujo completo cuesta **cero tokens**.

EL ESTADO NO SE GUARDA AQUÍ. Viaja al front en cada respuesta y vuelve en la
siguiente petición, igual que el historial de mensajes. Así el servicio sigue
sin estado: no hacen falta sesiones pegajosas ni Redis, y da igual qué
instancia de ECS atienda cada turno.

El paso pendiente **no se guarda**: se deduce de los datos ya recogidos
(`Flujo.siguiente_paso`). Es a propósito — si el paso viajara en el estado
podría desincronizarse de los datos, y esa clase de bug no existe si el paso
es una función pura de lo recogido.
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)


class FlujoNoDisponible(Exception):
    """No se puede continuar por algo ajeno al usuario (p. ej. se cayó el
    backend que trae las loterías). Lleva el mensaje que verá el cliente."""


@dataclass(frozen=True)
class Paso:
    """Una pregunta ya resuelta contra los datos que se llevan recogidos.

    `opciones` son las etiquetas que el front pinta como botones. El usuario
    puede pulsarlas, escribir el número o escribir el texto: `interpretar` se
    encarga de las tres formas.

    `interpretar` devuelve el valor a guardar, o None si la respuesta no sirve.
    `ayuda` es lo que se le dice entonces, sin gastar tokens.
    """
    id: str
    pregunta: str
    interpretar: Callable[[str], object | None]
    opciones: tuple[str, ...] = ()
    ayuda: str = ""


@dataclass(frozen=True)
class Flujo:
    producto: str
    # datos recogidos -> siguiente pregunta, o None si ya no falta nada.
    siguiente_paso: Callable[[dict], Paso | None]
    # datos completos -> payload para el front.
    formulario: Callable[[dict], dict]
    # datos completos -> texto de cierre que ve el usuario.
    resumen: Callable[[dict], str]


@dataclass(frozen=True)
class Avance:
    """Lo que hay que emitirle al front después de un turno del flujo."""
    mensaje: str
    opciones: tuple[str, ...] = ()
    # Estado a devolver para el siguiente turno. None = el flujo terminó o se
    # canceló, y el front no debe reenviar nada.
    estado: dict | None = None
    # Payload final. Solo viene en el último turno.
    formulario: dict | None = None
    # El usuario preguntó algo en vez de contestar: que conteste el modelo y
    # después se repite `mensaje` para retomar el flujo donde iba.
    consultar_modelo: bool = False


_FLUJOS: dict[str, Flujo] = {}


def registrar(flujo: Flujo) -> None:
    _FLUJOS[flujo.producto] = flujo


def hay_flujo(producto: str | None) -> bool:
    return producto in _FLUJOS


# --- Interpretación de lo que escribe el usuario -------------------------

# Salidas de emergencia. Sin esto, quien se arrepiente a mitad del flujo queda
# atrapado contestando preguntas que ya no quiere.
_CANCELAR = {
    "cancelar", "cancela", "salir", "salte", "ya no", "olvidalo", "olvidalo ya",
    "no quiero", "dejalo", "menu", "menu principal", "volver al menu", "inicio",
}

_ATRAS = {"atras", "volver", "regresar", "anterior", "me equivoque", "corregir"}

# Señales de que el mensaje es una duda y no una respuesta al paso. Solo se usa
# para decidir si vale la pena pagarle al modelo: si no parece pregunta, se
# reintenta el paso gratis.
_PARECE_PREGUNTA = re.compile(
    r"\?|\b(que|cual|cuales|como|cuanto|cuanta|cuantos|por que|porque|donde|"
    r"quien|explica\w*|explicame|significa|diferencia|entiendo|sirve|conviene|"
    r"recomiendas|mejor)\b"
)

MENSAJE_CANCELADO = (
    "Listo, cancelé la compra. Si quieres retomarla, escríbeme cuando gustes. 🍀"
)


def _normalizar(texto: str) -> str:
    """Misma normalización que el router: minúsculas, sin tildes ni signos."""
    import unicodedata

    plano = unicodedata.normalize("NFD", texto.lower())
    plano = "".join(c for c in plano if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9\s]", " ", plano)


def _limpiar(texto: str) -> str:
    return re.sub(r"\s+", " ", _normalizar(texto)).strip()


def elegir_de(texto: str, pares: list[tuple[str, object]]) -> object | None:
    """Resuelve una selección contra una lista de (etiqueta, valor).

    Acepta las tres formas en que puede llegar: el número de la opción, la
    etiqueta completa, o un trozo distintivo de ella ("dorado" por "Lotería
    del Dorado"). Si el trozo encaja en más de una opción NO se elige ninguna:
    adivinar la lotería equivocada es peor que volver a preguntar.
    """
    plano = _limpiar(texto)
    if not plano:
        return None

    if plano.isdigit():
        indice = int(plano) - 1
        if 0 <= indice < len(pares):
            return pares[indice][1]
        return None

    etiquetas = [_limpiar(etiqueta) for etiqueta, _ in pares]

    for etiqueta, (_, valor) in zip(etiquetas, pares):
        if etiqueta == plano:
            return valor

    coincidencias = [
        valor
        for etiqueta, (_, valor) in zip(etiquetas, pares)
        if plano in etiqueta or etiqueta in plano
    ]
    return coincidencias[0] if len(coincidencias) == 1 else None


def _pregunta_con_opciones(paso: Paso) -> str:
    """La pregunta con sus opciones numeradas debajo.

    Van en el texto además de en el evento `opciones_flujo` porque el usuario
    puede estar en un front que no pinte botones, y porque poder responder con
    el número es más rápido que escribir el nombre completo de una lotería.
    """
    if not paso.opciones:
        return paso.pregunta
    lineas = "\n".join(f"{i}. {o}" for i, o in enumerate(paso.opciones, 1))
    return f"{paso.pregunta}\n\n{lineas}"


def _avance_de(paso: Paso, producto: str, datos: dict, prefijo: str = "") -> Avance:
    return Avance(
        mensaje=prefijo + _pregunta_con_opciones(paso),
        opciones=paso.opciones,
        estado={"producto": producto, "datos": datos},
    )


# --- API del motor --------------------------------------------------------


def iniciar(producto: str) -> Avance | None:
    """Primera pregunta del flujo. None si ese producto no tiene flujo."""
    flujo = _FLUJOS.get(producto)
    if flujo is None:
        return None
    try:
        paso = flujo.siguiente_paso({})
    except FlujoNoDisponible as e:
        return Avance(mensaje=str(e))
    if paso is None:  # un flujo sin pasos no tiene sentido
        logger.error("El flujo de %s no devolvió ningún paso inicial", producto)
        return None
    return _avance_de(paso, producto, {})


def avanzar(producto: str, datos: dict, texto: str) -> Avance:
    """Procesa la respuesta del usuario al paso pendiente.

    `datos` es lo ya recogido; se copia, no se muta el que viene del request.
    """
    flujo = _FLUJOS.get(producto)
    if flujo is None:
        logger.warning("Llegó estado de un flujo desconocido: %s", producto)
        return Avance(mensaje=MENSAJE_CANCELADO)

    plano = _limpiar(texto)
    datos = dict(datos)

    if plano in _CANCELAR:
        return Avance(mensaje=MENSAJE_CANCELADO)

    try:
        if plano in _ATRAS:
            if datos:
                datos.popitem()  # los dict conservan el orden de inserción
            paso = flujo.siguiente_paso(datos)
            if paso is None:
                return Avance(mensaje=MENSAJE_CANCELADO)
            return _avance_de(paso, producto, datos, "Sin problema, volvamos atrás.\n\n")

        paso = flujo.siguiente_paso(datos)
        if paso is None:  # no debería pasar: el flujo ya había terminado
            return Avance(mensaje=MENSAJE_CANCELADO)

        valor = paso.interpretar(texto)

        if valor is None:
            # No contestó lo que se le pedía. Si suena a duda, que la resuelva
            # el modelo y después se retoma; si no, se reintenta gratis.
            if _PARECE_PREGUNTA.search(plano):
                return Avance(
                    mensaje=_pregunta_con_opciones(paso),
                    opciones=paso.opciones,
                    estado={"producto": producto, "datos": datos},
                    consultar_modelo=True,
                )
            prefijo = f"{paso.ayuda}\n\n" if paso.ayuda else ""
            return _avance_de(paso, producto, datos, prefijo)

        datos[paso.id] = valor

        siguiente = flujo.siguiente_paso(datos)
        if siguiente is not None:
            return _avance_de(siguiente, producto, datos)

        return Avance(
            mensaje=flujo.resumen(datos),
            formulario=flujo.formulario(datos),
        )
    except FlujoNoDisponible as e:
        return Avance(mensaje=str(e))
