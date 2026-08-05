"""Flujos guiados de compra.

Importar este paquete registra los flujos disponibles. Cada producto es una
"receta" de pasos (`chance.py`); el motor que los recorre es genérico, así que
agregar Astro o Baloto es escribir otra receta, no código nuevo.
"""
from app.flujos import (  # noqa: F401  (importar registra los flujos)
    astro,
    baloto,
    chance,
    chance_millonario,
    doble_play,
    miloto,
)
from app.flujos.motor import Avance, avanzar, hay_flujo, iniciar

__all__ = ["Avance", "avanzar", "hay_flujo", "iniciar"]
