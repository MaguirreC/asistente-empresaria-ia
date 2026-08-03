"""Servicio del asistente virtual de Facilísimo (Facibot).

Fase 1: expone /chat conectado a Claude en Bedrock.
Las fases siguientes añaden la base de conocimiento (RAG) y las herramientas
que consultan los endpoints del backend.
"""
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from app import analitica, flujos
from app.bedrock import BedrockError, stream_chat
from app.config import settings
from app.embeddings import documentos_de_la_ultima_consulta, precalentar
from app.router import (
    ACCION_AYUDA_COMPRA,
    ACCION_JUGAR_CHANCE,
    MENSAJE_REQUIERE_SESION,
    accion_de_menu,
    destino_navegacion,
    destinos_de_sesion,
    es_guion_ayuda_compra,
    es_menu,
    menu_texto,
    opciones_menu,
    producto_pedido,
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
    title="Facibot - Asistente Virtual Facilísimo",
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


@app.get("/analitica/resumen")
def analitica_resumen(dias: int = 7, x_admin_key: str = Header(default="")):
    """Métricas de uso para el panel administrativo.

    **Protegido:** expone las preguntas que escribieron los usuarios, así que
    no puede quedar abierto como el resto de la API. Se exige una clave
    compartida en la cabecera `X-Admin-Key`.

    Si no hay clave configurada en el servidor, el endpoint queda deshabilitado
    a propósito: es preferible que el panel no funcione a que estos datos se
    publiquen por un descuido de configuración.
    """
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=503,
            detail="La analítica no está habilitada: falta configurar ADMIN_API_KEY.",
        )
    # `compare_digest` en vez de `==` para no filtrar la clave por el tiempo
    # que tarda la comparación.
    if not secrets.compare_digest(x_admin_key, settings.admin_api_key):
        raise HTTPException(status_code=401, detail="Clave de administración inválida.")

    dias = max(1, min(dias, 90))  # el TTL de los registros es de 90 días
    return analitica.resumen(dias)


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


def _eventos_flujo(avance: "flujos.Avance"):
    """Emite lo que el front necesita para pintar un paso del flujo guiado.

    - `opciones_flujo`: los botones de ESTE paso. Se distingue de `opciones`
      (el menú) a propósito: son cosas distintas y el front las pinta distinto.
    - `flujo`: el estado a devolver en la siguiente petición. Si no llega, el
      flujo terminó (o se canceló) y el front deja de mandarlo.
    - `formulario`: solo en el último turno, con la compra ya armada.
    """
    if avance.opciones:
        yield f"data: {json.dumps({'opciones_flujo': list(avance.opciones)}, ensure_ascii=False)}\n\n"
    if avance.estado is not None:
        yield f"data: {json.dumps({'flujo': avance.estado}, ensure_ascii=False)}\n\n"
    if avance.formulario is not None:
        yield f"data: {json.dumps({'formulario': avance.formulario}, ensure_ascii=False)}\n\n"


def _producto_a_iniciar(
    accion: str | None, modulo: str | None, texto: str
) -> str | None:
    """Producto cuyo flujo guiado hay que arrancar en este turno, si alguno.

    Tres puertas de entrada, todas equivalentes: la opción del menú, el botón
    "¿Necesitas ayuda?" dentro del módulo, y escribirlo ("quiero hacer un
    chance"). Devuelve None si no hay flujo para lo que se pidió.
    """
    if accion == ACCION_JUGAR_CHANCE:
        producto = "chance"
    elif accion == ACCION_AYUDA_COMPRA:
        producto = modulo
    elif accion:
        return None  # cualquier otra acción del menú no arranca un flujo
    else:
        producto = producto_pedido(texto)
    return producto if flujos.hay_flujo(producto) else None


def _retomar_flujo(avance: "flujos.Avance"):
    """Repite la pregunta pendiente después de que el modelo resolvió una duda.

    Sin esto, el usuario que pregunta "¿qué es un combinado?" a mitad del flujo
    recibe la explicación y se queda sin saber que seguía una pregunta.
    """
    # Separado del texto del modelo por una línea en blanco, para que se lea
    # como un turno aparte y no como continuación de la explicación.
    delta = json.dumps({"delta": "\n\n" + avance.mensaje}, ensure_ascii=False)
    yield f"data: {delta}\n\n"
    yield from _eventos_flujo(avance)


