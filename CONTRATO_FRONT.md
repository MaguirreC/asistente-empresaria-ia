# Contrato del asistente virtual — guía para el front

Todo lo que necesitas para integrar el widget del asistente. Si algo no está
aquí, no hace falta saberlo para consumir el servicio.

> Para correr el servicio, configurarlo o desplegarlo, ver `README.md`.

---

## Dónde está el servicio

```
https://as-eb4b47ff567b437e9e2508de6254bf9f.ecs.us-east-1.on.aws
```

Ya viene con HTTPS y certificado válido. Todos los endpoints de este documento
cuelgan de esa base.

> ⚠️ **Ponlo en una variable de configuración, no incrustado en el código.**
> Es la URL que genera AWS, y cambiaría si el servicio se recrea. Más adelante
> se le va a poner un dominio propio (ver `DESPLIEGUE_AWS.md`), y cuando eso
> pase solo debería cambiar esa variable.

## En 30 segundos

- Son **tres endpoints**: `GET /health`, `GET /bienvenida` y `POST /chat`.
- `/chat` responde en **streaming SSE** sobre HTTPS normal. **No hay
  WebSocket** y no hace falta: el tráfico va en una sola dirección.
- **El servicio no guarda nada.** El front manda el historial completo en cada
  turno. No hay sesión de servidor, no hacen falta cookies ni sticky sessions.
- **El asistente nunca ve datos privados** del usuario (saldo, historial,
  puntos) y **no recibe el token de sesión**. Cuando le preguntan por algo
  suyo, le dice al front a qué pantalla llevarlo.
- Muchas respuestas se resuelven **sin invocar al modelo** (todo el menú, los
  saludos, los guiones de compra). Esas son gratis e instantáneas.
- **Chance, Astro, Chance Millonario, Doble Play Regional, Baloto y MiLoto
  tienen flujo guiado**: le arman la apuesta al usuario desde el chat y
  devuelven un formulario listo para rellenar la pantalla. Ver **sección 7**.
- ⚠️ **`usos_modelo` es obligatorio de implementar, no opcional.** Sin él, el
  límite de conversación no se activa nunca — ver la nota en la **sección 4**
  y el checklist (**sección 10**).

---

## 1. `GET /health`

Health check para el balanceador.

```json
{ "status": "ok", "model": "anthropic.claude-haiku-4-5" }
```

---

## 2. `GET /bienvenida` — al abrir el widget

Apenas se abre el chat, **antes de que el usuario escriba nada**:

```
GET /bienvenida?autenticado=false
```

```json
{
  "mensaje": "¡Hola! Soy Facibot, el asistente de Facilísimo. ¿En qué te ayudo?\n\n1. 🍀 Hacer un chance\n2. 🔮 Hacer un astro\n3. 🎰 Jugar acumulados\n4. 🎱 Jugar Baloto o MiLoto\n5. 📝 Cómo me registro\n…",
  "opciones": [
    { "numero": 1, "etiqueta": "🍀 Hacer un chance", "accion": "jugar_chance" },
    { "numero": 2, "etiqueta": "🔮 Hacer un astro", "accion": "jugar_astro" },
    { "numero": 3, "etiqueta": "🎰 Jugar acumulados", "accion": "jugar_acumulados" },
    { "numero": 4, "etiqueta": "🎱 Jugar Baloto o MiLoto", "accion": "jugar_lotos" },
    { "numero": 5, "etiqueta": "📝 Cómo me registro", "accion": "registro" }
  ],
  "aviso_tratamiento_datos": "Esta conversación puede quedar registrada para mejorar el servicio. Los datos que puedan identificarte (correo, documento, teléfono) se enmascaran antes de guardarse. Responsable: Red de Servicios del Quindío S.A. Política completa de tratamiento de datos: https://www.facilisimo.co/pdf/PoliticaTratamientoDatosPersonales.pdf"
}
```

> **No hardcodees el orden ni los números.** El menú se reordena de vez en
> cuando (por ejemplo, todas las opciones de "jugar" se agruparon al
> principio en esta sesión) — pinta siempre lo que llega en `opciones`, en
> el orden en que llega.

- **`mensaje`** ya trae el menú numerado escrito. Se puede pintar tal cual como
  primer mensaje del asistente.
- **`opciones`** son los mismos ítems como datos, por si prefieres pintar
  botones en vez de (o además de) la lista numerada.
- **`aviso_tratamiento_datos`** es el aviso de privacidad (Ley 1581 de 2012)
  que hay que mostrarle al usuario al abrir el chat. Se pinta **como un
  mensaje del asistente más, ANTES del mensaje del menú** (mismo estilo de
  burbuja, se puede usar una letra un poco más chica para diferenciarlo).
  **Pero NO se agrega al historial `messages`** que se manda de vuelta en
  `/chat` — es solo visual. Si se agrega al historial, deja de coincidir
  exactamente con el texto del menú y rompe la detección de "el usuario
  respondió un número del menú" (ver sección 4): solo el mensaje de
  `mensaje` va a `messages`, el aviso no.

Es `GET` y no `/chat` porque al abrir el widget todavía no hay conversación, y
`/chat` exige un historial con al menos un mensaje.

**No cuesta tokens:** el saludo y el menú salen del código.

### ⚠️ Requisito: el saludo debe ir en el historial

Agrega el mensaje de bienvenida al historial como un turno del asistente, igual
que cualquier otra respuesta:

```js
mensajes.push({ role: "assistant", content: respuesta.mensaje });
```

**Por qué importa:** un número suelto solo cuenta como opción de menú si el
menú es *lo último que vio el usuario*. Sin esto, después de usar una opción
los números dejan de funcionar.

Y al revés: la opción "Tuve un problema con una compra" termina preguntando
*"cuéntame cuál es tu caso"*. Si alguien responde `1` pensando en la primera
viñeta, **no** debe recibir la información de registro. Con el historial
completo el backend distingue los dos casos.

> El backend **filtra el menú antes de mandárselo al modelo** — no aporta a la
> respuesta, gastaría tokens y ensuciaría la búsqueda de documentos. El front
> no tiene que hacer nada al respecto.

### El usuario puede pulsar el botón o escribir el número

| El usuario… | El front manda |
|---|---|
| pulsa el botón | `POST /chat` con `action: "registro"` |
| escribe `1` | `POST /chat` normal, sin `action` |

En el segundo caso el backend reconoce que el mensaje es **solo el número de
una opción** y lo resuelve igual, gratis. Tiene que ser únicamente el número:
`"1"` es una selección de menú, pero `"1 chance"` o cualquier frase se tratan
como pregunta y van al modelo.

**Todas las opciones del menú se responden sin invocar al modelo.**

### Escribir libremente sigue funcionando igual

El menú no reemplaza nada: el usuario puede ignorarlo y escribir su pregunta
en cualquier momento.

### 2.1 El submenú "Otras consultas"

Para que el menú principal no se llene, las consultas menos frecuentes
(premios, problemas con una compra, PQRS) **no están ahí** — viven en un
submenú aparte, detrás de la opción **"📋 Otras consultas"**.

Funciona **exactamente igual** que el menú principal, con su propio evento:

