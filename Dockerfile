# Imagen base fijada a la variante de Debian, no solo a "3.11-slim": ese tag se
# mueve entre versiones de sistema operativo y dos builds del mismo commit
# podrían no ser iguales. Para reproducibilidad total, reemplazar por el digest
# (python:3.11-slim-bookworm@sha256:...).
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Las dependencias van antes que el código: mientras requirements.txt no cambie,
# Docker reutiliza esta capa y el build es mucho más rápido.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Usuario sin privilegios. Se crea después de instalar (pip necesita root) y el
# código queda de solo lectura para él: el servicio no escribe en disco, sus
# cachés viven en memoria.
RUN useradd --create-home --uid 10001 asistente
USER asistente

EXPOSE 8000

# El health check usa urllib (viene con Python) en vez de curl, que no está en
# la imagen slim y habría que instalar solo para esto.
#
# start-period generoso a propósito: al arrancar se precalculan los embeddings
# de la base de conocimiento contra Bedrock, y hasta que eso termina uvicorn
# todavía no acepta conexiones. Marcar el contenedor como enfermo durante ese
# rato provocaría un reinicio en bucle.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# En AWS (Fargate/App Runner) las credenciales salen del rol IAM de la task.
#
# --proxy-headers y --forwarded-allow-ips: detrás del ALB, uvicorn ve la IP del
# balanceador. Con esto respeta las cabeceras X-Forwarded-* y los logs muestran
# la IP real del cliente. Se confía en todas las procedencias porque el
# contenedor solo debe ser alcanzable desde el balanceador, nunca expuesto
# directo a internet.
#
# UN SOLO worker: los cachés (embeddings, loterías, acumulados, resultados) son
# por proceso y no se comparten. Varios workers duplicarían el trabajo sin
# ganar nada. Para escalar, más contenedores — no más workers.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
