"""Router determinista: resuelve en código lo que no necesita al modelo.

Cada consulta que atiende este módulo cuesta cero tokens. El modelo queda como
red de seguridad para lo que aquí no se reconoce.

CRITERIO IMPORTANTE: ante la duda, NO responder y dejar pasar al modelo. Es
mucho peor soltar una respuesta enlatada a una pregunta con matices que pagar
una llamada al modelo.
"""
import logging
import re
import unicodedata

from app.tools.acumulados import resumen_para_usuario as resumen_acumulados
from app.tools.loterias import resumen_para_usuario as resumen_loterias

logger = logging.getLogger(__name__)

# Acciones que el front dispara con botones. Al venir de un botón la intención
# es segura, así que no hay que adivinar nada.
ACCION_LOTERIAS_HOY = "loterias_hoy"
ACCION_ACUMULADOS = "acumulados"
ACCION_MENU = "menu"
ACCION_AYUDA_COMPRA = "ayuda_compra"
ACCION_REGISTRO = "registro"
ACCION_PREMIOS = "premios"
ACCION_RECARGAR = "recargar"
ACCION_PROBLEMA_COMPRA = "problema_compra"
ACCION_CONTACTO = "contacto"
# Arranca el flujo guiado que le arma la apuesta al usuario desde el chat.
ACCION_JUGAR_CHANCE = "jugar_chance"
# Opciones de usuario ya identificado. El asistente NO consulta estos datos:
# solo lleva al usuario a la pantalla donde ya están. Es una decisión de
# diseño — ver el bloque de navegación más abajo.
ACCION_VER_SALDO = "ver_saldo"
ACCION_MIS_COMPRAS = "mis_compras"

