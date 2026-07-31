"""Servicio del asistente virtual de Apostar / Facilísimo.

Fase 1: expone /chat conectado a Claude en Bedrock.
Las fases siguientes añaden la base de conocimiento (RAG) y las herramientas
que consultan los endpoints del backend.
"""
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from app.bedrock import BedrockError, stream_chat
from app.config import settings
from app.embeddings import precalentar
from app.router import (
    ACCION_AYUDA_COMPRA,
    accion_de_menu,
    destino_navegacion,
    es_guion_ayuda_compra,
    es_menu,
    menu_texto,
    opciones_menu,
    resolver_accion,
    resolver_texto,
    tiene_guion_ayuda_compra,
)
from app.schemas import BienvenidaResponse, ChatRequest, HealthResponse
from app.session_limit import MENSAJE_LIMITE_ALCANZADO, excede_limite

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
# httpx registra cada petición HTTP en INFO y tapa las líneas que sí interesan
# (uso de tokens, caché y decisiones del router).
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(_: FastAPI):
    """Deja el servicio listo ANTES de aceptar tráfico.

    Los embeddings de la base de conocimiento se calculan una vez por proceso.
    Si se dejan para la primera pregunta, ese usuario espera varios segundos de
    más — y con autoescalado vuelve a pasar con cada instancia nueva. Al
    hacerlo aquí, uvicorn no anuncia el arranque hasta que termina, así que el
    balanceador no le manda tráfico al contenedor antes de tiempo.

    Si falla, el servicio arranca igual: el retrieval reintenta en la primera
    pregunta y, si tampoco puede, cae a mandar toda la base de conocimiento. Un
    arranque más lento es mejor que un contenedor que no levanta.
    """
    if settings.precalentar_embeddings:
        inicio = time.monotonic()
        try:
            total = precalentar()
            logger.info(
                "Precalentado listo: %s documentos en %.1f s",
                total, time.monotonic() - inicio,
            )
        except Exception:
            logger.exception(
                "Falló el precalentado de embeddings. El servicio arranca igual: "
                "se reintentará en la primera pregunta."
            )
    else:
        logger.info("Precalentado de embeddings desactivado (PRECALENTAR_EMBEDDINGS)")
    yield