```
data: {"delta": "Estas son las consultas que puedo resolver directo:\n\n1. 🏆 Cómo reclamo un premio\n2. ⚠️ Tuve un problema con una compra\n3. 📞 Contacto y PQRS\n\n…"}
data: {"opciones": [
  { "numero": 1, "etiqueta": "🏆 Cómo reclamo un premio", "accion": "premios" },
  { "numero": 2, "etiqueta": "⚠️ Tuve un problema con una compra", "accion": "problema_compra" },
  { "numero": 3, "etiqueta": "📞 Contacto y PQRS", "accion": "contacto" }
]}
data: {"done": true}
```

Es el **mismo evento `opciones`** que el menú principal — no hay uno nuevo que
aprender. Y aplica la misma regla: agrega ese mensaje al historial, porque un
`"2"` suelto solo se resuelve contra el submenú si es **lo último que el
usuario vio** (si no, se interpreta contra el menú principal, o va al modelo).
No hace falta que el front distinga "estoy en el submenú" — el backend lo
deduce del historial, igual que con el menú principal.

**No cuesta tokens**, igual que el resto del menú.

### 2.2 El submenú "Jugar acumulados"

Mismo mecanismo, otro submenú: **Chance Millonario** y **Doble Play
Regional** son la misma familia de juego (doble acierto, 2 loterías + 5
números) y viven agrupados detrás de **"🎰 Jugar acumulados"** en vez de sumar
dos botones más al principal.

```
data: {"delta": "Chance Millonario y Doble Play se juegan igual: 2 loterías y 5 números. ¿Cuál de los dos quieres jugar?\n\n1. 💰 Chance Millonario\n2. 🎯 Doble Play Regional\n\n…"}
data: {"opciones": [
  { "numero": 1, "etiqueta": "💰 Chance Millonario", "accion": "jugar_chance_millonario" },
  { "numero": 2, "etiqueta": "🎯 Doble Play Regional", "accion": "jugar_doble_play" }
]}
data: {"done": true}
```

Elegir cualquiera de las dos **arranca su flujo guiado directamente** (sección
7.4.2) — no hay un paso intermedio. Mismas reglas que el resto: agrega el
mensaje al historial, `"1"`/`"2"` solo cuentan si el submenú es lo último que
el usuario vio, y no cuesta tokens.

### 2.3 El submenú "Jugar Baloto o MiLoto"

Mismo mecanismo, para los otros dos juegos con flujo guiado — van aparte de
"Jugar acumulados" porque no son de la misma familia (no son de doble
acierto):

```
data: {"delta": "¿Cuál de los dos quieres jugar?\n\n1. 🎱 Baloto\n2. 🎟️ MiLoto\n\n…"}
data: {"opciones": [
  { "numero": 1, "etiqueta": "🎱 Baloto", "accion": "jugar_baloto" },
  { "numero": 2, "etiqueta": "🎟️ MiLoto", "accion": "jugar_miloto" }
]}
data: {"done": true}
```

Igual que "Jugar acumulados": elegir cualquiera arranca su flujo guiado
directo (sección 7.4.3), mismo evento `opciones`, mismas reglas de historial.

### 2.4 El submenú "Recargas, paquetes y recaudos"

Estos tres **no son juegos y no tienen flujo guiado** — solo el guion
informativo de siempre (sección 8), pero arrancado directo desde el menú en
vez de necesitar que el usuario ya esté en la pantalla del producto:

```
data: {"delta": "¿Qué necesitas hacer?\n\n1. 📶 Recargas de celular\n2. 📦 Paquetes\n3. 🧾 Recaudos y facturas\n\n…"}
data: {"opciones": [
  { "numero": 1, "etiqueta": "📶 Recargas de celular", "accion": "ayuda_recargas" },
  { "numero": 2, "etiqueta": "📦 Paquetes", "accion": "ayuda_paquetes" },
  { "numero": 3, "etiqueta": "🧾 Recaudos y facturas", "accion": "ayuda_recaudos" }
]}
data: {"done": true}
```

Elegir una de las tres devuelve el guion informativo del producto **y además
un `navegacion`** hacia esa pantalla — a diferencia del botón "¿Necesitas
ayuda?" (sección 8.2), acá el usuario todavía no está en esa pantalla, así
que sí tiene sentido ofrecerle el atajo:

```
data: {"delta": "¡Hola! Soy Facibot, y te voy a guiar paso a paso para hacer tu recarga.\n\n…"}
data: {"navegacion": { "modulo": "recarga", "etiqueta": "Ir a recargas" }}
data: {"done": true}
```

---

## 3. `autenticado` — estado de sesión

Indica **si el usuario ya inició sesión**, en `GET /bienvenida` (query param) y
en **cada** `POST /chat`:

```json
{ "messages": [...], "autenticado": true }
```

Si no se manda, se asume `false` (anónimo), que es lo seguro.

**El asistente nunca recibe el token de sesión ni consulta datos privados**
(saldo, historial, puntos). Fue una decisión deliberada: menos superficie de
riesgo y ninguna credencial pasando por este servicio. Lo que hace es **llevar
al usuario a la pantalla donde el dato ya está**, y la muestra el front, que es
quien tiene la sesión.

| | Anónimo | Con sesión |
|---|---|---|
| Opción "Cómo me registro" | se muestra | **se oculta** |
| Opciones "Ver mi saldo" y "Mis compras" | no aparecen | **aparecen** |
| Opciones "Hacer un chance" y "Hacer un astro" | se muestran, pero **piden iniciar sesión** | arrancan el flujo |
| Pide saldo / historial / perfil | navega a `ingreso` | navega a la pantalla real |
| Pide "crear cuenta" | navega a `registro` | no se ofrece navegación |

Menú de **10 opciones** para anónimo y **11** con sesión, numeradas de corrido
en ambos casos (sin contar los submenús — 2.1 a 2.4 — que siempre tienen las
suyas propias).

> ⚠️ **El mismo número significa cosas distintas en cada estado:** el `2` de un
> anónimo es "cómo me registro" y el de alguien con sesión es "ver mi saldo".
> Por eso `autenticado` tiene que ir en **todos** los `POST /chat`, no solo al
> abrir. Si el usuario inicia sesión con el chat abierto, vuelve a pedir
> `/bienvenida` para repintar el menú.

---

## 4. `POST /chat`

### Request

```json
{
  "messages": [
    { "role": "user", "content": "¿cómo me registro?" },
    { "role": "assistant", "content": "Para registrarte…" },
    { "role": "user", "content": "¿y la colilla?" }
  ],
  "autenticado": false,
  "action": null,
  "contexto": null
}
```

| Campo | Obligatorio | Qué es |
|---|---|---|
| `messages` | sí | Historial completo. Solo roles `user` y `assistant`; las instrucciones del asistente viven en el servidor. **El último elemento SIEMPRE debe ser `role: "user"`** — es lo que el usuario acaba de escribir o pulsar. |
| `autenticado` | recomendado | Si el usuario tiene sesión iniciada. Por defecto `false`. |
| `action` | no | Id del botón que pulsó el usuario (ver tabla abajo). Se resuelve en código, sin costo. |
| `contexto` | no | `{ "modulo": "chance" }` — en qué producto está el usuario. Ver secciones 7 y 8. |
| `flujo` | no | Estado del flujo guiado, **tal como llegó** en el turno anterior. Ver sección 7. |
| `usos_modelo` | técnicamente opcional, en la práctica obligatorio | Cuántas respuestas de esta conversación costaron tokens. La API no lo exige, pero sin él el límite de conversación no se activa nunca. Ver abajo. |

