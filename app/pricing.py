"""Estimación de costo por consulta.

Es una ESTIMACIÓN calculada con las tarifas públicas de Bedrock y las reglas
de cacheo de Anthropic (escritura 1,25×, lectura 0,1× sobre el precio de
entrada, con TTL de 5 minutos — el que usamos). No sustituye la factura real
de AWS: para el gasto exacto, revisar Cost Explorer o la consola de Bedrock.

Verificado contra la página de precios de Bedrock (2026-07-29):
https://aws.amazon.com/bedrock/pricing/
"""
from dataclasses import dataclass

MULTIPLICADOR_ESCRITURA_CACHE = 1.25
MULTIPLICADOR_LECTURA_CACHE = 0.1


@dataclass(frozen=True)
class Tarifa:
    entrada_por_millon: float
    salida_por_millon: float


# USD por millón de tokens. IDs tal como los usa Bedrock (prefijo "anthropic.").
TARIFAS: dict[str, Tarifa] = {
    "anthropic.claude-haiku-4-5": Tarifa(entrada_por_millon=1.00, salida_por_millon=5.00),
    # Precio promocional de lanzamiento vigente hasta 2026-08-31; después sube
    # a $3.00 / $15.00. Si el proyecto sigue activo tras esa fecha, actualizar.
    "anthropic.claude-sonnet-5": Tarifa(entrada_por_millon=2.00, salida_por_millon=10.00),
}

# Si se usa un modelo sin tarifa registrada, se estima con la más cara conocida
# en vez de mostrar $0 — subestimar el gasto es el error que no queremos cometer.
_TARIFA_POR_DEFECTO = max(TARIFAS.values(), key=lambda t: t.entrada_por_millon)


def calcular_costo_usd(
    modelo: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> float:
    """Costo estimado de una sola llamada al modelo, en dólares."""
    tarifa = TARIFAS.get(modelo, _TARIFA_POR_DEFECTO)

    costo_entrada = (input_tokens / 1_000_000) * tarifa.entrada_por_millon
    costo_escritura = (
        (cache_creation_input_tokens / 1_000_000)
        * tarifa.entrada_por_millon
        * MULTIPLICADOR_ESCRITURA_CACHE
    )
    costo_lectura = (
        (cache_read_input_tokens / 1_000_000)
        * tarifa.entrada_por_millon
        * MULTIPLICADOR_LECTURA_CACHE
    )
    costo_salida = (output_tokens / 1_000_000) * tarifa.salida_por_millon

    return costo_entrada + costo_escritura + costo_lectura + costo_salida
