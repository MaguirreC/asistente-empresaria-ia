"""Límite de mensajes por conversación.

No hay sesión guardada en el servidor: el front manda el historial completo en
cada turno, así que basta con contar cuántos mensajes trae ya para saber qué
tan larga va la conversación. Sirve para frenar a alguien que intente usar el
asistente como su chat personal, más allá de si el modelo resiste o no cada
intento puntual.

Es deliberadamente simple: no crea un almacén de sesiones ni depende de cookies.
Alguien podría evadirlo recortando su propio historial antes de mandarlo, pero
eso ya requiere manipular el request a mano — no es el caso de un uso normal ni
casual, que es lo que esto busca frenar.
"""
from app.config import settings
from app.schemas import Message

MENSAJE_LIMITE_ALCANZADO = (
    "Esta conversación ya lleva bastante y prefiero que sigamos con una "
    "nueva. 🙂 Recarga la página o pulsa \"Limpiar\" para empezar de cero — "
    "seguiré aquí para ayudarte."
)


def excede_limite(messages: list[Message]) -> bool:
    """True si la conversación superó el tope configurado."""
    if not settings.limite_mensajes_activo:
        return False
    return len(messages) > settings.limite_mensajes_max
