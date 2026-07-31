"""Carga la base de conocimiento y la deja lista para el prompt.

`load_documents()` entrega cada documento por separado — lo usa el retrieval
(`app/embeddings.py`) para elegir solo los relevantes a cada pregunta.
`load_knowledge()` los entrega todos concatenados — es el respaldo si el
retrieval falla (mejor mandar de más que arriesgar una respuesta incompleta).
"""
from functools import lru_cache
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"


def _archivos_publicos() -> list[Path]:
    """Todos los .md salvo los que empiezan con "_" (notas internas del negocio,
    no se le entregan al modelo)."""
    return sorted(p for p in KNOWLEDGE_DIR.glob("*.md") if not p.name.startswith("_"))


@lru_cache(maxsize=1)
def load_documents() -> dict[str, str]:
    """{nombre_del_archivo: contenido} de cada documento, sin concatenar."""
    return {p.stem: p.read_text(encoding="utf-8").strip() for p in _archivos_publicos()}


@lru_cache(maxsize=1)
def load_knowledge() -> str:
    """Todos los documentos concatenados, en orden alfabético."""
    return "\n\n---\n\n".join(load_documents().values())
