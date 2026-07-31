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
  "mensaje": "¡Hola! Soy el asistente de Facilísimo. ¿En qué te ayudo?\n\n1. 📝 Cómo me registro\n2. 🎲 Loterías y horarios de hoy\n…",
  "opciones": [
    { "numero": 1, "etiqueta": "📝 Cómo me registro", "accion": "registro" },
    { "numero": 2, "etiqueta": "🎲 Loterías y horarios de hoy", "accion": "loterias_hoy" }
  ]
}
```

- **`mensaje`** ya trae el menú numerado escrito. Se puede pintar tal cual como
  primer mensaje del asistente.
- **`opciones`** son los mismos ítems como datos, por si prefieres pintar
  botones en vez de (o además de) la lista numerada.

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
| Pide saldo / historial / perfil | navega a `ingreso` | navega a la pantalla real |
| Pide "crear cuenta" | navega a `registro` | no se ofrece navegación |

Menú de **7 opciones** para anónimo y **8** con sesión, numeradas de corrido en
ambos casos.

> ⚠️ **El mismo número significa cosas distintas en cada estado:** el `1` de un
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
| `messages` | sí | Historial completo. Solo roles `user` y `assistant`; las instrucciones del asistente viven en el servidor. |
| `autenticado` | recomendado | Si el usuario tiene sesión iniciada. Por defecto `false`. |
| `action` | no | Id del botón que pulsó el usuario (ver tabla abajo). Se resuelve en código, sin costo. |
| `contexto` | no | `{ "modulo": "chance" }` — en qué producto está el usuario. Ver sección 7. |

> Hay un **tope de 30 mensajes por conversación**. Al superarlo, el asistente
> responde pidiendo empezar una nueva. Cuenta todo el historial, incluido el
> saludo de bienvenida.

### Valores de `action`

| `action` | Qué hace |
|---|---|
| `menu` | Devuelve el menú (con el evento `opciones`) |
| `registro` | Cómo registrarse → navega a `registro` |
| `ver_saldo` | Dónde ver el saldo → navega a `saldo` *(solo con sesión)* |
| `mis_compras` | Dónde ver las compras → navega a `historial` *(solo con sesión)* |
| `loterias_hoy` | Loterías y horarios de hoy (dato en vivo) |
| `acumulados` | Acumulados vigentes (dato en vivo) |
| `premios` | Cómo reclamar un premio → navega a `resultados` |
| `recargar` | Cómo recargar saldo → navega a `saldo` |
| `problema_compra` | Problemas con una compra → navega a `historial` |
| `contacto` | Contacto y PQRS → navega a `pqrs` |
| `ayuda_compra` | Arranca el co-piloto del módulo (ver sección 7) |

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

```json
data: {"delta": "Para radicar una PQRS, ingresa al apartado de PQRS…"}
data: {"navegacion": {"modulo": "pqrs", "etiqueta": "Ir a radicar mi PQRS"}}
data: {"done": true}
```

### Los siete destinos a mapear

| `modulo` | `etiqueta` | Cuándo llega |
|---|---|---|
| `registro` | Crear mi cuenta | preguntan cómo registrarse *(solo anónimos)* |
| `ingreso` | Iniciar sesión | un **anónimo** pide su saldo, historial o perfil |
| `pqrs` | Ir a radicar mi PQRS | preguntan por PQRS, quejas o reclamos |
| `saldo` | Ir a mi saldo | preguntan por su saldo o cómo recargarlo |
| `resultados` | Ver resultados | preguntan por resultados o si ganaron |
| `historial` | Ver mi historial de compras | preguntan por sus compras |
| `perfil` | Ir a mi perfil | preguntan por cambiar correo, celular o datos |

**El front debe mapear estos siete identificadores a sus rutas.** Es la lista
completa: si llega un `modulo` desconocido, lo más seguro es no pintar el botón
en vez de adivinar.

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
data: {"delta": "¡Hola! Soy el asistente de Facilísimo…\n\n1. 📝 Cómo me registro\n…"}
data: {"opciones": [{"numero": 1, "etiqueta": "📝 Cómo me registro", "accion": "registro"}]}
data: {"done": true}
```

Son los mismos objetos que devuelve `GET /bienvenida`, así que puedes
reutilizar el mismo componente.

---

## 7. Co-piloto de compra

