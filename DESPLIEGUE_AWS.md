# Despliegue en AWS — guía paso a paso (consola web)

Despliegue en **AWS App Runner**, que resuelve solo el HTTPS, el certificado,
el dominio y el escalado.

> Reemplaza en todo el documento:
> - `<CUENTA>` → tu Account ID de 12 dígitos (arriba a la derecha en la consola)
> - `<REGION>` → la región. **Debe ser la misma donde tienes Bedrock
>   habilitado** (el proyecto usa `us-east-1` por defecto)

---

## Por qué App Runner y no ECS + ALB

**No toca la red existente.** App Runner **no usa VPC**: corre en
infraestructura administrada por AWS. La VPC `RED_SRVWEB_FACILISIMO-vpc` queda
exactamente como está.

Eso importa por dos razones concretas de esa VPC:

- Tiene **2 subredes y las dos están en `us-east-1a`**. Un ALB exige mínimo
  2 subredes en **2 zonas distintas**: no se podría crear sin agregar una
  subred nueva a la red de producción.
- **No tiene NAT Gateway**, así que la subred privada no tiene salida a
  internet — y el asistente necesita salir para llamar a Bedrock.

**Y el asistente no necesita estar dentro de esa VPC.** Todo lo que consume es
público: Bedrock, el backend de ventas (`pda1g4win0.execute-api…`, que es API
Gateway público) y `resultados.facilisimo.co`. No accede a ninguna base de
datos ni recurso privado de Facilísimo.

> **Lo único a verificar:** que App Runner no bufferice el streaming SSE. Es la
> única razón por la que habría que volver a ECS + ALB. Está el paso 6.3 para
> comprobarlo en dos minutos, y el plan B por si acaso.

---

## 1. Subir la imagen a ECR

### 1.1 Crear el repositorio

**Consola → ECR → Repositories → Create repository**

| Campo | Valor |
|---|---|
| Visibility | `Private` |
| Repository name | `asistente-ia` |
| Tag immutability | `Disabled` (para poder reusar el tag `latest`) |
| Scan on push | `Enabled` (recomendado — avisa de vulnerabilidades) |

Copia la **URI** que queda:
`<CUENTA>.dkr.ecr.<REGION>.amazonaws.com/asistente-ia`

### 1.2 Subir la imagen

Esto sí es por terminal. El botón **"View push commands"** del repositorio te
muestra estos mismos comandos ya con tus valores:

```bash
aws ecr get-login-password --region <REGION> | docker login --username AWS --password-stdin <CUENTA>.dkr.ecr.<REGION>.amazonaws.com
```

```bash
docker build -t asistente-ia:prod .
```

```bash
docker tag asistente-ia:prod <CUENTA>.dkr.ecr.<REGION>.amazonaws.com/asistente-ia:latest
```

```bash
docker push <CUENTA>.dkr.ecr.<REGION>.amazonaws.com/asistente-ia:latest
```

> **Arquitectura:** App Runner corre en `x86_64`. Si construyes la imagen en un
> Mac con chip Apple sale en ARM y no arranca. En ese caso:
> `docker build --platform linux/amd64 -t asistente-ia:prod .`

---

## 2. Crear el rol que da acceso a Bedrock

App Runner usa **dos roles distintos**, y confundirlos es el error más común:

| Rol | Quién lo usa | Para qué |
|---|---|---|
| **Access role** | App Runner, *antes* de arrancar | Bajar la imagen de ECR |
| **Instance role** | **Tu aplicación**, ya corriendo | Llamar a Bedrock |

El *access role* lo crea la consola sola en el paso 3. El **instance role hay
que crearlo ahora**, porque el formulario te lo va a pedir.

**Consola → IAM → Roles → Create role**

| Campo | Valor |
|---|---|
| Trusted entity type | `AWS service` |
| Use case | busca **App Runner** y elige **`App Runner - Instance`** |
| Permissions | **ninguna** (se agrega abajo) |
| Role name | `asistente-ia-instance-role` |