> ⚠️ **Si `messages[-1].role` no es `"user"`, la API responde `400`.** Es
> literal: cuando el usuario pulsa un botón o escribe algo, ese mensaje
> **tiene que estar agregado al array `messages` antes de mandar la
> petición** — igual que ya se agrega la respuesta del asistente después de
> cada turno. Saltarse esto no es un detalle cosmético: el backend usa
> `messages[-1].content` como "lo que pidió el usuario" en TODO el router
> (menú, navegación, flujo guiado...). Si ese último mensaje termina siendo
> el saludo o el menú del propio asistente (por no haber agregado el turno
> del usuario), el router interpreta ESE texto como si fuera la petición —
> y como el menú menciona "Cómo recargo saldo", puede terminar sugiriendo
> navegar a `saldo` sin que tenga nada que ver con lo que el usuario pidió.
> Ya pasó exactamente eso en producción.

> Hay un **tope de 30 respuestas del modelo por conversación**. Al superarlo,
> el asistente responde pidiendo empezar una nueva.
>
> **No es un tope de mensajes.** El backend no guarda estado entre
> peticiones — solo ve el historial de texto que le mandas, y ahí una
> respuesta del router (o de un flujo guiado) se ve igual que una del modelo.
> No puede saber por sí solo cuáles costaron tokens. Por eso **el front tiene
> que traer la cuenta de vuelta**, en `usos_modelo`, igual que ya hace con
> `flujo`:
>
> ```js
> let usosModelo = 0;
> // cada vez que llega el evento `usage` en la respuesta:
> usosModelo += 1;
> // y se manda en la SIGUIENTE petición:
> { messages, usos_modelo: usosModelo, ... }
> ```
>
> Si no se manda, el backend asume `0` — es decir, **si no se implementa este
> campo, el límite nunca se activa**. No es un roto: el service no tiene otra
> forma de contar, así que confía en lo que le llega. Un usuario que
> manipule su propio request a mano podría evadirlo, pero eso ya no es el uso
> normal que este límite busca frenar.
>
> Por qué importa: sin este campo, si el límite se midiera sobre `messages`
> (como antes), una sola compra guiada de varios pasos (elegir loterías,
> números...) llenaría el tope sin haber costado nada — cortando exactamente
> el uso legítimo que el asistente existe para facilitar.

### Valores de `action`

| `action` | Qué hace |
|---|---|
| `menu` | Devuelve el menú (con el evento `opciones`) |
| `otras_consultas` | Devuelve el submenú "Otras consultas" (con el evento `opciones`) — ver sección 2.1 |
| `jugar_acumulados` | Devuelve el submenú "Jugar acumulados" (con el evento `opciones`) — ver sección 2.2 |
| `jugar_lotos` | Devuelve el submenú "Jugar Baloto o MiLoto" (con el evento `opciones`) — ver sección 2.3 |
| `servicios` | Devuelve el submenú "Recargas, paquetes y recaudos" (con el evento `opciones`) — ver sección 2.4 |
| `jugar_chance` | Arranca el flujo guiado de Chance (ver sección 7). **Sin sesión** responde que hace falta cuenta y manda dos `navegacion` |
| `jugar_astro` | Arranca el flujo guiado de Astro (ver sección 7.4.1). Mismo trato sin sesión que `jugar_chance` |
| `jugar_chance_millonario` | Arranca el flujo guiado de Chance Millonario (ver sección 7.4.2). Mismo trato sin sesión |
| `jugar_doble_play` | Arranca el flujo guiado de Doble Play Regional (ver sección 7.4.2). Mismo trato sin sesión |
| `jugar_baloto` | Arranca el flujo guiado de Baloto (ver sección 7.4.3). Mismo trato sin sesión |
| `jugar_miloto` | Arranca el flujo guiado de MiLoto (ver sección 7.4.3). Mismo trato sin sesión |
| `ayuda_recargas` | Guion informativo de Recargas, directo (sin pasar por `contexto.modulo`) → además navega a `recarga` |
| `ayuda_paquetes` | Guion informativo de Paquetes, directo → además navega a `paquete` |
| `ayuda_recaudos` | Guion informativo de Recaudos, directo → además navega a `recaudo` |
| `registro` | Cómo registrarse → navega a `registro` |
| `ver_saldo` | Dónde ver el saldo → navega a `saldo` *(solo con sesión)* |
| `mis_compras` | Dónde ver las compras → navega a `historial` *(solo con sesión)* |
| `loterias_hoy` | Loterías y horarios de hoy (dato en vivo) |
| `acumulados` | Acumulados vigentes (dato en vivo) |
| `premios` | Cómo reclamar un premio → navega a `resultados` (dentro del submenú) |
| `recargar` | Cómo recargar saldo → navega a `saldo` |
| `problema_compra` | Problemas con una compra → navega a `historial` (dentro del submenú) |
| `contacto` | Contacto y PQRS → navega a `pqrs` (dentro del submenú) |
| `ayuda_compra` | Con `modulo` en `"chance"`, `"astro"`, `"chance_millonario"`, `"doble_play"`, `"baloto"` o `"miloto"` arranca el flujo guiado (sección 7); con cualquier otro producto, el guion informativo (sección 8) |

Todas se resuelven **sin invocar al modelo**.

### Response: eventos SSE

`Content-Type: text/event-stream`. Una línea `data:` por evento, separados por
línea en blanco.

| Evento | Significado |
|---|---|
| `{"delta": "texto"}` | Fragmento de la respuesta. Concatenar en orden. |
| `{"descartar": true}` | **Borrar todo lo acumulado**: no era la respuesta final. Ver abajo. |
| `{"progreso": "texto"}` | Se está consultando un dato en vivo. Mostrarlo como estado. |
| `{"opciones": [...]}` | Botones del menú. Llega cuando la respuesta **es** el menú. |
| `{"opciones_flujo": [...]}` | Botones del paso actual del flujo guiado. Ver sección 7. |
| `{"flujo": {...}}` | Estado del flujo guiado: **devolvérselo tal cual** en la siguiente petición. Ver sección 7. |
| `{"formulario": {...}}` | La compra quedó armada: rellenar la pantalla con estos datos. Ver sección 7. |
| `{"navegacion": {...}}` | Llevar al usuario a otra pantalla. Ver sección 5. |
| `{"sugerencia_accion": {...}}` | Ofrecer el botón de ayuda guiada. Ver sección 7. |
| `{"usage": {"costo_usd": 0.0032}}` | Costo **estimado**. Solo llega si la respuesta usó el modelo; si la resolvió el router, no llega. |
| `{"error": "mensaje"}` | Hubo un problema; mostrar el mensaje. |
| `{"done": true}` | Fin de la respuesta. |

### Ejemplo de consumo

```js
const res = await fetch(`${API}/chat`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ messages, autenticado, contexto }),
});

const reader = res.body.getReader();
const decoder = new TextDecoder();
let buffer = "";
let respuesta = "";

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });

  const partes = buffer.split("\n\n");
  buffer = partes.pop();                       // fragmento incompleto

  for (const parte of partes) {
    const linea = parte.split("\n").find((l) => l.startsWith("data: "));
    if (!linea) continue;
    const evt = JSON.parse(linea.slice(6));

    if (evt.delta)             { respuesta += evt.delta; pintar(respuesta); }
    if (evt.descartar)         { respuesta = ""; pintar(""); }
    if (evt.progreso)          mostrarEstado(evt.progreso);
    if (evt.opciones)          pintarBotonesMenu(evt.opciones);
    if (evt.navegacion)        pintarBotonIrA(evt.navegacion);
    if (evt.sugerencia_accion) pintarBotonAyuda(evt.sugerencia_accion);
    if (evt.error)             mostrarError(evt.error);
    if (evt.done)              terminar();
  }
}

// La respuesta también va al historial, como cualquier turno.
if (respuesta.trim()) mensajes.push({ role: "assistant", content: respuesta });
```

