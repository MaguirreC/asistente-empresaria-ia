"""Flujo guiado para armar una apuesta de Chance.

Recoge, en este orden, lo mismo que pide la pantalla de Chance:

    fecha -> lotería -> número -> modalidades -> valor de cada modalidad

y termina devolviéndole al front un formulario con todo listo para mapear.

Por qué ese orden: la fecha define qué loterías hay disponibles, y el número
define cuánto paga cada modalidad (no cuáles se ofrecen — la pantalla muestra
las cuatro siempre). Preguntar al revés obligaría a rehacer pasos.

Los valores de premio salen de `knowledge/chance-tradicional.md`, que a su vez
cita el Decreto 1350 de 2003. Si cambian allá, hay que cambiarlos aquí.
"""
import re
from datetime import datetime, timedelta

from app.flujos.motor import Flujo, FlujoNoDisponible, Paso, elegir_de, registrar
from app.tools.loterias import BOGOTA, loterias_para_fecha

# Cuántos días hacia adelante deja elegir la pantalla (hoy + 6).
DIAS_DISPONIBLES = 7

# Mínimo por colilla, IVA incluido. Es la SUMA de la colilla la que debe
# cumplirlo, no cada modalidad por separado.
MINIMO_COLILLA = 600

_MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)
_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

# clave interna -> (etiqueta, paga con 3 cifras, paga con 4 cifras)
# La clave es la que viaja en el formulario; el front la mapea a su campo.
_MODALIDADES: dict[str, tuple[str, int, int]] = {
    "directo": ("Directo", 400, 4500),
    "combinado": ("Combinado", 83, 208),
    "pata": ("Pata", 50, 50),
    "una": ("Uña", 5, 5),
}


def _pesos(valor: int) -> str:
    return f"${valor:,.0f}".replace(",", ".")


# --- Paso 1: la fecha -----------------------------------------------------


def _fechas_disponibles() -> list[tuple[str, str]]:
    """(etiqueta, dd/MM/yyyy) para hoy y los días siguientes."""
    hoy = datetime.now(BOGOTA)
    pares = []
    for i in range(DIAS_DISPONIBLES):
        dia = hoy + timedelta(days=i)
        cuando = f"{_DIAS[dia.weekday()]} {dia.day} de {_MESES[dia.month - 1]}"
        etiqueta = f"Hoy — {cuando}" if i == 0 else cuando.capitalize()
        pares.append((etiqueta, dia.strftime("%d/%m/%Y")))
    return pares


def _paso_fecha() -> Paso:
    pares = _fechas_disponibles()
    return Paso(
        id="fecha",
        pregunta="Vamos a armar tu chance. 🍀\n\n¿Para qué día quieres jugar?",
        opciones=tuple(etiqueta for etiqueta, _ in pares),
        interpretar=lambda texto: elegir_de(texto, pares),
        ayuda="No identifiqué ese día. Elige uno de la lista, o responde con su número.",
    )


# --- Paso 2: la jornada, y paso 3: la lotería -----------------------------
#
# Un día cualquiera trae ~25 loterías. Listarlas todas en un chat es un muro de
# texto y 25 botones, así que primero se pregunta la jornada y después solo las
# de esa franja. Se agrupa por la HORA DE CIERRE real y no por el nombre: hay
# loterías sin jornada en el nombre (SAMAN, CRUZ ROJA, PIJAO DE ORO) y otras
# que la contradicen (PAISITA NOCHE cierra a las 5:45 p.m.).

# (etiqueta, desde, hasta) en horas. `None` es sin límite por ese lado.
_JORNADAS: tuple[tuple[str, int | None, int | None], ...] = (
    ("Mañana", None, 12),
    ("Mediodía", 12, 14),
    ("Tarde", 14, 18),
    ("Noche", 18, None),
)

# Por debajo de esto no vale la pena preguntar la jornada: se listan todas y se
# ahorra un paso.
_MAXIMO_SIN_AGRUPAR = 8


def _consultar(fecha: str) -> list[tuple]:
    """Loterías de la fecha, o se corta el flujo con un mensaje entendible.

    El listado viene cacheado 10 minutos en `tools.loterias`, así que llamarlo
    en cada paso no dispara una consulta nueva cada vez.
    """
    loterias = loterias_para_fecha(fecha)
    if loterias is None:
        raise FlujoNoDisponible(
            "No pude consultar las loterías en este momento. 🙏 Inténtalo de "
            "nuevo en un rato, o ármalo directamente en la pantalla de Chance."
        )
    if not loterias:
        raise FlujoNoDisponible(
            f"Para el {fecha} ya no hay loterías disponibles. Escríbeme de "
            "nuevo si quieres intentar con otro día."
        )
    return loterias


