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

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from botocore.exceptions import BotoCoreError, ClientError

from app import analitica, flujos
from app.bedrock import BedrockError, stream_chat
from app.config import settings
from app.embeddings import documentos_de_la_ultima_consulta, precalentar
from app.knowledge_loader import (
    DocumentoNoExiste,
    escribir_documento_admin,
    leer_documento_admin,
    listar_documentos_admin,
)
from app.router import (
    ACCION_AYUDA_COMPRA,
    ACCION_JUGAR_ASTRO,
    ACCION_JUGAR_BALOTO,
    ACCION_JUGAR_BETPLAY,
    ACCION_JUGAR_CHANCE,
    ACCION_JUGAR_CHANCE_MILLONARIO,
    ACCION_JUGAR_DOBLE_PLAY,
    ACCION_JUGAR_MILOTO,
    AVISO_TRATAMIENTO_DATOS,
    MENSAJE_REQUIERE_SESION,
    accion_de_menu,
    destino_navegacion,
    destinos_de_sesion,
    es_guion_ayuda_compra,
    es_menu,
    etiqueta_ayuda_compra,
    menu_texto,
    opciones_menu,
    opciones_submenu,
    producto_pedido,
    resolver_accion,
    resolver_texto,
    submenu_de,
    tiene_guion_ayuda_compra,
)
from app.schemas import (
    BienvenidaResponse,
    ChatRequest,
    DocumentoConocimiento,
    DocumentoResumen,
    GuardarDocumentoRequest,
    HealthResponse,
)
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
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["*"],
)


@app.exception_handler(StarletteHTTPException)
async def manejador_http_exception(request: Request, exc: StarletteHTTPException):
    """Registra en el log CUALQUIER error HTTP antes de devolverlo al cliente.

    Sin esto, un 400/401/404 no deja rastro en el log de la aplicación: solo
    aparece la línea de acceso de uvicorn (método, ruta y código), sin el
    detalle de qué pasó ni desde qué IP — que es justo lo que hace falta para
    diagnosticar un error reportado en producción sin poder reproducirlo.
    """
    logger.warning(
        "%s en %s %s: %s (cliente=%s)",
        exc.status_code,
        request.method,
        request.url.path,
        exc.detail,
        request.client.host if request.client else "?",
    )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def manejador_error_validacion(request: Request, exc: RequestValidationError):
    """Igual que el manejador anterior, pero para el 422 que dispara FastAPI
    cuando el body no cumple el esquema (falta un campo, un tipo equivocado,
    JSON mal formado...). Se loguea el body crudo para poder ver exactamente
    qué mandó el front, ya que `exc.errors()` solo dice qué campo falló, no
    qué se recibió.
    """
    body = await request.body()
    logger.warning(
        "422 en %s %s: %s (cliente=%s) body=%s",
        request.method,
        request.url.path,
        exc.errors(),
        request.client.host if request.client else "?",
        body[:2000],
    )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


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
        mensaje=menu_texto(autenticado),
        opciones=opciones_menu(autenticado),
        aviso_tratamiento_datos=AVISO_TRATAMIENTO_DATOS,
    )


def _verificar_admin_key(x_admin_key: str) -> None:
    """Puerta de entrada común a todo `/admin/*` y `/analitica/*`.

    Si no hay clave configurada en el servidor, el endpoint que llama queda
    deshabilitado a propósito: es preferible que el panel no funcione a que
    estos datos (o la edición de la base de conocimiento) queden abiertos por
    un descuido de configuración.
    """
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=503,
            detail="El panel administrativo no está habilitado: falta configurar ADMIN_API_KEY.",
        )
    # `compare_digest` en vez de `==` para no filtrar la clave por el tiempo
    # que tarda la comparación.
    if not secrets.compare_digest(x_admin_key, settings.admin_api_key):
        raise HTTPException(status_code=401, detail="Clave de administración inválida.")


def _verificar_knowledge_s3() -> None:
    """La edición de la base de conocimiento exige que exista el bucket."""
    if not settings.knowledge_s3_bucket:
        raise HTTPException(
            status_code=503,
            detail=(
                "La edición de la base de conocimiento no está habilitada: "
                "falta configurar KNOWLEDGE_S3_BUCKET."
            ),
        )


