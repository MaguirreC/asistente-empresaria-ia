"""Carga la base de conocimiento y la deja lista para el prompt.

`load_documents()` entrega cada documento por separado — lo usa el retrieval
(`app/embeddings.py`) para elegir solo los relevantes a cada pregunta.
`load_knowledge()` los entrega todos concatenados — es el respaldo si el
retrieval falla (mejor mandar de más que arriesgar una respuesta incompleta).

Si `settings.knowledge_s3_bucket` está configurado, los documentos se leen de
S3 (una vez por proceso, no en cada pregunta). Es lo que permite que el área
comercial los edite desde el panel administrativo sin que alguien tenga que
reconstruir y redesplegar la imagen. Sin bucket configurado, o si S3 falla,
se cae a los `.md` que viajan dentro de la imagen — nunca deja al asistente
sin base de conocimiento por un problema de S3.
"""
import logging
import re
from functools import lru_cache
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"

# Mismas reglas que los nombres de archivo locales: minúsculas, números y
# guiones, nada que empiece con "_" (esos son notas internas, ver
# `_archivos_publicos`) y nada que pueda escapar del prefijo `knowledge/` en
# S3 (barras, puntos, "..").
_NOMBRE_VALIDO = re.compile(r"[a-z0-9][a-z0-9-]{0,79}")


class DocumentoNoExiste(Exception):
    """No hay ningún documento con ese nombre en el bucket configurado."""


def _archivos_publicos() -> list[Path]:
    """Todos los .md salvo los que empiezan con "_" (notas internas del negocio,
    no se le entregan al modelo)."""
    return sorted(p for p in KNOWLEDGE_DIR.glob("*.md") if not p.name.startswith("_"))


def _desde_disco() -> dict[str, str]:
    return {p.stem: p.read_text(encoding="utf-8").strip() for p in _archivos_publicos()}


def _desde_s3() -> dict[str, str]:
    """{nombre: contenido} de los .md del bucket configurado.

    Mismo filtro que en disco: se ignoran las claves cuyo archivo empieza con
    "_". Cualquier error (red, permisos, bucket vacío) se propaga — quien
    llama (`load_documents`) decide si cae al respaldo local.
    """
    cliente = boto3.client("s3", region_name=settings.aws_region)
    paginador = cliente.get_paginator("list_objects_v2")
    documentos: dict[str, str] = {}
    for pagina in paginador.paginate(
        Bucket=settings.knowledge_s3_bucket, Prefix=settings.knowledge_s3_prefix
    ):
        for obj in pagina.get("Contents", []):
            nombre_archivo = Path(obj["Key"]).name
            if not nombre_archivo.endswith(".md") or nombre_archivo.startswith("_"):
                continue
            cuerpo = cliente.get_object(Bucket=settings.knowledge_s3_bucket, Key=obj["Key"])
            texto = cuerpo["Body"].read().decode("utf-8").strip()
            documentos[Path(nombre_archivo).stem] = texto

    if not documentos:
        # Un bucket configurado pero vacío (o con el prefijo mal escrito) es
        # casi seguro un error de configuración, no "no hay conocimiento
        # todavía" — mejor caer al respaldo local que arrancar sin nada.
        raise ValueError(
            f"El bucket {settings.knowledge_s3_bucket!r} no tiene documentos "
            f"en el prefijo {settings.knowledge_s3_prefix!r}"
        )
    return dict(sorted(documentos.items()))


@lru_cache(maxsize=1)
def load_documents() -> dict[str, str]:
    """{nombre_del_archivo: contenido} de cada documento, sin concatenar.

    Se calcula una sola vez por proceso: un cambio en S3 no se refleja hasta
    el próximo arranque (o redespliegue). Invalidar esto en caliente es tarea
    del panel administrativo, todavía no construido.
    """
    if settings.knowledge_s3_bucket:
        try:
            documentos = _desde_s3()
            logger.info(
                "Base de conocimiento cargada desde S3: %s documentos "
                "(bucket=%s, prefijo=%s)",
                len(documentos), settings.knowledge_s3_bucket, settings.knowledge_s3_prefix,
            )
            return documentos
        except (BotoCoreError, ClientError, ValueError):
            logger.exception(
                "No se pudo leer la base de conocimiento de S3 (bucket=%s, "
                "prefijo=%s); se usan los .md locales como respaldo.",
                settings.knowledge_s3_bucket, settings.knowledge_s3_prefix,
            )

    documentos = _desde_disco()
    logger.info("Base de conocimiento cargada del disco local: %s documentos", len(documentos))
    return documentos