def _de_la_jornada(loterias: list[tuple], jornada: str | None) -> list[tuple]:
    if jornada is None:
        return loterias
    desde, hasta = next((d, h) for e, d, h in _JORNADAS if e == jornada)
    return [
        l
        for l in loterias
        if l.hora is not None
        and (desde is None or l.hora.hour >= desde)
        and (hasta is None or l.hora.hour < hasta)
    ]


def _paso_jornada(loterias: list[tuple]) -> Paso:
    pares = []
    for etiqueta, _, _ in _JORNADAS:
        del_grupo = _de_la_jornada(loterias, etiqueta)
        if not del_grupo:
            continue  # nada abierto en esa franja: no se ofrece
        primera, ultima = del_grupo[0].hora_texto, del_grupo[-1].hora_texto
        rango = primera if len(del_grupo) == 1 else f"{primera} a {ultima}"
        pares.append((f"{etiqueta} — {len(del_grupo)} loterías, cierran {rango}", etiqueta))

    return Paso(
        id="jornada",
        pregunta=(
            f"Hay {len(loterias)} loterías disponibles. ¿A qué hora quieres jugar?"
        ),
        opciones=tuple(etiqueta for etiqueta, _ in pares),
        interpretar=lambda texto: elegir_de(texto, pares),
        ayuda="Elige una franja de la lista, o responde con su número.",
    )


def _paso_loteria(loterias: list[tuple], jornada: str | None) -> Paso:
    del_grupo = _de_la_jornada(loterias, jornada)
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
        id="loteria",
        pregunta="¿Con qué lotería quieres jugar?",
        opciones=tuple(etiqueta for etiqueta, _ in pares),
        interpretar=lambda texto: elegir_de(texto, pares),
        ayuda="No encontré esa lotería en la lista. Elige una, o responde con su número.",
    )


# --- Paso 3: el número ----------------------------------------------------

_SOLO_DIGITOS = re.compile(r"\D")


def _interpretar_numero(texto: str) -> str | None:
    digitos = _SOLO_DIGITOS.sub("", texto)
    return digitos if len(digitos) in (3, 4) else None


def _paso_numero() -> Paso:
    return Paso(
        id="numero",
        pregunta=(
            "¿A qué número quieres jugar?\n\n"
            "Puede ser de **4 cifras** (0000 a 9999) o de **3 cifras** "
            "(000 a 999). Con 3 cifras, si ganas recibes un 80% adicional."
        ),
        interpretar=_interpretar_numero,
        ayuda="Necesito un número de 3 o 4 cifras. Por ejemplo: 1234, o 567.",
    )


# --- Paso 4: las modalidades ----------------------------------------------


def _interpretar_modalidades(texto: str) -> tuple[str, ...] | None:
    """Selección múltiple: admite números, nombres, o las dos cosas mezcladas.

    Se devuelven en el orden de `_MODALIDADES` y sin repetir, para que el
    formulario final sea estable sin importar cómo lo haya escrito el usuario.
    """
    claves = list(_MODALIDADES)
    pares = [(_MODALIDADES[c][0], c) for c in claves]

    if re.search(r"\btodas?\b", texto, re.IGNORECASE):
        return tuple(claves)

    # "1 y 3", "1,3", "directo y pata" -> se parte y se resuelve cada trozo.
    trozos = [t for t in re.split(r"[,;/]|\sy\s|\se\s|\+", texto) if t.strip()]
    elegidas = {elegir_de(trozo, pares) for trozo in trozos}
    elegidas.discard(None)

    if not elegidas:
        return None
    return tuple(c for c in claves if c in elegidas)


def _paso_modalidades(numero: str) -> Paso:
    cifras = len(numero)
    indice = 1 if cifras == 3 else 2
    lineas = "\n".join(
        f"• **{_MODALIDADES[c][0]}** — paga {_pesos(_MODALIDADES[c][indice])} "
        f"por cada peso apostado"
        for c in _MODALIDADES
    )
    return Paso(
        id="modalidades",
        pregunta=(
            f"Vas a jugar el **{numero}**. ¿En qué modalidades quieres apostarlo?\n\n"
            f"{lineas}\n\n"
            "Puedes elegir **varias**: dime cuáles, separadas por coma."
        ),
        opciones=tuple(_MODALIDADES[c][0] for c in _MODALIDADES),
        interpretar=_interpretar_modalidades,
        ayuda=(
            "No identifiqué la modalidad. Puedes responder con los números "
            "(por ejemplo `1, 3`), con los nombres (`directo y pata`) o `todas`."
        ),
    )


