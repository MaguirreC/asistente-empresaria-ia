# Resumen del proyecto — Asistente Virtual Apostar/Facilísimo

Este documento existe para arrancar una sesión nueva sin perder contexto. Léelo
completo antes de tocar código.

## Qué es esto

Microservicio de IA (FastAPI + Claude en AWS Bedrock) que responde preguntas
de soporte y guía la compra en la web de Facilísimo (juegos de suerte y azar
en Colombia, regulado por Coljuegos). Es un **proyecto separado** del backend
Spring (`apostar-backend-web`); lo consume el front vía HTTP/SSE.

**Estado:** funcional en local, verificado con uso real (logs reales de
Bedrock). La **base de conocimiento está completa** para todos los productos
que se venden hoy. Lo que falta es principalmente de plataforma: desplegar en
AWS (Fase 6, no iniciada), las herramientas que necesitan usuario autenticado
(saldo, historial, puntos) y una suite de pruebas automáticas.

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
saludos, pedidos de menú ("menú", "opciones", "volver"), las 7 opciones del
menú numerado, "loterías de hoy", "acumulados" (genérico), y "cómo hago/juego
chance/astro" (con el guion de compra ya vetted, gratis, uno por módulo en
`_AYUDA_COMPRA`). Ante la duda, deja pasar al modelo — nunca inventa una
respuesta enlatada arriesgada.

**1b. Menú de bienvenida (`GET /bienvenida`)** — al abrir el widget, el front
pide el saludo y las 7 opciones numeradas; todas se responden gratis. El
usuario puede pulsar el botón o **escribir el número**. Un número suelto solo
cuenta como opción si el menú es lo último que vio (si no, "1" podría ser la
respuesta a otra pregunta). Para eso el menú viaja en el historial, y
`_para_el_modelo()` en `main.py` lo filtra antes de mandarlo al modelo: no
aporta nada, gastaría tokens y ensuciaría el retrieval.
El detalle de fuentes: `_RESPUESTAS_MENU` **duplica en resumen** lo que está
en `app/knowledge/*.md` — la tabla de qué documento alimenta cada opción está
en `app/knowledge/_PENDIENTES.md`. Si editas uno de esos documentos, revisa el
router.

**1c. Navegación (`app/router.py` → evento SSE `navegacion`)** — cuando la
consulta tiene una pantalla propia en la web, se le manda al front el atajo
para llevar al usuario: `registro`, `ingreso`, `pqrs`, `saldo`, `resultados`,
`historial` y `perfil`. Sale **conteste el router o conteste el modelo**. Se
manda un identificador de módulo, **no una URL**: las rutas las conoce el
front. Contrato completo en `CONTRATO_FRONT.md`.

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
- `loterias.py` — loterías/horarios de HOY vía `/chance/loterias` del backend
  (`https://pda1g4win0.execute-api.us-east-1.amazonaws.com/pro/api/v1/ventas-facilisimo`).
  El **estado de cada lotería (abierta/cerrada/minutos restantes) y el día de
  la semana se calculan en código**, nunca los infiere el modelo — hubo dos
  bugs reales por dejarle esos cálculos al modelo (ver abajo). Caché de 10 min.
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

**5. Co-piloto de compra (`app/router.py` + `CONTRATO_FRONT.md`)** —
campo `contexto: {"modulo": "chance"}` (o `"astro"`) que el front manda en
cada mensaje. Acción `ayuda_compra` da un saludo guiado gratis. El guion
**orienta sobre qué decidir, no dónde hacer clic** — no conocemos la UI real
del front, así que no se inventan instrucciones de "toca aquí". Además, el
backend también puede **sugerirle al front** que muestre el botón de ayuda
(evento SSE `sugerencia_accion`), cuando hay `contexto.modulo` activo y la
respuesta que se acaba de dar no fue ya ese guion — para no ofrecerlo dos
veces seguidas. Probado end-to-end en la interfaz de pruebas, incluida la
señal `sugerencia_accion` con el botón real en el navegador.

**6. Contador de costo (`app/pricing.py` + evento SSE `usage`)** — estimado
con tarifas reales de Bedrock verificadas (Haiku 4.5: $1/$5 por millón;
Sonnet 5: $2/$10 promocional hasta 2026-08-31). Solo cuenta lo que
efectivamente tocó el modelo; el router no emite este evento (es gratis).

**7. Límite de mensajes (`app/session_limit.py`)** — **encendido**: corta a los
30 mensajes por conversación, para frenar el uso como chat personal. Cuenta
todo el historial, incluido el menú de bienvenida. Se puede apagar puntualmente
con `LIMITE_MENSAJES_ACTIVO=false` en el `.env` para depurar una conversación
larga, pero en producción va encendido.

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
  baloto.com oficial.
- Chance Millonario — leído de chancemillonario.com (sitio oficial del
  producto).
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

**Fuera de alcance por decisión del negocio:** Puntos Leal, Polla mundialista y
Migración de usuarios. No se documentaron y **no hay que retomarlos** salvo que
el negocio lo pida.

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
| 4. Co-piloto de compra | ✅ Mecanismo completo en ambos sentidos (front→backend y backend→front) y guion de ayuda para los 9 productos vigentes |
| 5. Router de botones y menú | ✅ Menú numerado, navegación a módulos, todo gratis |
| 6. Infraestructura AWS (despliegue) | ⬜ No iniciada |

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

1. **Fase 6: desplegar en AWS** siguiendo la sección de arriba.
2. **Del lado del front**, para aprovechar lo que ya está: mandar
   `autenticado` en cada petición, meter el saludo de `/bienvenida` en el
   historial, y mapear los siete módulos de navegación a sus rutas. Los tres
   requisitos están en `CONTRATO_FRONT.md`.

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
- **Fuera de la base de conocimiento:** Puntos Leal, Polla mundialista y
  Migración de usuarios.
- **Sin documentar:** las dudas de Mega Chance y Paga Más que quedaban de la
  fuente original.

## Archivos de referencia importantes

- `CONTRATO_FRONT.md` — **todo lo que el front necesita**, en un solo lugar:
  los tres endpoints, el menú de bienvenida, `autenticado`, los eventos SSE,
  los siete módulos de navegación, el co-piloto y un checklist final de
  implementación. Es el documento que se le entrega al dev de front.
- `INVENTARIO_PREGUNTAS.md` — todas las preguntas posibles del backend
  analizado, con su estado.
- `README.md` — cómo correr, contrato de la API `/chat`, estructura del
  proyecto.
- `app/knowledge/_PENDIENTES.md` — lo que queda abierto, el histórico de dudas
  resueltas, y la tabla de qué documento alimenta cada respuesta enlatada del
  menú.
