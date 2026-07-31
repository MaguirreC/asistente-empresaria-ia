# Inventario de preguntas del asistente

Derivado del análisis del backend de ventas (~90 endpoints). Es la lista de lo
que un cliente puede preguntar y de lo que necesitamos para responderle.

**Cómo leerlo**

| Marca | Significado |
|---|---|
| ✅ | Ya está en la base de conocimiento |
| 📄 | **Falta documento** — necesitamos que el negocio lo escriba |
| 🔧 | Se resuelve con una **herramienta** (dato vivo del backend), no con documento |
| 🔐 | Herramienta que además requiere el usuario autenticado (JWT) |

> Regla: si el dato cambia solo (horarios, saldo, acumulados, estado de una
> compra), va por herramienta. Si es una regla o un procedimiento, va por documento.

---

## PRIORIDAD 1 — Sin esto el asistente no sirve

### 1. Registro y activación de cuenta
- 📄 ¿Cómo me registro? ¿Qué datos me piden?
nombres completos y reales como aparcen en el documento, fecha de nacimiento y expedicion de documento ciudad de nacimiento y expdicion 
- 📄 ¿Qué documentos acepta? (cédula, extranjería, pasaporte)
C.C y CE
- 📄 ¿Por qué necesitan mi fecha de expedición del documento?
datos necesarios para comprobar que el usuario es real 
- 📄 Me registré y no me llegó el correo de activación, ¿qué hago?
revisa que escribiste el correo correctamente si no comunicate al servicio al cliente para que te ayuden 
- 📄 ¿Hay edad mínima? *(18 años — confirmar cómo se valida)*
18 años se valida con los datos proporcionados
- 📄 ¿Qué es el campo "referido"?
si otro usuario te refirio a registrarte
- 📄 ¿Puedo tener dos cuentas?
no la cuenta es unica por usuario

### 2. Ingreso, contraseña y bloqueo de cuenta
- 📄 Olvidé mi contraseña, ¿cómo la recupero?
dandole al boton de olvide mi contraseña(aqui le podemos pedir el correo y hacer uso del endpoint) y restablecerla con las indicaciones del correo
- 📄 Mi cuenta está bloqueada, ¿por qué y cómo la desbloqueo?
  *(El backend bloquea a los 5 intentos fallidos y guarda un motivo)*
  comunicate a servicio cliente subiendo una pqrs
- 📄 ¿Por qué me pide un código para entrar?
nopide codigo para entrar solo el codigo es cuando restablece contrasñea
- 📄 Me dice "usuario inactivo", ¿qué significa?
tu usuario se encuesntra desactivado el motivo puede ser que nunca te has activado o te bloqueaste por intentos maximos o otro motivo 
- 📄 ¿Cómo cambio mi contraseña?
si estas dentro de tu perfil lo haces abriendo el menu y dandole a la opcion cambiar contraseña ahi ingresas la actual y la nueva
### 3. Saldo y recargas
- 📄 ¿Cómo recargo saldo? ¿Qué medios de pago aceptan?
  lo haces dandole click arriba en el apartado del saldo y al + ahi podras recargar tu cuenta pse y tarjetas 
- 📄 ¿Hay monto mínimo o máximo de recarga?
minimo 5 mil maximo 300.000
- 📄 ¿Cuánto tarda en reflejarse la recarga?
esto depende de tu banco pero normalmente pago cos tarjeta 1-2 min y pse maximo 15 min
- 📄 ¿Puedo retirar mi saldo? ¿Cómo?
actualmente no hay forma de retirar saldo directamente si solicitas a hacer un retiro voluntario de tu dinero debes subir una pqrs 
- 📄 ¿Tiene costo recargar?
no tiene costo lo que recargas es lo que sube a tu saldo 
- 🔐 ¿Cuánto saldo tengo? → `GET /balance` obtiene el balance del usuario 

### 4. Compra, pagos y transacciones
- 📄 ¿Cómo pago? *(el backend maneja PASARELA, SALDO y CUPÓN)*
puedes pagar directamente desde la pasarela de pago o recargar saldo o si tienes un ucpon de un producto puedes usarlo
- 📄 ¿Puedo combinar saldo con otro medio de pago?
no los medios de pagos no se pueden combinar
- 📄 ¿Qué significa cada estado?
  *(PENDING, APPROVED, REJECTED, FAILED, DECLINED, ERROR)*
  esto esta claro pendiente de la pasarela aprobado o declinada
- ✅ Me declinaron la compra, ¿me devuelven el dinero?
si tu compra fue aprobada por la pasarela pero declinamos la compra el valor de tu compra se vera reflejado en tu saldo
- 📄 Me cobraron dos veces, ¿qué hago?
comunicate con servicio al cliente o sube pqrs para darle seguimiento al caso
- 📄 Pagué y no me aparece la compra, ¿qué hago?
bsucala primero en el historial de compras si aun asi no aparece y se te desconto sube una pqrs 
- 🔐 ¿Cuál fue mi última compra? / historial → `GET /history/by-email`
hace el llamado