# --- Paso 5: el valor de cada modalidad -----------------------------------

_MONEDA = re.compile(r"[^\d]")


def _interpretar_valor(texto: str, falta_por_repartir: bool, ya_puesto: int):
    """Valor en pesos de una modalidad.

    Si es la ÚLTIMA modalidad pendiente se valida además el mínimo de la
    colilla: el mínimo aplica a la suma, no a cada modalidad, así que antes de
    la última todavía no se puede saber si se va a cumplir.
    """
    limpio = _MONEDA.sub("", texto)
    if not limpio:
        return None
    valor = int(limpio)
    if valor <= 0:
        return None
    if not falta_por_repartir and (ya_puesto + valor) < MINIMO_COLILLA:
        return None
    return valor


def _paso_valor(clave: str, datos: dict) -> Paso:
    etiqueta, paga3, paga4 = _MODALIDADES[clave]
    paga = paga3 if len(datos["numero"]) == 3 else paga4

    pendientes = [
        c for c in datos["modalidades"] if f"valor_{c}" not in datos
    ]
    es_la_ultima = len(pendientes) == 1
    ya_puesto = sum(
        datos[f"valor_{c}"] for c in datos["modalidades"] if f"valor_{c}" in datos
    )

    if es_la_ultima and ya_puesto < MINIMO_COLILLA:
        ayuda = (
            f"El mínimo por colilla es {_pesos(MINIMO_COLILLA)} sumando todo. "
            f"Llevas {_pesos(ya_puesto)}, así que aquí necesito al menos "
            f"{_pesos(MINIMO_COLILLA - ya_puesto)}."
        )
    else:
        ayuda = "Dime un valor en pesos. Por ejemplo: 1000."

    return Paso(
        id=f"valor_{clave}",
        pregunta=(
            f"¿Cuánto quieres apostar en **{etiqueta}**?\n\n"
            f"Paga {_pesos(paga)} por cada peso."
        ),
        interpretar=lambda texto: _interpretar_valor(texto, not es_la_ultima, ya_puesto),
        ayuda=ayuda,
    )


# --- Ensamblado del flujo -------------------------------------------------


def _siguiente_paso(datos: dict) -> Paso | None:
    if "fecha" not in datos:
        return _paso_fecha()

    if "loteria" not in datos:
        loterias = _consultar(datos["fecha"])
        # La jornada solo se pregunta cuando hay demasiadas para listarlas de
        # una. Si son pocas, ese paso no existe y `jornada` nunca entra a los
        # datos: `_paso_loteria` lo interpreta como "todas".
        if len(loterias) > _MAXIMO_SIN_AGRUPAR and "jornada" not in datos:
            return _paso_jornada(loterias)
        return _paso_loteria(loterias, datos.get("jornada"))

    if "numero" not in datos:
        return _paso_numero()
    if "modalidades" not in datos:
        return _paso_modalidades(datos["numero"])
    for clave in datos["modalidades"]:
        if f"valor_{clave}" not in datos:
            return _paso_valor(clave, datos)
    return None


def _formulario(datos: dict) -> dict:
    return {
        "producto": "chance",
        "fecha": datos["fecha"],
        # Objeto completo, no solo el nombre: el front elige por qué campo
        # mapea (`codigo` o `id`) sin depender de comparar cadenas.
        "loteria": datos["loteria"],
        "numero": datos["numero"],
        # clave de modalidad -> pesos. Solo las que el usuario eligió.
        "apuestas": {c: datos[f"valor_{c}"] for c in datos["modalidades"]},
    }


def _resumen(datos: dict) -> str:
    detalle = "\n".join(
        f"• {_MODALIDADES[c][0]}: {_pesos(datos[f'valor_{c}'])}"
        for c in datos["modalidades"]
    )
    total = sum(datos[f"valor_{c}"] for c in datos["modalidades"])
    return (
        "¡Listo! Así queda tu chance:\n\n"
        f"• **Fecha:** {datos['fecha']}\n"
        f"• **Lotería:** {datos['loteria']['nombre']}\n"
        f"• **Número:** {datos['numero']}\n"
        f"{detalle}\n"
        f"• **Total:** {_pesos(total)}\n\n"
        "Te lo dejo cargado en la pantalla de Chance para que lo revises y "
        "confirmes la compra. 👇"
    )


registrar(
    Flujo(
        producto="chance",
        siguiente_paso=_siguiente_paso,
        formulario=_formulario,
        resumen=_resumen,
    )
)
