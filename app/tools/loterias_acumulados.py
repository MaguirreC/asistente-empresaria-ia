"""Loterías disponibles para Chance Millonario y Doble Play Regional.

Endpoints propios, distintos del de chance tradicional (`loterias.py`):

- **No reciben `date`.** El backend siempre devuelve las loterías de HOY (cada
  una trae su propia `fechaSorteo`). Estos dos productos no tienen paso de
  fecha en el flujo guiado — se juega con lo que hay disponible hoy.
- **`horaCierre` viene en formato 24 horas** ("21:50"), no en 12 horas con
  AM/PM como en `/chance/loterias` — hace falta un parser aparte.
- **Doble Play no siempre trae `nombre`** (llega `null` para varias loterías,
  solo `nombreCorto`). Se completa con `_NOMBRE_POR_CODIGO_CORTO`, la tabla de
  equivalencias que usa el front — no es una lista inventada aquí.
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

# Mismo mapeo que usa el front (`lotteryNameByShort`) para completar el
# nombre cuando el backend de Doble Play manda `nombre: null` y solo trae el
# código corto (`nombreCorto`).
_NOMBRE_POR_CODIGO_CORTO: dict[str, str] = {
    "QUIN": "LOTERIA DEL QUINDIO",
    "ANTD": "ANTIOQUEÑITA DIA",
    "DOMA": "DORADO MAÑANA",
    "CAFD": "CAFETERITO DIA",
    "CHOD": "CHONTICO DIA",
    "PA1D": "PAISITA DIA",
    "FADI": "FANTASTICA DIA",
    "PIJA": "PIJAO DE ORO",
    "CARD": "LA CARIBEÑA DIA",
    "DOTA": "EL DORADO TARDE",
    "ANTT": "ANTIOQUEÑITA TARDE",
    "PA2N": "PAISITA NOCHE",
    "CARN": "LA CARIBEÑA NOCHE",
    "CAFN": "CAFETERITO NOCHE",
    "SINO": "SINUANO NOCHE",
    "BOGO": "LOTERIA DE BOGOTA",
    "CHON": "CHONTICO NOCHE",
    "FANO": "LA FANTASTICA NOCHE",
    "SIND": "SINUANO DIA",
    "CUND": "LOTERIA CUNDINAMARCA",
    "TOLI": "LOTERIA TOLIMA",
    "SANT": "LOTERIA SANTANDER",
    "RISA": "LOTERIA RISARALDA",
    "MEDE": "LOTERIA DE MEDELLIN",
    "META": "LOTERIA META",
    "VALL": "LOTERIA VALLE",
    "MANI": "LOTERIA MANIZALES",
    "HUIL": "LOTERIA HUILA",
    "CRUZ": "LOTERIA DE LA CRUZ ROJA",
    "BOYA": "LOTERIA BOYACA",
    "CAUC": "LOTERIA CAUCA",
    "PI3D": "PICK 3 DIA",
    "PI3N": "PICK 3 NOCHE",
    "PI4D": "PICK 4 DIA",
    "PI4N": "PICK 4 NOCHE",
    "SAMA": "SAMAN DE LA SUERTE",
    "CULD": "CULONA DIA",
}


class _SinDatos(Exception):
    """No se pudo obtener el listado ni siquiera de una copia previa."""


def _parsear_hora(texto: str | None) -> hora_del_dia | None:
    try:
        return datetime.strptime(texto, "%H:%M").time()
    except (ValueError, TypeError):
        return None


def _nombre_completo(lot: dict) -> str:
    nombre_corto = lot.get("nombreCorto")
    return (
        lot.get("nombre")
        or _NOMBRE_POR_CODIGO_CORTO.get((nombre_corto or "").upper())
        or nombre_corto
        or "sin nombre"
    )


def _normalizar(datos: dict) -> list[Loteria]:
    loterias = (datos.get("listadoLoterias") or {}).get("loterias") or []
    normalizadas = [
        Loteria(
            nombre=_nombre_completo(lot),
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