### 5. Premios
- ✅ ¿Cómo sé si gané? (apartado Resultados de la web)
- ✅ ¿Cómo reclamo un premio? ¿Dónde, con qué documentos? (colilla + cédula; RUT y certificado bancario actualizados si es de millones)
- ✅ ¿En cuánto tiempo me pagan? (inmediato tras verificar, en horario de oficina; límites de monto para efectivo/transferencia y dónde cobrar)
- ✅ ¿Hay retención en la fuente? ¿Desde qué monto? (20% sobre Ganancias Ocasionales si supera $2.513.952 — revisar cada enero, ver `_PENDIENTES.md`)
- ✅ ¿Cuánto tiempo tengo para reclamar antes de que caduque? (un año desde el sorteo)
- ✅ ¿Los premios pequeños se abonan al saldo automáticamente? (no, todos se cobran en punto de venta)
- 🔧 ¿Cuánto está el acumulado? → **ya implementado**, vía `resultados.facilisimo.co`

### 6. PQRS y servicio al cliente
- 📄 ¿Cómo radico una PQRS? *(el formulario pide nombre, apellido, cédula,
  correo, teléfono, departamento, municipio, asunto y aceptación de términos)*
  ingresar al apartado de pqrs aqui es donde aparece el boton que lo envia a ese apartado 
- 📄 ¿En cuánto tiempo responden?
maximo 15 dias habiles
- 📄 ¿Cuáles son los canales de atención y horarios?
3113843703

servicioalcliente@facilisimo.co de 8 a 15

---

## PRIORIDAD 2 — Productos

### 7. Chance
- ✅ Modalidades, premios y mínimos
- ✅ Mega Chance, Súper Chance, Paga Más, Doble Play
- ✅ Combinado 5 / Directo 5 no existen actualmente en la página; se eliminaron
  del documento
- ✅ Anulación: una vez hecha la apuesta, no se puede anular
- 🔧 ¿Qué loterías juegan hoy y hasta qué hora? → **ya implementado**
- 🔧 ¿Cuánto está el acumulado?

### 8. Lotería tradicional (billetes) — ✅ completo
- ✅ Qué es (compra virtual de billetes con fracciones y series) y diferencia con el chance
- ✅ Qué es una fracción, premio proporcional
- ✅ Número y serie: ambos deben coincidir para el premio mayor
- ✅ Cómo se cobra el premio (igual que chance, en punto de venta)
- 🔧 Loterías y sorteos disponibles → `/loteria/consultar-loterias`, `/consultar-sorteos`
- 🔧 Billetes disponibles → `/loteria/consultar-billetes`

### 9. Baloto — ✅ completo
- ✅ Cómo se juega (5 números del 1-43 + superbalota 1-16) y precio ($6.000)
- ✅ Días de sorteo (lunes, miércoles y sábados) — verificado en baloto.com
- ✅ Qué es Revancha ($3.000 adicionales, mismos números, acumulado paralelo)
- ✅ Cómo reclamo el premio (punto de venta Facilísimo)
- ✅ Por qué registro/términos aparte (requisito adicional del ONJ)
- 🔧 Sorteos, reglas y números aleatorios → `/baloto/sorteos`, `/reglas`, `/numeros-aleatorios`

### 10. Astro (Super Astro) — ✅ completo
- ✅ Cómo se juega, signo zodiacal, plan de premios (verificado con Coljuegos)
- ✅ Por qué la colilla llega de Corredor Empresarial
- 🔧 Sorteos y signos → `/astro/sorteos`, `/astro/signos`

### 11. MiLoto — ✅ completo
- ✅ Cómo se juega (5 números del 1-39), precio ($4.000) y días (lunes, martes, jueves y viernes) — verificado en baloto.com
- 🔧 Sorteos → `/miloto/sorteos`

### 12. Chance Millonario y Doble Play
- ✅ Doble Play (descripción general)
- ✅ Cómo funciona Chance Millonario ($6.000, doble acierto 4 cifras, acumulado paramutual desde $1.000M) — verificado en chancemillonario.com
- ✅ Diferencia Doble Play Local (3 cifras, solo Quindío, $3.000) vs. Regional (4 cifras, eje cafetero, $4.000)

### 13. Recargas de celular — ✅ completo
- ✅ Operadores (Claro, Movistar, Tigo, DIRECTV, WOM); recarga entre $1.000 y $50.000
- ✅ La recarga es inmediata
- ✅ Recargar a número equivocado: no se puede reversar
- 🔧 Operadores → `/recharge/operators`

### 14. Paquetes — ✅ completo
- ✅ Qué son (minutos+datos+redes por operador) y diferencia con recarga
- ✅ Vigencia varía por paquete, se consulta al momento de comprar
- 🔧 Operadores y paquetes → `/package/operators`, `/package/packages`