# Saludo inicial del co-piloto de compra, por módulo. Guía sobre QUÉ decidir,
# no sobre DÓNDE hacer clic — no conocemos el diseño real de cada pantalla del
# front, así que no se inventan instrucciones de "toca aquí" o "botón azul".
_AYUDA_COMPRA: dict[str, str] = {
    "chance": (
        "¡Hola! Soy Facibot, y te voy a guiar paso a paso para hacer "
        "tu apuesta de Chance.\n\n"
        "Antes de comprar, hay 4 cosas que decidir:\n"
        "1. **La modalidad** — Uña, Pata, Directo, Combinado, o una especial "
        "como Súper Chance o Paga Más. Cada una paga distinto.\n"
        "2. **El número** al que quieres apostar.\n"
        "3. **La lotería o sorteo** con el que juegas.\n"
        "4. **El valor** — mínimo $600 por colilla.\n\n"
        "¿Ya sabes qué modalidad quieres jugar, o te explico las diferencias primero?"
    ),
    "astro": (
        "¡Hola! Soy Facibot, y te voy a guiar paso a paso para hacer "
        "tu apuesta de Astro.\n\n"
        "Antes de comprar, hay 4 cosas que decidir:\n"
        "1. **El número** — siempre son 4 cifras (0000 a 9999). El premio "
        "depende de cuántas coincidan con el resultado.\n"
        "2. **El signo zodiacal** — uno de los 12.\n"
        "3. **El sorteo** — Astro Sol (día) o Astro Luna (noche).\n"
        "4. **El valor** — entre $500 y $10.000 por tiquete.\n\n"
        "¿Ya sabes qué número y signo quieres jugar, o te explico el plan de premios primero?"
    ),
    "baloto": (
        "¡Hola! Soy Facibot, y te voy a guiar paso a paso para jugar "
        "Baloto.\n\n"
        "Antes de comprar, hay 3 cosas que decidir:\n"
        "1. **Tus 5 números**, del 1 al 43. Puedes elegirlos tú o dejar que "
        "el sistema los tome al azar.\n"
        "2. **La Superbalota**, un número del 1 al 16.\n"
        "3. **Si juegas Revancha** — por $3.000 más participas con los mismos "
        "números en un segundo acumulado. Baloto solo cuesta $6.000; los dos "
        "juntos, $9.000.\n\n"
        "Ten en cuenta que para jugar Baloto por primera vez debes completar "
        "un registro adicional: lo exige el operador del juego, no Facilísimo.\n\n"
        "¿Ya tienes tus números, o prefieres que te explique cómo funciona el "
        "acumulado primero?"
    ),
    "miloto": (
        "¡Hola! Soy Facibot, y te voy a guiar paso a paso para jugar "
        "MiLoto.\n\n"
        "Antes de comprar, hay 2 cosas que decidir:\n"
        "1. **Tus 5 números**, del 1 al 39, sin repetir. Puedes elegirlos tú "
        "o dejar que el sistema los tome al azar.\n"
        "2. **Cuántas apuestas** haces — en un mismo tiquete caben hasta 5, y "
        "cada una cuesta $4.000.\n\n"
        "MiLoto juega lunes, martes, jueves y viernes.\n\n"
        "¿Ya tienes tus números, o te explico cómo se gana primero?"
    ),
    "loteria": (
        "¡Hola! Soy Facibot, y te voy a guiar paso a paso para comprar "
        "tu billete de lotería.\n\n"
        "Antes de comprar, hay 3 cosas que decidir:\n"
        "1. **Qué lotería** quieres jugar — cada una sortea un día fijo de la "
        "semana.\n"
        "2. **Qué billete** — cada uno tiene un número y una serie. Para el "
        "premio mayor deben coincidir los dos.\n"
        "3. **Cuántas fracciones** compras. No estás obligado a llevar el "
        "billete entero: cada fracción gana la parte proporcional del premio.\n\n"
        "¿Ya sabes qué lotería quieres, o te digo cuáles están disponibles?"
    ),
    "chance_millonario": (
        "¡Hola! Soy Facibot, y te voy a guiar paso a paso para jugar "
        "Chance Millonario.\n\n"
        "Es una modalidad de **doble acierto**: se cruza con las cuatro cifras "
        "del premio mayor de la última lotería del día, y juega todos los días, "
        "incluidos festivos. La apuesta cuesta **$6.000**.\n\n"
        "Ten en cuenta que el acumulado es **paramutual**: si en una misma "
        "fecha hay varios ganadores, el premio se reparte entre ellos.\n\n"
        "¿Quieres que te explique cómo se juega en detalle, o ya sabes qué "
        "números vas a elegir?"
    ),
    # Los tres que siguen no son juegos: no se eligen números, pero sí hay
    # decisiones que tomar y, sobre todo, datos que conviene confirmar ANTES de
    # pagar — una recarga a un número equivocado no se puede reversar.
    "recargas": (
        "¡Hola! Soy Facibot, y te voy a guiar paso a paso para hacer "
        "tu recarga.\n\n"
        "Antes de recargar, hay 3 cosas que confirmar:\n"
        "1. **El operador** — Claro, Movistar, Tigo, WOM o DIRECTV.\n"
        "2. **El número** al que va la recarga. Revísalo con cuidado: una vez "
        "hecha, **la recarga no se puede reversar**.\n"
        "3. **El monto** — entre $1.000 y $50.000.\n\n"
        "La recarga llega de inmediato.\n\n"
        "¿Ya tienes a la mano el número y el operador?"
    ),
    "paquetes": (
        "¡Hola! Soy Facibot, y te voy a guiar paso a paso para comprar "
        "tu paquete.\n\n"
        "Un paquete no es lo mismo que una recarga: en vez de cargarte saldo, "
        "incluye **minutos, datos y redes sociales** por un tiempo determinado.\n\n"
        "Antes de comprar, hay 3 cosas que decidir:\n"
        "1. **El operador**.\n"
        "2. **Qué paquete** — cada uno trae una combinación distinta y su "
        "**propia vigencia**, así que vale la pena mirar cuánto dura antes de "
        "elegir.\n"
        "3. **El número** al que va. Confírmalo bien antes de pagar.\n\n"
        "¿Ya sabes qué operador y qué paquete quieres, o te explico primero la "
        "diferencia con una recarga normal?"
    ),
    "recaudos": (
        "¡Hola! Soy Facibot, y te voy a guiar paso a paso para pagar "
        "tu factura.\n\n"
        "Antes de pagar, hay 3 cosas que confirmar:\n"
        "1. **Qué empresa o convenio** vas a pagar.\n"
        "2. **Los datos que pide ese convenio** — normalmente el número de "
        "referencia o de cuenta que aparece en tu factura, así que tenla a mano.\n"
        "3. **El valor** a pagar.\n\n"
        "Ten en cuenta que pagar por aquí **no tiene ningún costo adicional**, "
        "el pago se aplica en unos 20 minutos, y te llega una confirmación al "
        "correo (no es un recibo formal).\n\n"
        "¿Qué servicio vas a pagar?"
    ),
}

_AYUDA_COMPRA_GENERICA = (
    "¡Hola! Veo que estás en esta sección. Cuéntame qué necesitas y te ayudo "
    "a resolverlo, o dime directamente qué dudas tienes sobre este producto."
)


def tiene_guion_ayuda_compra(modulo: str | None) -> bool:
    """Si hay un guion de bienvenida ya escrito para ese módulo."""
    return modulo in _AYUDA_COMPRA


def es_guion_ayuda_compra(texto: str | None, modulo: str | None) -> bool:
    """Si `texto` es exactamente el guion de ayuda de ese módulo — para no
    ofrecer el botón de ayuda justo después de haber dado esa misma ayuda."""
    return texto is not None and texto == _AYUDA_COMPRA.get(modulo)

