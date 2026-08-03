"""Registro de consultas: qué preguntan los usuarios y qué no se supo responder.

Existe para una sola cosa: **saber qué le falta al asistente**. Sin esto, la
base de conocimiento solo puede crecer adivinando. Con esto, se puede ordenar
por lo que la gente pregunta de verdad.

## Privacidad

Las preguntas pueden traer datos personales sin que el usuario lo piense
("mi cédula es...", "mi correo es..."). Guardarlas es tratamiento de datos
personales y le aplica la Ley 1581, la misma que documentamos en
`knowledge/legal-y-juego-responsable.md`. Por eso:

- Se **enmascara antes de guardar**: correos, documentos y teléfonos nunca
  llegan a la base.
- Los registros **expiran solos** (`TTL_DIAS`), no se acumulan para siempre.
- **No se guarda nada que identifique al usuario.** El servicio no recibe su
  identidad y conviene que siga así.

## Nunca puede romper una respuesta

Registrar es secundario: si DynamoDB falla, se anota en el log y ya. Un fallo
de analítica jamás debe hacerle perder la respuesta a un usuario.
"""
import logging
import re
import time
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings

logger = logging.getLogger(__name__)

# Timeouts cortos y sin reintentos largos: esto corre después de haberle
# respondido al usuario, y no vale la pena sostener el worker por una métrica.
_CONFIG_BOTO = Config(
    connect_timeout=3, read_timeout=3, retries={"max_attempts": 2, "mode": "standard"}
)

# --- Enmascarado -----------------------------------------------------------
# Se aplica ANTES de guardar. Es deliberadamente agresivo: perder un dato de
# análisis es barato, guardar la cédula de alguien no.
_PATRONES_SENSIBLES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"[\w\.\-\+]+@[\w\.\-]+\.\w+"), "[correo]"),
    # 6 dígitos o más: cubre cédulas (6-10), teléfonos (10) y tarjetas (16).
    # Por debajo de 6 quedan los números de chance (3-5 cifras), que sí
    # interesan para el análisis y no identifican a nadie.
    (re.compile(r"\b\d{6,}\b"), "[dato]"),
)


def enmascarar(texto: str) -> str:
    """Quita del texto lo que pueda identificar a una persona."""
    for patron, reemplazo in _PATRONES_SENSIBLES:
        texto = patron.sub(reemplazo, texto)
    return texto


# --- Detección de "no supo responder" --------------------------------------
# HEURÍSTICA, y por lo tanto imperfecta: el modelo no siempre redacta igual.
# Sirve para ORDENAR la revisión, no como una medida exacta. Como se guardan
# todas las preguntas igual, un falso negativo no pierde información: solo
# hace que esa pregunta no aparezca marcada en el panel.
_FRASES_SIN_RESPUESTA = (
    "no tengo esa informacion",
    "no tengo informacion",
    "no cuento con esa informacion",
    "no dispongo de esa informacion",
    "no tengo ese dato",
    "no tengo el dato",
    "no encontre esa informacion",
    "no puedo ayudarte con eso",
    "no tengo detalles sobre",
    "te recomiendo comunicarte con servicio al cliente",
    "comunicate con servicio al cliente para",
)


def _sin_tildes(texto: str) -> str:
    plano = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in plano if unicodedata.category(c) != "Mn")


def parece_sin_respuesta(respuesta: str) -> bool:
    """Si la respuesta suena a "no lo sé". Aproximado, ver nota de arriba."""
    plano = _sin_tildes(respuesta)
    return any(frase in plano for frase in _FRASES_SIN_RESPUESTA)


# --- Escritura -------------------------------------------------------------
@lru_cache(maxsize=1)
def _tabla():
    recurso = boto3.resource(
        "dynamodb", region_name=settings.aws_region, config=_CONFIG_BOTO
    )
    return recurso.Table(settings.analitica_tabla)


