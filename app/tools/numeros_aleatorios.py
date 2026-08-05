"""Números aleatorios oficiales para Baloto y MiLoto.

A diferencia de Astro y Chance (donde el número lo genera este servicio con
`random`), acá el usuario pidió usar el generador **del propio backend de
ventas** — son los mismos endpoints que usa la pantalla real, así que el
número "al azar" que arma el asistente es idéntico al que daría la pantalla.

Sin caché a propósito: cada llamada debe traer números nuevos. Cachear esto
sería literalmente lo contrario de "al azar".
"""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TIMEOUT_SEGUNDOS = 15.0


def _consultar(ruta: str) -> list[str] | None:
    try:
        respuesta = httpx.get(f"{settings.backend_base_url}/{ruta}", timeout=TIMEOUT_SEGUNDOS)
        respuesta.raise_for_status()
        datos = respuesta.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.error("Fallo consultando %s: %s", ruta, e)
        return None
    return datos if isinstance(datos, list) else None


def numeros_aleatorios_baloto() -> tuple[list[str], str] | None:
    """(5 números 1-43, superbalota 1-16), o `None` si no se pudo consultar.

    El endpoint devuelve los 6 juntos en una sola lista — los primeros 5 son
    las bolas principales y la última es la superbalota (confirmado
    probando el endpoint real: los primeros 5 valores superan 16 en algunas
    corridas, la superbalota nunca).
    """
    datos = _consultar("baloto/numeros-aleatorios")
    if datos is None or len(datos) != 6:
        return None
    return datos[:5], datos[5]


def numeros_aleatorios_miloto() -> list[str] | None:
    """5 números (1-39), o `None` si no se pudo consultar. MiLoto no tiene
    balota secundaria (a diferencia de Baloto), por eso son solo 5."""
    datos = _consultar("miloto/numeros-aleatorios")
    if datos is None or len(datos) != 5:
        return None
    return datos