# --- Menú de opciones rápidas --------------------------------------------
# TODAS se responden en código, sin tocar el modelo. Es la lista única de la
# que salen tanto el texto numerado que ve el usuario como los botones que se
# le entregan al front (`GET /bienvenida`): así nunca se desincronizan.
#
# Ojo al agregar una opción: solo entra aquí si su respuesta se puede dar sin
# el modelo. Una opción que termine cayendo al modelo rompe la promesa de que
# el menú es gratis.
# Visibilidad de cada opción según si el usuario inició sesión:
#   SIEMPRE      -> se muestra en los dos casos
#   ANONIMO      -> solo a quien NO ha iniciado sesión (registrarse)
#   AUTENTICADO  -> solo a quien ya inició sesión (su saldo, sus compras)
SIEMPRE, ANONIMO, AUTENTICADO = "siempre", "anonimo", "autenticado"

_OPCIONES_MENU: tuple[tuple[str, str, str], ...] = (
    # Va de primera porque es lo que más gente viene a hacer. Se muestra a
    # todos: a quien no ha iniciado sesión se le explica que necesita cuenta y
    # se le ofrecen las dos puertas, en vez de esconderle la opción.
    ("🍀 Hacer un chance", ACCION_JUGAR_CHANCE, SIEMPRE),
    ("💰 Ver mi saldo", ACCION_VER_SALDO, AUTENTICADO),
    ("🧾 Mis compras", ACCION_MIS_COMPRAS, AUTENTICADO),
    ("📝 Cómo me registro", ACCION_REGISTRO, ANONIMO),
    ("🎲 Loterías y horarios de hoy", ACCION_LOTERIAS_HOY, SIEMPRE),
    ("💵 Ver acumulados", ACCION_ACUMULADOS, SIEMPRE),
    ("🏆 Cómo reclamo un premio", ACCION_PREMIOS, SIEMPRE),
    ("💳 Cómo recargo saldo", ACCION_RECARGAR, SIEMPRE),
    ("⚠️ Tuve un problema con una compra", ACCION_PROBLEMA_COMPRA, SIEMPRE),
    ("📞 Contacto y PQRS", ACCION_CONTACTO, SIEMPRE),
)

SALUDO_BIENVENIDA = "¡Hola! Soy Facibot, el asistente de Facilísimo. ¿En qué te ayudo?"

_PIE_MENU = "Responde con el número de la opción, o escríbeme tu pregunta directamente."


def _visibles(autenticado: bool) -> list[tuple[str, str]]:
    """(etiqueta, acción) de las opciones que aplican a este usuario."""
    permitido = AUTENTICADO if autenticado else ANONIMO
    return [
        (etiqueta, accion)
        for etiqueta, accion, visibilidad in _OPCIONES_MENU
        if visibilidad in (SIEMPRE, permitido)
    ]


def menu_texto(autenticado: bool = False) -> str:
    """Menú numerado. La numeración se calcula sobre las opciones visibles, así
    que nunca quedan huecos aunque se oculte alguna."""
    lineas = "\n".join(
        f"{i}. {etiqueta}" for i, (etiqueta, _) in enumerate(_visibles(autenticado), 1)
    )
    return f"{SALUDO_BIENVENIDA}\n\n{lineas}\n\n{_PIE_MENU}"


def opciones_menu(autenticado: bool = False) -> list[dict]:
    """Las opciones como datos, para que el front pinte botones."""
    return [
        {"numero": i, "etiqueta": etiqueta, "accion": accion}
        for i, (etiqueta, accion) in enumerate(_visibles(autenticado), 1)
    ]


def accion_de_menu(
    texto: str, ultimo_del_asistente: str | None = None, autenticado: bool = False
) -> str | None:
    """Si el mensaje es SOLO el número de una opción, devuelve su acción.

    Dos condiciones, y las dos importan:

    1. El mensaje entero debe ser el número. "1" es una selección de menú;
       "1 chance" o "juego 3 cifras" son preguntas y van al modelo.
    2. El menú tiene que ser **lo último que vio el usuario**. Si no, un "1"
       puede ser la respuesta a otra pregunta: la opción de "problema con una
       compra" termina con "cuéntame cuál es tu caso", y quien conteste "1"
       pensando en la primera viñeta recibiría otra cosa.

    Si todavía no hay ningún mensaje del asistente, el chat acaba de abrirse y
    lo único que el usuario pudo ver es el menú, así que el número cuenta.

    El número se resuelve contra el menú de ESTE usuario: el 1 de alguien con
    sesión iniciada es "ver mi saldo", y el de un anónimo es "cómo me registro".
    """
    plano = _normalizar(texto)
    if not plano.isdigit():
        return None
    visibles = _visibles(autenticado)
    indice = int(plano) - 1
    if not 0 <= indice < len(visibles):
        return None
    accion = visibles[indice][1]
    if ultimo_del_asistente is None:
        return accion
    return accion if es_menu(ultimo_del_asistente) else None


