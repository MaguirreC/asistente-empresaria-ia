# Asistente Virtual — Apostar / Facilísimo

Microservicio de IA para atención al cliente y asistencia de compra.
Independiente del backend Spring: se despliega en AWS y el front lo consume.

- **Stack:** Python 3.11 · FastAPI · Amazon Bedrock (Claude)
- **Estado:** funcional y probado en local. Falta desplegar en AWS.

| Documento | Para quién |
|---|---|
| Este `README.md` | quien **opera** el servicio: correr y configurar |
| [`DESPLIEGUE_AWS.md`](DESPLIEGUE_AWS.md) | guía paso a paso para desplegar en AWS |
| [`CONTRATO_FRONT.md`](CONTRATO_FRONT.md) | quien lo **consume**: el dev de front |
| [`RESUMEN_SESION.md`](RESUMEN_SESION.md) | arquitectura, decisiones tomadas y bugs ya resueltos |
| [`INVENTARIO_PREGUNTAS.md`](INVENTARIO_PREGUNTAS.md) | qué preguntas cubre la base de conocimiento |

---

## Requisitos previos en AWS

1. **Enviar los detalles del caso de uso.** Anthropic lo exige una sola vez por
   cuenta antes de poder invocar cualquier modelo. En la consola de Bedrock
   aparece un aviso con el botón **"Submit use case details"**.
   *Sin este paso ninguna llamada funciona.*
2. **Habilitar el modelo:** Bedrock → *Model access* → habilitar los modelos de
   Anthropic que vayamos a usar, **y también `Titan Text Embeddings V2`**
   (Amazon) — lo usa el retrieval de conocimiento (`app/embeddings.py`).
3. **Permisos IAM** para quien ejecute el servicio: acceso de invocación a
   Bedrock (`bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream`).
   En local se usan tus credenciales (`aws configure`); en AWS, el rol de la task.

### Modelos

En Bedrock el ID lleva el prefijo `anthropic.`:

| Modelo | ID en Bedrock | Para qué |
|---|---|---|
| Claude Haiku 4.5 | `anthropic.claude-haiku-4-5` | Preguntas frecuentes — rápido y económico (por defecto) |
| Claude Sonnet 5 | `anthropic.claude-sonnet-5` | Razonamiento, asistencia de compra, casos complejos |

> **Ojo con Sonnet 5:** trae *pensamiento adaptativo activado por defecto*, lo que
> mejora la calidad pero sube costo y latencia. Si lo usas para respuestas simples,
> conviene medir el gasto antes de dejarlo fijo.

---

