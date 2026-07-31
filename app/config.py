"""Configuración del servicio, leída desde variables de entorno (.env)."""
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Vuelca el .env al entorno del proceso. Además de nuestra configuración, esto
# permite que el SDK de AWS encuentre ahí las credenciales durante el desarrollo.
load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # AWS / Bedrock
    aws_region: str = "us-east-1"
    bedrock_model_id: str = "anthropic.claude-haiku-4-5"

    # Backend de ventas que consultan las herramientas
    backend_base_url: str = (
        "https://pda1g4win0.execute-api.us-east-1.amazonaws.com/pro/api/v1/ventas-facilisimo"
    )
    # Código de producto del chance tradicional, requerido por /chance/loterias
    chance_code_producto: str = "2033"

    # Sitio público de resultados. Domino aparte del backend de ventas: trae los
    # acumulados de Baloto/Revancha y de las modalidades de chance (millonario,
    # doble play local y regional) en una sola llamada, sin autenticación.
    resultados_acumulados_url: str = "https://resultados.facilisimo.co/acumulados/"

    # Mismo sitio: los números ganadores de todos los sorteos de una fecha.
    # Se consulta como ?fecha=dd-mm-yyyy
    resultados_sorteos_url: str = "https://resultados.facilisimo.co/resultados/"

    # Servicio
    cors_origins: str = "*"  # separados por coma
    max_tokens: int = 2048

    # Calcular los embeddings al arrancar, antes de atender tráfico, para que
    # el primer usuario no pague esa espera (~6 s con 24 documentos). En
    # producción va encendido. `run_dev.py` lo apaga: con recarga automática se
    # reinicia en cada archivo guardado y esperar en cada una es insufrible.
    precalentar_embeddings: bool = True

    # Tope de mensajes por conversación, para frenar el uso como chat personal.
    # Se puede apagar puntualmente con LIMITE_MENSAJES_ACTIVO=false en el .env
    # para depurar una conversación larga, pero en producción va encendido.
    #
    # El tope cuenta TODOS los mensajes del historial, incluido el menú de
    # bienvenida que el front agrega como turno del asistente.
    limite_mensajes_activo: bool = True
    limite_mensajes_max: int = 30

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