def es_menu(texto: str | None) -> bool:
    """Si ese texto es el menú, en cualquiera de sus dos variantes.

    Se comprueban las dos porque el historial puede traer un menú pintado
    cuando el usuario todavía no había iniciado sesión (o al revés).
    """
    return texto is not None and texto in (menu_texto(False), menu_texto(True))


# --- Respuestas enlatadas del menú ---------------------------------------
# CUIDADO AL EDITAR: esto duplica, resumido, lo que está en
# `app/knowledge/*.md`. Si cambia el documento fuente hay que actualizar aquí
# también — el modelo lee el .md, pero estas respuestas no pasan por él.
# Cada una indica de qué documento salió.
_RESPUESTAS_MENU: dict[str, str] = {
    # Estas dos NO consultan nada: solo acompañan al botón de navegación.
    ACCION_VER_SALDO: (
        "Tu saldo lo ves en la parte de arriba de la página, en el apartado de "
        "**saldo**. Ahí mismo puedes recargarlo con el botón **+**.\n\n"
        "Te dejo el acceso directo. 👇"
    ),
    ACCION_MIS_COMPRAS: (
        "En tu **historial de compras** encuentras todo lo que has comprado, "
        "con su fecha y su estado.\n\n"
        "Si una compra no aparece ahí y sí te descontaron el dinero, radica una "
        "PQRS para que le hagan seguimiento.\n\n"
        "Te dejo el acceso directo. 👇"
    ),
    # fuente: knowledge/registro-y-cuenta.md
    ACCION_REGISTRO: (
        "Para crear tu cuenta necesitas tener a mano tu documento, porque los "
        "datos deben coincidir exactamente con él:\n\n"
        "• **Nombres y apellidos completos**, tal como aparecen en el documento\n"
        "• **Fecha de nacimiento**\n"
        "• **Fecha de expedición** del documento\n"
        "• **Ciudad de nacimiento** y **ciudad de expedición**\n\n"
        "Aceptamos **cédula de ciudadanía (C.C.)** y **cédula de extranjería "
        "(C.E.)**. Debes ser **mayor de 18 años** y solo puedes tener **una "
        "cuenta**.\n\n"
        "Al terminar te llega un **correo de activación**. Si no aparece, revisa "
        "que hayas escrito bien tu correo y mira en la carpeta de spam.\n\n"
        "Abajo tienes el acceso directo para crear tu cuenta. 👇"
    ),
    # fuente: knowledge/premios.md
    ACCION_PREMIOS: (
        "Los premios se cobran **presencialmente** en un punto de venta "
        "Facilísimo. Lleva:\n\n"
        "• El **tiquete impreso** que te llegó al correo\n"
        "• Tu **documento de identidad**, original y copia\n\n"
        "Si el premio es de **millones**, además necesitas **RUT** y "
        "**certificado bancario**, ambos expedidos dentro del último mes.\n\n"
        "Tienes **un año** desde el sorteo para reclamarlo, y el premio solo se "
        "entrega al jugador, nunca a un tercero."
    ),
    # fuente: knowledge/saldo-y-recargas.md
    ACCION_RECARGAR: (
        "Para recargar saldo entra al apartado de **saldo** y pulsa el **+**. "
        "Puedes pagar con **PSE** o **tarjeta**.\n\n"
        "• Mínimo **$5.000**, máximo **$300.000**\n"
        "• Recargar **no tiene ningún costo**: lo que recargas es lo que sube a "
        "tu saldo\n"
        "• Con tarjeta se refleja en 1-2 minutos; por PSE puede tardar hasta 15"
    ),
    # fuente: knowledge/pagos-y-transacciones.md + transacciones-declinadas.md
    ACCION_PROBLEMA_COMPRA: (
        "Depende del caso:\n\n"
        "• **Te declinaron la compra:** el dinero vuelve solo a tu saldo, no se "
        "pierde. Revisa tu saldo en la plataforma.\n"
        "• **Pagaste y no aparece:** búscala primero en tu historial de compras.\n"
        "• **Te cobraron dos veces**, o **el saldo no volvió**: radica una PQRS "
        "para que el caso quede registrado y con seguimiento.\n\n"
        "Cuéntame cuál es tu caso y te ayudo."
    ),
    # fuente: knowledge/pqrs-y-contacto.md
    ACCION_CONTACTO: (
        "Puedes escribirnos por:\n\n"
        "• **WhatsApp / teléfono:** 311 384 3703\n"
        "• **Correo:** servicioalcliente@facilisimo.co\n\n"
        "**Horario:** lunes a viernes de 7:30 a.m. a 12:30 m.d. y de 1:30 p.m. a "
        "5:00 p.m.; sábados de 8:00 a.m. a 12:00 m.d.\n\n"
        "Si quieres que tu caso quede **registrado y con seguimiento**, radica "
        "una **PQRS** desde el apartado de PQRS de la página. Responden en "
        "máximo 15 días hábiles."
    ),
}

