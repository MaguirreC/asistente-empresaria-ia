"""Loterías disponibles hoy con su hora de cierre de ventas.

Estos datos cambian a diario, por eso se consultan en vivo al backend en lugar
de guardarse en la base de conocimiento.

El estado de cada lotería (abierta o cerrada, y cuánto falta) se calcula aquí y
no en el modelo. Comparar horas es donde los modelos pequeños se equivocan, y
equivocarse significa decirle a un cliente que no alcanza a jugar cuando sí.

Expone dos vistas del mismo dato:
  - `loterias_del_dia`      -> para el modelo, cuando la pregunta tiene matices.
  - `resumen_para_usuario`  -> para el router, sin gastar tokens.
"""
import logging
import time
from datetime import datetime, time as hora_del_dia
from zoneinfo import ZoneInfo

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# El backend y las loterías operan en hora de Colombia. Calcular la fecha en UTC
# haría que después de las 7 p.m. se consultara el día siguiente.
BOGOTA = ZoneInfo("America/Bogota")

# El endpoint consulta a SETA, que es un tercero y puede tardar.
TIMEOUT_SEGUNDOS = 30.0

# El listado del día es el mismo para todos los usuarios, así que se guarda en
# memoria: solo el primero paga la espera y se alivia la carga sobre SETA.
CACHE_TTL_SEGUNDOS = 600  # 10 minutos

# fecha -> (momento en que se guardó, loterías normalizadas)
_cache: dict[str, tuple[float, list[tuple[str, str, hora_del_dia | None]]]] = {}

# Nombre del día en español, calculado en código. El modelo NUNCA debe deducir
# el día de la semana a partir de la fecha numérica: ya vimos que se equivoca
# (dijo "martes" para un 29 de julio que en realidad cayó miércoles). Es el
# mismo tipo de cálculo que las horas de cierre — si el código puede hacerlo
# sin margen de error, no se le deja al modelo.
_NOMBRES_DIA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _dia_semana(fecha: datetime) -> str:
    return _NOMBRES_DIA[fecha.weekday()]


class _SinDatos(Exception):
    """No se pudo obtener el listado ni siquiera de una copia previa."""


def _parsear_hora(texto: str | None) -> hora_del_dia | None:
    try:
        return datetime.strptime(texto, "%I:%M %p").time()
    except (ValueError, TypeError):
        return None


def _describir_espera(minutos: int) -> str:
    if minutos < 60:
        return f"faltan {minutos} minutos"
    horas, resto = divmod(minutos, 60)
    return f"faltan {horas} h" if resto == 0 else f"faltan {horas} h {resto} min"


def _consultar_backend(fecha: str) -> list[tuple[str, str, hora_del_dia | None]]:
    """Pide el listado al backend y lo normaliza, ordenado por hora de cierre."""
    respuesta = httpx.get(
        f"{settings.backend_base_url}/chance/loterias",
        params={"codeProducto": settings.chance_code_producto, "date": fecha},
        timeout=TIMEOUT_SEGUNDOS,
    )
    respuesta.raise_for_status()
    datos = respuesta.json()

    if not datos.get("estado"):
        raise ValueError(f"El backend respondió estado=false: {datos.get('error')}")

    loterias = (datos.get("listadoLoterias") or {}).get("loterias") or []
    normalizadas = [
        (
            lot.get("nombre") or "sin nombre",
            lot.get("horaCierre") or "hora no informada",
            _parsear_hora(lot.get("horaCierre")),
        )
        for lot in loterias
    ]
    return sorted(normalizadas, key=lambda l: (l[2] is None, l[2] or hora_del_dia.min))


def _obtener_loterias(fecha: str) -> list[tuple[str, str, hora_del_dia | None]]:
    """Listado del día, desde el caché si está vigente.

    Si el backend falla pero hay una copia del mismo día, la devuelve: un dato
    de hace unos minutos es mejor que no responder. Nunca sirve otra fecha.
    """
    guardado = _cache.get(fecha)
    if guardado and (time.monotonic() - guardado[0]) < CACHE_TTL_SEGUNDOS:
        return guardado[1]

    try:
        loterias = _consultar_backend(fecha)
    except (httpx.HTTPError, ValueError) as e:
        logger.error("Fallo consultando loterías: %s", e)
        if guardado is None:
            raise _SinDatos from e
        logger.warning("Usando listado en caché vencido del %s", fecha)
        return guardado[1]

    _cache.clear()  # solo interesa el día en curso
    _cache[fecha] = (time.monotonic(), loterias)
    return loterias