> Ojo: hay dos opciones parecidas. Tiene que ser **`App Runner - Instance`**
> (principal `tasks.apprunner.amazonaws.com`), no la otra.

Ya creado el rol: **Add permissions → Create inline policy → JSON**, y pega:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EmbeddingsTitanYModelosBase",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/amazon.titan-embed-text-v2*",
        "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5*",
        "arn:aws:bedrock:<REGION>:<CUENTA>:inference-profile/*"
      ]
    },
    {
      "Sid": "ClaudeViaMantle",
      "Effect": "Allow",
      "Action": "bedrock-mantle:CreateInference",
      "Resource": "arn:aws:bedrock-mantle:<REGION>:<CUENTA>:project/default"
    }
  ]
}
```

Nombre de la política: `asistente-ia-bedrock`.

> ⚠️ **El segundo bloque es imprescindible y no es nada obvio.** El proyecto usa
> el SDK `AnthropicBedrockMantle`, que **no** llama al `bedrock:InvokeModel`
> clásico sino a **`bedrock-mantle:CreateInference`** — otro espacio de nombres
> de IAM.
>
> Sin ese bloque la app arranca bien, el retrieval funciona, y la primera
> consulta falla con un 403 aunque la política tenga `bedrock:*` completo. En
> local no se nota, porque las credenciales de usuario suelen ser amplias: solo
> aparece al pasar a un rol con permisos mínimos.
>
> El primer bloque tampoco sobra: **Titan sí usa `bedrock:InvokeModel` normal**.
> Hacen falta los dos.

> Son **dos** modelos y hacen falta los dos: Claude responde y **Titan calcula
> los embeddings** del retrieval. Si olvidas Titan, el asistente arranca igual
> pero cae al respaldo de mandar toda la base de conocimiento en cada consulta,
> y el costo por consulta se dispara **sin ningún error visible**.
>
> Si más adelante cambias de modelo (por ejemplo a Sonnet), agrega su ARN aquí.

---

## 3. Crear el servicio en App Runner

**Consola → App Runner → Services → Create service**

### 3.1 Origen

| Campo | Valor |
|---|---|
| Repository type | `Container registry` |
| Provider | `Amazon ECR` |
| Container image URI | `<CUENTA>.dkr.ecr.<REGION>.amazonaws.com/asistente-ia:latest` |
| Deployment trigger | `Manual` |
| ECR access role | **Create new service role** (deja que la consola lo cree) |

> **Deployment trigger en `Manual` a propósito.** Con `Automatic`, cada `docker
> push` redespliega solo. Cómodo, pero significa que subir una imagen a medio
> probar sale a producción sin que nadie lo apruebe. Se puede cambiar después.

### 3.2 Configuración del servicio

| Campo | Valor |
|---|---|
| Service name | `asistente-ia` |
| Virtual CPU | `0.5 vCPU` |
| Virtual memory | `1 GB` |
| **Port** | **`8000`** ← el default es 8080, hay que cambiarlo |

**Variables de entorno** (*Environment variables*, como `Plain text`):

| Nombre | Valor | Por qué |
|---|---|---|
| `AWS_REGION` | `<REGION>` | Para que el SDK apunte a la región correcta |
| `CORS_ORIGINS` | el dominio del front | **Cámbialo**: el default `*` es demasiado abierto para producción |

**No pongas credenciales de AWS.** Las toma del instance role. Si agregas
`AWS_ACCESS_KEY_ID` aquí, anulas el mecanismo seguro.

Estas ya vienen bien por defecto y **no hace falta declararlas**:
`PRECALENTAR_EMBEDDINGS=true`, `LIMITE_MENSAJES_ACTIVO=true`.

**Instance role:** elige `asistente-ia-instance-role`, el del paso 2.

### 3.3 Health check ⚠️

Despliega la sección **Health check** y cámbiala. El default es TCP y no sirve:

| Campo | Valor |
|---|---|
| Protocol | **`HTTP`** (no TCP) |
| Path | `/health` |
| Interval | `10` segundos |
| Timeout | `5` segundos |
| Healthy threshold | `1` |
| **Unhealthy threshold** | **`5`** |

**Por qué el umbral alto:** al arrancar, el contenedor precalcula los
embeddings de los 24 documentos contra Bedrock, y **uvicorn no acepta
conexiones hasta que termina** (unos 6–15 segundos). Con `5 × 10s = 50s` de
tolerancia hay margen de sobra. Con el umbral por defecto, App Runner podría
declarar el despliegue fallido antes de que la app llegue a estar lista.

### 3.4 Autoescalado

**Configure auto scaling → Add new configuration**

| Campo | Valor |
|---|---|
| Configuration name | `asistente-ia-escalado` |
| Concurrency | `50` |
| Minimum size | `1` |
| Maximum size | `3` |

> Máximo bajo a propósito: los cachés (embeddings, loterías, acumulados,
> resultados) viven **en memoria y son por instancia**. Cada instancia nueva
> arranca con todo frío y vuelve a precalcular los embeddings. Con este
> tráfico, pocas instancias calientes rinden mejor que muchas frías.

Luego **Create & deploy**. Tarda unos 5–10 minutos.

---

## 4. Habilitar los modelos en Bedrock

Si no se hizo antes, es requisito y no da un error obvio:

**Consola → Bedrock → Model access → Modify model access**, y habilita:

- **Claude Haiku 4.5** (Anthropic)
- **Titan Text Embeddings V2** (Amazon)

Anthropic además exige, una sola vez por cuenta, enviar los detalles del caso
de uso (**"Submit use case details"**). Sin ese paso ninguna llamada funciona.

---

## 5. Verificar

Cuando el servicio quede en **`Running`**, copia la **Default domain** que da
App Runner (algo como `abc123.us-east-1.awsapprunner.com`). Ya viene con HTTPS.

Prueba en este orden, que aísla el problema:

**5.1 ¿Levantó?**

```bash
curl https://TU-DOMINIO.awsapprunner.com/health
```

Debe responder `{"status":"ok","model":"anthropic.claude-haiku-4-5"}`.

**5.2 ¿Responde sin tocar Bedrock?**

```bash
curl "https://TU-DOMINIO.awsapprunner.com/bienvenida?autenticado=false"
```

Debe devolver el menú con 7 opciones. Si esto funciona y lo siguiente no, el
problema es de **permisos de Bedrock**, no de la app.

**5.3 ⚠️ ¿El streaming llega por partes?**

Esta es **la prueba que decide si App Runner sirve**:

```bash
curl -N -X POST https://TU-DOMINIO.awsapprunner.com/chat -H "Content-Type: application/json" -d "{\"messages\":[{\"role\":\"user\",\"content\":\"que es el chance?\"}]}"
```

- ✅ **Bien:** el texto aparece **de a pedazos**, progresivamente.
- ❌ **Mal:** se queda unos segundos en blanco y luego aparece **todo de golpe**.

Si aparece todo de golpe, algo está bufferizando y hay que pasar al **plan B**
(sección 8). El servicio funcionaría igual, pero el usuario esperaría en blanco
en vez de ver la respuesta escribirse.

### Si algo falla

Los logs están en **App Runner → tu servicio → Logs**, con dos pestañas:
*Deployment logs* (el arranque) y *Application logs* (la app corriendo).

| Síntoma | Causa más probable |
|---|---|
| El despliegue falla y reintenta | Health check en TCP o umbral bajo (paso 3.3) |
| `Unable to locate credentials` | Falta el **instance role** (no el access role) |
| `AccessDeniedException` de Bedrock | Falta un modelo en la política, o no está habilitado en *Model access* |
| Health check falla pero el log no muestra errores | El puerto quedó en 8080; debe ser **8000** |
| `exec format error` | Imagen construida en ARM (Mac). Reconstruye con `--platform linux/amd64` |
| La respuesta llega toda junta al final | Buffering — ver plan B |

---

## 6. Actualizar la app

Cada vez que cambie el código:

```bash
docker build -t asistente-ia:prod .
```

```bash
docker tag asistente-ia:prod <CUENTA>.dkr.ecr.<REGION>.amazonaws.com/asistente-ia:latest
```

```bash
docker push <CUENTA>.dkr.ecr.<REGION>.amazonaws.com/asistente-ia:latest
```

Y en la consola: **App Runner → tu servicio → Deploy**.

App Runner levanta la versión nueva, espera a que pase el health check y recién
ahí manda tráfico: **el despliegue no corta el servicio**.

> Un cambio en la base de conocimiento (`app/knowledge/*.md`) también requiere
> reconstruir y redesplegar: los `.md` viajan dentro de la imagen.

---

## 5bis. Analítica: la tabla de DynamoDB

Necesaria para el panel del admin. Sin ella el asistente **funciona igual**
—registrar nunca rompe una respuesta—, solo que no guarda nada y el panel
aparece vacío.

### Crear la tabla

**Consola → DynamoDB → Tables → Create table**

| Campo | Valor |
|---|---|
| Table name | `asistente-ia-consultas` |
| Partition key | `fecha` — tipo `String` |
| Sort key | `id` — tipo `String` |
| Table settings | `Customize settings` |
| Capacity mode | **`On-demand`** |

`On-demand` porque el tráfico es bajo e irregular: se paga por escritura real
en vez de reservar capacidad que estaría ociosa.

Particionar por **fecha** permite consultar un rango de días sin escanear la
tabla entera, que es lo que hace el panel.

### Activar el borrado automático

Ya creada: pestaña **Additional settings → Time to Live (TTL) → Turn on**, con
atributo **`ttl`**.

Es lo que hace que los registros se borren solos a los 90 días. **No es
opcional:** son preguntas de usuarios y no deben acumularse indefinidamente.

### Darle permiso al asistente

**IAM → `asistente-ia-instance-role` → Add permissions → Create inline policy → JSON**:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AnaliticaDeConsultas",
      "Effect": "Allow",
      "Action": ["dynamodb:PutItem", "dynamodb:Query"],
      "Resource": "arn:aws:dynamodb:<REGION>:<CUENTA>:table/asistente-ia-consultas"
    }
  ]
}
```

Nombre: `asistente-ia-analitica`. Solo escribir y consultar: **no puede borrar
ni leer la tabla entera**.

### Configurar la clave del panel

El endpoint `/analitica/resumen` expone las preguntas de los usuarios, así que
exige una clave. **Sin clave configurada responde 503 a propósito**: es
preferible que el panel no funcione a que estos datos queden abiertos.

Genera una cadena larga y aleatoria y ponla como variable de entorno del
servicio (**ECS → Express Mode → Update → Additional configurations**):

| Nombre | Valor |
|---|---|
| `ADMIN_API_KEY` | la cadena que generaste |

La misma cadena va en `asistenteAdminKey` de los `environment.ts` del front
administrativo.

> ⚠️ **Deuda de seguridad, anotada a propósito.** Esa clave termina dentro del
> bundle de JavaScript: cualquiera con acceso al panel puede leerla desde el
> navegador y usarla directamente contra el endpoint. Lo que expone son
> preguntas ya enmascaradas y contadores, así que el riesgo es acotado — pero
> antes de producción el panel debería pedir estos datos a
> `api-administrativo`, y la clave vivir solo en ese backend.

## 6bis. Dominio propio — decisión tomada: por ahora NO

Hoy el servicio responde en la URL que genera AWS, que ya trae HTTPS y
certificado válido:

```
https://as-eb4b47ff567b437e9e2508de6254bf9f.ecs.us-east-1.on.aws
```

**Se decidió no registrar un dominio propio todavía.** El razonamiento: el
asistente lo consume el front por HTTP desde JavaScript, así que **el usuario
final nunca ve esa URL**. Un dominio nuevo sería una compra anual recurrente
por algo que no cambia nada funcionalmente.

Cuando exista un subdominio de Facilísimo (por ejemplo `asistente.facilisimo.co`),
se conecta así:

1. **Certificado en ACM**, en `us-east-1`, para ese subdominio. Validación por
   DNS (se valida sola si el dominio está en Route 53).
2. **Agregar el certificado** al listener HTTPS del ALB que creó Express Mode,
   como certificado adicional.
3. **⚠️ Agregar una regla al listener** que acepte ese `Host`, apuntando al
   target group del servicio. **Este es el paso que se olvida:** Express Mode
   enruta por *host-header*, así que con solo apuntar el DNS el balanceador
   responde 404 — el dominio nuevo no coincide con ninguna regla existente.
4. **Registro DNS**: un Alias tipo A al ALB si el dominio está en Route 53, o
   un CNAME al DNS del balanceador si está en otro proveedor.
5. Avisarle al front para que cambie la variable con la URL base.

> Express Mode puede reescribir la configuración del listener al actualizar el
> servicio. Conviene verificar que la regla del dominio siga ahí después del
> primer despliegue posterior.

## 7. Después de que funcione

- **Dominio propio:** App Runner → tu servicio → *Custom domains*. Pide el
  certificado y valida por DNS; App Runner gestiona la renovación.
- **Restringir `CORS_ORIGINS`** al dominio real del front, si quedó en `*`.
- **Alarma de costo:** ✅ hecho — ver sección 9.
- **Ojo con el costo en reposo:** App Runner cobra la memoria aprovisionada
  aunque no haya tráfico. Si el asistente va a estar parado mucho tiempo
  (por ejemplo, mientras el front todavía no lo integra), se puede **pausar**
  el servicio desde la consola.

---

## 8. Plan B: si el streaming no funciona

Solo si la prueba 5.3 muestra que la respuesta llega toda de golpe.

La alternativa es **ECS Fargate + ALB**, donde el streaming SSE funciona con
certeza. Pero **no en la VPC de Facilísimo**, por lo explicado al inicio: habría
que crear una **VPC nueva y dedicada** con 2 subredes públicas en 2 zonas
distintas, su Internet Gateway, un ALB y el servicio ECS.

Es más trabajo y suma costo fijo, pero sigue sin tocar la red de producción.

Si llegas a ese punto, avísame y armo la guía. Lo hecho hasta aquí se aprovecha
casi todo: la imagen en ECR y la política de Bedrock se reusan tal cual; solo
cambia el rol (pasa a ser un *task role* de ECS, con el mismo JSON).

---

## 9. Control de gasto: presupuesto y bloqueo automático (AWS Budgets)

Dos capas: **alertas** (te avisan) y una **acción automática** (corta el
acceso a Bedrock si el gasto se sale de control). Hecho por consola, sin
código — todo vive en AWS, no en este repo.

> ⚠️ **La razón de tener las dos capas y no solo alertas:** los datos de costo
> en AWS Budgets no son en tiempo real (van varias horas atrás), así que esto
> es una red de seguridad para gasto anómalo *sostenido*, no un tope duro
> instantáneo. El límite de 30 respuestas por conversación
> (`app/session_limit.py`) ya cubre el caso más probable (alguien usando el
> chat como personal) — esto es la protección de fondo para lo que ese límite
> no alcance a frenar.

### 9.1 La trampa que hay que evitar

El SDK que usa este proyecto (`AnthropicBedrockMantle`) **no llama a
`bedrock:InvokeModel`**, llama a **`bedrock-mantle:CreateInference`** sobre
un espacio de nombres de IAM distinto (ver sección 2, y el bug #13 de
`RESUMEN_SESION.md`). Si la política de bloqueo solo niega
`bedrock:InvokeModel`, el chat **sigue funcionando y gastando** — falsa
sensación de seguridad. Hacen falta las dos acciones negadas:

- `bedrock-mantle:CreateInference` → corta el chat (Haiku/Sonnet), el
  grueso del gasto.
- `bedrock:InvokeModel` (+ `InvokeModelWithResponseStream`) → corta Titan
  (embeddings), más chico pero no cero.

### 9.2 Crear la política de bloqueo (IAM)

**IAM → Policies → Create policy → pestaña JSON:**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BloquearBedrockPorPresupuesto",
      "Effect": "Deny",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
        "bedrock-mantle:CreateInference"
      ],
      "Resource": "*"
    }
  ]
}
```

Nombre: **`asistente-ia-bedrock-deny`**. El `Resource: "*"` no es un
problema de alcance: esta política solo se **adjunta** al rol de la
aplicación (el paso siguiente decide dónde pega, no la política en sí).

### 9.3 Crear el presupuesto con alertas

**Billing and Cost Management → Budgets → Create budget → Customize (advanced):**

| Campo | Valor |
|---|---|
| Nombre | `asistente-ia-bedrock-mensual` |
| Budget type | Cost budget |
| Period | Monthly, recurring |
| Budgeted amount | Fixed, **$500** |
| Scope | **Filter specific AWS cost dimensions** → Dimension `Service`, Includes, Value `Bedrock` |

> **El filtro por servicio importa.** Sin él, el presupuesto cuenta *toda* la
> factura de AWS (App Runner, DynamoDB, transferencia de datos...), no solo
> lo que gasta el chat — diluye la señal justo de lo que se quiere vigilar.

**Alertas** (botón *Add alert threshold*, se repite 4 veces): 50%, 80%, 90%
y 100% del costo real (*Actual*), todas con los mismos destinatarios de
correo.

### 9.4 Crear el rol que ejecuta la acción

AWS Budgets necesita permiso para adjuntar/quitar la política del rol de la
app — no lo tiene por defecto. **IAM → Roles → Create role → Custom trust
policy:**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "budgets.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

Sin políticas de la lista al crearlo — se agrega una **inline policy**
después (*Add permissions → Create inline policy → JSON*), acotada al rol y
la política específicos de este proyecto (reemplaza `<CUENTA>` por el número
de cuenta de 12 dígitos, **sin** los símbolos `<` `>`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:ListAttachedRolePolicies"
      ],
      "Resource": "arn:aws:iam::<CUENTA>:role/asistente-ia-instance-role"
    },
    {
      "Effect": "Allow",
      "Action": [
        "iam:GetPolicy",
        "iam:ListPolicyVersions"
      ],
      "Resource": "arn:aws:iam::<CUENTA>:policy/asistente-ia-bedrock-deny"
    }
  ]
}
```

Nombre del rol: **`asistente-ia-budgets-action-role`**.

### 9.5 Conectar todo: la acción en el Alert #4 (100%)

De vuelta en el presupuesto (o al crearlo, en el paso de alertas), en el
**Alert de 100%** → **Add action**:

| Campo | Valor |
|---|---|
| Select IAM role | `asistente-ia-budgets-action-role` (el de 9.4) |
| Action type | IAM Policy |
| Select an existing IAM Policy | `asistente-ia-bedrock-deny` (el de 9.2) |
| Aplicar a (user/group/role) | `asistente-ia-instance-role` — **el rol de la app**, no el de Budgets |

**No adjuntar acciones a los alerts de 50/80/90%** — esos son solo aviso por
correo, sin acción automática. Solo el de 100% ejecuta el bloqueo.

*Create budget* para terminar.

### 9.6 Qué pasa cuando se dispara, y cómo revertir

Bedrock empieza a rechazar las llamadas. El código ya lo maneja sin
romperse: `app/bedrock.py` captura el error y responde con un mensaje de
"tuvimos un inconveniente" en vez de tumbar el stream (ver `BedrockError` en
`app/main.py`).

**Lo que NO se cae:** el menú, los flujos guiados (Chance, Astro, Chance
Millonario, Doble Play, Baloto, MiLoto), loterías y acumulados en vivo — todo
eso es gratis, no toca Bedrock. Solo se corta la parte que de verdad cuesta.

**Para revertir manualmente:** IAM → Roles → `asistente-ia-instance-role` →
Permissions → quitar la política `asistente-ia-bedrock-deny`. El presupuesto
mensual se reinicia solo al empezar el mes siguiente; la acción no se
revierte sola a mitad de mes.