# --- Navegación: llevar al usuario a un módulo de la página ---------------
# Distinto de `sugerencia_accion`: aquella devuelve al usuario a /chat, esta
# le pide al front que lo MUEVA a otra pantalla de la web.
#
# Se manda un identificador de módulo, NO una URL: las rutas reales las
# conoce el front, no este servicio. Inventar una URL aquí sería adivinar.
#
# Registro único de destinos. Solo entran secciones que sabemos que existen
# (están descritas en `app/knowledge/*.md`); no se inventan pantallas.
#
# DECISIÓN DE DISEÑO: el asistente **no consulta datos privados** del usuario
# (saldo, historial, puntos). No recibe ni maneja el token de sesión. Cuando le
# preguntan por algo suyo, lo lleva a la pantalla donde ese dato ya está, y la
# muestra el front, que es quien tiene la sesión. Menos superficie de riesgo y
# nada de credenciales pasando por este servicio.
_DESTINOS: dict[str, str] = {
    # Pantallas de gestión de la cuenta.
    "registro": "Crear mi cuenta",
    "ingreso": "Iniciar sesión",
    "pqrs": "Ir a radicar mi PQRS",
    "saldo": "Ir a mi saldo",
    "resultados": "Ver resultados",
    "historial": "Ver mi historial de compras",
    "perfil": "Ir a mi perfil",
    # Pantallas de compra. Se ofrecen cuando el usuario pregunta por un
    # producto en el que NO está: de nada sirve explicarle cómo jugar Baloto
    # si además tiene que buscar dónde se juega.
    "chance": "Ir a jugar Chance",
    "astro": "Ir a jugar Astro",
    "baloto": "Ir a jugar Baloto",
    "miloto": "Ir a jugar MiLoto",
    "loteria": "Ir a comprar Lotería",
    "chance_millonario": "Ir a Chance Millonario",
    "recargas": "Ir a recargas",
    "paquetes": "Ir a paquetes",
    "recaudos": "Ir a pagar servicios",
}

# Destino que corresponde a cada opción del menú.
_DESTINO_POR_ACCION: dict[str, str] = {
    ACCION_REGISTRO: "registro",
    ACCION_CONTACTO: "pqrs",
    ACCION_RECARGAR: "saldo",
    ACCION_PREMIOS: "resultados",
    ACCION_PROBLEMA_COMPRA: "historial",
    ACCION_VER_SALDO: "saldo",
    ACCION_MIS_COMPRAS: "historial",
}

# Destinos que no tienen sentido sin sesión iniciada: a quien no ha entrado se
# lo manda a iniciar sesión, que es el paso que de verdad le falta.
_REQUIEREN_SESION = {"saldo", "historial", "perfil"}

# --- Comprar exige tener cuenta -------------------------------------------
# El flujo guiado termina dejando una compra lista para confirmar, y eso no se
# puede hacer sin sesión. Se corta ANTES de empezar a preguntar: hacerle
# recorrer seis pasos para toparse con un muro al final sería peor.
MENSAJE_REQUIERE_SESION = (
    "Para hacer tu chance necesitas tener una cuenta e iniciar sesión. 🔐\n\n"
    "Es rápido: si ya tienes cuenta, entra con tu documento y contraseña. Si "
    "todavía no, crear una toma un par de minutos — solo necesitas tu documento "
    "a la mano y ser mayor de 18 años.\n\n"
    "Apenas entres, vuelve y te armo la apuesta paso a paso. 👇"
)


def destinos_de_sesion() -> list[dict[str, str]]:
    """Las dos puertas de entrada, para ofrecerlas juntas.

    Van las dos porque no sabemos cuál necesita el usuario: pedirle que se
    registre a quien ya tiene cuenta es tan molesto como lo contrario.
    """
    return [
        {"modulo": destino, "etiqueta": _DESTINOS[destino]}
        for destino in ("ingreso", "registro")
    ]