El texto puede traer **negritas de Markdown** (`**así**`). Conviene
convertirlas, escapando el resto para evitar inyección de HTML.

### `descartar` y `progreso`: por qué existen

La respuesta llega **escribiéndose en vivo**, palabra por palabra. Pero cuando
el asistente necesita un dato en vivo (loterías de hoy, acumulados,
resultados), el modelo **a veces empieza a escribir una respuesta y recién
después decide consultar la herramienta** — y esa primera versión todavía no
tiene el dato real.

Como no se puede saber de antemano si eso va a pasar, la secuencia real es:

```
data: {"delta": "El Dorado cierra a las…"}     ← respuesta prematura
data: {"descartar": true}                       ← no servía, borrala
data: {"progreso": "Consultando las loterías de hoy…"}
data: {"delta": "Sí, alcanzas: El Dorado…"}    ← la buena, ya con el dato
data: {"done": true}
```

**Qué debe hacer el front:**

- Con `descartar`: vaciar el texto acumulado y lo que esté mostrando.
- Con `progreso`: reemplazar el contenido por ese texto, como estado (en gris,
  en cursiva, con un indicador de actividad). Los `delta` que lleguen después
  lo reemplazan.

La alternativa era esperar a tener la respuesta completa antes de mostrar
nada, y **se midió: eran 15–20 segundos de pantalla en blanco**. En un chat de
atención al cliente, eso parece que se colgó.

> La mayoría de las respuestas **no** pasan por esto: van directo en `delta` de
> principio a fin. Solo ocurre en las que consultan datos en vivo.

---

## 5. Evento `navegacion` — llevar al usuario a otra pantalla

Llega **siempre que la consulta tenga una pantalla propia**, sin importar si
contestó el router o el modelo. La idea es que el cliente nunca tenga que ir a
buscar la sección a mano.

> ⚠️ **Puede llegar más de una vez en la misma respuesta.** Cuando hay dos
> caminos igual de válidos se manda un evento por cada uno, y hay que pintar
> **un botón por evento**. Hoy pasa en un caso: alguien sin sesión pide hacer
> un chance y se le ofrecen `ingreso` y `registro`. Si tu handler asume un solo
> destino y lo sobrescribe, se perderá el primer botón.

```json
data: {"delta": "Para radicar una PQRS, ingresa al apartado de PQRS…"}
data: {"navegacion": {"modulo": "pqrs", "etiqueta": "Ir a radicar mi PQRS"}}
data: {"done": true}
```

### Los destinos a mapear

**Pantallas de gestión de la cuenta:**

| `modulo` | `etiqueta` | Cuándo llega |
|---|---|---|
| `registro` | Crear mi cuenta | preguntan cómo registrarse *(solo anónimos)* |
| `ingreso` | Iniciar sesión | un **anónimo** pide su saldo, historial o perfil |
| `pqrs` | Ir a radicar mi PQRS | preguntan por PQRS, quejas o reclamos |
| `saldo` | Ir a mi saldo | preguntan por su saldo o cómo recargarlo |
| `resultados` | Ver resultados | preguntan por resultados o si ganaron |
| `historial` | Ver mi historial de compras | preguntan por sus compras |
| `perfil` | Ir a mi perfil | preguntan por cambiar correo, celular o datos |

**Pantallas de compra**, cuando el usuario pregunta por un producto:

| `modulo` | `etiqueta` |
|---|---|
| `chance` | Ir a jugar Chance |
| `astro` | Ir a jugar Astro |
| `baloto` | Ir a jugar Baloto |
| `miloto` | Ir a jugar MiLoto |
| `loteria` | Ir a comprar Lotería |
| `chance_millonario` | Ir a Chance Millonario |
| `recarga` | Ir a recargas |
| `paquete` | Ir a paquetes |
| `recaudo` | Ir a pagar servicios |

> **No se ofrece ir a donde el usuario ya está.** Si manda
> `contexto.modulo: "chance"` y el usuario pregunta por chance, **no llega
> `navegacion`** — sería mandarlo a la pantalla en la que está parado. Pero si
> estando en Chance pregunta por Baloto, sí llega el destino a `baloto`.

**El front debe mapear estos identificadores a sus rutas.** Si llega un
`modulo` desconocido, lo más seguro es no pintar el botón en vez de adivinar.

**Se manda un identificador, no una URL**, a propósito: las rutas reales las
conoce el front. Poner una URL aquí sería adivinarla.

**Usa un botón, no navegues automáticamente.** El texto de la respuesta suele
explicar qué datos hay que tener a mano; sacar al usuario de la pantalla antes
de que lo lea sería contraproducente.

> ⚠️ La respuesta de "cómo me registro" termina con *"Abajo tienes el acceso
> directo para crear tu cuenta 👇"*. Si el front **no** pinta el botón, esa
> frase queda colgada.

**Cuándo NO llega:** si la consulta no corresponde claramente a ninguna
pantalla. Los patrones son estrechos a propósito — mandar al usuario a la
sección equivocada es peor que no ofrecerle el atajo.

---

## 6. Evento `opciones` — volver al menú

El usuario puede pedir el menú de tres formas, y las tres devuelven
**exactamente el mismo menú**:

- pulsando un botón "Menú" (`action: "menu"`),
- escribiendo "menú", "opciones", "volver", "inicio", "ayuda"…,
- o simplemente saludando ("hola", "buenas"…).

En los tres casos, junto al texto llega el evento con los botones, para que el
front no tenga que acordarse de repintarlos:

```json
data: {"delta": "¡Hola! Soy Facibot, el asistente de Facilísimo…\n\n1. 🍀 Hacer un chance\n…"}
data: {"opciones": [{"numero": 1, "etiqueta": "📝 Cómo me registro", "accion": "registro"}]}
data: {"done": true}
```

Son los mismos objetos que devuelve `GET /bienvenida`, así que puedes
reutilizar el mismo componente.

---

## 7. Flujo guiado de compra

> **Esto es lo nuevo.** Antes, a quien quería jugar se le mandaba a la pantalla
> del producto con un `navegacion` y allá se las arreglaba solo. Ahora el
> asistente **le arma la compra desde el chat** y te devuelve los datos ya
> listos para rellenar la pantalla.
>
> Hoy tienen flujo guiado **Chance, Astro, Chance Millonario, Doble Play
> Regional, Baloto y MiLoto**. Los demás cuatro productos (Lotería, Recargas,
> Paquetes y Recaudos) siguen con el guion informativo de la sección 8, sin
> cambios. Es el mismo motor para los seis — lo que cambia es la forma del
> `formulario` final (7.4 Chance, 7.4.1 Astro, 7.4.2 Chance Millonario /
> Doble Play, 7.4.3 Baloto / MiLoto).

### 7.1 Cómo funciona

Es una secuencia fija de preguntas. **No pasa por el modelo: cuesta cero
tokens** y responde al instante.

```
fecha → [jornada] → lotería → número → modalidades → valor de c/u → formulario
```

