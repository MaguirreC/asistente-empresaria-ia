# Resumen del proyecto — Facibot, Asistente Virtual Facilísimo

Este documento existe para arrancar una sesión nueva sin perder contexto. Léelo
completo antes de tocar código.

## Qué es esto

Microservicio de IA (FastAPI + Claude en AWS Bedrock) que responde preguntas
de soporte y guía la compra en la web de Facilísimo (juegos de suerte y azar
en Colombia, regulado por Coljuegos). Es un **proyecto separado** del backend
Spring (`apostar-backend-web`); lo consume el front vía HTTP/SSE.

**Estado: desplegado y funcionando en AWS**, con HTTPS y respondiendo en vivo
(ECS Express Mode; la URL está en `CONTRATO_FRONT.md`). La **base de
conocimiento está completa** para todos los productos que se venden hoy.

> ⚠️ **El flujo guiado de compra (sección 5b) creció mucho en la última
> sesión** (de 1 producto a 6) y todavía no lo consume ningún front real —
> solo está probado con `TestClient` y contra los endpoints reales del
> backend de ventas, en local. Confirmar qué versión hay corriendo en AWS
> antes de asumir que ya incluye todo esto.

El asistente se llama **Facibot**. La co-marca "Apostar" se eliminó de todo el
proyecto: es Facilísimo a secas.

Lo que queda es integración y operación: que el front implemente el flujo
guiado (incluido el campo `usos_modelo`, nuevo), destrabar el despliegue
automático, y restringir `CORS_ORIGINS`. Ver **"Lo que sigue"** más abajo.

## Cómo correrlo

```bash
.venv\Scripts\activate
pip install -r requirements.txt
python run_dev.py                       # NO usar `uvicorn --reload-include "*.md"` directo:
                                          # PowerShell rompe el patrón "*.md" antes de
                                          # que llegue a uvicorn. run_dev.py lo evita
                                          # poniendo el patrón en código, no en el shell.
```

Abrir `http://localhost:8000` — hay una interfaz de pruebas integrada (no es
el widget real, ese lo construye el equipo de front).

**Requisitos en AWS (una sola vez):** enviar el "use case" de Anthropic en
Bedrock, habilitar **Claude Haiku 4.5** y **Titan Text Embeddings V2** en
*Model access*, y tener credenciales AWS en el entorno (`aws configure` o
variables `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` en `.env`).

## Arquitectura (de mayor a menor prioridad de lectura del costo)

**1. Router determinista (`app/router.py`)** — resuelve sin tocar el modelo:
saludos, pedidos de menú ("menú", "opciones", "volver"), las opciones del
menú numerado, "loterías de hoy", "acumulados" (genérico), y "cómo hago/juego
chance/astro" (con el guion de compra ya vetted, gratis, uno por módulo en
`_AYUDA_COMPRA`). Ante la duda, deja pasar al modelo — nunca inventa una
respuesta enlatada arriesgada.

Todos los productos con flujo guiado (`_INTENCION_AMPLIA`: chance, astro,
chance_millonario, doble_play, baloto, miloto) reconocen además
"hacer/hazme/hágame/necesito", porque así se pide en la calle ("hazme un
baloto"). Los otros cuatro (loteria, recargas, paquetes, recaudos) usan un set
más angosto porque su verbo natural no es "jugar/hacer" sino uno propio
("recargar", "pagar") — meterlos en `_INTENCION_AMPLIA` rompía "quiero
recargar mi celular" (ver bug #17). `chance` lleva además un lookahead
negativo para no robarle los mensajes a `chance_millonario`, que se evalúa
antes en el diccionario.

**1b. Menú de bienvenida (`GET /bienvenida`)** — al abrir el widget, el front
pide el saludo y las opciones numeradas (**10 para anónimo, 11 con sesión**);
todas se responden gratis. El
usuario puede pulsar el botón o **escribir el número**. Un número suelto solo
cuenta como opción si el menú (o el submenú que corresponda) es lo último que
vio (si no, "1" podría ser la respuesta a otra pregunta). Para eso el menú
viaja en el historial, y `_para_el_modelo()` en `main.py` lo filtra antes de
mandarlo al modelo: no aporta nada, gastaría tokens y ensuciaría el retrieval.
El detalle de fuentes: `_RESPUESTAS_MENU` **duplica en resumen** lo que está
en `app/knowledge/*.md` — la tabla de qué documento alimenta cada opción está
en `app/knowledge/_PENDIENTES.md`. Si editas uno de esos documentos, revisa el
router.

**Submenús (`_SUBMENUS` en `router.py`)** — para no llenar el menú principal,
las opciones menos frecuentes viven en submenús aparte, cada uno con su propio
id (que es a la vez la acción que lo abre): **"📋 Otras consultas"**
(premios, problema con una compra, PQRS), **"🎰 Jugar acumulados"** (Chance
Millonario, Doble Play), **"🎱 Jugar Baloto o MiLoto"**, y **"💳 Recargas,
paquetes y recaudos"** (estos tres últimos sin flujo guiado, solo arrancan su
guion informativo directo — con `navegacion` incluido, porque a diferencia
del botón "¿Necesitas ayuda?" acá el usuario todavía no está en esa
pantalla). Mismo mecanismo en los cuatro — mismo evento `opciones`, mismo
criterio de "el número cuenta si es lo último que vio". `submenu_de(texto)`
es la función que dice a cuál submenú corresponde un texto, si a alguno;
agregar un submenú nuevo es una entrada más en `_SUBMENUS`, no lógica nueva.

**1c. Navegación (`app/router.py` → evento SSE `navegacion`)** — cuando la
consulta tiene una pantalla propia en la web, se le manda al front el atajo
para llevar al usuario. Son dos grupos: **gestión de cuenta** (`registro`,
`ingreso`, `pqrs`, `saldo`, `resultados`, `historial`, `perfil`) y **compra de
productos** (los 10 módulos del co-piloto, incluido `doble_play`).

