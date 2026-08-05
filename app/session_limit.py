"""Límite de mensajes por conversación.

No hay sesión guardada en el servidor: el front manda el historial completo en
cada turno. Sirve para frenar a alguien que intente usar el asistente como su
chat personal — y como eso es un tema de COSTO, se mide sobre cuántas
respuestas de la conversación de verdad invocaron al modelo
(`request.usos_modelo`), no sobre cuántos mensajes trae el historial.

Esa distinción importa desde que existe el flujo guiado de compra: armar un
Chance Millonario son ~8 turnos (loterías, números) que salen del router y el
motor de flujos, cero tokens. Si se contaran como los demás, una sola compra
agotaría el tope sin haber costado nada — se estaría frenando exactamente el
uso legítimo que el asistente existe para facilitar.

`usos_modelo` no lo calcula el backend: no puede. Al no guardar estado, en
cada petición solo ve el historial de texto, y una respuesta del router y una
del modelo son indistinguibles ahí — así que el front lo trae de vuelta
(sumando 1 por cada evento `usage` recibido), igual que ya hace con `flujo`.

Es deliberadamente simple: no crea un almacén de sesiones ni depende de
cookies. Alguien podría evadirlo mandando `usos_modelo` en cero a propósito,
pero eso ya requiere manipular el request a mano — no es el caso de un uso
normal ni casual, que es lo que esto busca frenar.
"""
from app.config import settings

MENSAJE_LIMITE_ALCANZADO = (
    "Esta conversación ya lleva bastante y prefiero que sigamos con una "
    "nueva. 🙂 Recarga la página o pulsa \"Limpiar\" para empezar de cero — "
    "seguiré aquí para ayudarte."
)


def excede_limite(usos_modelo: int) -> bool:
    """True si la conversación superó el tope configurado de respuestas del
    modelo (no de mensajes totales — ver docstring del módulo)."""
    if not settings.limite_mensajes_activo:
        return False
    return usos_modelo > settings.limite_mensajes_max
