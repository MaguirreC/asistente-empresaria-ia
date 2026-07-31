"""Acumulados vigentes de Baloto, Revancha y las modalidades de chance.

Fuente: el sitio público de resultados (resultados.facilisimo.co), un dominio
aparte del backend de ventas. En una sola llamada, sin autenticación, trae
Baloto, Revancha, Chance Millonario y Doble Play (local y regional).

Los montos se formatean aquí, en código, no se le pide al modelo que reescriba
cifras de nueve o diez dígitos: es exactamente el tipo de cálculo donde un
modelo pequeño se equivoca, igual que pasó con las horas de cierre.
"""
import logging
import time

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TIMEOUT_SEGUNDOS = 15.0

# Los acumulados solo cambian tras cada sorteo, no en tiempo real, pero se
# refresca cada 10 minutos para no quedar con un valor viejo tras un sorteo.
CACHE_TTL_SEGUNDOS = 600

# (momento en que se guardó, datos crudos ya parseados) — una sola entrada,
# a diferencia de las loterías no hay una fecha que lo llave.
_cache: tuple[float, dict] | None = None


class _SinDatos(Exception):
    """No se pudo obtener el dato ni siquiera de una copia previa."""


def _formatear_pesos(valor: str | None) -> str:
    """'2043336752' -> '$2.043.336.752'. Si no es un número, lo deja tal cual."""
    try:
        return f"${int(valor):,}".replace(",", ".")
    except (TypeError, ValueError):
        return valor or "no informado"


def _consultar_backend() -> dict:
    respuesta = httpx.get(settings.resultados_acumulados_url, timeout=TIMEOUT_SEGUNDOS)
    respuesta.raise_for_status()
    datos = respuesta.json()

    # "estado" llega como el texto "true"/"false", no un booleano.
    if str(datos.get("estado")).lower() != "true":
        raise ValueError(f"El sitio de resultados respondió estado={datos.get('estado')}")

    return datos


def _obtener_datos() -> dict:
    """Datos crudos, desde el caché si está vigente."""
    global _cache

    if _cache and (time.monotonic() - _cache[0]) < CACHE_TTL_SEGUNDOS:
        return _cache[1]

    try:
        datos = _consultar_backend()
    except (httpx.HTTPError, ValueError) as e:
        logger.error("Fallo consultando acumulados: %s", e)
        if _cache is None:
            raise _SinDatos from e
        logger.warning("Usando acumulados en caché vencido")
        return _cache[1]

    _cache = (time.monotonic(), datos)
    return datos


def _lineas(datos: dict) -> list[str]:
    """Todas las cifras ya formateadas, una línea por producto."""
    lineas = []

    baloto = datos.get("acumulados-baloto") or {}
    if baloto.get("baloto"):
        lineas.append(
            f"- BALOTO: {_formatear_pesos(baloto.get('baloto'))} "
            f"(sorteo del {baloto.get('fecha-sorteo', 'fecha no informada')})"
        )
    if baloto.get("revancha"):
        lineas.append(f"- REVANCHA: {_formatear_pesos(baloto.get('revancha'))}")

    for item in (datos.get("lista-acumulados") or {}).get("acumulado", []):
        nombre = item.get("nombre-subproducto") or item.get("nombre-tipo-chance") or "sin nombre"
        cifras = item.get("cifras")
        monto = _formatear_pesos(item.get("premio-acumulado"))
        costo = _formatear_pesos(item.get("valor-fijo"))
        detalle = f" ({cifras} cifras)" if cifras else ""
        lineas.append(f"- {nombre}{detalle}: {monto} — cuesta {costo} jugarlo")

    return lineas


def resumen_para_usuario() -> str:
    """Listado completo, listo para mostrar sin pasar por el modelo."""
    try:
        lineas = _lineas(_obtener_datos())
    except _SinDatos:
        return (
            "No pude consultar los acumulados en este momento. "
            "Puedes verlos directamente en la página. 🙏"
        )

    if not lineas:
        return "No hay acumulados disponibles en este momento."

    return "Estos son los acumulados vigentes:\n" + "\n".join(lineas)


def acumulados_actuales() -> str:
    """Para el modelo: incluye los nombres tal como los da la fuente, para que
    pueda relacionar la pregunta del usuario (p. ej. "doble play local") con la
    entrada correcta aunque el usuario no use el nombre exacto del sistema.
    """
    try:
        lineas = _lineas(_obtener_datos())
    except _SinDatos:
        return (
            "No se pudo consultar los acumulados en este momento. "
            "Dile al usuario que los revise directamente en la página."
        )

    if not lineas:
        return "No hay acumulados disponibles en este momento."

    return (
        "Acumulados vigentes. Los montos YA ESTÁN FORMATEADOS: cópialos tal "
        "cual, no hagas cuentas ni los reescribas.\n" + "\n".join(lineas)
    )