# Cuando contesta el MODELO no hay acción, así que el destino se deduce del
# mensaje del usuario. Los patrones son deliberadamente ESTRECHOS: ofrecer un
# botón que lleva a la pantalla equivocada es peor que no ofrecer ninguno.
_PATRONES_DESTINO: tuple[tuple[str, tuple[re.Pattern, ...]], ...] = (
    ("pqrs", (
        # "pqr" sin la S es habitual: la gente lo escribe en singular.
        re.compile(r"\bpqrs?\b"),
        re.compile(r"\b(radic|pon|sub|present)\w*\b.{0,25}\b(queja|reclamo|peticion|pqrs?)\b"),
    )),
    ("registro", (
        re.compile(r"\bcomo me registro\b"),
        re.compile(r"\bregistrarme\b"),
        re.compile(r"\b(crear|abrir|hacer)\b.{0,12}\bcuenta\b"),
    )),
    ("saldo", (
        # Recargarlo…
        re.compile(r"\brecarg\w+\b.{0,15}\b(saldo|cuenta|billetera)\b"),
        re.compile(r"\b(subir|meter|abonar|cargar)\b.{0,10}\bsaldo\b"),
        re.compile(r"\bcomo recargo\b"),
        # …y consultarlo, que es la otra mitad: la pantalla es la misma.
        # Solo "MI saldo": "el saldo" a secas es ambiguo, puede referirse al de
        # un acumulado ("el saldo de premios acumulados") y mandaría al usuario
        # a la pantalla equivocada.
        re.compile(r"\bmi saldo\b"),
        re.compile(r"\bcuanto (saldo|dinero) (tengo|me queda)\b"),
        re.compile(r"\b(ver|veo|consultar|revisar|reviso) (mi |el )?saldo\b"),
    )),
    ("resultados", (
        re.compile(r"\bresultados?\b"),
        re.compile(r"\bnumeros? ganador\w*\b"),
        re.compile(r"\bcomo se si (me )?gane\b"),
    )),
    ("historial", (
        re.compile(r"\bhistorial\b"),
        re.compile(r"\bmis compras\b"),
    )),
    ("perfil", (
        re.compile(r"\bmi perfil\b"),
        re.compile(r"\b(actualizar|cambiar|modificar)\b.{0,20}\b(datos|correo|celular|telefono|perfil)\b"),
    )),
)

# "Recargar" es ambiguo: recargar SALDO es una pantalla, recargar un CELULAR
# es otro producto. Si se nombra el celular, no se ofrece el módulo de saldo.
_RECARGA_DE_CELULAR = re.compile(
    r"\b(celular|movil|minutos|datos|paquete|operador|claro|movistar|tigo|wom|directv)\b"
)


def _destino_por_texto(texto: str) -> str | None:
    plano = _normalizar(texto)
    for destino, patrones in _PATRONES_DESTINO:
        if not any(p.search(plano) for p in patrones):
            continue
        if destino == "saldo" and _RECARGA_DE_CELULAR.search(plano):
            return None
        return destino
    return None


def destino_navegacion(
    accion: str | None,
    texto: str | None = None,
    autenticado: bool = False,
    modulo_actual: str | None = None,
) -> dict[str, str] | None:
    """Módulo al que el front debería poder llevar al usuario, si aplica.

    Primero la acción (intención exacta, viene de un botón o del menú). Si no
    hay acción —porque contestó el modelo— se deduce del mensaje del usuario.
    Devuelve None cuando no hay un destino claro: no ofrecer botón es una
    respuesta válida.

    `modulo_actual` es dónde está parado el usuario (`contexto.modulo`), y sirve
    para no ofrecerle ir a donde ya está.

    La sesión cambia dos cosas:
    - A quien ya entró no se le ofrece crear cuenta.
    - A quien no ha entrado, pedirle "mi saldo" o "mis compras" lo lleva a
      iniciar sesión, que es lo que realmente le falta para verlos.
    """
    modulo = _DESTINO_POR_ACCION.get(accion) if accion else None

    # ¿Preguntó por un producto? Entonces el destino es la pantalla de ese
    # producto — salvo que ya esté en ella, que es el caso de haber pulsado
    # "¿Necesitas ayuda?" dentro del módulo.
    if modulo is None and texto:
        producto = _modulo_ayuda_compra(_normalizar(texto))
        if producto and producto != modulo_actual:
            modulo = producto

    if modulo is None and texto:
        modulo = _destino_por_texto(texto)
    if modulo is None:
        return None

    if autenticado and modulo == "registro":
        return None
    if not autenticado and modulo in _REQUIEREN_SESION:
        modulo = "ingreso"

    return {"modulo": modulo, "etiqueta": _DESTINOS[modulo]}


DESPEDIDA = "¡Con gusto! Si necesitas algo más, aquí estoy. 🍀"


