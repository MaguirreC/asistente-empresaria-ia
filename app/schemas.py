"""Modelos de entrada/salida de la API."""
from typing import Any, Dict, Literal, List
from pydantic import BaseModel, Field


class Message(BaseModel):
    """Un turno de la conversación."""
    role: Literal["user", "assistant"]
    content: str


class Contexto(BaseModel):
    """En qué módulo/producto de la página está el usuario.

    El front lo manda en cada mensaje mientras el usuario está dentro de un
    módulo de compra (p. ej. Chance), para que el asistente sepa dónde está sin
    que el usuario tenga que decirlo ("aquí", "esta página").
    """
    modulo: str  # "chance", "baloto", "recargas"... ver CONTRATO_FRONT.md


class EstadoFlujo(BaseModel):
    """Dónde va el flujo guiado de compra.

    Lo emite el backend en cada turno y el front lo devuelve tal cual en el
    siguiente, igual que hace con `messages`. **No se guarda en el servidor**:
    así el servicio sigue sin estado y da igual qué instancia atienda cada
    turno — sin sesiones pegajosas ni Redis.

    El front no tiene que interpretarlo ni construirlo: solo reenviarlo.
    """
    producto: str  # "chance"
    datos: Dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    """Cuerpo de la petición al endpoint /chat.

    El front envía el historial completo de la conversación.
    """
    messages: List[Message] = Field(..., min_length=1)

    # Cuando el usuario pulsa un botón, el front manda aquí la acción. Al venir
    # de un botón la intención es exacta, así que se resuelve en código y no
    # cuesta tokens.
    action: str | None = None

    # En qué módulo de compra está el usuario, si está en alguno.
    contexto: Contexto | None = None

    # Estado del flujo guiado, tal como lo devolvió el backend en el turno
    # anterior. Se manda mientras el flujo esté en curso; cuando el backend
    # deja de devolverlo, el flujo terminó y el front no vuelve a mandarlo.
    flujo: EstadoFlujo | None = None

    # Si el usuario ya inició sesión en la página. El front lo sabe; este
    # servicio NO recibe el token ni consulta datos privados (saldo, historial):
    # solo cambia qué opciones ofrece y a dónde enruta.
    #
    # Va aquí y no dentro de `contexto` a propósito: `contexto` solo se manda
    # cuando el usuario está dentro de un módulo de compra, y el estado de
    # sesión hace falta siempre.
    #
    # Por defecto False: si el front no lo manda, se trata como anónimo, que es
    # lo seguro (se le ofrece registrarse e iniciar sesión, no datos suyos).
    autenticado: bool = False


class OpcionMenu(BaseModel):
    """Una opción rápida del menú inicial.

    El front puede pintarla como botón. Si el usuario prefiere escribir, basta
    con que mande el `numero` como texto: el router lo resuelve igual.
    """
    numero: int
    etiqueta: str
    accion: str


class BienvenidaResponse(BaseModel):
    """Saludo y opciones rápidas que el widget muestra al abrirse.

    No cuesta tokens: sale del código, no del modelo.
    """
    mensaje: str
    opciones: List[OpcionMenu]


class HealthResponse(BaseModel):
    status: str
    model: str