Guía al usuario paso a paso mientras está en la página de un producto.

### 7.1 Arrancar el flujo (botón "¿Necesitas ayuda?")

```json
POST /chat
{
  "messages": [{ "role": "user", "content": "necesito ayuda" }],
  "action": "ayuda_compra",
  "contexto": { "modulo": "chance" }
}
```

- `messages` puede traer cualquier texto placeholder (lo exige el esquema). Si
  ya hay conversación, se manda tal cual.
- `action: "ayuda_compra"` es fijo.
- `contexto.modulo` identifica el producto.

**No cuesta nada:** el guion sale del código.

### 7.2 Mantener el contexto

Mientras el usuario siga dentro del módulo (aunque no pulse ningún botón),
sigue mandando `contexto` en **cada** mensaje:

```json
{ "messages": [ /* historial completo */ ], "contexto": { "modulo": "chance" } }
```

Así, si escribe "¿y esto cuánto cuesta?", el asistente sabe que "esto" es
Chance. **Si el usuario sale del módulo**, simplemente deja de mandar
`contexto`.

### 7.3 Módulos con guion

| `contexto.modulo` | Producto |
|---|---|
| `chance` | Chance tradicional |
| `astro` | Super Astro |
| `baloto` | Baloto y Revancha |
| `miloto` | MiLoto |
| `loteria` | Lotería tradicional (billetes) |
| `chance_millonario` | Chance Millonario |
| `recargas` | Recargas de celular |
| `paquetes` | Paquetes |
| `recaudos` | Pago de facturas |
| cualquier otro | Saludo genérico, no falla |

Los nueve productos que se venden hoy tienen guion.

Si el usuario **escribe** (sin pulsar botón) algo como "cómo hago chance",
"quiero jugar baloto" o "cómo compro un billete", el router lo detecta solo y
responde con el mismo guion gratis.

Dos casos donde el router **no** adivina, a propósito:

- Un "cómo juego la lotería" **a secas** va al modelo: mucha gente le dice
  "lotería" a cualquier juego de azar. Hace falta una señal inequívoca
  ("billete", "fracción") o que mandes `contexto.modulo`.
- **"Recargar" es ambiguo:** recargar el *celular* es `recargas`, recargar el
  *saldo* de la cuenta es otra cosa.

### 7.4 El backend también sugiere el botón

En sentido contrario, el backend puede pedirle al front que ofrezca el botón de
ayuda guiada:

```json
data: {"delta": "El chance es el juego de suerte y azar…"}
data: {"sugerencia_accion": {
  "accion": "ayuda_compra",
  "etiqueta": "¿Quieres que te ayude a hacer tu apuesta?",
  "contexto": { "modulo": "chance" }
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

## 8. Lo que el asistente NO hace

- **No ejecuta compras ni transacciones.** Solo informa y guía; el usuario
  siempre confirma y ejecuta.
- **No ve datos privados** (saldo, historial, cupones, puntos, datos
  personales) ni los pide. Enruta a la pantalla donde están.
- **No sabe en qué paso del formulario está el usuario.** El contexto es a
  nivel de módulo completo, no paso a paso.
- **No hace deep-linking:** no puede seleccionar una modalidad por el usuario
  ni moverlo entre pantallas — solo sugerir el destino.
- **Los guiones orientan sobre qué decidir, no dónde hacer clic.** No conocemos
  el diseño exacto de cada pantalla, así que evitan instrucciones como "toca el
  botón azul de arriba". Si quieres ese nivel de detalle, hay que describir la
  pantalla real para escribirlo con precisión.

---

## 9. Checklist de implementación

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
- [ ] Mapear los **siete módulos de navegación** a las rutas reales de la web.
- [ ] Mandar `contexto.modulo` mientras el usuario esté dentro de un producto.
- [ ] Convertir las negritas de Markdown, escapando el resto del HTML.

---

## 10. Probarlo sin el front

El servicio trae una interfaz de pruebas en `http://localhost:8000` que
reproduce todo el contrato: el menú de bienvenida, el interruptor **🔓 Sesión
iniciada**, el selector **📍 Página simulada** para el `contexto.modulo`, los
botones de navegación y un contador de costo que muestra qué respuestas fueron
gratis.

No es el widget real — es para verificar el comportamiento del backend.
