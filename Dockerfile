FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# En AWS (Fargate/App Runner) el contenedor toma las credenciales del rol IAM.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
