"""Recuperación de conocimiento por similitud (RAG real).

En vez de mandarle al modelo TODA la base de conocimiento en cada consulta, se
calcula una sola vez el "embedding" (huella numérica) de cada documento, y en
cada pregunta se compara contra el embedding de esa pregunta para elegir solo
los documentos más relacionados. Así el costo por consulta no depende de
cuánto conocimiento tengamos guardado en total: siguen siendo 2-3 documentos
aunque haya 100 guardados.

Se calcula una sola vez por proceso y se guarda en memoria — sin base de datos
nueva. La base de conocimiento es de decenas de documentos, no miles; no hace
falta más que esto.
"""
import json
import logging
import re
import unicodedata
from collections import deque
from functools import lru_cache

import boto3
import botocore.exceptions

from app.config import settings
from app.knowledge_loader import load_documents, load_knowledge

logger = logging.getLogger(__name__)

EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"

# 256 dimensiones alcanza para un corpus chico de temas bien separados (cada
# documento es un tema propio), y hace la búsqueda más liviana y rápida.
DIMENSIONES = 256

TOP_K = 4

# Cuánto pesa la coincidencia LITERAL de palabras frente a la similitud
# semántica. Existe porque el buscador semántico solo puede fallar con
# términos cortos y específicos de la jerga del negocio ("Pata", "Uña"):
# "cuánto paga la pata" quedó más cerca, semánticamente, de un documento sobre
# MEDIOS DE PAGO que del documento que de verdad tiene la respuesta, porque
# "paga" se parece a "pagos". Si la palabra aparece tal cual en el documento,
# debe pesar, sin importar qué tan "parecido" le parezca al buscador semántico.
#
# Se separa en dos niveles: título y cuerpo. El título pesa mucho más porque
# es la señal más confiable del tema real del documento — un documento puede
# MENCIONAR "chance" varias veces sin ser sobre chance (p. ej. astro.md lo
# menciona 7 veces solo para explicar que NO se compara con el chance), y
# contar solo presencia/ausencia en el cuerpo no distingue esos casos.
PESO_LEXICO_CUERPO = 0.4
PESO_LEXICO_TITULO = 0.8

_STOPWORDS = {
    "que", "como", "cuando", "donde", "para", "por", "con", "del", "las",
    "los", "una", "uno", "unas", "unos", "esta", "este", "estos", "estas",
    "eso", "esa", "ese", "esos", "esas", "cual", "cuales", "quien", "quienes",
    "hay", "soy", "eres", "muy", "mas", "pero", "tambien", "cuanto", "cuanta",
}


class _SinEmbeddings(Exception):
    """No se pudo calcular el embedding (red, credenciales, throttling, etc.)."""


@lru_cache(maxsize=1)
def _cliente():
    return boto3.client("bedrock-runtime", region_name=settings.aws_region)


def _embeber(texto: str) -> list[float]:
    """Pide el embedding de un texto a Titan.

    Se pide normalizado (norma 1), así la similitud coseno entre dos vectores
    se reduce a un simple producto punto — no hace falta numpy para esto.
    """
    cuerpo = json.dumps({"inputText": texto, "dimensions": DIMENSIONES, "normalize": True})
    try:
        respuesta = _cliente().invoke_model(
            modelId=EMBED_MODEL_ID,
            body=cuerpo,
            contentType="application/json",
            accept="application/json",
        )
        datos = json.loads(respuesta["body"].read())
        return datos["embedding"]
    except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError, KeyError) as e:
        raise _SinEmbeddings(str(e)) from e


def _similitud(a: list[float], b: list[float]) -> float:
    """Coseno entre dos vectores normalizados = producto punto."""
    return sum(x * y for x, y in zip(a, b))


def _normalizar(texto: str) -> str:
    """Minúsculas y sin tildes, para comparar términos de forma estable."""
    plano = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in plano if unicodedata.category(c) != "Mn")


def _titulo(documento: str) -> str:
    """Primera línea del documento, sin el `#` de markdown."""
    primera_linea = documento.strip().splitlines()[0] if documento.strip() else ""
    return primera_linea.lstrip("#").strip()


