# Pendientes de la base de conocimiento

Este archivo **no se le entrega al modelo**. Es la lista de vacíos y dudas que
hay que resolver con el negocio.

## 🟠 Abiertas

1. **Cupón en un carrito con varios productos.** Cada cupón está atado a un
   producto específico, pero en un mismo carrito se pueden llevar productos
   distintos (chance + recaudo, por ejemplo) y todo se paga con un solo medio.
   ¿Qué pasa si se quiere pagar con cupón un carrito mixto? ¿El cupón solo
   sirve si el carrito tiene únicamente ese producto?

## 🟢 Revisión periódica

14. **Umbral de retención ($2.513.952).** Está atado a UVT y **cambia cada año**.
    Hay que revisarlo cada enero o el asistente dará una cifra desactualizada.

## ⚠️ Mantenimiento: contenido duplicado en el router

Las **opciones del menú rápido** (`_RESPUESTAS_MENU` en `app/router.py`) son
respuestas escritas a mano que **resumen** estos documentos, para poder
contestarlas sin gastar en el modelo. El modelo lee el `.md`; esas respuestas
no pasan por él.

**Si editas uno de estos documentos, revisa también el router:**

| Documento | Opción del menú |
|---|---|
| `registro-y-cuenta.md` | 1 · Cómo me registro |
| `premios.md` | 4 · Cómo reclamo un premio |
| `saldo-y-recargas.md` | 5 · Cómo recargo saldo |
| `pagos-y-transacciones.md` + `transacciones-declinadas.md` | 6 · Problema con una compra |
| `pqrs-y-contacto.md` | 7 · Contacto y PQRS |

Las opciones 2 y 3 (loterías y acumulados) no tienen este problema: salen de
herramientas en vivo, no de texto escrito.

## Regla mientras estos vacíos existan

El asistente **no debe inventar** ninguno de estos datos. Si le preguntan algo
que no está en la base de conocimiento, debe decir que no lo tiene y remitir al
canal de servicio al cliente.

---

## Resueltas (histórico)

- **Código de inicio de sesión.** `UserController.login()` no valida
  `codeLogin`; esa verificación solo existe en `/user/validate-login`, que el
  flujo actual no usa. El código es únicamente para restablecer la
  contraseña.
- **Directo 5 y Combinado 5** no existen actualmente en la página (aunque la
  ley los contempla). Eliminados de `chance-tradicional.md`.
- **Anulación de apuestas (chance y Baloto).** No se puede anular una
  apuesta ya hecha, en ninguno de los dos productos. Documentado en
  `chance-tradicional.md` y `baloto.md`.
- **Astro — premios excluyentes.** Al acertar las 4 cifras + signo, se paga
  SOLO el premio mayor (42.000×), no se acumula con los de 3 y 2 cifras.
  Documentado en `astro.md`.
- **Tiempo y forma de pago del premio.** Inmediato una vez se verifica la
  apuesta (en horario de oficina). Efectivo hasta $500.000 en cualquier
  punto; entre $500.000 y $9.000.000 en la oficina principal del municipio;
  más de $9.000.000 en la oficina principal de Armenia, por transferencia.
  Documentos: colilla + cédula (premios menores), + RUT y certificado
  bancario actualizados del último mes (premios de millones). Documentado en
  `premios.md`.
- **Caducidad del premio.** Un año desde la fecha del sorteo. Documentado en
  `premios.md`.
- **Puntos de venta.** Actualmente no hay forma de mostrarle al usuario el
  punto de venta más cercano (no existe listado ni buscador); posible mejora
  a futuro. Documentado en `premios.md`.
- **Estados de transacción.** Solo tres están en uso real: `PENDING`,
  `APPROVED` y `DECLINED` (documentados en `pagos-y-transacciones.md`).
  `REJECTED`, `FAILED` y `ERROR` ya no se usan.
- **Horario de atención.** Lunes a viernes 7:30 a.m.–12:30 m.d. y 1:30
  p.m.–5:00 p.m.; sábados 8:00 a.m.–12:00 m.d. Documentado en
  `pqrs-y-contacto.md`.
- **Súper Chance — "hasta dos veces por cada valor".** Hasta 2 apuestas por
  cada uno de los tres valores fijos ($4.500, $5.500, $6.000). Tabla de
  pagos en `otras-modalidades.md`.
- **Diferencia Doble Play Local vs. Regional.** Local: 3 cifras, solo
  Quindío, $3.000, acumulado desde $8.000.000. Regional: 4 cifras, eje
  cafetero (Armenia, Pereira, Manizales), $4.000, acumulado desde
  $487.394.958. Documentado en `otras-modalidades.md`.
- **Transparencia del sorteo.** Se envía un delegado como testigo/jurado,
  vigilado por Supersalud, con aval de Coljuegos. Documentado en
  `legal-y-juego-responsable.md`.
- **Montos de recarga de celular.** Mínimo $1.000, máximo $50.000.
  Documentado en `recargas-celular.md`.