El paso de **jornada** (mañana / mediodía / tarde / noche) aparece solo cuando
ese día hay más de 8 loterías — y suele haber ~25, así que casi siempre sale.
Sin él, elegir lotería sería una lista de 25 botones. Al front le da igual: los
pasos no se codifican en el front, solo se reenvía `flujo`.

En cada turno el backend te manda:

```
data: {"delta": "¿Con qué lotería quieres jugar?\n\n1. El Dorado\n2. Cruz Roja"}
data: {"opciones_flujo": ["El Dorado", "Cruz Roja"]}
data: {"flujo": {"producto": "chance", "datos": {"fecha": "03/08/2026"}}}
data: {"done": true}
```

- **`delta`** ya trae la pregunta con las opciones numeradas. Se puede pintar
  tal cual.
- **`opciones_flujo`** son las mismas opciones como datos, para pintar botones.
  Es un evento distinto de `opciones` (el menú) a propósito: son cosas
  distintas y probablemente las pintes distinto.
- **`flujo`** es el estado.

### 7.2 Lo único que tienes que hacer: devolver `flujo`

Guarda el último `flujo` que recibiste y mándalo en la siguiente petición:

```json
{
  "messages": [ /* historial completo */ ],
  "flujo": { "producto": "chance", "datos": { "fecha": "03/08/2026" } },
  "autenticado": true
}
```

**No lo interpretes ni lo construyas** — es una caja negra: solo reenvíalo. Su
estructura interna puede cambiar sin avisar.

> **Por qué viaja al front:** el servicio sigue **sin estado**, igual que
> siempre. No hay sesión de servidor, así que no hacen falta sticky sessions ni
> Redis, y da igual qué instancia atienda cada turno.

**Cuándo dejar de mandarlo:** si una respuesta **no** trae el evento `flujo`,
el flujo terminó (o se canceló). Borra el que tenías y deja de enviarlo.

### 7.3 Arrancar el flujo

Tres formas, todas gratis y equivalentes:

| El usuario… | El front manda |
|---|---|
| pulsa **"🍀 Hacer un chance"** en el menú | `action: "jugar_chance"` |
| pulsa "¿Necesitas ayuda?" en la pantalla de Chance | `action: "ayuda_compra"` + `contexto: {"modulo": "chance"}` |
| escribe "quiero hacer un chance", "hazme un chance", "ayuda con un chance"… | nada especial, `POST /chat` normal |

**Astro arranca igual**: `action: "jugar_astro"` desde el botón "🔮 Hacer un
astro" del menú, `ayuda_compra` + `contexto: {"modulo": "astro"}` desde su
pantalla, o escribiendo "quiero jugar astro", "hazme un astro", etc.

**Chance Millonario y Doble Play Regional** no tienen botón directo en el
menú principal — se llega al submenú **"🎰 Jugar acumulados"** (sección 2.2)
y de ahí sí: `action: "jugar_chance_millonario"` o `action:
"jugar_doble_play"`. También arrancan escribiendo "quiero jugar chance
millonario", "hazme un doble play", etc., o con `ayuda_compra` +
`contexto: {"modulo": "chance_millonario"}` / `{"modulo": "doble_play"}`
desde sus pantallas.

**Baloto y MiLoto tampoco tienen botón dedicado en el menú ni submenú
propio** — arrancan solo por las mismas dos vías: `ayuda_compra` +
`contexto: {"modulo": "baloto"}` / `{"modulo": "miloto"}` desde sus
pantallas, o escribiendo "quiero jugar baloto", "hazme un miloto", etc.

### 7.3.1 Hace falta sesión iniciada

El flujo termina en una compra lista para confirmar, y eso no se puede hacer
sin cuenta. Por eso, si llega `autenticado: false`, **el flujo no arranca** por
ninguna de las vías (aplica igual a Chance y a Astro). En su lugar:

```
data: {"delta": "Para hacer tu compra necesitas tener una cuenta e iniciar sesión. 🔐…"}
data: {"navegacion": {"modulo": "ingreso", "etiqueta": "Iniciar sesión"}}
data: {"navegacion": {"modulo": "registro", "etiqueta": "Crear mi cuenta"}}
data: {"done": true}
```

**No llega `flujo`**, así que no hay nada que reenviar. Se ofrecen las dos
puertas porque no sabemos cuál necesita: pedirle que se registre a quien ya
tiene cuenta molesta tanto como lo contrario.

> Se corta **antes de la primera pregunta**, a propósito: hacerle recorrer seis
> pasos para toparse con un muro al final sería peor. Y la opción **sí se
> muestra** en el menú a los anónimos — esconderla no le explica a nadie qué le
> falta.

### 7.4 El final: el evento `formulario`

Cuando ya se recogió todo:

```
data: {"delta": "¡Listo! Así queda tu chance:\n\n• **Fecha:** 03/08/2026\n…"}
data: {"formulario": {
  "producto": "chance",
  "fecha": "04/08/2026",
  "loteria": { "codigo": 45, "id": 27, "nombre": "CHONTICO NOCHE", "nombreCorto": "CHON" },
  "numero": "1234",
  "apuestas": { "directo": 1000, "pata": 500 }
}}
data: {"done": true}
```

**Qué hacer:** llevar al usuario a la pantalla de Chance con esos campos ya
rellenos, para que **revise y confirme**. El texto termina diciendo *"Te lo
dejo cargado en la pantalla de Chance para que lo revises y confirmes"*, así
que si no lo rellenas la frase queda colgada.

| Campo | Qué es |
|---|---|
| `fecha` | `dd/MM/yyyy`. Sale del mismo selector de días de la pantalla |
| `loteria` | **Objeto**, no un string. Trae `codigo`, `id`, `nombre` y `nombreCorto` **tal como los devuelve `/chance/loterias`** |
| `numero` | String de **3 o 4 dígitos**. Con 3, la pantalla deja una casilla en blanco |
| `apuestas` | Modalidad → pesos. Claves posibles: `directo`, `combinado`, `pata`, `una`. **Solo vienen las que el usuario eligió** |

> **Mapea por `codigo` o `id`, no por `nombre`.** Los cuatro campos vienen del
> mismo endpoint que ya usa la pantalla de Chance, así que el identificador
> encaja directo. El `nombre` va solo para mostrar: llega en mayúsculas y sin
> tildes normalizar (`LA CARIBEÑA DIA`), así que compararlo como texto es
> pedir problemas.

> ⚠️ **El asistente no compra nada.** Deja el formulario armado; el usuario
> siempre confirma y ejecuta la compra en la pantalla. Ver sección 8.

### 7.4.1 El formulario de Astro

Distinto orden de preguntas y **distinta forma de `formulario`** — a diferencia
de Chance, el sorteo aplica a todo el tiquete y el usuario puede repetir
varias apuestas (otro número, otro signo, otro valor) sin volver a elegirlo:

```
data: {"delta": "¡Listo! Así queda tu Astro:\n\n• **Sorteo:** Los dos — Sol y Luna\n…"}
data: {"formulario": {
  "producto": "astro",
  "sorteo": "ambos",
  "apuestas": [
    { "numero": "1234", "signo": "leo", "valor": 2000 },
    { "numero": "5678", "signo": "todos", "valor": 3000 }
  ]
}}
data: {"done": true}
```

