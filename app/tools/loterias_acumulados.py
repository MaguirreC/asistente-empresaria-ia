"""Loterías disponibles para Chance Millonario y Doble Play Regional.

Endpoints propios, distintos del de chance tradicional (`loterias.py`):

- **No reciben `date`.** El backend siempre devuelve las loterías de HOY (cada
  una trae su propia `fechaSorteo`). Estos dos productos no tienen paso de
  fecha en el flujo guiado — se juega con lo que hay disponible hoy.
- **`horaCierre` viene en formato 24 horas** ("21:50"), no en 12 horas con
  AM/PM como en `/chance/loterias` — hace falta un parser aparte.
- **Doble Play no siempre trae `nombre`** (llega `null` para varias loterías,
  solo `nombreCorto`). No se inventa el nombre completo a partir del código
  corto: se muestra tal cual lo da el backend.
"""
import logging
import time
from datetime import datetime, time as hora_del_dia

import httpx

from app.config import settings
from app.tools.loterias import BOGOTA, Loteria

logger = logging.getLogger(__name__)

TIMEOUT_SEGUNDOS = 30.0
CACHE_TTL_SEGUNDOS = 600  # 10 minutos, igual que el chance tradicional


class _SinDatos(Exception):
    """No se pudo obtener el listado ni siquiera de una copia previa."""


def _parsear_hora(texto: str | None) -> hora_del_dia | None:
    try:
        return datetime.strptime(texto, "%H:%M").time()
    except (ValueError, TypeError):
        return None


def _normalizar(datos: dict) -> list[Loteria]:
    loterias = (datos.get("listadoLoterias") or {}).get("loterias") or []
    normalizadas = [
        Loteria(
            nombre=lot.get("nombre") or lot.get("nombreCorto") or "sin nombre",
            hora_texto=lot.get("horaCierre") or "hora no informada",
            hora=_parsear_hora(lot.get("horaCierre")),
            codigo=lot.get("codigo"),
            id_=lot.get("id"),
            nombre_corto=lot.get("nombreCorto"),
        )
        for lot in loterias
    ]
    return sorted(normalizadas, key=lambda l: (l.hora is None, l.hora or hora_del_dia.min))


# ruta del endpoint -> (momento en que se guardó, loterías normalizadas)
_cache: dict[str, tuple[float, list[Loteria]]] = {}


def _consultar(ruta: str) -> list[Loteria]:
    respuesta = httpx.get(f"{settings.backend_base_url}/{ruta}", timeout=TIMEOUT_SEGUNDOS)
    respuesta.raise_for_status()
    datos = respuesta.json()
    if not datos.get("estado"):
        raise ValueError(f"El backend respondió estado=false: {datos.get('error')}")
    return _normalizar(datos)


def _obtener(ruta: str) -> list[Loteria]:
    """Listado desde el caché si está vigente, igual que `loterias.py`."""
    guardado = _cache.get(ruta)
    if guardado and (time.monotonic() - guardado[0]) < CACHE_TTL_SEGUNDOS:
        return guardado[1]

    try:
        loterias = _consultar(ruta)
    except (httpx.HTTPError, ValueError) as e:
        logger.error("Fallo consultando %s: %s", ruta, e)
        if guardado is None:
            raise _SinDatos from e
        logger.warning("Usando listado en caché vencido de %s", ruta)
        return guardado[1]

    _cache[ruta] = (time.monotonic(), loterias)
    return loterias


def _vigentes(loterias: list[Loteria]) -> list[Loteria]:
    """Solo las que no han cerrado. Ofrecer una ya cerrada sería dejar armar
    una compra que el front va a rechazar después."""
    ahora = datetime.now(BOGOTA).time()
    return [l for l in loterias if l.hora is None or l.hora > ahora]


def loterias_chance_millonario() -> list[Loteria] | None:
    """Loterías vigentes hoy para Chance Millonario, o `None` si no se pudo
    consultar (distinto de `[]`, que sería "hoy no juega ninguna")."""
    try:
        return _vigentes(_obtener("chance-millonario/parametros"))
    except _SinDatos:
        return None


def loterias_doble_play_regional() -> list[Loteria] | None:
    """Loterías vigentes hoy para Doble Play Regional. Mismo contrato de
    `None` vs. `[]` que `loterias_chance_millonario`."""
    try:
        return _vigentes(_obtener("doble-play/parametros"))
    except _SinDatos:
        return None