def _clasificar(
    loterias: list[tuple[str, str, hora_del_dia | None]], ahora: datetime
) -> tuple[list[tuple[str, str, int]], list[str], list[str]]:
    """Separa en abiertas (con minutos restantes), cerradas y sin hora legible."""
    minutos_ahora = ahora.hour * 60 + ahora.minute
    abiertas, cerradas, indefinidas = [], [], []
    for nombre, hora_texto, hora in loterias:
        if hora is None:
            indefinidas.append(nombre)
            continue
        restantes = (hora.hour * 60 + hora.minute) - minutos_ahora
        if restantes > 0:
            abiertas.append((nombre, hora_texto, restantes))
        else:
            cerradas.append(nombre)
    return abiertas, cerradas, indefinidas


def resumen_para_usuario() -> str:
    """Listado listo para mostrar, sin pasar por el modelo.

    Lo usa el router cuando la pregunta es claramente por los horarios del día.
    El dato ya está completo: redactarlo con un modelo sería pagar por reescribir
    lo que el código ya sabe.
    """
    ahora = datetime.now(BOGOTA)
    fecha = ahora.strftime("%d/%m/%Y")

    try:
        loterias = _obtener_loterias(fecha)
    except _SinDatos:
        return (
            "No pude consultar los horarios en este momento. "
            "Puedes verlos directamente en la página. 🙏"
        )

    if not loterias:
        return f"Para hoy ({fecha}) no hay loterías disponibles."

    abiertas, cerradas, _ = _clasificar(loterias, ahora)

    partes = [
        f"Loterías de hoy, {_dia_semana(ahora)} {fecha}. "
        f"Son las {ahora.strftime('%I:%M %p')}.\n"
    ]
    if abiertas:
        partes.append("Todavía abiertas:")
        partes += [
            f"• {nombre} — cierra {hora} ({_describir_espera(min_)})"
            for nombre, hora, min_ in abiertas
        ]
    else:
        partes.append("Ya cerraron todas las loterías de hoy.")
    if cerradas:
        partes.append(f"\nYa cerraron: {', '.join(cerradas)}.")
    return "\n".join(partes)


def loterias_del_dia() -> str:
    """Listado con el estado ya resuelto, para que el modelo solo lo relate."""
    ahora = datetime.now(BOGOTA)
    fecha = ahora.strftime("%d/%m/%Y")  # el backend exige dd/MM/yyyy

    try:
        loterias = _obtener_loterias(fecha)
    except _SinDatos:
        return (
            "No se pudo consultar el listado de loterías en este momento. "
            "Dile al usuario que lo revise directamente en la página."
        )

    if not loterias:
        return f"Para hoy ({fecha}) no hay loterías disponibles."

    abiertas, cerradas, indefinidas = _clasificar(loterias, ahora)
    lineas = [
        f"- {nombre}: cierra a las {hora} — ABIERTA, {_describir_espera(min_)}"
        for nombre, hora, min_ in abiertas
    ]
    lineas += [f"- {nombre}: YA CERRÓ para hoy" for nombre in cerradas]
    lineas += [
        f"- {nombre}: no se pudo determinar si sigue abierta" for nombre in indefinidas
    ]

    return (
        f"Fecha: {fecha}. Hoy es {_dia_semana(ahora)}. "
        f"Hora actual en Colombia: {ahora.strftime('%I:%M %p')}.\n"
        f"El día de la semana y el estado de cada lotería YA ESTÁN CALCULADOS: "
        f"úsalos tal cual. No calcules ni adivines el día de la semana a partir "
        f"de la fecha numérica, y no vuelvas a comparar las horas por tu cuenta.\n"
        f"Listado de loterías de hoy ({len(loterias)}):\n"
        + "\n".join(lineas)
    )