def _puntaje_lexico(consulta: str, documento: str) -> float:
    """Cuánto pesan las palabras clave de la consulta que aparecen tal cual en
    el documento — con más peso si aparecen en el título que en el cuerpo."""
    palabras = {
        p for p in re.findall(r"[a-z0-9]+", _normalizar(consulta))
        if len(p) > 2 and p not in _STOPWORDS
    }
    if not palabras:
        return 0.0

    cuerpo_normalizado = _normalizar(documento)
    titulo_normalizado = _normalizar(_titulo(documento))

    en_cuerpo = sum(1 for p in palabras if p in cuerpo_normalizado) / len(palabras)
    en_titulo = sum(1 for p in palabras if p in titulo_normalizado) / len(palabras)

    return PESO_LEXICO_CUERPO * en_cuerpo + PESO_LEXICO_TITULO * en_titulo


@lru_cache(maxsize=1)
def _embeddings_documentos() -> list[tuple[str, str, list[float]]]:
    """(nombre, texto, embedding) de cada documento. Se calcula una sola vez
    por proceso — la primera pregunta real paga esa espera, el resto no."""
    resultado = [
        (nombre, texto, _embeber(texto)) for nombre, texto in load_documents().items()
    ]
    logger.info("Embeddings calculados para %s documentos de conocimiento", len(resultado))
    return resultado


def invalidar_cache() -> None:
    """Limpia el caché de embeddings calculados.

    La llama `knowledge_loader.escribir_documento_admin` cuando se edita un
    documento desde el panel administrativo, para que la próxima pregunta
    recalcule con el contenido nuevo sin tener que reiniciar el proceso.
    """
    _embeddings_documentos.cache_clear()


def documentos_relevantes(texto_consulta: str, top_k: int = TOP_K) -> str:
    """Los `top_k` documentos más relacionados con la consulta, concatenados.

    Combina similitud semántica (embeddings) con coincidencia literal de
    palabras. Solo semántica falla con términos cortos y específicos de la
    jerga del negocio; solo léxica falla con sinónimos o preguntas indirectas.
    Juntas cubren los dos casos.

    Si falla el cálculo del embedding (red, credenciales, throttling), se cae
    de vuelta a mandar TODA la base de conocimiento para esa consulta: sale
    más caro esa vez, pero nunca se sacrifica la exactitud de la respuesta por
    ahorrar en un momento en que el retrieval no está disponible.
    """
    try:
        embedding_consulta = _embeber(texto_consulta)
        candidatos = _embeddings_documentos()
    except _SinEmbeddings as e:
        logger.error("Fallo el retrieval, se manda la base completa como respaldo: %s", e)
        return load_knowledge()

    puntuados = [
        (
            _similitud(embedding_consulta, vector) + _puntaje_lexico(texto_consulta, texto),
            nombre,
            texto,
        )
        for nombre, texto, vector in candidatos
    ]
    elegidos = sorted(puntuados, key=lambda t: t[0], reverse=True)[:top_k]

    nombres = [nombre for _, nombre, _ in elegidos]
    logger.info("Retrieval eligió: %s", nombres)
    _ultimos_elegidos.append(nombres)
    return "\n\n---\n\n".join(texto for _, _, texto in elegidos)


# Los nombres de la última búsqueda, para poder registrarlos en la analítica.
# Es una cola con tope: un `deque` con `maxlen` es seguro entre hilos para
# append/pop, y así no crece sin control si nadie los consume.
_ultimos_elegidos: deque[list[str]] = deque(maxlen=64)


def documentos_de_la_ultima_consulta() -> list[str]:
    """Qué documentos eligió el retrieval más reciente.

    Sirve para el panel: cuando una respuesta salió mal, saber qué documentos
    vio el modelo distingue un problema de RETRIEVAL (trajo los equivocados) de
    uno de CONTENIDO (los trajo bien pero les falta el dato). Fue justo así
    como se diagnosticó el bug #8.

    Si el retrieval cayó al respaldo de mandar toda la base, devuelve vacío.
    """
    return _ultimos_elegidos.pop() if _ultimos_elegidos else []


def precalentar() -> int:
    """Calcula ya los embeddings de la base de conocimiento.

    Existe para llamarla al arrancar el servicio: si no, el cálculo (una
    llamada a Titan por documento) lo paga el PRIMER usuario del proceso, que
    espera varios segundos de más. Con autoescalado eso vuelve a pasar cada vez
    que sube una instancia.

    Devuelve cuántos documentos quedaron listos.
    """
    return len(_embeddings_documentos())
