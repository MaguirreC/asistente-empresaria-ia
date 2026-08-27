"""Flujo guiado para recargar la cuenta de Betplay.

El más simple de todos: un único paso, el monto a recargar. No hay números ni
modalidades que elegir — Betplay es una cuenta externa, así que el asistente
solo recoge el valor y se lo entrega a la pantalla de Betplay ya cargado, igual
que el resto de los flujos guiados (`formulario`).

No se valida un monto mínimo o máximo: a diferencia de Chance/Astro/Baloto,
esos límites no están confirmados en `knowledge/` para Betplay, y no se
inventan (ver CLAUDE.md). Si el negocio confirma un rango, agregarlo en
`_interpretar_monto`.
"""
import re

from app.flujos.motor import Flujo, Paso, formatear_pesos, registrar

_SOLO_DIGITOS = re.compile(r"\D")


def _interpretar_monto(texto: str) -> int | None:
    limpio = _SOLO_DIGITOS.sub("", texto)
    if not limpio:
        return None
    valor = int(limpio)
    return valor if valor > 0 else None


def _paso_monto() -> Paso:
    return Paso(
        id="monto",
        pregunta=(
            "¡Hola! Soy Facibot, y te ayudo a recargar tu cuenta de "
            "Betplay.\n\n¿Cuánto quieres recargar?"
        ),
        interpretar=_interpretar_monto,
        ayuda="Dime un monto en pesos. Por ejemplo: 8000.",
    )


def _siguiente_paso(datos: dict) -> Paso | None:
    if "monto" not in datos:
        return _paso_monto()
    return None


def _formulario(datos: dict) -> dict:
    return {"producto": "betplay", "monto": datos["monto"]}


def _resumen(datos: dict) -> str:
    return (
        f"¡Listo! Te dejo cargado el monto de {formatear_pesos(datos['monto'])} "
        "en la pantalla de Betplay para que confirmes la recarga. 👇"
    )


registrar(
    Flujo(
        producto="betplay",
        siguiente_paso=_siguiente_paso,
        formulario=_formulario,
        resumen=_resumen,
    )
)