@app.get("/analitica/resumen")
def analitica_resumen(dias: int = 7, x_admin_key: str = Header(default="")):
    """Métricas de uso para el panel administrativo.

    **Protegido:** expone las preguntas que escribieron los usuarios, así que
    no puede quedar abierto como el resto de la API. Se exige una clave
    compartida en la cabecera `X-Admin-Key`.
    """
    _verificar_admin_key(x_admin_key)
    dias = max(1, min(dias, 90))  # el TTL de los registros es de 90 días
    return analitica.resumen(dias)


@app.get("/admin/conocimiento", response_model=list[DocumentoResumen])
def admin_listar_conocimiento(x_admin_key: str = Header(default="")):
    """Lista los documentos de la base de conocimiento, para el panel admin.

    Lee directo de S3 (sin la caché que usa el chat en vivo): el panel debe
    ver siempre el estado real, no lo que quedó cacheado al arrancar.
    """
    _verificar_admin_key(x_admin_key)
    _verificar_knowledge_s3()
    try:
        return listar_documentos_admin()
    except (BotoCoreError, ClientError):
        logger.exception("No se pudo listar la base de conocimiento en S3")
        raise HTTPException(status_code=502, detail="No se pudo consultar S3.")


@app.get("/admin/conocimiento/{nombre}", response_model=DocumentoConocimiento)
def admin_leer_conocimiento(nombre: str, x_admin_key: str = Header(default="")):
    """Contenido de un documento, para editarlo en el panel admin."""
    _verificar_admin_key(x_admin_key)
    _verificar_knowledge_s3()
    try:
        contenido = leer_documento_admin(nombre)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DocumentoNoExiste:
        raise HTTPException(status_code=404, detail=f"No existe el documento {nombre!r}.")
    except (BotoCoreError, ClientError):
        logger.exception("No se pudo leer %r de S3", nombre)
        raise HTTPException(status_code=502, detail="No se pudo leer el documento de S3.")
    return DocumentoConocimiento(nombre=nombre, contenido=contenido)