@lru_cache(maxsize=1)
def load_knowledge() -> str:
    """Todos los documentos concatenados, en orden alfabético."""
    return "\n\n---\n\n".join(load_documents().values())


# --- Panel administrativo: leer y escribir directo, sin caché ---------------
# `load_documents()`/`load_knowledge()` de arriba están cacheados a propósito
# (los usa el chat en vivo, en cada pregunta). El panel admin necesita lo
# contrario: ver siempre el estado real de S3, aunque sea más lento — y que
# guardar un cambio se refleje de inmediato en el chat, sin reiniciar.
#
# Quien llama (los endpoints de `main.py`) es responsable de verificar antes
# que `settings.knowledge_s3_bucket` esté configurado: estas funciones no lo
# validan, porque sin bucket no hay nada que listar/leer/escribir.


def _validar_nombre(nombre: str) -> None:
    if not _NOMBRE_VALIDO.fullmatch(nombre):
        raise ValueError(
            "El nombre solo puede tener minúsculas, números y guiones "
            "(ej. 'baloto', 'chance-tradicional')."
        )


def _clave_s3(nombre: str) -> str:
    _validar_nombre(nombre)
    return f"{settings.knowledge_s3_prefix}{nombre}.md"


def listar_documentos_admin() -> list[dict]:
    """[{nombre, bytes}] de cada documento, leído directo de S3."""
    cliente = boto3.client("s3", region_name=settings.aws_region)
    paginador = cliente.get_paginator("list_objects_v2")
    resultado = []
    for pagina in paginador.paginate(
        Bucket=settings.knowledge_s3_bucket, Prefix=settings.knowledge_s3_prefix
    ):
        for obj in pagina.get("Contents", []):
            nombre_archivo = Path(obj["Key"]).name
            if not nombre_archivo.endswith(".md") or nombre_archivo.startswith("_"):
                continue
            resultado.append({"nombre": Path(nombre_archivo).stem, "bytes": obj["Size"]})
    return sorted(resultado, key=lambda d: d["nombre"])


def leer_documento_admin(nombre: str) -> str:
    """Contenido de un documento, leído directo de S3 (sin caché)."""
    cliente = boto3.client("s3", region_name=settings.aws_region)
    try:
        objeto = cliente.get_object(Bucket=settings.knowledge_s3_bucket, Key=_clave_s3(nombre))
    except cliente.exceptions.NoSuchKey:
        raise DocumentoNoExiste(nombre) from None
    return objeto["Body"].read().decode("utf-8")


def escribir_documento_admin(nombre: str, contenido: str) -> None:
    """Crea o actualiza un documento en S3, y refresca la caché en memoria de
    ESTA instancia — la próxima pregunta ya usa el contenido nuevo, sin
    reiniciar el proceso.

    Limitación conocida: con más de una instancia corriendo (autoescalado),
    las demás siguen sirviendo la versión vieja hasta que reinicien — ver
    DESPLIEGUE_AWS.md, sección 5ter.
    """
    contenido = contenido.strip()
    if not contenido:
        raise ValueError("El documento no puede quedar vacío.")

    cliente = boto3.client("s3", region_name=settings.aws_region)
    cliente.put_object(
        Bucket=settings.knowledge_s3_bucket,
        Key=_clave_s3(nombre),
        Body=contenido.encode("utf-8"),
        ContentType="text/markdown; charset=utf-8",
    )
    _invalidar_cache()


def _invalidar_cache() -> None:
    load_documents.cache_clear()
    load_knowledge.cache_clear()
    # Import diferido: `embeddings` importa de este módulo, así que
    # importarlo arriba crearía un ciclo.
    from app import embeddings
    embeddings.invalidar_cache()
