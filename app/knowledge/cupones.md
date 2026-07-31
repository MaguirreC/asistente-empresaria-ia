# Cupones

## Qué es un cupón

Un cupón es un **medio de pago** que el cliente puede usar para pagar el
producto al que ese cupón está asociado (el backend lo maneja como una opción
de pago más, junto a PASARELA y SALDO — ver `pagos-y-transacciones.md`).

## Cómo se obtiene

- Como **recompensa** que da la página.
- Por **promociones eventuales o temporales**.

## A qué producto aplica

Cada cupón está **atado a un producto específico**, no sirve para cualquier
compra.

## Vencimiento y uso

- Cada cupón trae su **propia fecha de vencimiento** desde que se entrega.
- **No se puede usar parcialmente**: se usa completo o no se usa.

## Herramientas relacionadas (datos en vivo, no en este documento)

- Cupones disponibles del usuario → `GET /coupons/available`
