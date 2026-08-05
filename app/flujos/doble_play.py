"""Flujo guiado para armar una apuesta de Doble Play Regional.

Misma mecánica que Chance Millonario (`chance_millonario.py`) — son de la
misma familia de juego (`knowledge/otras-modalidades.md`): se eligen **2
loterías** de las que juegan hoy y **5 números**, con la expectativa de que 2
de esos números coincidan con el resultado.

Solo se construye **Doble Play Regional** por ahora (4 cifras, eje cafetero).
Doble Play **Local** (3 cifras, Quindío) queda pendiente — no hay endpoint de
parámetros para esa variante todavía.

El valor **no se pregunta**: es fijo, $4.000.
"""
import re

from app.flujos.motor import (
    MAXIMO_SIN_AGRUPAR,
    Flujo,
    FlujoNoDisponible,
    Paso,
    formatear_pesos,
    numero_aleatorio,
    paso_jornada,
    paso_loteria,
    pide_aleatorio,
    registrar,
)
from app.tools.loterias_acumulados import loterias_doble_play_regional

VALOR_APUESTA = 4_000

CANTIDAD_NUMEROS = 5


# --- Las dos loterías -------------------------------------------------------


def _loterias_disponibles() -> list:
    loterias = loterias_doble_play_regional()
    if loterias is None:
        raise FlujoNoDisponible(
            "No pude consultar las loterías de Doble Play en este momento. 🙏 "
            "Inténtalo de nuevo en un rato, o ármalo directamente en la pantalla."
        )
    if not loterias:
        raise FlujoNoDisponible(
            "Hoy ya no hay loterías disponibles para Doble Play. Escríbeme de "
            "nuevo mañana."
        )
    return loterias


def _paso_primera_loteria(datos: dict) -> Paso:
    loterias = _loterias_disponibles()
    if len(loterias) > MAXIMO_SIN_AGRUPAR and "jornada_1" not in datos:
        return paso_jornada(loterias, id="jornada_1")
    return paso_loteria(
        loterias,
        datos.get("jornada_1"),
        pregunta="Vamos a armar tu Doble Play Regional. 🎯\n\n¿Cuál es la primera lotería que quieres jugar?",
        id="loteria_1",
    )


def _paso_segunda_loteria(datos: dict) -> Paso:
    loterias = [
        l for l in _loterias_disponibles() if l.codigo != datos["loteria_1"]["codigo"]
    ]
    if len(loterias) > MAXIMO_SIN_AGRUPAR and "jornada_2" not in datos:
        return paso_jornada(loterias, id="jornada_2")
    return paso_loteria(
        loterias,
        datos.get("jornada_2"),
        pregunta="¿Y la segunda lotería?",
        id="loteria_2",
    )


# --- Los 5 números -----------------------------------------------------------

_SOLO_DIGITOS = re.compile(r"\D")


def _interpretar_numero(texto: str) -> str | None:
    digitos = _SOLO_DIGITOS.sub("", texto)
    if len(digitos) == 4:
        return digitos
    if texto.strip() == "1" or pide_aleatorio(texto):
        return numero_aleatorio(4)
    return None


def _paso_numero(indice: int) -> Paso:
    return Paso(
        id=f"numero_{indice}",
        pregunta=(
            f"Número {indice} de {CANTIDAD_NUMEROS} — ¿cuál eliges?\n\n"
            "Siempre son **4 cifras** (0000 a 9999)."
        ),
        opciones=("Elegir un número al azar 🎲",),
        interpretar=_interpretar_numero,
        ayuda="Necesito un número de 4 cifras exactas. También puedes pedirme uno al azar.",
    )


# --- Ensamblado del flujo ----------------------------------------------------


def _siguiente_paso(datos: dict) -> Paso | None:
    if "loteria_1" not in datos:
        return _paso_primera_loteria(datos)
    if "loteria_2" not in datos:
        return _paso_segunda_loteria(datos)
    for i in range(1, CANTIDAD_NUMEROS + 1):
        if f"numero_{i}" not in datos:
            return _paso_numero(i)
    return None


def _formulario(datos: dict) -> dict:
    return {
        "producto": "doble_play_regional",
        "loterias": [datos["loteria_1"], datos["loteria_2"]],
        "numeros": [datos[f"numero_{i}"] for i in range(1, CANTIDAD_NUMEROS + 1)],
        "valor": VALOR_APUESTA,
    }


def _resumen(datos: dict) -> str:
    numeros = ", ".join(f"**{datos[f'numero_{i}']}**" for i in range(1, CANTIDAD_NUMEROS + 1))
    return (
        "¡Listo! Así queda tu Doble Play Regional:\n\n"
        f"• **Loterías:** {datos['loteria_1']['nombre']} y {datos['loteria_2']['nombre']}\n"
        f"• **Números:** {numeros}\n"
        f"• **Valor:** {formatear_pesos(VALOR_APUESTA)}\n\n"
        "Te lo dejo cargado en la pantalla de Doble Play para que lo revises "
        "y confirmes la compra. 👇"
    )


registrar(
    Flujo(
        producto="doble_play",
        siguiente_paso=_siguiente_paso,
        formulario=_formulario,
        resumen=_resumen,
    )
)