Sale **conteste el router o conteste el modelo**. Se manda un identificador de
módulo, **no una URL**: las rutas las conoce el front.

Dos reglas que evitan sugerencias absurdas:
- **No se ofrece ir a donde el usuario ya está.** Si `contexto.modulo` es
  `chance` y pregunta por chance, no llega `navegacion`. Si estando en chance
  pregunta por baloto, sí.
- A un anónimo que pide algo suyo (saldo, historial, perfil) se lo manda a
  `ingreso`, que es el paso que realmente le falta.

Contrato completo en `CONTRATO_FRONT.md`.

**1d. Estado de sesión (`autenticado`)** — el front manda en cada petición si
el usuario ya inició sesión. Con eso el menú se adapta: a un anónimo se le
ofrece registrarse; a quien ya entró, ver su saldo y sus compras. **La
numeración cambia entre los dos menús**, por eso `autenticado` tiene que ir en
todos los `POST /chat` y no solo al abrir. Si un anónimo pide algo suyo, se lo
enruta a `ingreso` en vez de a la pantalla, que es el paso que le falta.

**2. RAG real por similitud (`app/embeddings.py`)** — NO se manda la base de
conocimiento completa en cada consulta. Se calcula un embedding por
documento (una vez, en memoria, con Titan) y por cada pregunta se eligen los
`TOP_K=4` documentos más relacionados. Es **búsqueda híbrida**: similitud
semántica + coincidencia literal de palabras clave (`PESO_LEXICO=0.6`) — la
semántica sola falla con jerga corta como "Pata" (se confunde con "pagos").
Si el embedding falla (red, credenciales), cae de vuelta a mandar TODA la
base como respaldo — nunca sacrifica exactitud por ahorrar.
**Qué texto se busca** (`_texto_para_retrieval` en `bedrock.py`): la pregunta
del usuario, y el turno anterior del asistente **solo si la pregunta es corta**
(≤6 palabras) y recortado a 200 caracteres. Ese recorte no es cosmético: el
puntaje léxico es la *fracción* de palabras de la consulta presentes en el
documento, así que pegarle una respuesta larga diluye las palabras del usuario
hasta volverlas invisibles (ver bug #8).

**3. System prompt chico y cacheado (`app/bedrock.py`)** — `_instrucciones_base()`
ya NO tiene el conocimiento (~1.000 tokens). El TTL del caché es de **1 hora**
(no el default de 5 min), para amortizar mejor con tráfico real espaciado.
**Ojo:** con este tamaño el caché de Haiku 4.5 casi nunca se activa (el
mínimo cacheable de ese modelo es 4.096 tokens) — es una consecuencia
aceptada de haber achicado el prompt, no un bug. El costo real por consulta
en logs reales: ~$0,004–0,006.

**4. Herramientas (`app/tools/`)** — datos que cambian solo, nunca van al
conocimiento estático:
- `loterias.py` — loterías/horarios vía `/chance/loterias` del backend
  (`https://pda1g4win0.execute-api.us-east-1.amazonaws.com/pro/api/v1/ventas-facilisimo`).
  El **estado de cada lotería (abierta/cerrada/minutos restantes) y el día de
  la semana se calculan en código**, nunca los infiere el modelo — hubo dos
  bugs reales por dejarle esos cálculos al modelo (ver abajo). Caché de 10 min,
  **ahora multi-fecha** (antes se limpiaba en cada consulta porque solo
  interesaba el día en curso; el flujo guiado deja elegir dentro de la semana).
  Cada ítem es un `NamedTuple` **`Loteria`** que además del nombre y la hora
  lleva `codigo`, `id_` y `nombre_corto` — los identificadores que el flujo
  guiado le devuelve al front. Tres vistas del mismo dato:
  `resumen_para_usuario()` (router, sin tokens), `loterias_del_dia()`
  (herramienta del modelo) y `loterias_para_fecha()` (flujo guiado; devuelve
  **`None` si no se pudo consultar**, que es distinto de `[]` = "ese día no
  juega ninguna").
- `acumulados.py` — Baloto/Revancha/Chance Millonario/Doble Play vía
  `resultados.facilisimo.co/acumulados/` (dominio público aparte del backend
  de ventas). Los montos se formatean en código, no los reescribe el modelo.
- `resultados.py` — números ganadores por lotería y fecha, vía
  `resultados.facilisimo.co/resultados/?fecha=dd-mm-yyyy`. **Toda la búsqueda
  es código**: retrocede día a día desde la fecha pedida hasta encontrar la
  lotería (tope 8 días). Una sola regla cubre los dos casos: las diarias
  aparecen hoy o ayer, y las tradicionales (semanales) al llegar a su día de
  sorteo. El emparejamiento de nombres tolera variantes ("bogotá" →
  "LOTERIA BOGOTA", "extra de colombia" → "EXTRA DE COLOM"). Si no aparece,
  dice que no lo encontró: **nunca se inventa un número ganador**.
  ⚠️ El endpoint **no responde 404 para una fecha sin resultados: deja la
  conexión colgada** hasta el timeout. Por eso el timeout es corto (8 s, contra
  los ~0,4 s que tarda una fecha con datos) y los fallos se cachean 5 minutos.

**5. Co-piloto informativo (`app/router.py` + `CONTRATO_FRONT.md`)** —
campo `contexto: {"modulo": "recaudo"}` que el front manda en
cada mensaje. Acción `ayuda_compra` da un saludo guiado gratis. **Seis
productos ya no pasan por aquí: tienen flujo guiado** (5b) — chance, astro,
chance_millonario, doble_play, baloto, miloto; esto aplica a los otros 4
(loteria, recargas, paquetes, recaudos). El guion **orienta sobre qué
decidir, no dónde hacer clic** — no conocemos la UI real del front, así que
no se inventan instrucciones de "toca aquí". Además, el backend también
puede **sugerirle al front** que muestre el botón de ayuda (evento SSE
`sugerencia_accion`), cuando hay `contexto.modulo` activo y la respuesta que
se acaba de dar no fue ya ese guion — para no ofrecerlo dos veces seguidas.

**5b. Flujo guiado de compra (`app/flujos/`)** — ⭐ **seis productos ya**:
Chance, Astro, Chance Millonario, Doble Play Regional, Baloto y MiLoto. En
vez de mandar al usuario a la pantalla y dejarlo solo, le arma la apuesta
desde el chat preguntando paso a paso, y al final le devuelve al front un
**formulario listo para rellenar** (distinto en forma para cada producto —
ver `CONTRATO_FRONT.md` § 7.4 a 7.4.3). El usuario revisa y confirma; el
backend **nunca compra nada**.

Piezas:
- `app/flujos/motor.py` — motor genérico: recorre pasos, valida, arma el
  payload. También trae los helpers que comparten varias recetas:
  `pide_aleatorio()`/`numero_aleatorio()` (detectar "dame uno al azar" y
  generarlo), y `paso_jornada()`/`paso_loteria()` (agrupar una lista larga de
  loterías por hora de cierre — nació en Chance, la reusan
  chance_millonario.py y doble_play.py).
- `app/flujos/chance.py`, `astro.py`, `chance_millonario.py`, `doble_play.py`,
  `baloto.py`, `miloto.py` — una "receta" por producto. Agregar uno nuevo es
  escribir otra receta, no código nuevo (salvo que necesite un patrón que
  ninguna otra usa todavía).
- `app/tools/loterias_acumulados.py` — loterías vigentes para Chance
  Millonario y Doble Play, vía `/chance-millonario/parametros` y
  `/doble-play/parametros` del backend de ventas. Endpoints propios,
  **sin fecha** (siempre traen las de HOY) y con `horaCierre` en formato 24h
  (a diferencia de `/chance/loterias`, que usa 12h AM/PM) — hace falta un
  parser aparte. Doble Play a veces no trae `nombre` (solo `nombreCorto`); no
  se inventa el nombre completo, se muestra el corto tal cual.
- `app/tools/numeros_aleatorios.py` — números "al azar" para Baloto y MiLoto,
  pedidos **al propio backend de ventas** (`/baloto/numeros-aleatorios`,
  `/miloto/numeros-aleatorios`), no generados por este servicio — así el
  número que arma el asistente es idéntico al que daría la pantalla real. Sin
  caché a propósito.

Decisiones que conviene no reabrir sin entender por qué:

- **El estado NO se guarda en el servidor.** Viaja al front en un evento
  `flujo` y vuelve en la siguiente petición, igual que el historial. Así el
  servicio sigue **sin estado**: nada de Redis ni sticky sessions, y da igual
  qué instancia de ECS atienda cada turno.
- **El paso pendiente no se guarda: se deduce** de los datos ya recogidos
  (`_siguiente_paso`). Si el paso viajara en el estado podría desincronizarse
  de los datos; siendo función pura, esa clase de bug no existe.
- **Cuesta cero tokens.** Solo se llama al modelo si el usuario pregunta algo
  en vez de contestar ("¿qué es un combinado?"): responde el modelo y después
  se repite la pregunta pendiente en el mismo turno. Si lo que escribió es
  simplemente inválido, se reintenta gratis.
- **El paso de jornada** (mañana/mediodía/tarde/noche) existe porque un día
  cualquiera trae **~25 loterías** y listarlas todas en un chat es un muro.
  Solo aparece si hay más de 8. Agrupa por **hora de cierre real, no por el
  nombre**: hay loterías sin jornada en el nombre (SAMAN, CRUZ ROJA) y otras
  que la contradicen (PAISITA NOCHE cierra 5:45 p.m.).
- **La lotería viaja como objeto**, no como nombre: `{codigo, id, nombre,
  nombreCorto}` tal como los da el backend. Mapear por nombre es frágil —
  llegan en mayúsculas y sin tildes normalizar. Se mandan los cuatro porque no
  sabemos cuál espera el endpoint de compra; **falta confirmarlo con el front**.
- **Exige sesión iniciada**, y se corta **antes de la primera pregunta**:
  hacerle recorrer seis pasos para toparse con un muro al final sería peor. Se
  ofrecen las dos puertas (`ingreso` y `registro`) emitiendo **`navegacion` dos
  veces** — no se inventó un evento nuevo, el front ya sabe pintar ese. El
  mensaje (`MENSAJE_REQUIERE_SESION`) es genérico ("tu compra"), no menciona
  "chance": aplica igual a los seis productos.
- Las modalidades de Chance **no son excluyentes**: la pantalla real permite
  apostar a varias a la vez, cada una con su valor. El mínimo de $600 es sobre
  la **suma** de la colilla, no sobre cada modalidad.
- **Astro respeta el horario real de cada sorteo**, calculado en código: Sol
  cierra 10 min antes de su sorteo (3:50 p.m., lunes a sábado, no juega
  domingo); Luna cierra 10 min antes del suyo (varía por día). Si ya cerraron
  los dos, el flujo se corta con `FlujoNoDisponible` en vez de dejar elegir
  algo que ya no se puede jugar. **Limitación aceptada:** no hay calendario de
  festivos colombianos en el proyecto, así que un festivo entre semana se
  trata como día normal — decidido no resolverlo por ahora.
- **Chance Millonario y Doble Play son la misma familia de juego**: 2
  loterías + 5 números de 4 cifras, valor fijo (no se pregunta). **Doble Play
  Local (3 cifras, Quindío) no se ofrece en Facilísimo actualmente** —
  decisión del negocio, no queda pendiente de construir.
- **Baloto y MiLoto dejan hasta 5 apuestas por tiquete**, cada una con sus
  propios números (y su propia superbalota en Baloto) — confirmado con el
  negocio para Baloto, que el documento de conocimiento no decía explícito
  (a diferencia de MiLoto). **Revancha no es por apuesta**: se pregunta una
  sola vez por todo el tiquete, $3.000 fijos sin importar cuántas apuestas
  haya — coincide con `/baloto/reglas`, donde el addon REVANCHA trae
  `"maxBoards": 1` a diferencia del juego principal (`"maxBoards": 5`). Lo
  que sí queda fuera del flujo (backend real lo soporta, guion informativo no
  lo promete): comprar para **varios sorteos seguidos** (`durations` en
  `/baloto/reglas`).
- Salidas de emergencia: `cancelar` corta, `atrás` deshace el último dato.

Eventos SSE nuevos: `opciones_flujo` (botones del paso), `flujo` (estado a
reenviar), `formulario` (el payload final). Campo nuevo en el request: `flujo`.
Todo documentado en **`CONTRATO_FRONT.md` § 7**.

**6. Contador de costo (`app/pricing.py` + evento SSE `usage`)** — estimado
con tarifas reales de Bedrock verificadas (Haiku 4.5: $1/$5 por millón;
Sonnet 5: $2/$10 promocional hasta 2026-08-31). Solo cuenta lo que
efectivamente tocó el modelo; el router no emite este evento (es gratis).

**7. Límite de mensajes (`app/session_limit.py`)** — **encendido**: corta a
las 30 **respuestas del modelo** por conversación (no 30 mensajes), para
frenar el uso como chat personal. Se mide sobre `request.usos_modelo`, un
contador que **el front trae de vuelta** (suma 1 por cada evento `usage`
recibido) — el backend no guarda estado, así que no puede saber por sí solo
cuáles de los mensajes pasados costaron tokens y cuáles salieron gratis del
router o de un flujo guiado (ver bug #18). Si el front no manda
`usos_modelo`, el límite **nunca se activa** (no es un error, es el
comportamiento por defecto más seguro dado que no se puede calcular solo). Se
puede apagar puntualmente con `LIMITE_MENSAJES_ACTIVO=false` en el `.env`
para depurar una conversación larga, pero en producción va encendido.

**8. Validación de `messages[-1]`** — la API ahora responde **400** si el
último mensaje del array no es `role: "user"`. Existe porque el router (y
prácticamente todo `main.py`) asume que `messages[-1].content` es lo que el
usuario acaba de pedir; si el front manda el historial sin haber agregado el
turno del usuario, ese último mensaje queda siendo el saludo o el menú del
propio asistente, y el router lo interpreta como si fuera la petición real
(ver bug #18 — así se originó una sugerencia de navegación completamente
absurda en producción).

**9. Log de cada turno (`main.py`, evento `finally` de `/chat`)** — un
`logger.info` con pregunta, respuesta, acción, módulo y destino de
navegación de cada turno, pensado para depurar en los logs del servidor
(CloudWatch en producción) sin tener que reproducir nada aparte. No depende
de que la analítica de DynamoDB (`app/analitica.py`) esté configurada.

## Bugs reales encontrados y corregidos (para no repetirlos)

1. **Comparación de horas la hacía el modelo** → decía "no alcanzas" cuando sí
   alcanzaba. Arreglo: el estado (abierta/cerrada + minutos) se calcula en
   `loterias.py`, el modelo solo lo repite.
2. **Día de la semana lo calculaba el modelo** → dijo "martes" para un 29 de
   julio que era miércoles (verificado con fuentes externas). Arreglo:
   `_dia_semana()` en código con `datetime.weekday()`.
3. **Mensaje duplicado** → el modelo a veces escribe la respuesta completa
   ANTES de llamar a una herramienta, y la repite después de confirmarla.
   Primer arreglo: bufferear cada vuelta y mostrar solo la final. Funcionaba,
   pero **mató el streaming** (ver bug #12); hoy se resuelve emitiendo el texto
   en vivo y avisando con un evento `descartar` cuando la vuelta era intermedia.
4. **`"*.md"` no se expandía en `--reload-include` en PowerShell** (ni con
   `uvicorn` ni con `python -m uvicorn`) → se creó `run_dev.py`.
5. **Retrieval semántico eligió documentos equivocados** para "cuánto paga la
   pata" (confundió "paga" con "pagos"). Arreglo: búsqueda híbrida
   (semántica + léxica).
6. **El refuerzo léxico casi cayó en la misma trampa**: "qué es el chance"
   trajo `astro.md` primero, porque ese documento menciona "chance" 7 veces
   (al explicar que Astro NO se compara con el chance) — más que
   `chance-tradicional.md`, que solo lo menciona 2 veces. Arreglo: el
   refuerzo léxico ahora pesa mucho más si la palabra aparece en el
   **título** del documento que si solo aparece en el cuerpo
   (`PESO_LEXICO_TITULO=0.8` vs `PESO_LEXICO_CUERPO=0.4`).
7. **`astro.md` describía mal el mecanismo del juego**: decía que jugar solo
   3 o 2 cifras era una modalidad aparte (como Uña/Pata en el chance). El
   negocio corrigió: **siempre se juega un número de 4 cifras**; los premios
   de 3/2 cifras son por coincidencia PARCIAL de ese mismo número, no
   productos distintos. Corregido, y quedó pendiente en `_PENDIENTES.md` si
   al acertar las 4 cifras se paga solo el premio mayor o los tres se
   acumulan (resuelto después: **son excluyentes**, se paga solo el mayor).
8. **El contexto de la conversación ahogaba la pregunta en el retrieval.**
   "las principales", como seguimiento, perdía `calendario-loterias-…` y el
   asistente contestaba "no tengo esa información" — pero la misma pregunta
   completa sí funcionaba. Causa: se le pegaba la respuesta anterior ENTERA al
   texto de búsqueda, y como el puntaje léxico es la *fracción* de palabras
   encontradas, la palabra del usuario quedaba en 1 de ~40. Arreglo doble:
   (a) el turno anterior solo se arrastra si la pregunta es corta, y recortado;
   (b) el título del documento pasó a incluir "principales", que es la palabra
   que usa la gente (el título pesa 0,8 contra 0,4 del cuerpo).
9. **El modelo inventó horarios y día de la semana** aunque existía la
   herramienta: dijo "hoy es viernes… cierran a las 9:50 PM" **sin llamarla**,
   porque el calendario semanal de la base de conocimiento le parecía
   suficiente. Es el mismo fallo que #1 y #2, pero por omisión de la
   herramienta. Arreglo: sección explícita en el system prompt con los tres
   datos que NO puede dar de memoria (día/horarios, número ganador, acumulado)
   y de qué herramienta sale cada uno.
10. **El modelo se inventaba el menú.** Ante un "menú" suelto (que el router no
    reconocía) devolvía un menú propio, con opciones que **no existen** — entre
    ellas "retiros", que la plataforma no ofrece. Arreglo: el router reconoce
    los pedidos de menú, y el system prompt prohíbe inventar menús.
12. **La respuesta no se transmitía en vivo: llegaba entera al final.** El
    arreglo del bug #3 hacía `"".join(stream.text_stream)`, que consume el
    stream completo antes de emitir nada. Medido en producción: **15–20
    segundos de pantalla en blanco** y después todo de golpe. El docstring
    incluso afirmaba que las respuestas sin herramienta "conservan el
    streaming", y no era cierto. Arreglo: emitir cada fragmento apenas llega
    y, si la vuelta resulta ser de herramienta, mandar `descartar` +
    `progreso`. Verificado: pasó de 1 evento a **600 repartidos en 14,7 s**.
    Lección: al arreglar un bug, medir lo que el arreglo se lleva puesto.

13. **`bedrock-mantle` es un espacio de nombres de IAM distinto de `bedrock`.**
    El SDK `AnthropicBedrockMantle` no llama a `bedrock:InvokeModel` sino a
    `bedrock-mantle:CreateInference` sobre
    `arn:aws:bedrock-mantle:<region>:<cuenta>:project/default`. Con un rol de
    permisos mínimos da 403 aunque la política tenga `bedrock:*` completo. En
    local no se nota, porque las credenciales de usuario suelen ser amplias.
    Titan (embeddings) sí usa `bedrock:InvokeModel` normal, así que hacen falta
    **los dos** permisos.

11. **Confundió "medios de pago" con "productos".** De "los medios de pago no
    se combinan" dedujo que no se podían comprar dos productos en una misma
    transacción, y mandaba al usuario a hacer dos compras. Arreglo: se explicitó
    en `pagos-y-transacciones.md` que **varios productos sí van en un mismo
    carrito**, con una advertencia sobre esa confusión.

14. **El menú se robaba las respuestas del flujo guiado.** Estando en el paso
    "elige la fecha", el usuario contesta `1` y recibía… "ver mi saldo":
    `accion_de_menu()` interpretaba el número como opción del menú antes de que
    el flujo lo viera. Arreglo: con `request.flujo` presente **no se interpreta
    como menú** — el flujo manda. Lección: **solo apareció con la prueba
    end-to-end sobre HTTP**; probando el motor aislado el bug no existe, porque
    vive en el orden de precedencia de `/chat`, no en el motor.

15. **`chance` le robaba los mensajes a `chance_millonario`.** Al ensanchar los
    patrones de chance para aceptar "hacer/necesito/hazme", "quiero hacer un
    chance millonario" empezó a caer en el guion de chance a secas —
    `chance_millonario` se evalúa antes, pero solo reconoce los verbos de
    `_INTENCION`, que no incluye esos. Arreglo: lookahead negativo
    `(?!\s+millonario)` en los patrones de chance.

16. **Asumí que un endpoint no funcionaba sin haberlo probado.** Dije que no
    había red hacia `/chance/loterias` y mockeé las pruebas; el usuario insistió
    y el endpoint respondía perfecto. Peor: al probarlo de verdad apareció un
    problema de diseño que el mock tapaba (**25 loterías en una sola lista**),
    que motivó el paso de jornada. Lección: **probar antes de afirmar que algo
    no se puede probar** — el mock no solo era innecesario, escondía el bug.

17. **Al ampliar los verbos de intención para más productos, rompí "quiero
    recargar mi celular" y "quiero pagar una factura".** Reusé
    `_INTENCION_AMPLIA` (que exige "quiero **jugar/hacer**") también para
    `recargas` y `recaudos`, cuyo verbo natural es el propio dominio
    ("recargar", "pagar"), no "hacer". Se detectó probando explícitamente los
    casos que ya funcionaban antes del cambio. Arreglo: esos dos productos
    quedaron con su propio set de palabras sueltas (`quiero|como|necesito|
    hacer|hazme|hagame|ayuda(me)?`), no el fragmento compartido. **Lección:**
    al generalizar un helper reusado en varios sitios, probar TODOS los casos
    que ya pasaban antes, no solo el caso nuevo que motivó el cambio.

18. **El backend confiaba ciegamente en que `messages[-1]` era del usuario.**
    Un front real mandó el historial sin haber agregado el turno del usuario
    (quedó `messages` con el saludo del propio asistente como último
    elemento). El router usó ese texto como si fuera lo que pidió el usuario
    — y como el menú menciona literalmente "Cómo recargo saldo", el patrón de
    navegación a `saldo` hizo match, y "Ver acumulados" terminó sugiriendo ir
    a `saldo` sin relación con lo pedido. Arreglo: `POST /chat` ahora responde
    **400** si `messages[-1].role != "user"`, con un mensaje que apunta a
    `CONTRATO_FRONT.md` § 4. De paso se agregó un log de cada turno
    (pregunta/respuesta/acción/módulo/destino) para depurar esta clase de
    casos sin tener que reproducirlos. **Lección:** cuando un dato del
    request alimenta TODO el router sin volver a validarse, blindarlo en el
    borde de la API es más barato que perseguir cada síntoma downstream.

19. **El system prompt listaba "el acumulado" como dato obligatorio de
    herramienta, pero la frase que dispara la regla se le olvidó mencionar.**
    Decía "si vas a decir 'hoy', un día de la semana, una hora de cierre o un
    número ganador, llama primero a la herramienta" — sin nombrar el
    acumulado, aunque el ítem 3 de la lista de arriba sí lo incluía. El
    modelo, al preguntarle "¿cuánto paga Chance Millonario?", explicaba el
    mecanismo paramutual (correcto) pero no consultaba el monto vigente, y
    terminaba ofreciéndolo como algo aparte que el usuario tenía que pedir.
    Arreglo: se agregó el acumulado a la frase disparadora, y una regla
    explícita de que para juegos paramutuales (Baloto, Revancha, Chance
    Millonario, Doble Play) "¿cuánto paga/gana X?" **es** una pregunta por el
    acumulado, no una pregunta conceptual. Verificado contra el modelo real
    en los tres productos.

20. **La etiqueta "Los dos — Sol y Luna" del paso de sorteo de Astro
    contenía la palabra "luna"**, así que escribir "luna" quedaba ambiguo
    entre esa opción y "Astro Luna" — `elegir_de()` encontraba dos
    coincidencias por substring y, correctamente, se negaba a adivinar. El
    flujo quedaba trabado repitiendo la pregunta. Arreglo: renombrada a "Los
    dos sorteos", sin repetir los nombres individuales. **Lección:** al
    escribir etiquetas de opciones que se resuelven por coincidencia de
    substring (`elegir_de`), revisar que ninguna sea substring de otra ni
    comparta palabras completas con las demás.

## Regla de diseño que atraviesa todo el proyecto

**Si algo se puede calcular en código sin margen de error, no se le deja al
modelo.** Horas, fechas, montos, formato de dinero — todo eso es código. El
modelo redacta y decide intención; no hace cuentas ni compara.

## Verificado contra fuentes reales (no inventado)

- Precios de Bedrock (Haiku 4.5, Sonnet 5, Titan Embeddings) — WebSearch +
  página oficial de AWS.
- Multiplicadores de caché (1,25×/0,1× a 5 min; 2×/0,1× a 1h) — doc oficial
  de Anthropic.
- Plan de premios de Super Astro — los MONTOS coinciden exacto con Coljuegos.
  El MECANISMO (siempre 4 cifras, con premios por coincidencia parcial) lo
  corrigió el negocio después; no venía bien interpretado en la primera
  versión del documento (ver bug #7).
- Calendario semanal de loterías tradicionales — cruzado con dos fuentes
  independientes + una captura del propio sitio de Facilísimo.
- Premios del chance tradicional — vienen de una fuente interna
  (`chance-rvg.txt`). La duda del premio de Combinado 5 quedó sin objeto: el
  negocio confirmó que **las modalidades de 5 cifras no existen hoy en la
  página** y se eliminaron del documento.
- Baloto y MiLoto (mecánica, precio, días de sorteo) — contrastados contra
  baloto.com oficial, y además contra `/baloto/reglas` y `/miloto/reglas`
  **reales** del propio backend de ventas (`basePrice`, rangos de números,
  `maxBoards`) al construir el flujo guiado.
- Chance Millonario — leído de chancemillonario.com (sitio oficial del
  producto). **La mecánica de compra se corrigió después**: el documento
  decía que el resultado "se cruza con la última lotería del día"
  (automático); el negocio confirmó que en realidad el usuario **elige 2
  loterías**, igual que Doble Play — corregido en `chance-millonario.md` y en
  el guion de `router.py`.
- Actualización anual de datos del perfil — es requisito **SARLAFT**
  (Ley 1908 de 2018), no una decisión de la empresa.

## Base de conocimiento — estado (`app/knowledge/*.md`)

**24 documentos, todos los productos que se venden hoy están cubiertos:**

- *Cuenta y plataforma:* registro y cuenta, ingreso y contraseña, saldo y
  recargas, pagos y transacciones, transacciones declinadas, premios, colillas,
  PQRS y contacto, perfil y datos personales, legal y juego responsable.
- *Juegos:* chance tradicional, otras modalidades (Mega/Súper Chance, Paga Más,
  Doble Play local y regional), calendario de loterías principales, lotería
  tradicional (billetes), Baloto, MiLoto, Astro, Chance Millonario.
- *Otros productos:* recargas de celular, paquetes, recaudos, cupones, códigos
  promocionales.

**Fuera de alcance por decisión del negocio:** Puntos Leal, Polla mundialista,
Migración de usuarios, **y Doble Play Local** (Facilísimo no lo ofrece
actualmente — solo la variante Regional tiene flujo guiado). No se
documentaron/construyeron y **no hay que retomarlos** salvo que el negocio lo
pida.

`app/knowledge/_PENDIENTES.md` tiene lo que queda abierto y el histórico de
dudas resueltas (no se le entrega al modelo, es solo para el equipo). Hoy solo
quedan dos cosas: si un cupón sirve en un carrito con varios productos, y
revisar cada enero el umbral de retención, que está atado a la UVT.

## Cómo seguir alimentando conocimiento (el proceso que ya funciona)

1. Revisar `INVENTARIO_PREGUNTAS.md`, elegir un tema con 📄.
2. El usuario responde las preguntas ahí mismo, con el conocimiento real del
   negocio (no hace falta que venga ordenado — capturas, texto pegado, notas
   sueltas, todo sirve).
3. Se verifica contra fuente oficial cuando el tema toca dinero/premios/reglas
   regulatorias (Coljuegos u otra fuente confiable) antes de guardarlo.
4. Se escribe el `.md` en `app/knowledge/`.
5. Si el servidor está corriendo con `run_dev.py`, el archivo nuevo se
   recarga solo (vigila `*.md`). Si no, hay que reiniciar.
6. Se verifica que el modelo responda bien preguntando en la interfaz.

## Lo que falta por fases

| Fase | Estado |
|---|---|
| 1. Scaffolding (FastAPI + Bedrock) | ✅ |
| 2. Base de conocimiento (RAG) | ✅ Todos los productos vigentes documentados |
| 3. Herramientas (datos en vivo) | ✅ Loterías, acumulados y resultados. Los datos privados (saldo/historial/puntos) **no se consultan por decisión de diseño**: se enruta al usuario a su pantalla |
| 4. Co-piloto de compra | ✅ Mecanismo completo en ambos sentidos (front→backend y backend→front) y guion de ayuda para los 10 productos vigentes |
| 5. Router de botones y menú | ✅ Menú numerado, submenús, navegación a módulos, todo gratis |
| 6. Infraestructura AWS (despliegue) | ✅ ECS Express Mode. **Falta confirmar si lo desplegado incluye el flujo guiado y todo lo de esta sesión** |
| 7. Flujo guiado de compra | 🟡 Chance, Astro, Chance Millonario, Doble Play Regional, Baloto y MiLoto completos y probados en local (incluido contra los endpoints reales del backend de ventas). Falta: que el front lo implemente (incluido `usos_modelo`), redesplegar, y las recetas de Lotería/Recargas/Paquetes/Recaudos si el negocio las quiere |

## Despliegue (Fase 6) — cómo hacerlo

**HTTPS con SSE, no WebSocket.** Es lo que ya está construido y es lo correcto:
el tráfico va en una sola dirección (el usuario pregunta, el servidor
responde en streaming), SSE viaja sobre HTTPS normal sin configuración
especial, y como **el servicio es sin estado** (el front manda el historial
completo) **no hacen falta sticky sessions**: cualquier instancia atiende
cualquier petición. Un WebSocket ataría cada usuario a una instancia y
obligaría a manejar reconexiones y despliegues sin cortar conexiones vivas.

**Dónde: ECS Fargate detrás de un ALB.** El `Dockerfile` ya está listo y el
contenedor toma las credenciales del **rol IAM de la task** — sin llaves en el
`.env`.

**Evitar Lambda + API Gateway:** API Gateway **bufferiza la respuesta** y rompe
el streaming; el usuario vería todo de golpe al final en vez de irlo leyendo.

Ajustes concretos:

- **Idle timeout del ALB en 120 s.** Una respuesta con herramienta puede tardar
  ~15 s.
- **Misma región que Bedrock** (`us-east-1` según `config.py`).
- **Sin CloudFront delante de `/chat`**, salvo que se verifique que no
  bufferiza. El header `X-Accel-Buffering: no` ya va puesto para nginx.
- **1 worker de uvicorn por contenedor**, y escalar por contenedores. Los
  cachés (embeddings, loterías, acumulados, resultados) son **por proceso**:
  varios workers duplicarían el trabajo sin compartir nada.
- **Autoescalado suave.** Instancias que suben y bajan seguido arrancan siempre
  con el caché frío.

**Rendimiento:** el arranque precalcula los embeddings (`lifespan` en
`main.py`), así que el primer usuario no paga esos ~6 s. Si falla, el servicio
arranca igual y se reintenta en la primera pregunta. En local `run_dev.py` lo
apaga, porque con recarga automática se reiniciaría en cada archivo guardado.

> El **caché de prompt de Bedrock hoy no se activa**: el system prompt está por
> debajo del mínimo de 4.096 tokens de Haiku. Es una consecuencia aceptada de
> haberlo achicado — pero si algún día crece por encima de ese umbral, el costo
> por consulta baja bastante.

## Lo que sigue (por orden de valor)

1. **Que el front implemente el flujo guiado completo** (`CONTRATO_FRONT.md`
   § 7), que ya no es solo Chance. En concreto: guardar y reenviar `flujo`,
   pintar `opciones_flujo` como botones, con `formulario` rellenar la pantalla
   que corresponda (**cuatro formas distintas** de formulario — 7.4 a 7.4.3,
   no asumir que son iguales), manejar los dos submenús nuevos (2.1 y 2.2), y
   **sumar/reenviar `usos_modelo`** (sin esto el límite de conversación nunca
   se activa). Ojo con dos detalles que rompen callados: `navegacion` **puede
   llegar dos veces** en la misma respuesta (hay que acumular, no
   sobrescribir), y el menú **pasó de 7/8 a 8/9 opciones**.
2. **Que el front agregue el mensaje del usuario a `messages` antes de
   mandarlo.** Ahora es un requisito duro: `POST /chat` responde 400 si
   `messages[-1].role` no es `"user"` (ver bug #18). Si el front actual no lo
   hace siempre, esa llamada específica les va a empezar a fallar.
3. **Confirmar con el front qué identificador de lotería usa** el endpoint de
   compra (`codigo` o `id`), para Chance/Chance Millonario/Doble Play. Hoy se
   mandan los cuatro campos por las dudas.
4. **Redesplegar en AWS** con todo lo de esta sesión (ver aviso al inicio del
   documento — hay que confirmar primero qué versión hay corriendo).
5. **Verificar si la pantalla de Chance acepta las 4 modalidades con números de
   3 cifras.** Hoy el flujo las ofrece todas; si la UI restringe alguna, hay que
   filtrarlas en `_paso_modalidades`.
6. Recetas de flujo para Lotería/Recargas/Paquetes/Recaudos, si el negocio las
   quiere. `postman_collection.json` sigue desactualizado — decidido no
   priorizarlo por ahora.

### Estado del despliegue

Corriendo en **ECS Express Mode** (Fargate + ALB, HTTPS automático), en la VPC
por defecto — **la VPC de Facilísimo no se tocó**. La URL está en
`CONTRATO_FRONT.md`. La guía completa, con los errores que costaron encontrar,
en `DESPLIEGUE_AWS.md`.

**Control de gasto: ✅ configurado.** Presupuesto mensual de **$500** en AWS
Budgets, filtrado a Bedrock, con alertas al 50/80/90/100% por correo y una
**Budget Action** que al 100% adjunta una política Deny
(`asistente-ia-bedrock-deny`) al rol de la app
(`asistente-ia-instance-role`), cortando el acceso a Bedrock. Hecho por
consola, no toca este repo. Guía completa (con la trampa de
`bedrock-mantle:CreateInference` vs. `bedrock:InvokeModel`, que hay que negar
las dos) en `DESPLIEGUE_AWS.md` § 9.

Pendientes chicos:

- **Despliegue automático**: el workflow de GitHub Actions está listo y los
  roles de AWS creados, pero la cuenta de GitHub está **bloqueada por
  facturación** y el job no llega a arrancar. Al destrabarlo, basta con
  *Re-run all jobs*; no hace falta volver a hacer push.
- **`CORS_ORIGINS`** sigue en `*`. Restringirlo al dominio del front antes de
  salir a producción (se cambia como variable de entorno y se redespliega).
- **Dominio propio**: decidido no hacerlo por ahora (ver `DESPLIEGUE_AWS.md`
  § 6bis). Se usa la URL de AWS, que ya trae HTTPS.

### Decisiones de alcance ya tomadas (no reabrir sin pedirlo)

- **Sin herramientas con JWT.** El asistente **no consulta datos privados**
  (saldo, historial, puntos) y **no recibe el token de sesión**: por seguridad
  y para mantener el bot simple. En su lugar **enruta**: le dice al usuario
  dónde está el dato y el front lo lleva ahí, que es quien tiene la sesión.
  Solo necesita saber `autenticado` (sí/no).
- **Sin suite de pruebas automáticas.** Se verifica a mano en la interfaz.
  Ojo con esto al agregar documentos: los 24 actuales ya compiten por
  `TOP_K=4`, y el retrieval se degrada solo (así aparecieron los bugs #5, #6
  y #8). Al agregar un `.md`, conviene reprobar a mano las preguntas de los
  temas vecinos.
  Para el flujo guiado, probar **siempre sobre HTTP** (`TestClient` o el banco
  de pruebas), no solo el motor aislado: el bug #14 vivía en el orden de
  precedencia de `/chat` y el motor por sí solo lo pasaba limpio.
- **Fuera de la base de conocimiento:** Puntos Leal, Polla mundialista,
  Migración de usuarios, **y Doble Play Local** (no se ofrece en Facilísimo).
- **Sin documentar:** las dudas de Mega Chance y Paga Más que quedaban de la
  fuente original.
- **Festivos colombianos, sin resolver a propósito.** El flujo de Astro
  distingue domingo (`datetime.weekday()`) pero no festivos entre semana —
  decidido que no importa por ahora, no agregar un calendario de festivos sin
  que lo pidan.
- **Baloto sin varios "boards" ni compra para varios sorteos seguidos**,
  aunque el backend real lo soporta (`/baloto/reglas`). El flujo replica
  exactamente lo que promete el guion informativo (números + superbalota +
  Revancha); ofrecer más es un cambio de alcance explícito, no un ajuste.

## Archivos de referencia importantes

- `CONTRATO_FRONT.md` — **todo lo que el front necesita**, en un solo lugar:
  los tres endpoints, el menú de bienvenida, `autenticado`, los eventos SSE,
  los módulos de navegación, el **flujo guiado (§ 7)**, el co-piloto
  informativo (§ 8) y un checklist final. Es el documento que se le entrega al
  dev de front.
- `app/flujos/` — el flujo guiado. `motor.py` es genérico (incluye los
  helpers compartidos: número aleatorio, agrupado por jornada). Seis recetas:
  `chance.py`, `astro.py`, `chance_millonario.py`, `doble_play.py`,
  `baloto.py`, `miloto.py`. Para agregar un producto, copiar la estructura de
  la receta más parecida.
- `app/tools/loterias_acumulados.py` y `app/tools/numeros_aleatorios.py` —
  las herramientas nuevas que consumen el backend de ventas para el flujo
  guiado (loterías de Chance Millonario/Doble Play, números al azar oficiales
  de Baloto/MiLoto).
- `app/static/index.html` — banco de pruebas. Ya entiende los eventos del flujo
  y pinta el `formulario` como tarjeta con el JSON crudo, que es justo lo que
  hay que verificar. **No es el widget real.**
- `INVENTARIO_PREGUNTAS.md` — todas las preguntas posibles del backend
  analizado, con su estado.
- `README.md` — cómo correr, contrato de la API `/chat`, estructura del
  proyecto.
- `app/knowledge/_PENDIENTES.md` — lo que queda abierto, el histórico de dudas
  resueltas, y la tabla de qué documento alimenta cada respuesta enlatada del
  menú.
