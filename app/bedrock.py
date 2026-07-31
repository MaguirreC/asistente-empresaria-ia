"""Cliente de Claude en Amazon Bedrock.

Usa el cliente oficial de Anthropic para Bedrock (Mantle), que expone la misma
API de mensajes que la API directa.
"""
import logging
from functools import lru_cache
from typing import Iterator, List, Literal, NamedTuple

import anthropic
from anthropic import AnthropicBedrockMantle

from app.config import settings
from app.embeddings import documentos_relevantes
from app.pricing import calcular_costo_usd
from app.prompts.system import SYSTEM_PROMPT
from app.schemas import Message
from app.tools import TOOL_DEFINITIONS, execute_tool

logger = logging.getLogger(__name__)

# Tope de vueltas del ciclo consulta -> herramienta -> respuesta. Evita que un
# modelo confundido quede pidiendo herramientas indefinidamente.
MAX_ITERACIONES_HERRAMIENTAS = 5


class BedrockError(RuntimeError):
    """Error al invocar el modelo, ya traducido a algo que podemos mostrar."""


class ChatEvent(NamedTuple):
    """Lo que produce stream_chat: fragmentos de texto y, al final, el costo."""
    tipo: Literal["texto", "costo"]
    valor: str | float


@lru_cache(maxsize=1)
def _client() -> AnthropicBedrockMantle:
    # Las credenciales salen de la cadena estándar de AWS: rol IAM en la nube,
    # `aws configure` o variables de entorno en local.
    return AnthropicBedrockMantle(aws_region=settings.aws_region)


@lru_cache(maxsize=1)
def _instrucciones_base() -> str:
    """Instrucciones del asistente, SIN la base de conocimiento completa.

    El conocimiento ya no vive aquí: se recupera por similitud (retrieval, ver
    app/embeddings.py) según cada pregunta y se inyecta en el mensaje del
    usuario, no en el system prompt. Así este bloque se queda chico y estable
    sin importar cuánto crezca la base de conocimiento total, y sigue
    aprovechando el caché al 100%.
    """
    return (
        f"{SYSTEM_PROMPT}\n\n"
        "Con la pregunta del usuario puede venir una nota \"[Material de "
        "referencia para esta pregunta]\" con la información ya seleccionada "
        "para ese caso. Responde solo con base en ese material, en el resultado "
        "de las herramientas, o en las instrucciones de arriba. Si la pregunta "
        "no se puede responder con nada de eso, dilo con franqueza en vez de "
        "suponer."
    )