| Campo | Qué es |
|---|---|
| `sorteo` | `"sol"`, `"luna"` o `"ambos"` |
| `apuestas` | **Lista**, una por cada número que jugó (puede ser una sola) |
| `apuestas[].numero` | String de **exactamente 4 dígitos** — en Astro no existe la modalidad de 3 o 2 cifras |
| `apuestas[].signo` | Uno de los 12 signos en minúsculas y sin tilde (`"leo"`, `"escorpion"`, `"geminis"`...), o `"todos"` si el usuario quiso jugarlos todos |
| `apuestas[].valor` | Pesos, entre 500 y 10.000 |

> `signo: "todos"` significa que el usuario quiere apostar ese número en los
> 12 signos. El asistente no sabe cómo lo modela la pantalla (¿12 líneas?
> ¿una casilla "todos"?) — mapéalo según tu UI real.

### 7.4.2 El formulario de Chance Millonario y Doble Play Regional

Misma mecánica para los dos (son la misma familia de juego): eligen **2
loterías** y **5 números de 4 cifras**. El valor **no se pregunta** — es fijo,
así que **no aparece un paso de valor**, solo en el formulario final.

```
data: {"delta": "¡Listo! Así queda tu Chance Millonario:\n\n• **Loterías:** EL DORADO MAÑANA y SAMÁN DE LA SUERTE\n…"}
data: {"formulario": {
  "producto": "chance_millonario",
  "loterias": [
    { "codigo": 13, "id": -827260349, "nombre": "EL DORADO MANANA", "nombreCorto": "DOMA" },
    { "codigo": 63, "id": -827175756, "nombre": "SAMAN DE LA SUERTE", "nombreCorto": "SAMA" }
  ],
  "numeros": ["1234", "5678", "9012", "3456", "7890"],
  "valor": 6000
}}
data: {"done": true}
```

Con Doble Play Regional es igual, salvo `"producto": "doble_play_regional"` y
`"valor": 4000`.

| Campo | Qué es |
|---|---|
| `loterias` | **Lista de 2 objetos**, no nombres — mismo criterio que Chance (7.4): mapea por `codigo` o `id`, nunca por `nombre` |
| `loterias[].nombre` | ⚠️ En Doble Play puede venir **igual al `nombreCorto`** (`"DOMA"`, `"CAFD"`...): ese endpoint no siempre trae el nombre completo, y el asistente no lo inventa |
| `numeros` | **Lista de exactamente 5** strings de 4 dígitos cada uno |
| `valor` | Fijo — $6.000 en Chance Millonario, $4.000 en Doble Play Regional. No lo eligió el usuario |

> **Estos dos flujos no tienen paso de fecha.** El endpoint que trae las
> loterías siempre devuelve las de HOY — no hay opción de armar la apuesta
> para otro día, a diferencia de Chance.
>
> **Doble Play Local (3 cifras, Quindío) no está construido todavía** — hoy
> solo existe la variante Regional (4 cifras, eje cafetero).

### 7.4.3 El formulario de Baloto y MiLoto

Los números "al azar" de estos dos salen del propio backend de ventas
(`/baloto/numeros-aleatorios`, `/miloto/numeros-aleatorios`) — es el mismo
generador que usa la pantalla real, no algo que este servicio inventa.

**Baloto** deja hacer **hasta 5 apuestas** en un mismo tiquete (igual que
MiLoto), cada una con sus propios 5 números y su propia Superbalota. Revancha
es distinto: se pregunta **una sola vez para todo el tiquete**, no por
apuesta — coincide con el backend real (`/baloto/reglas`: el juego principal
admite hasta 5 "boards", pero el addon Revancha admite máximo 1):

```
data: {"delta": "¡Listo! Así queda tu Baloto:\n\n• 05, 12, 23, 34, 43 — Superbalota 07\n…"}
data: {"formulario": {
  "producto": "baloto",
  "apuestas": [
    { "numeros": ["05", "12", "23", "34", "43"], "superbalota": "07" },
    { "numeros": ["35", "01", "12", "15", "28"], "superbalota": "09" }
  ],
  "revancha": true,
  "valor": 15000
}}
data: {"done": true}
```

| Campo | Qué es |
|---|---|
| `apuestas` | **Lista de objetos** — una por cada apuesta (entre 1 y 5) |
| `apuestas[].numeros` | **Lista de exactamente 5** strings de 2 dígitos, del `"01"` al `"43"`, sin repetir dentro de esa apuesta |
| `apuestas[].superbalota` | String de 2 dígitos, del `"01"` al `"16"` — propia de esa apuesta |
| `revancha` | `true`/`false` — aplica a **todo el tiquete**, no a una apuesta en particular |
| `valor` | `6000 × cantidad de apuestas`, más `3000` si `revancha` es `true`. No lo eligió el usuario, es la suma de precios fijos |

> **Baloto no tiene paso de fecha ni deja comprar para varios sorteos
> seguidos** — el backend real sí lo soporta (`/baloto/reglas` trae
> `durations`), pero el flujo guiado replica exactamente lo que ya promete el
> guion informativo (sección 8): números, superbalota, cuántas apuestas y
> Revancha. Ofrecer varios sorteos seguidos sería un cambio de alcance, no un
> ajuste chico.

**MiLoto** deja hacer **hasta 5 apuestas** en un mismo tiquete:

```
data: {"delta": "¡Listo! Así queda tu MiLoto:\n\n• 03, 09, 15, 27, 39\n• 07, 39, 29, 09, 11\n…"}
data: {"formulario": {
  "producto": "miloto",
  "apuestas": [
    ["03", "09", "15", "27", "39"],
    ["07", "39", "29", "09", "11"]
  ],
  "valor_por_apuesta": 4000,
  "valor_total": 8000
}}
data: {"done": true}
```

| Campo | Qué es |
|---|---|
| `apuestas` | **Lista de listas** — una por cada apuesta que hizo (entre 1 y 5), cada una con **exactamente 5** strings de 2 dígitos del `"01"` al `"39"`, sin repetir dentro de la misma apuesta (sí pueden repetirse números entre apuestas distintas) |
| `valor_por_apuesta` | Fijo, `4000` |
| `valor_total` | `4000 × cantidad de apuestas` |

### 7.5 Qué pasa si el usuario se sale del guion

Está contemplado, no hay que hacer nada especial:

| El usuario escribe… | Qué pasa |
|---|---|
| algo inválido ("12" como número) | Se le explica y se repite la pregunta. **Gratis** |
| una duda ("¿qué es un combinado?") | **Responde el modelo** (esto sí cuesta tokens) y después se repite la pregunta pendiente, en el mismo turno |
| `atrás`, `volver`, `me equivoqué` | Deshace el último dato y vuelve a preguntarlo |
| `cancelar`, `salir`, `menú` | Termina el flujo. **No llega `flujo`**: deja de mandarlo |

También puede responder con el **número** de la opción (`2`) o escribir el
texto (`dorado`); las dos formas funcionan.

### 7.6 Mientras hay flujo en curso, no llega `navegacion`

Sería sacar al usuario de la pantalla justo mientras arma su compra. La
navegación de la sección 5 sigue igual para todo lo demás (saldo, PQRS,
registro, resultados).

---

## 8. Co-piloto informativo (los demás productos)

Para los **cuatro productos que no tienen flujo guiado**, "¿Necesitas ayuda?"
sigue devolviendo un guion informativo: explica qué hay que decidir, pero **no
recoge datos ni devuelve un formulario**.

> ⚠️ **Chance ya no pasa por aquí.** `ayuda_compra` con
> `contexto.modulo: "chance"` arranca el flujo de la sección 7. Esta sección
> aplica a astro, baloto, miloto, loteria, chance_millonario, recargas,
> paquetes y recaudos.

