# Pagos y estados de una transacción

## Medios de pago disponibles

Al comprar un producto, el usuario puede pagar de tres formas:

1. **Pasarela de pago** — directamente con PSE o tarjeta.
2. **Saldo** — con el dinero que ya tiene cargado en la cuenta.
3. **Cupón** — si tiene un cupón asignado para ese producto.

## Varios productos SÍ se pueden comprar juntos

En un **mismo carrito** se pueden agregar productos distintos y pagarlos en
una sola transacción: por ejemplo, un chance y el pago de un recaudo, o una
recarga y un Baloto. No hay que hacer una compra por producto.

## Los medios de pago NO se combinan

Lo que no se puede combinar son los **medios de pago**, no los productos.

**No se puede pagar una parte con saldo y otra con tarjeta.** Todo el carrito
se paga con un solo medio (saldo, pasarela o cupón).

Si al usuario no le alcanza el saldo, debe recargar primero o pagar el carrito
completo por la pasarela.

> **Ojo con esta confusión:** que los medios de pago no se combinen **no**
> significa que haya que comprar los productos por separado. Nunca decirle al
> usuario que debe hacer dos compras distintas para llevar dos productos.

## Estados de una transacción

- **Pendiente** — el pago está en proceso en la pasarela; todavía no se
  confirma.
- **Aprobada** — el pago se confirmó y la apuesta quedó registrada.
- **Declinada** — el pago se aprobó pero la apuesta no pudo registrarse ante el
  operador del juego. El dinero vuelve al saldo automáticamente.

## Casos problemáticos

### "Pagué y no me aparece la compra"

1. Buscarla primero en el **historial de compras**.
2. Si no aparece **y el dinero sí se descontó**, radicar una **PQRS** para que
   le den seguimiento al caso.

### "Me cobraron dos veces"

Comunicarse con **servicio al cliente** o radicar una **PQRS** para que revisen
y den seguimiento al caso.

Nunca prometas una devolución ni un plazo: eso lo determina el equipo que
atiende la PQRS.