@lru_cache(maxsize=1)
def _bloque_system() -> list[dict]:
    """System prompt marcado para que Bedrock lo cachee.

    Debe ser idéntico byte a byte entre llamadas para que el caché funcione:
    nada de fechas, conocimiento variable ni datos por consulta aquí (por eso
    la hora va en la herramienta, y el conocimiento en el mensaje del usuario).

    TTL de 1 hora en vez de los 5 minutos por defecto: con tráfico real y
    espaciado, amortiza mejor la escritura del caché.
    """
    return [
        {
            "type": "text",
            "text": _instrucciones_base(),
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]


def _to_api_messages(
    messages: List[Message], modulo: str | None, conocimiento: str
) -> list[dict]:
    """Convierte los mensajes al formato de la API, agregando en el ÚLTIMO
    mensaje del usuario el conocimiento recuperado para esta pregunta y, si
    aplica, en qué módulo de compra está.

    Va en el mensaje del usuario y no en el system prompt a propósito: el
    system prompt tiene que ser idéntico byte a byte entre llamadas para que
    el caché funcione, y tanto el conocimiento relevante como el módulo
    cambian en cada consulta.
    """
    api_messages = [{"role": m.role, "content": m.content} for m in messages]
    if api_messages and api_messages[-1]["role"] == "user":
        partes = []
        if conocimiento:
            partes.append(f"[Material de referencia para esta pregunta]\n{conocimiento}")
        if modulo:
            partes.append(f"[Contexto: el usuario está en la página de {modulo}]")
        if partes:
            nota = "\n\n".join(partes) + "\n\n"
            api_messages[-1] = {**api_messages[-1], "content": nota + api_messages[-1]["content"]}
    return api_messages


# El turno anterior solo se arrastra si la pregunta es demasiado corta para
# valerse sola, y recortado. El puntaje léxico del retrieval es la FRACCIÓN de
# palabras de la consulta que aparecen en el documento: pegarle una respuesta
# larga diluye las palabras del usuario hasta volverlas insignificantes.
# Pasó de verdad — "las principales" perdió el documento del calendario de
# loterías porque quedó ahogada en la respuesta anterior del asistente.
MAX_CONTEXTO_ANTERIOR = 200   # caracteres
MAX_PALABRAS_PREGUNTA_CORTA = 6


def _texto_para_retrieval(messages: List[Message], modulo: str | None) -> str:
    """Qué se embebe para elegir los documentos relevantes.

    Siempre la última pregunta del usuario, más el módulo de compra si el
    front lo mandó. La respuesta anterior del asistente se suma **solo cuando
    la pregunta es corta** ("y esto cuánto cuesta?", "las principales"), que
    es cuando sola no dice de qué está hablando, y recortada para que dé
    contexto sin tapar lo que el usuario preguntó. Una pregunta completa se
    basta sola: arrastrarle el turno anterior solo le agrega ruido.
    """
    if not messages:
        return ""
    pregunta = messages[-1].content
    partes = []
    if modulo:
        partes.append(f"Tema: {modulo}.")
    es_corta = len(pregunta.split()) <= MAX_PALABRAS_PREGUNTA_CORTA
    if es_corta and len(messages) >= 2 and messages[-2].role == "assistant":
        partes.append(messages[-2].content[:MAX_CONTEXTO_ANTERIOR])
    partes.append(pregunta)
    return " ".join(partes)


def stream_chat(messages: List[Message], modulo: str | None = None) -> Iterator[ChatEvent]:
    """Responde al usuario, consultando herramientas si el modelo las pide.

    El texto de una vuelta que termina llamando a una herramienta NUNCA se le
    muestra al usuario: el modelo a veces escribe ahí su respuesta antes de
    confirmar el dato con la herramienta, y luego la repite ya confirmada. Se
    bufferea cada vuelta y solo se entrega la de la respuesta final (la que ya
    tiene el dato real), evitando ese doble mensaje.

    A cambio se pierde el efecto de "escribiendo en vivo" en las preguntas que
    usan herramientas: el texto aparece completo en cuanto está listo, no
    letra por letra. Las respuestas que no necesitan herramientas (la mayoría)
    conservan el streaming en vivo, porque son una sola vuelta.

    Al final entrega un evento "costo" con lo que costó TODA la respuesta,
    sumando las llamadas al modelo que hicieron falta (una consulta con
    herramienta implica dos: la que pide el dato y la que ya responde con él)
    y el retrieval del conocimiento relevante.
    """
    conocimiento = documentos_relevantes(_texto_para_retrieval(messages, modulo))
    conversacion = _to_api_messages(messages, modulo, conocimiento)
    costo_acumulado = 0.0

    try:
        for _ in range(MAX_ITERACIONES_HERRAMIENTAS):
            # Sin `temperature` a propósito: los modelos nuevos rechazan los
            # parámetros de muestreo. El tono se controla desde el system prompt.
            with _client().messages.stream(
                model=settings.bedrock_model_id,
                max_tokens=settings.max_tokens,
                system=_bloque_system(),
                tools=TOOL_DEFINITIONS,
                messages=conversacion,
            ) as stream:
                texto_de_la_vuelta = "".join(stream.text_stream)
                respuesta = stream.get_final_message()

            uso = respuesta.usage
            cache_write = getattr(uso, "cache_creation_input_tokens", 0)
            cache_read = getattr(uso, "cache_read_input_tokens", 0)
            costo_acumulado += calcular_costo_usd(
                modelo=settings.bedrock_model_id,
                input_tokens=uso.input_tokens,
                output_tokens=uso.output_tokens,
                cache_creation_input_tokens=cache_write,
                cache_read_input_tokens=cache_read,
            )

            # cache_read alto = el prefijo se está reutilizando y sale ~10x más
            # barato. Si sale siempre en 0, algo lo está invalidando.
            logger.info(
                "uso input=%s output=%s cache_write=%s cache_read=%s costo_usd=%.5f stop=%s",
                uso.input_tokens, uso.output_tokens, cache_write, cache_read,
                costo_acumulado, respuesta.stop_reason,
            )

            if respuesta.stop_reason != "tool_use":
                # Esta es la respuesta final: la única que se le muestra al usuario.
                yield ChatEvent("texto", texto_de_la_vuelta)
                yield ChatEvent("costo", costo_acumulado)
                return

            # Vuelta intermedia: se descarta su texto (si escribió algo) y se
            # ejecutan las herramientas que pidió.
            conversacion.append({"role": "assistant", "content": respuesta.content})
            resultados = [
                {
                    "type": "tool_result",
                    "tool_use_id": bloque.id,
                    "content": execute_tool(bloque.name, bloque.input or {}),
                }
                for bloque in respuesta.content
                if bloque.type == "tool_use"
            ]
            conversacion.append({"role": "user", "content": resultados})

        logger.warning("Se alcanzó el tope de %s iteraciones de herramientas",
                       MAX_ITERACIONES_HERRAMIENTAS)
        yield ChatEvent(
            "texto",
            "Estoy teniendo problemas para completar esta consulta. "
            "¿Puedes intentar de nuevo o preguntarlo de otra forma?",
        )
        yield ChatEvent("costo", costo_acumulado)

    except anthropic.NotFoundError as e:
        logger.error("Modelo no encontrado: %s", e)
        raise BedrockError(
            f"El modelo '{settings.bedrock_model_id}' no existe en la región "
            f"'{settings.aws_region}'. Revisa el ID exacto en la consola de Bedrock."
        ) from e
    except anthropic.PermissionDeniedError as e:
        logger.error("Acceso denegado: %s", e)
        raise BedrockError(
            "Sin acceso al modelo. Verifica que lo habilitaste en 'Model access', "
            "que enviaste los detalles del caso de uso que exige Anthropic, y que "
            "el rol IAM tiene permisos sobre Bedrock."
        ) from e
    except anthropic.RateLimitError as e:
        logger.warning("Throttling de Bedrock: %s", e)
        raise BedrockError("Hay mucha demanda en este momento. Intenta de nuevo en unos segundos.") from e
    except anthropic.APIConnectionError as e:
        logger.error("Fallo de conexión: %s", e)
        raise BedrockError(
            "No se pudo conectar con Bedrock. Revisa las credenciales de AWS y la región."
        ) from e
    except anthropic.APIStatusError as e:
        logger.error("Error de Bedrock status=%s: %s", e.status_code, e)
        raise BedrockError(f"Error del servicio de IA (HTTP {e.status_code}).") from e
    except RuntimeError as e:
        # El SDK lanza un RuntimeError plano cuando no encuentra credenciales AWS.
        if "credentials" in str(e).lower():
            logger.error("Sin credenciales AWS: %s", e)
            raise BedrockError(
                "No se encontraron credenciales de AWS. Configúralas con `aws configure` "
                "en local, o asigna un rol IAM al servicio en AWS."
            ) from e
        raise