@app.put("/admin/conocimiento/{nombre}", response_model=DocumentoConocimiento)
def admin_guardar_conocimiento(
    nombre: str, body: GuardarDocumentoRequest, x_admin_key: str = Header(default="")
):
    """Crea o actualiza un documento desde el panel admin.

    El cambio queda disponible para el chat de inmediato en esta instancia
    (se invalida la caché en memoria al guardar) — pero con más de una
    instancia corriendo, las demás siguen sirviendo la versión vieja hasta
    que reinicien. Ver DESPLIEGUE_AWS.md, sección 5ter.
    """
    _verificar_admin_key(x_admin_key)
    _verificar_knowledge_s3()
    try:
        escribir_documento_admin(nombre, body.contenido)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (BotoCoreError, ClientError):
        logger.exception("No se pudo guardar %r en S3", nombre)
        raise HTTPException(status_code=502, detail="No se pudo guardar el documento en S3.")
    logger.info("Documento de conocimiento %r actualizado desde el panel admin", nombre)
    return DocumentoConocimiento(nombre=nombre, contenido=body.contenido.strip())


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
        "etiqueta": etiqueta_ayuda_compra(modulo),
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

    Aplica igual a cualquier submenú (Otras consultas, Jugar acumulados...):
    tampoco le sirven al modelo.
    """
    return [
        m for m in messages
        if not (m.role == "assistant" and (es_menu(m.content) or submenu_de(m.content)))
    ]


def _opciones_si_es_menu(respuesta: str, autenticado: bool):
    """Manda los botones junto al menú (o el submenú que corresponda).

    El menú se puede pedir de tres formas (botón "Menú", escribir "menú", o un
    saludo). En las tres el usuario debe ver lo mismo que al abrir el widget,
    así que las opciones viajan con el texto en vez de que el front tenga que
    acordarse de volver a pintarlas. Mismo trato para cualquier submenú.
    """
    if respuesta == menu_texto(autenticado):
        opciones = opciones_menu(autenticado)
    else:
        id_submenu = submenu_de(respuesta)
        if id_submenu is None:
            return
        opciones = opciones_submenu(id_submenu)
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


# Acción del menú/submenú -> producto que arranca. Un diccionario en vez de
# una cadena de if/elif: agregar el botón de un producto nuevo (cuando ya
# tiene flujo guiado) es agregar una entrada aquí, no una rama más.
_ACCION_A_PRODUCTO: dict[str, str] = {
    ACCION_JUGAR_CHANCE: "chance",
    ACCION_JUGAR_ASTRO: "astro",
    ACCION_JUGAR_CHANCE_MILLONARIO: "chance_millonario",
    ACCION_JUGAR_DOBLE_PLAY: "doble_play",
    ACCION_JUGAR_BALOTO: "baloto",
    ACCION_JUGAR_MILOTO: "miloto",
    ACCION_JUGAR_BETPLAY: "betplay",
}


def _producto_a_iniciar(
    accion: str | None, modulo: str | None, texto: str
) -> str | None:
    """Producto cuyo flujo guiado hay que arrancar en este turno, si alguno.

    Tres puertas de entrada, todas equivalentes: la opción del menú, el botón
    "¿Necesitas ayuda?" dentro del módulo, y escribirlo ("quiero hacer un
    chance"). Devuelve None si no hay flujo para lo que se pidió.
    """
    if accion in _ACCION_A_PRODUCTO:
        producto = _ACCION_A_PRODUCTO[accion]
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
    # El contrato asume que `messages[-1]` es lo que el usuario acaba de
    # escribir (CONTRATO_FRONT.md § 4) — todo el router lo trata como tal sin
    # volver a comprobarlo. Si el front manda el historial sin agregar el
    # turno del usuario, ese último mensaje queda siendo el saludo o el menú
    # del propio asistente, y el router lo interpreta como si fuera lo que
    # pidió el usuario: ya pasó, y el síntoma fue una sugerencia de
    # navegación rarísima ("Ver acumulados" sugiriendo ir a "saldo", porque
    # el texto del menú menciona "recargo saldo"). Mejor cortar acá con un
    # error claro que dejar que el router adivine sobre el mensaje equivocado.
    if request.messages[-1].role != "user":
        # Sin este log, un 400 en producción no dice nada más que el código de
        # estado: no hay forma de saber qué mandó el front para reproducirlo.
        # Se registran los roles (no el contenido completo) porque alcanza
        # para diagnosticar el caso y no repite en el log lo que ya se ve en
        # el `detail` de la respuesta.
        logger.warning(
            "400 en /chat: el último mensaje no es del usuario. "
            "roles=%s ultimo_role=%s accion=%s modulo=%s autenticado=%s",
            [m.role for m in request.messages],
            request.messages[-1].role,
            request.action,
            request.contexto.modulo if request.contexto else None,
            request.autenticado,
        )
        raise HTTPException(
            status_code=400,
            detail=(
                "El último mensaje de 'messages' debe ser del usuario "
                "(role: 'user'). Agrega el mensaje del usuario al historial "
                "antes de mandarlo — ver CONTRATO_FRONT.md sección 4."
            ),
        )

    def event_stream():
        # Se va llevando para registrarlo al final, cuando el usuario ya tiene
        # su respuesta. Ver el bloque `finally`.
        registro: dict = {"pregunta": "", "respuesta": "", "origen": "modelo"}

        # Separado de `registro`: ese dict se manda tal cual a
        # `analitica.registrar(**registro)`, que no tiene un parámetro
        # `destino`. Queda en None salvo que el turno termine ofreciendo un
        # `navegacion` — se loguea aparte (ver `finally`) para poder ver en
        # los logs del servidor, sin depender de que la analítica de DynamoDB
        # esté configurada, decisiones como "a este mensaje le ofrecí ir a
        # saldo" — es justo lo que hace falta para depurar el bug de hoy.
        destino = None

        try:
            # Antes que nada, por si la conversación ya es demasiado larga.
            if excede_limite(request.usos_modelo):
                logger.warning(
                    "Conversación cortada por límite de respuestas del modelo (%s)",
                    request.usos_modelo,
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
                    destino = destinos_de_sesion()
                    registro.update(
                        respuesta=MENSAJE_REQUIERE_SESION, origen="router", accion=accion
                    )
                    yield f"data: {json.dumps({'delta': MENSAJE_REQUIERE_SESION}, ensure_ascii=False)}\n\n"
                    for puerta in destino:
                        yield from _navegacion(puerta)
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

            # Log de cada turno completo: pregunta, respuesta y a dónde se
            # sugirió navegar (si acaso). Va al log del servidor (CloudWatch
            # en producción), no depende de que la analítica de DynamoDB esté
            # configurada — sirve para depurar en caliente qué decidió el
            # router sin tener que reproducirlo aparte.
            if registro.get("pregunta"):
                logger.info(
                    "TURNO pregunta=%r respuesta=%r accion=%s modulo=%s destino=%s",
                    registro.get("pregunta"),
                    registro.get("respuesta"),
                    registro.get("accion"),
                    registro.get("modulo"),
                    destino,
                )

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