### 15. Recaudos (pago de servicios) — ✅ completo
- ✅ Qué servicios (no solo públicos: funerarias, seguros, TV/internet local, etc.) — consultar siempre el listado en vivo
- ✅ Tarda en promedio 20 minutos en aplicarse
- ✅ Comprobante llega al correo, pero no como recibo formal
- ✅ Sin costo adicional
- 🔧 Convenios, campos y valor → `/recaudo/recaudos`, `/campos`, `/valor`

---

## PRIORIDAD 3 — Fidelización y otros

### 16. Puntos Leal — ⛔ fuera de alcance (decisión del negocio)
- 📄 ¿Cómo gano puntos? *(se configuran por grupo de producto, con monto
  mínimo, monto por punto y tope por transacción)*
- 📄 ¿Los puntos vencen? ¿Cuándo?
- 📄 ¿Cómo los redimo y qué puedo pedir?
- 📄 ¿Hay límite de redenciones por día o por usuario?
- 🔐 ¿Cuántos puntos tengo? → `GET /points/current`
- 🔐 Mi historial de puntos → `GET /points/history`
- 🔧 Catálogo de premios → `GET /points/rewards`

### 17. Cupones — ✅ completo
- ✅ Qué es (medio de pago atado a un producto) y cómo se obtiene (recompensas, promociones)
- ✅ Sirve solo para el producto al que está atado
- ✅ Vencen con fecha propia; no se pueden usar parcialmente
- 🔐 Mis cupones disponibles → `GET /coupons/available`

### 18. Códigos promocionales — ✅ completo
- ✅ Cómo se redime (menú del header → "Código promocional", recarga saldo)
- ✅ Por qué dice "ya usado" (uso único por usuario) o "alcanzó el límite" (tope total de usos)
- ✅ No se puede usar el mismo código dos veces por el mismo usuario

### 19. Polla mundialista y predicciones — ⛔ fuera de alcance (decisión del negocio)
- 📄 ¿Qué es la polla y cómo participo?
- 📄 ¿Cómo obtengo derecho a jugar? *(se gana comprando ciertos productos)*
- 📄 ¿Qué premios tiene? ¿Hasta cuándo puedo participar?
- 🔐 ¿Tengo derecho? / mis pollas → `/polla/elegibilidad`, `/polla/mis-pollas`

### 20. Migración de usuarios (apostar → facilísimo) — ⛔ fuera de alcance (decisión del negocio)
- 📄 ¿Por qué me piden restablecer la contraseña?
- 📄 ¿Por qué me piden actualizar mis datos?
- 📄 ¿Mi saldo y mi historial se conservaron?

### 21. Perfil y datos personales — ✅ completo
- ✅ Cómo cambio correo/celular ("Actualizar perfil" en el menú del nombre)
- ✅ Por qué actualización anual (requisito SARLAFT, Ley 1908 de 2018 — verificado)
- ✅ Cómo elimino mi cuenta (por PQRS)
- ✅ Cómo desactivo notificaciones (por PQRS)

### 22. Legal y juego responsable
- ✅ Términos y condiciones, política de privacidad, tratamiento de datos
- ✅ ¿Dónde busco ayuda si siento que juego demasiado? (línea 1-800-111-511, Coljuegos)
- ✅ ¿Quién los regula? (Coljuegos regula; ONJ es el operador concesionario de Baloto, no el regulador)
- ✅ ¿Cómo sé que el sorteo es transparente? (delegado testigo/jurado, vigilado por Supersalud, aval de Coljuegos)

---

## Resumen

**Los 19 temas vigentes están documentados** (24 archivos en
`app/knowledge/`). Los tres restantes se descartaron por decisión del negocio.

| Estado | Temas |
|---|---|
| ✅ Documentados | 1–15, 17, 18, 21, 22 |
| ⛔ Fuera de alcance | 16 Puntos Leal · 19 Polla mundialista · 20 Migración de usuarios |

### Herramientas

| Herramienta | Estado |
|---|---|
| Loterías y horarios de hoy | ✅ `app/tools/loterias.py` |
| Acumulados vigentes | ✅ `app/tools/acumulados.py` |
| Resultados (número ganador) por lotería y fecha | ✅ `app/tools/resultados.py` |
| Saldo, historial y puntos del usuario | ⬜ requieren JWT — sin diseñar |

Las marcadas 🔧 en las secciones de arriba que no aparecen en esta tabla
(billetes de lotería, sorteos de Baloto/MiLoto, operadores de recarga,
convenios de recaudo…) se resuelven hoy con la base de conocimiento; se
convertirán en herramientas solo si el dato empieza a cambiar seguido.

### Lo único que sigue abierto

Ver `app/knowledge/_PENDIENTES.md`:

1. Si un **cupón** sirve en un carrito con varios productos (está atado a un
   producto, pero el carrito puede llevar varios).
2. Revisar cada enero el **umbral de retención**, que está atado a la UVT.