@app.post("/chat")
def chat(request: ChatRequest):
    """Conversación con el asistente, en streaming (SSE).

    El front recibe eventos `data: {...}`:
      {"delta": "texto"}          -> fragmento de la respuesta, apenas se genera
      {"descartar": true}         -> borrar los deltas recibidos: no eran la respuesta final
      {"progreso": "texto"}       -> se está consultando un dato en vivo; mostrarlo como estado
      {"usage": {"costo_usd"}}    -> costo ESTIMADO de esta respuesta (solo si usó el modelo)
      {"opciones": [...]}         -> botones del menú, cuando la respuesta ES el menú
      {"sugerencia_accion": {...}}-> el front puede ofrecer un botón de ayuda guiada
      {"navegacion": {...}}       -> el front puede llevar al usuario a otro módulo
      {"error": "mensaje"}        -> ocurrió un problema
      {"done": true}              -> fin de la respuesta
    """

    def event_stream():
        # Se va llevando para registrarlo al final, cuando el usuario ya tiene
        # su respuesta. Ver el bloque `finally`.
        registro: dict = {"pregunta": "", "respuesta": "", "origen": "modelo"}

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
            registro.update(
                pregunta=texto_usuario, modulo=modulo, autenticado=request.autenticado
            )

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
            #
            # Con un flujo guiado en curso NO se interpreta como menú: ahí los
            # pasos también se contestan con números, y un "1" es "la primera
            # lotería de la lista", no "ver mi saldo". El flujo manda.
            accion = request.action or (
                None
                if request.flujo is not None
                else accion_de_menu(
                    texto_usuario, ultimo_del_asistente, request.autenticado
                )
            )

            # --- Flujo guiado de compra ------------------------------------
            # Va antes que todo lo demás: si hay un flujo en curso, lo que
            # escribió el usuario es la respuesta a la pregunta pendiente, no
            # una consulta suelta. Cuesta cero tokens.
            #
            # `retomar` queda cargado cuando el usuario preguntó algo en vez de
            # contestar: responde el modelo y al final se repite la pregunta
            # del paso para no dejarlo sin saber por dónde iba.
            retomar = None

            if request.flujo is not None and not accion:
                avance = flujos.avanzar(
                    request.flujo.producto, request.flujo.datos, texto_usuario
                )
                if avance.consultar_modelo:
                    retomar = avance
                else:
                    logger.info(
                        "Flujo guiado de %s, sin invocar al modelo",
                        request.flujo.producto,
                    )
                    registro.update(respuesta=avance.mensaje, origen="flujo")
                    yield f"data: {json.dumps({'delta': avance.mensaje}, ensure_ascii=False)}\n\n"
                    yield from _eventos_flujo(avance)
                    return

            # Arrancar el flujo: por la opción del menú, por el botón
            # "¿Necesitas ayuda?" del módulo, o porque el usuario escribió que
            # quiere jugar ese producto.
            if retomar is None:
                producto = _producto_a_iniciar(accion, modulo, texto_usuario)

                # Comprar exige cuenta. Se corta aquí, antes de la primera
                # pregunta, y se ofrecen las dos puertas: entrar o registrarse.
                if producto and not request.autenticado:
                    logger.info("Flujo de %s pedido sin sesión iniciada", producto)
                    registro.update(
                        respuesta=MENSAJE_REQUIERE_SESION, origen="router", accion=accion
                    )
                    yield f"data: {json.dumps({'delta': MENSAJE_REQUIERE_SESION}, ensure_ascii=False)}\n\n"
                    for destino in destinos_de_sesion():
                        yield from _navegacion(destino)
                    return

                if producto:
                    avance = flujos.iniciar(producto)
                    logger.info("Arranca el flujo guiado de %s", producto)
                    registro.update(respuesta=avance.mensaje, origen="flujo")
                    yield f"data: {json.dumps({'delta': avance.mensaje}, ensure_ascii=False)}\n\n"
                    yield from _eventos_flujo(avance)
                    return

            # Si lo que se está resolviendo tiene una pantalla propia en la web,
            # se le ofrece al usuario el atajo para llegar — conteste el router
            # o conteste el modelo, para no obligarlo a buscarla a mano.
            #
            # Con un flujo en curso NO se ofrece: el usuario está armando su
            # compra desde el chat, y mandarlo a otra pantalla ahí lo pierde.
            destino = (
                None
                if retomar
                else destino_navegacion(
                    accion, texto_usuario, request.autenticado, modulo
                )
            )

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
                registro.update(
                    respuesta=respuesta_directa, origen="router", accion=accion
                )
                yield f"data: {json.dumps({'delta': respuesta_directa}, ensure_ascii=False)}\n\n"
                yield from _opciones_si_es_menu(respuesta_directa, request.autenticado)
                yield from _navegacion(destino)
                if retomar:
                    # La duda salió gratis (horarios, acumulados...). Se retoma
                    # el flujo donde iba en vez de dejarlo colgado.
                    yield from _retomar_flujo(retomar)
                else:
                    yield from _sugerencia_ayuda_compra(modulo, respuesta_directa)
                return

            acumulado = ""
            for evento in stream_chat(_para_el_modelo(request.messages), modulo):
                if evento.tipo == "texto":
                    acumulado += evento.valor
                    yield f"data: {json.dumps({'delta': evento.valor}, ensure_ascii=False)}\n\n"
                elif evento.tipo == "descartar":
                    # Ese texto no era la respuesta final: tampoco debe contar
                    # como tal al analizar si el asistente supo responder.
                    acumulado = ""
                    yield f"data: {json.dumps({'descartar': True})}\n\n"
                elif evento.tipo == "herramienta":
                    yield f"data: {json.dumps({'progreso': evento.valor}, ensure_ascii=False)}\n\n"
                elif evento.tipo == "costo":
                    registro["costo_usd"] = evento.valor
                    yield f"data: {json.dumps({'usage': {'costo_usd': evento.valor}})}\n\n"
            registro["respuesta"] = acumulado
            # Qué documentos vio el modelo: distingue un fallo de retrieval de
            # uno de contenido cuando se revisa una respuesta mala.
            registro["documentos"] = documentos_de_la_ultima_consulta()
            yield from _navegacion(destino)
            if retomar:
                yield from _retomar_flujo(retomar)
            else:
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

            # Se registra DESPUÉS del `done`: el usuario ya tiene la respuesta
            # completa, así que nada de esto le agrega espera. Y `registrar`
            # nunca lanza excepciones, así que tampoco puede romper el stream.
            if registro["pregunta"]:
                analitica.registrar(**registro)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