def _normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y sin signos, para comparar de forma estable."""
    plano = unicodedata.normalize("NFD", texto.lower())
    plano = "".join(c for c in plano if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9\s]", " ", plano).strip()


# --- Saludos ------------------------------------------------------------
# Solo mensajes cortos y sin más contenido: "hola" se atiende aquí, pero
# "hola, cuanto paga el directo 3" debe ir al modelo.
_SALUDOS = {
    "hola", "holaa", "buenas", "buenos dias", "buenas tardes", "buenas noches",
    "hey", "ola", "que mas", "buen dia", "hola buenas",
}

_AGRADECIMIENTOS = {
    "gracias", "muchas gracias", "mil gracias", "listo gracias", "ok gracias",
    "vale gracias", "chao", "adios", "hasta luego", "bye",
}

# --- Volver al menú -------------------------------------------------------
# Sin esto la petición cae al modelo, que se inventa un menú DISTINTO del real
# (ya pasó: devolvió otras opciones, con otros emojis y sin numerar) y encima
# cobra tokens. El menú es de la aplicación, no del modelo.
_PEDIDOS_MENU = {
    "menu", "menu principal", "el menu", "ver menu", "ver el menu",
    "volver al menu", "regresar al menu", "mostrar menu", "muestrame el menu",
    "opciones", "ver opciones", "mas opciones", "otras opciones",
    "inicio", "volver al inicio", "volver", "ayuda", "help",
}

# --- Loterías y horarios ------------------------------------------------
_PALABRAS_LOTERIA = ("loteria", "loterias", "sorteo", "sorteos")
_PALABRAS_HORARIO = ("hora", "horas", "horario", "horarios", "cierra", "cierran",
                     "cierre", "alcanzo", "alcanza", "tiempo")

# Frases que preguntan por el listado del día de forma inequívoca.
_PATRONES_LOTERIAS_HOY = (
    re.compile(r"\bque (loterias|sorteos) (juegan|hay|estan)\b"),
    re.compile(r"\b(loterias|sorteos) (de hoy|disponibles|del dia)\b"),
    re.compile(r"\bhorarios? (de|del) (hoy|dia|cierre)\b"),
    re.compile(r"\ba que hora cierran\b"),
    re.compile(r"\bcuales (loterias|sorteos)\b"),
)


def _es_consulta_de_loterias(texto: str) -> bool:
    """Verdadero solo si pregunta por el listado general del día."""
    if any(p.search(texto) for p in _PATRONES_LOTERIAS_HOY):
        return True
    # "loterias hoy" / "horarios hoy" y variantes muy cortas.
    if "hoy" in texto and len(texto.split()) <= 4:
        return any(p in texto for p in _PALABRAS_LOTERIA) or any(
            p in texto for p in _PALABRAS_HORARIO
        )
    return False


# --- Acumulados -----------------------------------------------------------
# Solo se resuelve aquí la pregunta GENÉRICA ("¿cuáles son los acumulados?").
# En cuanto se nombra un producto puntual ("doble play local", "revancha") se
# deja pasar al modelo: mapear el nombre que usa el cliente al nombre exacto
# que trae la fuente es tarea de entender lenguaje, no de comparar texto.
_PALABRAS_PRODUCTO_ACUMULADO = (
    "baloto", "revancha", "doble play", "dobleplay", "millonario",
)


def _es_consulta_de_acumulados_general(texto: str) -> bool:
    if "acumulado" not in texto and "acumulados" not in texto:
        return False
    if any(p in texto for p in _PALABRAS_PRODUCTO_ACUMULADO):
        return False
    return True


# --- "¿Cómo hago/juego X?" -> guion de ayuda_compra, gratis ---------------
# Solo intención de ACCIÓN ("cómo HAGO/JUEGO/COMPRO"), no de información
# ("cómo FUNCIONA" sigue yendo al modelo con la base de conocimiento real).
# Evita dos problemas a la vez: pagar una llamada al modelo para repetir el
# mismo guion siempre, y que el modelo se invente pasos de una pantalla que
# no conocemos (ya pasó: "elige la lotería", "escribe tu número"... nadie
# verificó que la UI real funcione así).
#
# EL ORDEN IMPORTA: se devuelve el primero que coincida. "chance_millonario"
# va antes que "chance" porque "quiero jugar chance millonario" también
# encaja en los patrones de chance, y ahí ganaría el guion equivocado.
_INTENCION = r"\b(como (hago|juego|apuesto|compro|puedo jugar)|quiero (jugar|apostar|comprar)|ayuda(me)?)\b"

_PATRONES_AYUDA_COMPRA: dict[str, tuple[re.Pattern, ...]] = {
    "chance_millonario": (
        re.compile(_INTENCION + r".*\bchance millonario\b"),
        re.compile(r"\bchance millonario\b.*" + _INTENCION),
    ),
    # Chance tiene su propio set de verbos porque en la calle se pide de formas
    # que no encajan en _INTENCION: "hazme un chance", "necesito hacer un
    # chance". Ambas direcciones, igual que los demás juegos.
    # El lookahead negativo evita robarle el turno a "chance_millonario" (ese
    # entra primero en el diccionario, pero solo reconoce los verbos de
    # _INTENCION, que no incluye "hacer/necesito/hazme").
    "chance": (
        re.compile(
            r"\b(como (hago|juego|apuesto|compro|puedo jugar)|"
            r"(quiero|necesito) (jugar|apostar|comprar|hacer)|"
            r"hazme|hagame|ayuda(me)?)\b.*\bchance\b(?!\s+millonario)"
        ),
        re.compile(
            r"\bchance\b(?!\s+millonario).*\b(como (hago|juego|apuesto|compro|puedo jugar)|"
            r"(quiero|necesito) (jugar|apostar|comprar|hacer)|"
            r"hazme|hagame|ayuda(me)?)\b"
        ),
    ),
    "astro": (
        re.compile(r"\bcomo (hago|juego|apuesto|compro|puedo jugar)\b.*\b(super )?astro\b"),
        re.compile(r"\bquiero (jugar|apostar|comprar)( al)? (super )?astro\b"),
        re.compile(r"\bayuda(me)?\b.*\b(super )?astro\b"),
    ),
    "baloto": (
        re.compile(_INTENCION + r".*\bbaloto\b"),
        re.compile(r"\bbaloto\b.*" + _INTENCION),
    ),
    "miloto": (
        re.compile(_INTENCION + r".*\bmi ?loto\b"),
        re.compile(r"\bmi ?loto\b.*" + _INTENCION),
    ),
    # Solo con señales inequívocas de la lotería tradicional. Un "cómo juego la
    # lotería" a secas se deja pasar al modelo: mucha gente le dice "lotería" a
    # cualquier juego de azar, y darle el guion de billetes sería adivinar.
    "loteria": (
        re.compile(_INTENCION + r".*\b(billete|billetes|fraccion|fracciones)\b"),
        re.compile(r"\b(billete|billetes|fraccion|fracciones)\b.*" + _INTENCION),
        re.compile(_INTENCION + r".*\bloteria tradicional\b"),
    ),
    # Recargar el CELULAR y recargar el SALDO de la cuenta son cosas distintas.
    # Por eso se exige que aparezca el celular: "quiero recargar saldo" no debe
    # caer aquí, sino ir al conocimiento de saldo y recargas.
    "recargas": (
        re.compile(
            r"\b(quiero|como|necesito|ayuda(me)?)\b.*\brecarg\w+\b.{0,30}"
            r"\b(celular|movil|numero|linea|minutos)\b"
        ),
        re.compile(
            r"\brecarg\w+\b.{0,30}\b(celular|movil|numero|linea|minutos)\b.*"
            r"\b(quiero|como|necesito|ayuda(me)?)\b"
        ),
    ),
    "paquetes": (
        re.compile(_INTENCION + r".*\bpaquete\b"),
        re.compile(r"\bpaquete\b.*" + _INTENCION),
    ),
    "recaudos": (
        re.compile(
            r"\b(quiero|como|necesito|donde)\b.{0,20}\bpag\w+\b.{0,25}"
            r"\b(factura|recibo|servicio|servicios|recaudo)\b"
        ),
        re.compile(r"\bayuda(me)?\b.*\b(recaudo|factura)\b"),
    ),
}


def _modulo_ayuda_compra(texto: str) -> str | None:
    for modulo, patrones in _PATRONES_AYUDA_COMPRA.items():
        if any(p.search(texto) for p in patrones):
            return modulo
    return None


def producto_pedido(texto: str) -> str | None:
    """Producto sobre el que el usuario pide ayuda para comprar, si alguno.

    Lo usa `/chat` para decidir si arranca un flujo guiado antes de caer en la
    respuesta enlatada. Misma detección que usa `resolver_texto`, expuesta
    aparte para no duplicar los patrones.
    """
    return _modulo_ayuda_compra(_normalizar(texto))


def resolver_accion(
    accion: str, modulo: str | None = None, autenticado: bool = False
) -> str | None:
    """Atiende un botón del front. Intención conocida, cero ambigüedad."""
    if accion == ACCION_MENU:
        return menu_texto(autenticado)
    if accion == ACCION_LOTERIAS_HOY:
        return resumen_loterias()
    if accion == ACCION_ACUMULADOS:
        return resumen_acumulados()
    if accion == ACCION_AYUDA_COMPRA:
        return _AYUDA_COMPRA.get(modulo, _AYUDA_COMPRA_GENERICA)
    if accion in _RESPUESTAS_MENU:
        return _RESPUESTAS_MENU[accion]
    logger.warning("Acción desconocida recibida del front: %s", accion)
    return None


def resolver_texto(texto: str, autenticado: bool = False) -> str | None:
    """Intenta responder sin el modelo. Devuelve None si no está seguro."""
    plano = _normalizar(texto)
    if not plano:
        return None

    if plano in _SALUDOS or plano in _PEDIDOS_MENU:
        return menu_texto(autenticado)

    if plano in _AGRADECIMIENTOS:
        return DESPEDIDA

    if _es_consulta_de_loterias(plano):
        return resumen_loterias()

    if _es_consulta_de_acumulados_general(plano):
        return resumen_acumulados()

    modulo = _modulo_ayuda_compra(plano)
    if modulo:
        return _AYUDA_COMPRA[modulo]

    return None
