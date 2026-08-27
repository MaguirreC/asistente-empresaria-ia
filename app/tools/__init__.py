"""Registro de herramientas que el asistente puede usar.

Cada herramienta es una consulta al backend de ventas. Aquí se declara qué
existe (para el modelo) y cómo se ejecuta (para el servicio).
"""
import logging

from app.tools.acumulados import acumulados_actuales
from app.tools.loterias import loterias_del_dia
from app.tools.resultados import resultados_loteria

logger = logging.getLogger(__name__)

# Lo que ve el modelo. La descripción es lo que decide si la usa o no, así que
# dice explícitamente CUÁNDO llamarla.
TOOL_DEFINITIONS = [
    {
        "name": "loterias_del_dia",
        "description": (
            "Consulta el listado real de loterías y sorteos disponibles hoy, con la "
            "hora de cierre de ventas de cada uno y la hora actual en Colombia. "
            "Úsala siempre que el usuario pregunte qué loterías juegan hoy, hasta qué "
            "hora puede apostar, si todavía alcanza a jugar una lotería, o a qué hora "
            "cierra un sorteo. Estos horarios cambian a diario: nunca los respondas "
            "de memoria, consulta siempre esta herramienta."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "acumulados_actuales",
        "description": (
            "Consulta los premios acumulados vigentes de Baloto, Revancha, "
            "Chance Millonario y Doble Play (local y regional). Úsala siempre "
            "que el usuario pregunte por el acumulado de cualquiera de estos "
            "productos, cuánto va el premio mayor, o cuánto se puede ganar. "
            "Estos montos cambian tras cada sorteo: nunca los respondas de "
            "memoria, consulta siempre esta herramienta."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "resultados_loteria",
        "description": (
            "Consulta el NÚMERO GANADOR de una lotería o sorteo ya realizado. "
            "Úsala siempre que el usuario pregunte por el resultado de una "
            "lotería, qué número salió, qué número CAYÓ (forma coloquial muy "
            "común en Colombia: 'qué cayó en el chontico', 'qué cayó hoy' — "
            "significa lo mismo que 'qué salió', no lo trates como ambiguo ni "
            "pidas aclaración), cuál fue el ganador, o si quiere saber si "
            "ganó. Sirve tanto para los sorteos diarios (Chontico, Sinuano, "
            "Dorado, Astro, Pick 3/4...) como para las loterías tradicionales "
            "principales (Bogotá, Medellín, Boyacá...). "
            "La herramienta se encarga sola de encontrar el sorteo más reciente "
            "de esa lotería, así que basta con pasarle el nombre. "
            "Nunca respondas un número ganador de memoria ni lo deduzcas: si no "
            "lo entrega esta herramienta, no lo tienes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "loteria": {
                    "type": "string",
                    "description": (
                        "Nombre de la lotería tal como la nombró el usuario "
                        "(por ejemplo 'chontico', 'bogotá', 'astro sol'). "
                        "Omítelo para traer todos los resultados de la fecha."
                    ),
                },
                "fecha": {
                    "type": "string",
                    "description": (
                        "Fecha del sorteo. Solo si el usuario pidió una fecha "
                        "concreta; si no dijo ninguna, omite este campo y se "
                        "busca desde hoy hacia atrás. Si el usuario SÍ dijo el "
                        "año, usa dd-mm-aaaa. Si NO dijo el año (lo normal: "
                        "'el 11 de agosto', 'el 25 de diciembre'), manda SOLO "
                        "dd-mm — NUNCA inventes ni deduzcas el año tú mismo, "
                        "el sistema lo resuelve solo con el año correcto."
                    ),
                },
            },
        },
    },
]

_HANDLERS = {
    "loterias_del_dia": loterias_del_dia,
    "acumulados_actuales": acumulados_actuales,
    "resultados_loteria": resultados_loteria,
}

# Qué mostrarle al usuario mientras se consulta el dato. Consultar una
# herramienta implica una segunda llamada al modelo, así que son los segundos
# de espera más largos: conviene que vea que algo está pasando.
_ETIQUETAS_PROGRESO = {
    "loterias_del_dia": "Consultando las loterías de hoy…",
    "acumulados_actuales": "Consultando los acumulados…",
    "resultados_loteria": "Buscando el resultado del sorteo…",
}


def etiqueta_progreso(name: str) -> str:
    """Frase para mostrar mientras corre esa herramienta."""
    return _ETIQUETAS_PROGRESO.get(name, "Consultando la información…")


def execute_tool(name: str, tool_input: dict) -> str:
    """Ejecuta una herramienta y devuelve su resultado como texto.

    Nunca lanza excepciones: un fallo se le devuelve al modelo como texto para
    que se lo explique al usuario en lugar de romper la conversación.
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        logger.error("El modelo pidió una herramienta desconocida: %s", name)
        return f"La herramienta '{name}' no existe."

    try:
        logger.info("Ejecutando herramienta %s", name)
        return handler(**tool_input)
    except Exception:
        logger.exception("Fallo ejecutando la herramienta %s", name)
        return (
            f"Hubo un problema consultando '{name}'. "
            "Dile al usuario que no pudiste obtener el dato en este momento."
        )