app = FastAPI(
    title="Asistente Virtual - Apostar / Facilísimo",
    description="Servicio de IA para atención al cliente y asistencia de compra.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


INDEX_HTML = Path(__file__).parent / "static" / "index.html"


@app.get("/", include_in_schema=False)
def interfaz():
    """Interfaz mínima para probar el asistente sin usar la terminal.

    Es una herramienta interna de desarrollo. El widget definitivo lo construye
    el equipo de front dentro de la página de Facilísimo.
    """
    return FileResponse(INDEX_HTML)


@app.get("/health", response_model=HealthResponse)
def health():
    """Health check para el balanceador de AWS."""
    return HealthResponse(status="ok", model=settings.bedrock_model_id)


@app.get("/bienvenida", response_model=BienvenidaResponse)
def bienvenida(autenticado: bool = False):
    """Saludo y opciones rápidas para mostrar al abrir el widget.

    Es GET y no `/chat` a propósito: al abrir el chat todavía no hay
    conversación, y `/chat` exige un historial con al menos un mensaje. Obligar
    al front a inventar un mensaje falso solo para recibir el saludo sería
    ensuciar el contrato.

    `?autenticado=true` si el usuario ya inició sesión: el menú cambia (no se
    le ofrece registrarse, y sí ver su saldo y sus compras).

    No invoca al modelo: el saludo y el menú salen del código.
    """
    return BienvenidaResponse(
        mensaje=menu_texto(autenticado), opciones=opciones_menu(autenticado)
    )


def _sugerencia_ayuda_compra(modulo: str | None, respuesta: str | None):
    """Ofrece el botón de ayuda guiada, salvo que la respuesta YA sea ese
    guion (no tiene sentido ofrecerlo justo después de haberlo dado).

    Depende de que el front mande `contexto.modulo`: sin eso no sabemos en
    qué producto está el usuario, así que no se sugiere nada.
    """
    if not tiene_guion_ayuda_compra(modulo) or es_guion_ayuda_compra(respuesta, modulo):
        return
    sugerencia = {
        "accion": ACCION_AYUDA_COMPRA,
        "etiqueta": "¿Quieres que te ayude a hacer tu apuesta?",
        "contexto": {"modulo": modulo},
    }
    yield f"data: {json.dumps({'sugerencia_accion': sugerencia}, ensure_ascii=False)}\n\n"


def _para_el_modelo(messages: list) -> list:
    """El historial sin los menús.

    El menú sí viaja en el historial que manda el front, porque el usuario lo
    vio y porque hace falta para saber si un "3" es una opción o la respuesta
    a otra pregunta. Pero al modelo no se le manda:

    - no aporta nada para redactar la respuesta y gasta tokens en cada turno;
    - ensucia el retrieval, porque el menú nombra media base de conocimiento
      ("registro", "premio", "acumulados", "PQRS"...) y compite con la
      pregunta real al elegir documentos;
    - si quedara de primero rompería la llamada: la API exige que la
      conversación empiece con un mensaje del usuario.
    """
    return [m for m in messages if not (m.role == "assistant" and es_menu(m.content))]


def _opciones_si_es_menu(respuesta: str, autenticado: bool):
    """Manda los botones junto al menú, venga de donde venga.

    El menú se puede pedir de tres formas (botón "Menú", escribir "menú", o un
    saludo). En las tres el usuario debe ver lo mismo que al abrir el widget,
    así que las opciones viajan con el texto en vez de que el front tenga que
    acordarse de volver a pintarlas.
    """
    if respuesta != menu_texto(autenticado):
        return
    opciones = opciones_menu(autenticado)
    yield f"data: {json.dumps({'opciones': opciones}, ensure_ascii=False)}\n\n"


def _navegacion(destino: dict | None):
    """Le pide al front que pueda llevar al usuario a otro módulo de la web.

    Se manda el identificador del módulo, no una URL: las rutas reales las
    conoce el front. Es una sugerencia, no una orden — quien decide si navega
    (y cuándo) es el front, para no sacar al usuario del chat de golpe.
    """
    if not destino:
        return
    yield f"data: {json.dumps({'navegacion': destino}, ensure_ascii=False)}\n\n"


@app.post("/chat")
def chat(request: ChatRequest):
    """Conversación con el asistente, en streaming (SSE).

    El front recibe eventos `data: {...}`:
      {"delta": "texto"}          -> fragmento de la respuesta
      {"usage": {"costo_usd"}}    -> costo ESTIMADO de esta respuesta (solo si usó el modelo)
      {"opciones": [...]}         -> botones del menú, cuando la respuesta ES el menú
      {"sugerencia_accion": {...}}-> el front puede ofrecer un botón de ayuda guiada
      {"navegacion": {...}}       -> el front puede llevar al usuario a otro módulo
      {"error": "mensaje"}        -> ocurrió un problema
      {"done": true}              -> fin de la respuesta
    """

    def event_stream():
        try:
            # Antes que nada, por si la conversación ya es demasiado larga.
            if excede_limite(request.messages):
                logger.warning(
                    "Conversación cortada por límite de mensajes (%s mensajes)",
                    len(request.messages),
                )
                yield f"data: {json.dumps({'delta': MENSAJE_LIMITE_ALCANZADO}, ensure_ascii=False)}\n\n"
                return

            modulo = request.contexto.modulo if request.contexto else None

            texto_usuario = request.messages[-1].content

            # Qué fue lo último que dijo el asistente: hace falta para saber si
            # un "3" es una opción del menú o la respuesta a otra pregunta.
            ultimo_del_asistente = next(
                (m.content for m in reversed(request.messages) if m.role == "assistant"),
                None,
            )

            # Un botón trae la acción explícita. Si el usuario escribió solo el
            # número de una opción del menú ("3"), equivale a haber pulsado ese
            # botón, así que se trata igual. El número se resuelve contra el
            # menú de ESTE usuario, que cambia según haya iniciado sesión.
            accion = request.action or accion_de_menu(
                texto_usuario, ultimo_del_asistente, request.autenticado
            )

            # Si lo que se está resolviendo tiene una pantalla propia en la web,
            # se le ofrece al usuario el atajo para llegar — conteste el router
            # o conteste el modelo, para no obligarlo a buscarla a mano.
            destino = destino_navegacion(accion, texto_usuario, request.autenticado)

            # Luego se intenta resolver en código. Un saludo o una consulta de
            # horarios no necesitan al modelo, y así no cuestan tokens.
            respuesta_directa = (
                resolver_accion(accion, modulo, request.autenticado)
                if accion
                else resolver_texto(texto_usuario, request.autenticado)
            )
            if respuesta_directa is not None:
                logger.info(
                    "Resuelto por el router, sin invocar al modelo (accion=%s)", accion
                )
                yield f"data: {json.dumps({'delta': respuesta_directa}, ensure_ascii=False)}\n\n"
                yield from _opciones_si_es_menu(respuesta_directa, request.autenticado)
                yield from _navegacion(destino)
                yield from _sugerencia_ayuda_compra(modulo, respuesta_directa)
                return

            for evento in stream_chat(_para_el_modelo(request.messages), modulo):
                if evento.tipo == "texto":
                    yield f"data: {json.dumps({'delta': evento.valor}, ensure_ascii=False)}\n\n"
                elif evento.tipo == "costo":
                    yield f"data: {json.dumps({'usage': {'costo_usd': evento.valor}})}\n\n"
            yield from _navegacion(destino)
            yield from _sugerencia_ayuda_compra(modulo, None)
        except BedrockError as e:
            logger.error("Error atendiendo /chat: %s", e)
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
        except Exception:
            logger.exception("Error inesperado atendiendo /chat")
            mensaje = "Tuvimos un inconveniente atendiendo tu solicitud. Intenta de nuevo."
            yield f"data: {json.dumps({'error': mensaje}, ensure_ascii=False)}\n\n"
        finally:
            yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
