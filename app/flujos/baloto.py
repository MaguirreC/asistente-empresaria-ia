"""Flujo guiado para armar un tiquete de Baloto (+ Revancha opcional).

Recoge, en este orden:

    cuántas apuestas (1 a 5) -> por cada una: 5 números (1-43) + superbalota
    (1-16) -> ¿juegas Revancha? (una sola vez, para todo el tiquete)

y termina devolviéndole al front un formulario con todo listo para mapear.

Igual que MiLoto: un tiquete admite **hasta 5 apuestas**, cada una con sus
propios números y su propia superbalota — confirmado con el negocio (el
documento de conocimiento no lo decía explícito, a diferencia de MiLoto).
Revancha, en cambio, **no es por apuesta**: se juega una sola vez por
tiquete, por $3.000 adicionales en total, sin importar cuántas apuestas haya
— coincide con `/baloto/reglas`, donde el addon REVANCHA trae
`"maxBoards": 1` a diferencia del juego principal (`"maxBoards": 5`).

Los números "al azar" salen del propio backend de ventas
(`tools/numeros_aleatorios.py`), no de este servicio: así el número que arma
el asistente es idéntico al que daría la pantalla real.
"""
import re

from app.flujos.motor import (
    Flujo,
    FlujoNoDisponible,
    Paso,
    elegir_de,
    formatear_pesos,
    pide_aleatorio,
    registrar,
)
from app.tools.numeros_aleatorios import numeros_aleatorios_baloto

VALOR_BALOTO = 6_000
VALOR_REVANCHA = 3_000
MAXIMO_APUESTAS = 5

MIN_NUMERO, MAX_NUMERO = 1, 43
MIN_SUPERBALOTA, MAX_SUPERBALOTA = 1, 16

_MENSAJE_SIN_DATOS = (
    "No pude consultar los números al azar en este momento. 🙏 Inténtalo de "
    "nuevo en un rato, o escribe tú mismo los números."
)


# --- Paso 1: cuántas apuestas -------------------------------------------------


def _interpretar_cantidad(texto: str) -> int | None:
    limpio = re.sub(r"\D", "", texto)
    if not limpio:
        return None
    n = int(limpio)
    return n if 1 <= n <= MAXIMO_APUESTAS else None


def _paso_cantidad() -> Paso:
    return Paso(
        id="cantidad",
        pregunta=(
            "Vamos a armar tu Baloto. 🎱\n\n"
            f"¿Cuántas apuestas quieres hacer en este tiquete? Puedes hacer "
            f"hasta {MAXIMO_APUESTAS}, cada una cuesta {formatear_pesos(VALOR_BALOTO)}."
        ),
        opciones=tuple(str(i) for i in range(1, MAXIMO_APUESTAS + 1)),
        interpretar=_interpretar_cantidad,
        ayuda=f"Dime un número del 1 al {MAXIMO_APUESTAS}.",
    )


# --- Paso 2: los 5 números de cada apuesta -----------------------------------


def _interpretar_numeros(texto: str) -> list[str] | None:
    if texto.strip() == "1" or pide_aleatorio(texto):
        datos = numeros_aleatorios_baloto()
        if datos is None:
            raise FlujoNoDisponible(_MENSAJE_SIN_DATOS)
        return datos[0]

    crudos = re.split(r"[,\s]+", texto.strip())
    try:
        numeros = [int(n) for n in crudos if n]
    except ValueError:
        return None
    if len(numeros) != 5:
        return None
    if not all(MIN_NUMERO <= n <= MAX_NUMERO for n in numeros):
        return None
    if len(set(numeros)) != 5:  # deben ser distintos entre sí
        return None
    return [f"{n:02d}" for n in numeros]


def _paso_numeros(indice: int, cantidad: int) -> Paso:
    prefijo = (
        "Dime tus **5 números**"
        if cantidad == 1
        else f"Apuesta {indice} de {cantidad} — dime sus **5 números**"
    )
    return Paso(
        id=f"numeros_{indice}",
        pregunta=(
            f"{prefijo}, del {MIN_NUMERO} al {MAX_NUMERO} y sin repetir — "
            "separados por comas o espacios (ejemplo: 5, 12, 23, 34, 43)."
        ),
        opciones=("Elegirlos al azar 🎲",),
        interpretar=_interpretar_numeros,
        ayuda=(
            f"Necesito 5 números distintos entre {MIN_NUMERO} y {MAX_NUMERO}, "
            "separados por comas o espacios. También puedes pedirme que los "
            "elija al azar."
        ),
    )