### 8.1 Ejemplo completo: alguien pide ayuda con Baloto

Escenario: el usuario está en la pantalla de Baloto y pulsa **"¿Necesitas
ayuda?"**.

**Paso 1 — El front abre el widget y dispara el guion.** No manda al usuario a
escribir nada: pulsó un botón, así que la intención ya es conocida.

```json
POST /chat
{
  "messages": [{ "role": "user", "content": "necesito ayuda" }],
  "action": "ayuda_compra",
  "contexto": { "modulo": "baloto" },
  "autenticado": true
}
```

Respuesta (**gratis**, no pasa por el modelo — llega en un solo `delta`):

```
data: {"delta": "¡Hola! Soy Facibot, y te voy a guiar paso a paso para jugar Baloto.\n\nAntes de comprar, hay 3 cosas que decidir:\n1. **Tus 5 números**, del 1 al 43…\n…\n¿Ya tienes tus números, o prefieres que te explique cómo funciona el acumulado primero?"}
data: {"done": true}
```

El front lo pinta como mensaje del asistente y lo agrega al historial.

**Paso 2 — El usuario responde.** Escribe *"explicame el acumulado"*. El front
manda el historial completo **y sigue mandando `contexto`**:

```json
POST /chat
{
  "messages": [
    { "role": "user", "content": "necesito ayuda" },
    { "role": "assistant", "content": "¡Hola! Soy Facibot, y te voy a guiar…" },
    { "role": "user", "content": "explicame el acumulado" }
  ],
  "contexto": { "modulo": "baloto" },
  "autenticado": true
}
```

Sin `action` esta vez: es texto libre. Ahora **sí** responde el modelo, y llega
escribiéndose en vivo:

```
data: {"delta": "Claro. El acumulado de Baloto"}
data: {"delta": " arranca en $4.000 millones…"}
…
data: {"usage": {"costo_usd": 0.0054}}
data: {"done": true}
```

**Paso 3 — El usuario pregunta algo que necesita un dato en vivo:** *"¿cuánto va
el acumulado?"*. Misma petición, y la respuesta ahora pasa por una herramienta:

```
data: {"delta": "El acumulado va en…"}           ← respuesta prematura
data: {"descartar": true}                        ← borrala
data: {"progreso": "Consultando los acumulados…"}
data: {"delta": "El acumulado de Baloto va"}     ← la buena, ya con el dato
data: {"delta": " en $8.500 millones…"}
data: {"usage": {"costo_usd": 0.0121}}
data: {"done": true}
```

**Si el usuario sale de la pantalla de Baloto**, el front deja de mandar
`contexto` y listo — no hay que avisar nada más.

> **Lo único que el front tiene que recordar aquí:** mandar el historial
> completo, seguir mandando `contexto.modulo` mientras esté en el producto, y
> agregar cada respuesta del asistente al historial.

### 8.2 Arrancar el guion (botón "¿Necesitas ayuda?")

```json
POST /chat
{
  "messages": [{ "role": "user", "content": "necesito ayuda" }],
  "action": "ayuda_compra",
  "contexto": { "modulo": "baloto" }
}
```

- `messages` puede traer cualquier texto placeholder (lo exige el esquema). Si
  ya hay conversación, se manda tal cual.
- `action: "ayuda_compra"` es fijo.
- `contexto.modulo` identifica el producto.

**No cuesta nada:** el guion sale del código.

### 8.3 Mantener el contexto

Mientras el usuario siga dentro del módulo (aunque no pulse ningún botón),
sigue mandando `contexto` en **cada** mensaje:

```json
{ "messages": [ /* historial completo */ ], "contexto": { "modulo": "baloto" } }
```

Así, si escribe "¿y esto cuánto cuesta?", el asistente sabe que "esto" es
Baloto. **Si el usuario sale del módulo**, simplemente deja de mandar
`contexto`.

### 8.4 Módulos con guion

| `contexto.modulo` | Producto |
|---|---|
| `chance` | ⚠️ **Tiene flujo guiado** — ver sección 7, no aplica aquí |
| `astro` | ⚠️ **Tiene flujo guiado** — ver sección 7, no aplica aquí |
| `chance_millonario` | ⚠️ **Tiene flujo guiado** — ver sección 7.4.2, no aplica aquí |
| `doble_play` | ⚠️ **Tiene flujo guiado** — ver sección 7.4.2, no aplica aquí |
| `baloto` | ⚠️ **Tiene flujo guiado** — ver sección 7.4.3, no aplica aquí |
| `miloto` | ⚠️ **Tiene flujo guiado** — ver sección 7.4.3, no aplica aquí |
| `loteria` | Lotería tradicional (billetes) |
| `recarga` | Recargas de celular |
| `paquete` | Paquetes |
| `recaudo` | Pago de facturas |
| cualquier otro | Saludo genérico, no falla |

Los diez productos que se venden hoy tienen guion; **seis de ellos además
tienen flujo guiado** (Chance, Astro, Chance Millonario, Doble Play, Baloto,
MiLoto — sección 7), que tiene prioridad sobre el guion.

Si el usuario **escribe** (sin pulsar botón) algo como "quiero jugar baloto" o
"cómo compro un billete", el router lo detecta solo y responde con el mismo
guion gratis — o arranca el flujo guiado si el producto lo tiene.

Dos casos donde el router **no** adivina, a propósito:

- Un "cómo juego la lotería" **a secas** va al modelo: mucha gente le dice
  "lotería" a cualquier juego de azar. Hace falta una señal inequívoca
  ("billete", "fracción") o que mandes `contexto.modulo`.
- **"Recargar" es ambiguo:** recargar el *celular* es `recarga`, recargar el
  *saldo* de la cuenta es otra cosa.

### 8.5 El backend también sugiere el botón

En sentido contrario, el backend puede pedirle al front que ofrezca el botón de
ayuda guiada:

```json
data: {"delta": "Baloto es el juego de acumulado más grande del país…"}
data: {"sugerencia_accion": {
  "accion": "ayuda_compra",
  "etiqueta": "¿Quieres que te ayude a hacer tu apuesta?",
  "contexto": { "modulo": "baloto" }
}}
data: {"done": true}
```

**Cuándo llega:** solo si mandaste `contexto.modulo`, ese módulo tiene guion, y
la respuesta que se acaba de dar **no fue ya** ese guion (para no ofrecerlo dos
veces seguidas).

**Qué hacer:** mostrar un botón con el texto de `etiqueta`. Si lo pulsan,
mandar un `POST /chat` con `action` y `contexto` **tal cual vienen** en la
sugerencia.

---

## 9. Lo que el asistente NO hace

- **No ejecuta compras ni transacciones.** Ni siquiera con el flujo guiado: ahí
  deja el formulario armado, pero **quien confirma y paga es el usuario**, en la
  pantalla del producto. El backend nunca llama a un endpoint de compra.
- **No ve datos privados** (saldo, historial, cupones, puntos, datos
  personales) ni los pide. Enruta a la pantalla donde están.
- **No recibe el token de sesión.** De `autenticado` solo sabe true/false.
- **No sabe qué está viendo el usuario en la pantalla.** Sabe en qué módulo
  está (`contexto.modulo`) y, si hay flujo guiado en curso, qué datos lleva
  recogidos — pero no ve el formulario real ni si el usuario lo tocó a mano.
