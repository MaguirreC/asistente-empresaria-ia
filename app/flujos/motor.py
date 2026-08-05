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
import random
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)


def formatear_pesos(valor: int) -> str:
    return f"${valor:,.0f}".replace(",", ".")


def sin_tildes(texto: str) -> str:
    plano = unicodedata.normalize("NFD", texto)
    return "".join(c for c in plano if unicodedata.category(c) != "Mn")


# Formas de pedir que el sistema elija un número por el usuario, en vez de
# escribirlo. Común a cualquier receta que pida un número (Chance, Astro...).
_PIDE_ALEATORIO = re.compile(
    r"\b(aleatori\w*|azar|random|sorpres\w*|sorprend\w*|cualquier\w*|"
    r"(elig\w*|escog\w*)\s+(tu|usted|por\s+mi|por\s+ti))\b",
    re.IGNORECASE,
)


def pide_aleatorio(texto: str) -> bool:
    return bool(_PIDE_ALEATORIO.search(sin_tildes(texto)))


def numero_aleatorio(cifras: int) -> str:
    """Un número de `cifras` dígitos, con ceros a la izquierda si hace falta."""
    return f"{random.randint(0, 10 ** cifras - 1):0{cifras}d}"


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


# --- Elegir una lotería de una lista larga, agrupando por jornada ---------
#
# Nació en chance.py y ahora la usan también chance_millonario.py y
# doble_play.py: un día cualquiera trae entre 14 y 25 loterías, y listarlas
# todas de una sería un muro de botones. Se agrupa por la HORA DE CIERRE real
# y no por el nombre: hay loterías sin jornada en el nombre (SAMAN, CRUZ ROJA)
# y otras que la contradicen (PAISITA NOCHE cierra 5:45 p.m.).

# (etiqueta, desde, hasta) en horas. `None` es sin límite por ese lado.
JORNADAS: tuple[tuple[str, int | None, int | None], ...] = (
    ("Mañana", None, 12),
    ("Mediodía", 12, 14),
    ("Tarde", 14, 18),
    ("Noche", 18, None),
)

# Por debajo de esto no vale la pena preguntar la jornada: se listan todas y se
# ahorra un paso.
MAXIMO_SIN_AGRUPAR = 8


def de_la_jornada(loterias: list, jornada: str | None) -> list:
    if jornada is None:
        return list(loterias)
    desde, hasta = next((d, h) for e, d, h in JORNADAS if e == jornada)
    return [
        l
        for l in loterias
        if l.hora is not None
        and (desde is None or l.hora.hour >= desde)
        and (hasta is None or l.hora.hour < hasta)
    ]


def paso_jornada(loterias: list, id: str = "jornada") -> Paso:
    pares = []
    for etiqueta, _, _ in JORNADAS:
        del_grupo = de_la_jornada(loterias, etiqueta)
        if not del_grupo:
            continue  # nada abierto en esa franja: no se ofrece
        primera, ultima = del_grupo[0].hora_texto, del_grupo[-1].hora_texto
        rango = primera if len(del_grupo) == 1 else f"{primera} a {ultima}"
        pares.append((f"{etiqueta} — {len(del_grupo)} loterías, cierran {rango}", etiqueta))

    return Paso(
        id=id,
        pregunta=f"Hay {len(loterias)} loterías disponibles. ¿A qué hora quieres jugar?",
        opciones=tuple(etiqueta for etiqueta, _ in pares),
        interpretar=lambda texto: elegir_de(texto, pares),
        ayuda="Elige una franja de la lista, o responde con su número.",
    )


def paso_loteria(loterias: list, jornada: str | None, pregunta: str, id: str = "loteria") -> Paso:
    del_grupo = de_la_jornada(loterias, jornada)
    # La hora de cierre va en la etiqueta: es justo lo que el usuario necesita
    # para decidir, y evita que elija una que cierra en cinco minutos.
    #
    # Lo que se guarda NO es el nombre sino la identidad completa que devuelve
    # el backend. Así el front arma la compra por código y no tiene que casar
    # nombres a mano — que además vienen en mayúsculas y sin tildes normalizar.
    pares = [
        (
            f"{l.nombre} — cierra {l.hora_texto}",
            {
                "codigo": l.codigo,
                "id": l.id_,
                "nombre": l.nombre,
                "nombreCorto": l.nombre_corto,
            },
        )
        for l in del_grupo
    ]
    return Paso(
        id=id,
        pregunta=pregunta,
        opciones=tuple(etiqueta for etiqueta, _ in pares),
        interpretar=lambda texto: elegir_de(texto, pares),
        ayuda="No encontré esa lotería en la lista. Elige una, o responde con su número.",
    )


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
