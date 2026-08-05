"""Flujo guiado para armar un tiquete de MiLoto.

Recoge, en este orden:

    cuántas apuestas (1 a 5) -> los 5 números de cada apuesta

y termina devolviéndole al front un formulario con todo listo para mapear.

A diferencia de Baloto, `knowledge/miloto.md` sí dice explícito que "en un
mismo tiquete se pueden hacer hasta 5 apuestas" — por eso el primer paso
pregunta cuántas, y luego se repite el paso de números esa cantidad de veces.
Cada apuesta cuesta $4.000 (fijo), así que el valor total es una cuenta, no
algo que se pregunte.

Los números "al azar" salen del propio backend de ventas
(`tools/numeros_aleatorios.py`), igual que en Baloto.
"""
import re

from app.flujos.motor import (
    Flujo,
    FlujoNoDisponible,
    Paso,
    pide_aleatorio,
    formatear_pesos,
    registrar,
)
from app.tools.numeros_aleatorios import numeros_aleatorios_miloto

VALOR_APUESTA = 4_000
MAXIMO_APUESTAS = 5

MIN_NUMERO, MAX_NUMERO = 1, 39

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
            "Vamos a armar tu MiLoto. 🎟️\n\n"
            f"¿Cuántas apuestas quieres hacer en este tiquete? Puedes hacer "
            f"hasta {MAXIMO_APUESTAS}, cada una cuesta {formatear_pesos(VALOR_APUESTA)}."
        ),
        opciones=tuple(str(i) for i in range(1, MAXIMO_APUESTAS + 1)),
        interpretar=_interpretar_cantidad,
        ayuda=f"Dime un número del 1 al {MAXIMO_APUESTAS}.",
    )


# --- Paso 2: los 5 números de cada apuesta -----------------------------------


def _interpretar_numeros(texto: str) -> list[str] | None:
    if texto.strip() == "1" or pide_aleatorio(texto):
        numeros = numeros_aleatorios_miloto()
        if numeros is None:
            raise FlujoNoDisponible(_MENSAJE_SIN_DATOS)
        return numeros

    crudos = re.split(r"[,\s]+", texto.strip())
    try:
        numeros = [int(n) for n in crudos if n]
    except ValueError:
        return None
    if len(numeros) != 5:
        return None
    if not all(MIN_NUMERO <= n <= MAX_NUMERO for n in numeros):
        return None
    if len(set(numeros)) != 5:  # sin repetir, dentro de la misma apuesta
        return None
    return [f"{n:02d}" for n in numeros]


def _paso_numeros(indice: int, cantidad: int) -> Paso:
    prefijo = (
        "¿Cuáles son tus 5 números?"
        if cantidad == 1
        else f"Apuesta {indice} de {cantidad} — ¿cuáles son tus 5 números?"
    )
    return Paso(
        id=f"numeros_{indice}",
        pregunta=(
            f"{prefijo}\n\n"
            f"Del {MIN_NUMERO} al {MAX_NUMERO}, sin repetir — separados por "
            "comas o espacios (ejemplo: 3, 9, 15, 27, 39)."
        ),
        opciones=("Elegirlos al azar 🎲",),
        interpretar=_interpretar_numeros,
        ayuda=(
            f"Necesito 5 números distintos entre {MIN_NUMERO} y {MAX_NUMERO}, "
            "separados por comas o espacios. También puedes pedirme que los "
            "elija al azar."
        ),
    )


# --- Ensamblado del flujo ------------------------------------------------------


def _siguiente_paso(datos: dict) -> Paso | None:
    if "cantidad" not in datos:
        return _paso_cantidad()
    cantidad = datos["cantidad"]
    for i in range(1, cantidad + 1):
        if f"numeros_{i}" not in datos:
            return _paso_numeros(i, cantidad)
    return None


def _apuestas(datos: dict) -> list[list[str]]:
    return [datos[f"numeros_{i}"] for i in range(1, datos["cantidad"] + 1)]


def _formulario(datos: dict) -> dict:
    apuestas = _apuestas(datos)
    return {
        "producto": "miloto",
        "apuestas": apuestas,
        "valor_por_apuesta": VALOR_APUESTA,
        "valor_total": VALOR_APUESTA * len(apuestas),
    }


def _resumen(datos: dict) -> str:
    apuestas = _apuestas(datos)
    detalle = "\n".join(f"• {', '.join(numeros)}" for numeros in apuestas)
    return (
        "¡Listo! Así queda tu MiLoto:\n\n"
        f"{detalle}\n\n"
        f"• **Total:** {formatear_pesos(VALOR_APUESTA * len(apuestas))}\n\n"
        "Te lo dejo cargado en la pantalla de MiLoto para que lo revises y "
        "confirmes la compra. 👇"
    )


registrar(
    Flujo(
        producto="miloto",
        siguiente_paso=_siguiente_paso,
        formulario=_formulario,
        resumen=_resumen,
    )
)