- **No mueve al usuario entre pantallas.** `navegacion` y `formulario` son
  sugerencias: el front decide si navega y cuándo.
- **Los guiones orientan sobre qué decidir, no dónde hacer clic.** No conocemos
  el diseño exacto de cada pantalla, así que evitan instrucciones como "toca el
  botón azul de arriba". Si quieres ese nivel de detalle, hay que describir la
  pantalla real para escribirlo con precisión.

---

## 10. Checklist de implementación

- [ ] Al abrir el widget, llamar a `GET /bienvenida?autenticado=…` y pintar el
      mensaje y los botones.
- [ ] **Agregar el mensaje de bienvenida al historial** como turno `assistant`.
- [ ] Mandar `autenticado` en **todas** las peticiones, no solo al abrir.
- [ ] Repintar el menú (`GET /bienvenida`) si el usuario inicia o cierra sesión
      con el chat abierto.
- [ ] Mandar el historial completo en cada `POST /chat`, e ir agregando cada
      respuesta del asistente.
- [ ] Manejar los eventos `delta`, `descartar`, `progreso`, `opciones`,
      `navegacion`, `sugerencia_accion`, `error` y `done`.
- [ ] **Flujo guiado (sección 7):** guardar el `flujo` que llega y reenviarlo
      en la siguiente petición; dejar de mandarlo cuando no llegue.
- [ ] Pintar `opciones_flujo` como botones del paso actual.
- [ ] Con `formulario`, rellenar la pantalla del producto (Chance 7.4, Astro
      7.4.1, Chance Millonario/Doble Play 7.4.2, Baloto/MiLoto 7.4.3) y dejar
      que el usuario revise y confirme. Las cuatro formas de `formulario` son
      distintas entre sí — no asumir que son iguales.
- [ ] **Los cuatro submenús (secciones 2.1 a 2.4):** "Otras consultas",
      "Jugar acumulados", "Jugar Baloto o MiLoto" y "Recargas, paquetes y
      recaudos" se tratan igual que el menú principal — mismo evento
      `opciones`, mismo mecanismo de números.
- [ ] **`usos_modelo`:** sumar 1 por cada evento `usage` recibido y mandarlo
      de vuelta en cada petición. Sin esto el límite de conversación (30
      respuestas del modelo) nunca se activa — ver nota en la sección 4.
- [ ] Mapear los **módulos de navegación** a las rutas reales de la web (7 de gestión + 10 de productos, incluido `doble_play`).
- [ ] Mandar `contexto.modulo` mientras el usuario esté dentro de un producto.
- [ ] Convertir las negritas de Markdown, escapando el resto del HTML.

---

## 11. Probarlo con Postman

👉 **Hay una colección lista para importar: [`postman_collection.json`](postman_collection.json)**

En Postman: **Import → File** y elegí ese archivo. Trae **23 peticiones en 6
carpetas**, cada una con su explicación de qué verifica y qué esperar.

La URL está en la variable `base_url` de la colección: si el servicio cambia de
dirección, se toca en un solo lugar.

Recorrido sugerido la primera vez: carpeta **1** (que levanta), **2** (lo que
no cuesta tokens), **3** (el co-piloto de compra) y **4** (donde entra el
modelo).

Base: `https://as-eb4b47ff567b437e9e2508de6254bf9f.ecs.us-east-1.on.aws`

En todas las peticiones a `/chat`: método **POST**, header
`Content-Type: application/json`, y el cuerpo en **Body → raw → JSON**.

**Empezar por lo más simple** (`GET /health`, sin cuerpo):

```
GET {{base}}/health
```

**El menú** (`GET`, tampoco lleva cuerpo):

```
GET {{base}}/bienvenida?autenticado=false
```

**Una pregunta normal:**

```json
{
  "messages": [
    { "role": "user", "content": "¿cuánto paga un directo de 3 cifras?" }
  ],
  "autenticado": false
}
```

**Una opción del menú** (gratis, no toca el modelo):

```json
{
  "messages": [
    { "role": "user", "content": "necesito ayuda" }
  ],
  "action": "contacto",
  "autenticado": false
}
```

**Arrancar el flujo guiado de Chance** (sección 7). Fíjate en la respuesta: trae
`opciones_flujo` y `flujo`:

```json
{
  "messages": [
    { "role": "user", "content": "necesito ayuda" }
  ],
  "action": "ayuda_compra",
  "contexto": { "modulo": "chance" },
  "autenticado": true
}
```

**Lo mismo pero sin sesión** — no arranca el flujo, responde que hace falta
cuenta y manda dos eventos `navegacion`:

```json
{
  "messages": [
    { "role": "user", "content": "necesito ayuda" }
  ],
  "action": "jugar_chance",
  "autenticado": false
}
```

**El guion informativo de otro producto** (sección 8):

```json
{
  "messages": [
    { "role": "user", "content": "necesito ayuda" }
  ],
  "action": "ayuda_compra",
  "contexto": { "modulo": "baloto" },
  "autenticado": true
}
```

**Una conversación con historial** — así es como el front manda de verdad:

```json
{
  "messages": [
    { "role": "user", "content": "necesito ayuda" },
    { "role": "assistant", "content": "¡Hola! Soy Facibot, y te voy a guiar paso a paso…" },
    { "role": "user", "content": "explicame las diferencias" }
  ],
  "contexto": { "modulo": "baloto" },
  "autenticado": true
}
```

**Un paso del flujo guiado** — nota el `flujo`, que es el estado que devolvió
la respuesta anterior:

```json
{
  "messages": [
    { "role": "user", "content": "quiero hacer un chance" },
    { "role": "assistant", "content": "Vamos a armar tu chance. 🍀 ¿Para qué día…" },
    { "role": "user", "content": "2" }
  ],
  "flujo": { "producto": "chance", "datos": {} },
  "autenticado": true
}
```

### ⚠️ Postman no sirve para verificar el streaming

Postman **acumula la respuesta y te la muestra completa al final**. Vas a ver
todos los eventos `data:` juntos, así que sirve para revisar **qué** responde,
pero no para comprobar que llega progresivamente.

Para eso hace falta `curl -N` (la `-N` desactiva el buffering):

```bash
curl -N -X POST https://as-eb4b47ff567b437e9e2508de6254bf9f.ecs.us-east-1.on.aws/chat -H "Content-Type: application/json" -d "{\"messages\":[{\"role\":\"user\",\"content\":\"explicame las modalidades del chance\"}]}"
```

Si el texto va apareciendo de a poco, el streaming funciona. Si aparece todo
de golpe tras varios segundos, algo lo está bufferizando.

### Cómo leer la respuesta

Llega como texto plano, un evento por línea, separados por línea en blanco:

```
data: {"delta": "El chance es…"}

data: {"usage": {"costo_usd": 0.0054}}

data: {"done": true}
```

La respuesta que ve el usuario es la **concatenación de todos los `delta`**, en
orden. Un truco útil para depurar: si **no** aparece el evento `usage`, esa
respuesta la resolvió el router y **no costó tokens**.

## 12. Probarlo sin el front

El servicio trae una interfaz de pruebas en `http://localhost:8000` que
reproduce todo el contrato: el menú de bienvenida, el interruptor **🔓 Sesión
iniciada**, el selector **📍 Página simulada** para el `contexto.modulo`, los
botones de navegación y un contador de costo que muestra qué respuestas fueron
gratis.

No es el widget real — es para verificar el comportamiento del backend.