# --- Paso 3: la superbalota de cada apuesta ----------------------------------


def _interpretar_superbalota(texto: str) -> str | None:
    if texto.strip() == "1" or pide_aleatorio(texto):
        datos = numeros_aleatorios_baloto()
        if datos is None:
            raise FlujoNoDisponible(_MENSAJE_SIN_DATOS)
        return datos[1]

    limpio = re.sub(r"\D", "", texto)
    if not limpio:
        return None
    n = int(limpio)
    if not MIN_SUPERBALOTA <= n <= MAX_SUPERBALOTA:
        return None
    return f"{n:02d}"


def _paso_superbalota(indice: int, numeros: list[str]) -> Paso:
    return Paso(
        id=f"superbalota_{indice}",
        pregunta=(
            f"Para el **{', '.join(numeros)}** — ¿cuál es tu Superbalota? "
            f"Un número del {MIN_SUPERBALOTA} al {MAX_SUPERBALOTA}."
        ),
        opciones=("Elegirla al azar 🎲",),
        interpretar=_interpretar_superbalota,
        ayuda=(
            f"Necesito un número entre {MIN_SUPERBALOTA} y {MAX_SUPERBALOTA}. "
            "También puedes pedirme que la elija al azar."
        ),
    )


# --- Paso 4: Revancha, una sola vez por todo el tiquete ----------------------


def _paso_revancha() -> Paso:
    pares = [
        (f"Sí, jugar Revancha también (+{formatear_pesos(VALOR_REVANCHA)})", True),
        ("No, solo Baloto", False),
    ]
    return Paso(
        id="revancha",
        pregunta=(
            "¿Quieres jugar también Revancha? Participas con los mismos "
            f"números en un acumulado aparte, por {formatear_pesos(VALOR_REVANCHA)} "
            "más — una sola vez, sin importar cuántas apuestas hayas hecho."
        ),
        opciones=tuple(etiqueta for etiqueta, _ in pares),
        interpretar=lambda texto: elegir_de(texto, pares),
        ayuda="Responde sí o no, o con el número de la opción.",
    )


# --- Ensamblado del flujo ------------------------------------------------------


def _siguiente_paso(datos: dict) -> Paso | None:
    if "cantidad" not in datos:
        return _paso_cantidad()
    cantidad = datos["cantidad"]
    for i in range(1, cantidad + 1):
        if f"numeros_{i}" not in datos:
            return _paso_numeros(i, cantidad)
        if f"superbalota_{i}" not in datos:
            return _paso_superbalota(i, datos[f"numeros_{i}"])
    if "revancha" not in datos:
        return _paso_revancha()
    return None


def _apuestas(datos: dict) -> list[dict]:
    return [
        {"numeros": datos[f"numeros_{i}"], "superbalota": datos[f"superbalota_{i}"]}
        for i in range(1, datos["cantidad"] + 1)
    ]


def _valor_total(datos: dict) -> int:
    return VALOR_BALOTO * datos["cantidad"] + (VALOR_REVANCHA if datos["revancha"] else 0)


def _formulario(datos: dict) -> dict:
    return {
        "producto": "baloto",
        "apuestas": _apuestas(datos),
        "revancha": datos["revancha"],
        "valor": _valor_total(datos),
    }


def _resumen(datos: dict) -> str:
    detalle = "\n".join(
        f"• {', '.join(a['numeros'])} — Superbalota {a['superbalota']}"
        for a in _apuestas(datos)
    )
    revancha = "Sí" if datos["revancha"] else "No"
    return (
        "¡Listo! Así queda tu Baloto:\n\n"
        f"{detalle}\n\n"
        f"• **Revancha:** {revancha}\n"
        f"• **Total:** {formatear_pesos(_valor_total(datos))}\n\n"
        "Te lo dejo cargado en la pantalla de Baloto para que lo revises y "
        "confirmes la compra. 👇\n\n"
        "Recuerda: si es tu primera vez jugando Baloto, la plataforma te va "
        "a pedir un registro adicional que exige el operador del juego."
    )


registrar(
    Flujo(
        producto="baloto",
        siguiente_paso=_siguiente_paso,
        formulario=_formulario,
        resumen=_resumen,
    )
)