## Correr en local

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
copy .env.example .env            # y ajusta los valores
python run_dev.py
```

Documentación interactiva: http://localhost:8000/docs

> **Por qué `python run_dev.py` y no `uvicorn ... --reload-include "*.md"`.**
> En PowerShell (Windows), el patrón `"*.md"` pasado por línea de comandos se
> expande antes de llegarle a uvicorn — pasa igual con `uvicorn.exe` que con
> `python -m uvicorn`, así que no es cosa del ejecutable, es el shell. La
> salida es poner el patrón en código: `run_dev.py` llama a `uvicorn.run(...)`
> directamente, sin pasar por ninguna línea de comandos que un shell pueda
> tocar. Si prefieres el CLI de todas formas (por ejemplo en Linux/macOS, sin
> este problema), sirve `uvicorn app.main:app --reload --reload-include "*.md" --port 8000`.
>
> **Vigilar los `.md` es obligatorio.** Por defecto `--reload` solo vigila
> archivos `.py`. Sin esto, cada vez que se agrega o edita un
> documento en `app/knowledge/` el servidor sigue sirviendo el contenido
> viejo hasta reiniciarlo a mano — y el error es silencioso: el bot responde
> sin el dato nuevo y no hay ningún aviso de que está desactualizado.

### Probar

```bash
curl http://localhost:8000/health
```

```bash
curl -N -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d "{\"messages\":[{\"role\":\"user\",\"content\":\"como me registro?\"}]}"
```

---

## Contrato de la API

👉 **Está completo en [`CONTRATO_FRONT.md`](CONTRATO_FRONT.md)** — es el
documento que se le entrega al dev de front.

En resumen, son tres endpoints:

| Endpoint | Para qué |
|---|---|
| `GET /health` | Health check del balanceador |
| `GET /bienvenida` | Saludo y menú de opciones al abrir el widget |
| `POST /chat` | La conversación, en streaming SSE |

Lo que conviene saber sin abrir el otro documento:

- **SSE sobre HTTPS normal**, no WebSocket: el tráfico va en una sola dirección.
- **El servicio no guarda estado.** El front manda el historial completo en
  cada turno; no hay sesión de servidor ni hacen falta sticky sessions.
- **Nunca recibe el token de sesión** ni consulta datos privados del usuario.
  Solo se le dice si hay sesión (`autenticado: true/false`) para adaptar el
  menú y enrutar.
- Buena parte de las respuestas (el menú completo, saludos, guiones de compra)
  **se resuelven sin invocar al modelo**: son gratis e instantáneas.

---

## Docker

```bash
docker build -t asistente-ia .
```

```bash
docker run -p 8000:8000 --env-file .env asistente-ia
```

La imagen corre como **usuario sin privilegios** (`asistente`, uid 10001), trae
`HEALTHCHECK` propio y arranca uvicorn con `--proxy-headers` para respetar las
cabeceras del balanceador.

> **Un solo worker por contenedor.** Los cachés (embeddings, loterías,
> acumulados, resultados) viven en memoria y son por proceso: varios workers
> duplicarían el trabajo sin compartir nada. Para escalar, más contenedores.

> El `HEALTHCHECK` tiene un `start-period` de 90 s porque al arrancar se
> precalculan los embeddings contra Bedrock y uvicorn no acepta conexiones
> hasta que termina. Sin ese margen, el contenedor se reiniciaría en bucle.

---

## Estructura

```
app/
├── main.py              # FastAPI, /chat (SSE), /bienvenida, precalentado al arrancar
├── bedrock.py           # cliente Claude en Bedrock (Mantle) + streaming + costo
├── embeddings.py        # retrieval: elige los documentos relevantes por consulta (Titan)
├── router.py            # respuestas sin modelo: menú, botones, saludos, navegación, co-piloto
├── pricing.py           # tarifas de Bedrock, cálculo del costo estimado
├── session_limit.py     # tope de mensajes por conversación (encendido)
├── config.py            # configuración por variables de entorno
├── schemas.py           # modelos de request/response
├── knowledge_loader.py  # carga los .md de app/knowledge/
├── knowledge/*.md       # base de conocimiento (un tema por archivo)
│   └── _PENDIENTES.md   # dudas del negocio y notas internas — NO se le entrega al modelo
├── tools/               # datos en vivo: loterías del día, acumulados, resultados
├── prompts/system.py    # personalidad + guardrails de juego responsable
└── static/index.html    # interfaz de pruebas (no es el widget real)
```

Los archivos de `knowledge/` que empiezan con `_` son notas internas: el
cargador los excluye, así que nunca llegan al modelo.

El cliente de Claude usa el SDK oficial de Anthropic para Bedrock
(`AnthropicBedrockMantle`), que expone la misma API de mensajes que la API
directa. El retrieval de conocimiento (`embeddings.py`) usa `boto3` directo
contra Bedrock, porque Titan Embeddings no habla la API de Anthropic.

---

## Estado de las fases

| Fase | Estado |
|---|---|
| 1. Scaffolding (FastAPI + Bedrock) | ✅ |
| 2. Base de conocimiento (RAG) | ✅ Todos los productos vigentes documentados |
| 3. Herramientas (datos en vivo) | ✅ Loterías del día, acumulados y resultados |
| 4. Co-piloto de compra | ✅ Guion para los 9 productos |
| 5. Router de botones y menú | ✅ Menú numerado y navegación, sin costo |
| 6. Infraestructura AWS | ⬜ **Pendiente** — ver `RESUMEN_SESION.md` § Despliegue |

---

## Despliegue

👉 **Guía paso a paso: [`DESPLIEGUE_AWS.md`](DESPLIEGUE_AWS.md)**

Va en **AWS App Runner**, que resuelve solo el HTTPS, el certificado, el
dominio y el escalado. **No usa VPC**, así que no toca la red existente — y el
servicio no la necesita: todo lo que consume (Bedrock, el backend de ventas y
`resultados.facilisimo.co`) es público.

**No usar Lambda + API Gateway:** bufferiza la respuesta y rompe el streaming.

Los tres puntos que más se pasan por alto, marcados con ⚠️ en la guía:

1. **Health check en HTTP `/health` con umbral alto** — el default es TCP, y al
   arrancar la app tarda unos segundos precalculando embeddings antes de
   aceptar conexiones.
2. **Puerto 8000** — App Runner propone 8080 por defecto.
3. **Dos roles IAM distintos**: el *access role* baja la imagen de ECR; el
   *instance role* es el que da permiso sobre Bedrock.

Y una verificación que decide todo: que el **streaming SSE llegue por partes**
y no de golpe (paso 5.3). Si se bufferiza, el plan B es ECS + ALB en una VPC
nueva y dedicada.

---

## Seguridad

- Las credenciales **nunca** van en el código ni en `.env` versionado (`.gitignore` lo cubre).
- En AWS se usa rol IAM, no llaves.
- El asistente **no ejecuta compras**: solo informa y guía.
- **No recibe el token de sesión del usuario ni consulta sus datos privados**
  (saldo, historial, puntos). Solo se le indica si hay sesión iniciada, para
  adaptar el menú y enrutarlo a la pantalla correcta.
- Tope de mensajes por conversación, para frenar el uso como chat personal.
- Guardrails de juego responsable en `app/prompts/system.py`.