def registrar(
    *,
    pregunta: str,
    respuesta: str,
    origen: str,
    accion: str | None = None,
    costo_usd: float = 0.0,
    documentos: list[str] | None = None,
    modulo: str | None = None,
    autenticado: bool = False,
) -> None:
    """Guarda una consulta. Nunca lanza excepciones.

    `origen` es "router" (resuelto en código, sin costo) o "modelo".
    """
    if not settings.analitica_activa:
        return

    ahora = datetime.now(timezone.utc)
    try:
        _tabla().put_item(
            Item={
                # Partición por día: permite consultar un rango sin escanear
                # la tabla entera.
                "fecha": ahora.strftime("%Y-%m-%d"),
                "id": f"{ahora.isoformat()}#{uuid.uuid4().hex[:8]}",
                "pregunta": enmascarar(pregunta)[:500],
                "sin_respuesta": parece_sin_respuesta(respuesta),
                "origen": origen,
                "accion": accion or "-",
                # DynamoDB no acepta float; se guarda en millonésimas de dólar
                # como entero para no perder precisión.
                "costo_micro_usd": int(round(costo_usd * 1_000_000)),
                "documentos": documentos or [],
                "modulo": modulo or "-",
                "autenticado": autenticado,
                "ttl": int((ahora + timedelta(days=settings.analitica_dias_retencion)).timestamp()),
            }
        )
    except (BotoCoreError, ClientError, ValueError):
        # A propósito no se relanza: el usuario ya recibió su respuesta y una
        # métrica perdida no justifica ensuciarle la experiencia.
        logger.exception("No se pudo registrar la consulta en analítica")


# --- Lectura (para el panel del admin) -------------------------------------
def resumen(dias: int = 7) -> dict:
    """Métricas agregadas de los últimos `dias` días.

    Se consulta día por día (la partición es la fecha) y se agrega en memoria:
    con este volumen es más simple y barato que mantener contadores aparte.
    """
    hoy = datetime.now(timezone.utc).date()
    fechas = [(hoy - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(dias)]

    por_dia: list[dict] = []
    sin_respuesta: list[dict] = []
    total = total_router = 0
    costo_micro = 0

    tabla = _tabla()
    for fecha in reversed(fechas):
        items, ultimo = [], None
        while True:
            kwargs = {
                "KeyConditionExpression": boto3.dynamodb.conditions.Key("fecha").eq(fecha)
            }
            if ultimo:
                kwargs["ExclusiveStartKey"] = ultimo
            resp = tabla.query(**kwargs)
            items.extend(resp.get("Items", []))
            ultimo = resp.get("LastEvaluatedKey")
            if not ultimo:
                break

        del_router = sum(1 for i in items if i.get("origen") == "router")
        costo_dia = sum(int(i.get("costo_micro_usd", 0)) for i in items)

        total += len(items)
        total_router += del_router
        costo_micro += costo_dia

        por_dia.append({
            "fecha": fecha,
            "consultas": len(items),
            "resueltas_por_router": del_router,
            "costo_usd": round(costo_dia / 1_000_000, 4),
        })

        sin_respuesta += [
            {
                "fecha": fecha,
                "pregunta": i.get("pregunta", ""),
                "documentos": list(i.get("documentos") or []),
                "modulo": i.get("modulo", "-"),
            }
            for i in items if i.get("sin_respuesta")
        ]

    return {
        "dias": dias,
        "total_consultas": total,
        "resueltas_por_router": total_router,
        # El dato que justifica la arquitectura: cuántas no costaron nada.
        "porcentaje_gratis": round(100 * total_router / total, 1) if total else 0.0,
        "costo_total_usd": round(costo_micro / 1_000_000, 4),
        "costo_promedio_usd": round(costo_micro / 1_000_000 / total, 5) if total else 0.0,
        "por_dia": por_dia,
        # Lo más útil del panel: qué preguntaron que no se supo responder.
        "preguntas_sin_respuesta": sorted(
            sin_respuesta, key=lambda p: p["fecha"], reverse=True
        )[:100],
    }
