"""Arranque para desarrollo local, vigilando también los .md de conocimiento.

Existe porque en PowerShell (Windows) el patrón "*.md" pasado por línea de
comandos (--reload-include) se expande antes de llegarle a uvicorn, sin
importar cómo se invoque (uvicorn.exe o `python -m uvicorn`). Poniendo el
patrón aquí, en código, ningún shell lo toca.

Uso: python run_dev.py
"""
import os

import uvicorn

# En desarrollo el servidor se reinicia con cada archivo que se guarda, y
# precalcular los embeddings tarda unos segundos: esperarlos en cada guardado
# haría el ciclo insufrible. En producción sí van encendidos (ver config.py),
# para que el primer usuario no pague esa espera.
# Se define antes de arrancar para que lo hereden los procesos de recarga.
os.environ.setdefault("PRECALENTAR_EMBEDDINGS", "false")

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_includes=["*.md"],
    )
