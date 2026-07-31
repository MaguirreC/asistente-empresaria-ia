"""Resultados (números ganadores) de las loterías y sorteos, por fecha.

Fuente: el sitio público de resultados (resultados.facilisimo.co), el mismo
dominio de donde salen los acumulados. Una sola llamada trae TODOS los sorteos
de una fecha.

Toda la búsqueda se resuelve aquí, en código, y al modelo solo se le entrega el
resultado ya encontrado y formateado. Es la regla de siempre del proyecto: si
se puede calcular sin margen de error, no se le deja al modelo — y aquí hay
fechas, días de la semana y números de premio, que es justo donde ya se
equivocó antes.

## Cómo busca

Retrocede día a día desde la fecha pedida (por defecto hoy) hasta encontrar la
lotería, con un tope de `DIAS_HACIA_ATRAS`. Una sola regla cubre los dos casos:

- Las de sorteo **diario** (Chontico, Sinuano, Dorado...) aparecen hoy; si aún
  no sortearon, aparece la de ayer.
- Las **principales** (tradicionales, una vez por semana) aparecen al retroceder
  hasta su día: por eso el tope es de 8 días y no de 2.

Si tras ese recorrido no aparece, se dice que no se encontró. Nunca se inventa
un número ganador.
"""
import logging
import re
import time
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

BOGOTA = ZoneInfo("America/Bogota")

# Medido: una fecha con resultados responde en ~0,4 s. En cambio una fecha
# todavía sin resultados (el día en curso, antes de que empiecen los sorteos)
# NO responde 404: deja la conexión colgada hasta que corta el timeout. Por eso
# el margen es corto — esperar más no traería nada, solo haría esperar al
# usuario.
TIMEOUT_SEGUNDOS = 8.0

# 8 días: cubre una semana completa, que es lo que necesita una lotería
# tradicional (sortea un solo día a la semana), más un día de margen.
DIAS_HACIA_ATRAS = 8

# Los resultados de un día pasado ya no cambian, así que se guardan sin
# vencimiento. Los de hoy sí cambian a medida que van sorteando.
CACHE_TTL_HOY_SEGUNDOS = 600  # 10 minutos

# Cuánto se recuerda que una fecha falló. Sin esto, mientras el día en curso no
# tenga resultados, CADA consulta volvería a esperar el timeout completo antes
# de pasar al día anterior.
CACHE_TTL_FALLO_SEGUNDOS = 300  # 5 minutos

# fecha (dd-mm-yyyy) -> (momento en que se guardó, sorteos)
_cache: dict[str, tuple[float, list[dict]]] = {}

# fecha (dd-mm-yyyy) -> momento en que falló
_fallos: dict[str, float] = {}

_NOMBRES_DIA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

# Palabras que el usuario escribe pero que no distinguen una lotería de otra.
# "lotería" tiene que estar aquí: si no, "la lotería del chontico" no encontraría
# "CHONTICO DIA", que no lleva esa palabra en el nombre.
_PALABRAS_VACIAS = {
    "loteria", "loterias", "sorteo", "sorteos", "resultado", "resultados",
    "del", "de", "la", "el", "los", "las", "un", "una", "y", "que",
}


class _SinDatos(Exception):
    """No se pudo consultar la fuente de resultados."""


def _normalizar(texto: str) -> str:
    """Minúsculas y sin tildes. La ñ de 'Antioqueñita' queda como n, y el
    usuario que escribe con o sin tilde encuentra lo mismo."""
    plano = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in plano if unicodedata.category(c) != "Mn")


def _palabras(texto: str) -> list[str]:
    return [
        p for p in re.findall(r"[a-z0-9]+", _normalizar(texto))
        if p not in _PALABRAS_VACIAS
    ]


def _coincide(consulta: str, nombre_loteria: str) -> bool:
    """Si lo que escribió el usuario identifica a esa lotería.

    Cada palabra buscada debe aparecer en el nombre. Se acepta que una sea
    prefijo de la otra (con 4 letras o más) porque la fuente trae nombres
    recortados: "EXTRA DE COLOM" tiene que encontrarse con "extra de colombia".
    """
    buscadas = _palabras(consulta)
    if not buscadas:
        return False
    del_nombre = _palabras(nombre_loteria)

    for palabra in buscadas:
        if any(
            palabra == p
            or (len(palabra) >= 4 and p.startswith(palabra))
            or (len(p) >= 4 and palabra.startswith(p))
            for p in del_nombre
        ):
            continue
        return False
    return True


def _consultar(fecha: str) -> list[dict]:
    """Todos los sorteos de esa fecha (formato dd-mm-yyyy)."""
    respuesta = httpx.get(
        settings.resultados_sorteos_url,
        params={"fecha": fecha},
        timeout=TIMEOUT_SEGUNDOS,
    )
    respuesta.raise_for_status()
    datos = respuesta.json()

    if str(datos.get("estado")).lower() != "true":
        raise ValueError(f"El sitio de resultados respondió estado={datos.get('estado')}")

    return datos.get("sorteos") or []


def _sorteos_de(fecha: str, es_hoy: bool) -> list[dict]:
    """Sorteos de esa fecha, desde el caché cuando se puede."""
    guardado = _cache.get(fecha)
    if guardado is not None:
        vigente = not es_hoy or (time.monotonic() - guardado[0]) < CACHE_TTL_HOY_SEGUNDOS
        if vigente:
            return guardado[1]

    # Si esta fecha acaba de fallar, no se vuelve a intentar: se ahorra el
    # timeout completo y se pasa derecho al día anterior.
    fallo = _fallos.get(fecha)
    if fallo is not None and (time.monotonic() - fallo) < CACHE_TTL_FALLO_SEGUNDOS:
        if guardado is not None:
            return guardado[1]
        raise _SinDatos(f"La fecha {fecha} falló hace poco")

    try:
        sorteos = _consultar(fecha)
    except (httpx.HTTPError, ValueError) as e:
        logger.error("Fallo consultando resultados del %s: %s", fecha, e)
        _fallos[fecha] = time.monotonic()
        if guardado is not None:
            return guardado[1]
        raise _SinDatos from e

    _fallos.pop(fecha, None)
    _cache[fecha] = (time.monotonic(), sorteos)
    return sorteos


def _premio(sorteo: dict) -> str:
    """Una línea con el resultado, ya formateada.

    El campo `serie` no significa lo mismo en todos los juegos: en Astro es el
    signo zodiacal, en los sorteos que no manejan serie llega como "*". Se
    resuelve aquí para que el modelo no tenga que interpretarlo.
    """
    nombre = sorteo.get("nombre_loteria") or "sin nombre"
    numero = sorteo.get("numero_ganador") or "no informado"
    serie = (sorteo.get("serie") or "").strip()

    if "astro" in _normalizar(nombre):
        extra = f" — signo: {serie}" if serie and serie != "*" else ""
    elif serie and serie != "*":
        extra = f" — serie: {serie}"
    else:
        extra = ""
    return f"- {nombre}: {numero}{extra}"


def _fecha_legible(fecha_dt: datetime, hoy: datetime) -> str:
    """'jueves 30/07/2026 (hoy)'. El día de la semana se calcula en código:
    el modelo ya se equivocó antes deduciéndolo de la fecha numérica."""
    dias = (hoy.date() - fecha_dt.date()).days
    if dias == 0:
        cuando = " (hoy)"
    elif dias == 1:
        cuando = " (ayer)"
    else:
        cuando = ""
    return f"{_NOMBRES_DIA[fecha_dt.weekday()]} {fecha_dt.strftime('%d/%m/%Y')}{cuando}"


def _parsear_fecha(texto: str | None, hoy: datetime) -> datetime | None:
    """Acepta dd-mm-yyyy, dd/mm/yyyy o yyyy-mm-dd. None si no se entiende."""
    if not texto:
        return hoy
    limpio = texto.strip().replace("/", "-")
    for formato in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(limpio, formato).replace(tzinfo=BOGOTA)
        except ValueError:
            continue
    return None


def resultados_loteria(loteria: str | None = None, fecha: str | None = None) -> str:
    """Resultado de una lotería, o todos los de una fecha si no se nombra ninguna.

    `fecha` es opcional; si no viene, se busca desde hoy hacia atrás.
    """
    hoy = datetime.now(BOGOTA)
    inicio = _parsear_fecha(fecha, hoy)
    if inicio is None:
        return (
            f"No entendí la fecha '{fecha}'. Pídele al usuario que la diga como "
            f"día/mes/año."
        )

    # Sin lotería concreta: los resultados de esa fecha, tal cual.
    if not loteria or not _palabras(loteria):
        try:
            sorteos = _sorteos_de(inicio.strftime("%d-%m-%Y"), inicio.date() == hoy.date())
        except _SinDatos:
            return (
                "No se pudieron consultar los resultados en este momento. Dile al "
                "usuario que los revise en el apartado de Resultados de la página."
            )
        if not sorteos:
            return f"No hay resultados publicados para el {_fecha_legible(inicio, hoy)}."
        return (
            f"Resultados del {_fecha_legible(inicio, hoy)} "
            f"({len(sorteos)} sorteos). Ya están completos y verificados: "
            f"repítelos tal cual, no cambies ningún número.\n"
            + "\n".join(_premio(s) for s in sorteos)
        )

    # Con lotería: se retrocede hasta encontrarla.
    # Se cuentan los días que SÍ se pudieron consultar: que falle uno (el día
    # en curso suele no tener resultados todavía) no debe hacernos decir "no
    # se pudo consultar" cuando en realidad revisamos la semana entera y esa
    # lotería no aparece. Son dos respuestas muy distintas para el usuario.
    dias_consultados = 0
    for atras in range(DIAS_HACIA_ATRAS):
        dia = inicio - timedelta(days=atras)
        try:
            sorteos = _sorteos_de(dia.strftime("%d-%m-%Y"), dia.date() == hoy.date())
        except _SinDatos:
            continue
        dias_consultados += 1

        encontrados = [s for s in sorteos if _coincide(loteria, s.get("nombre_loteria", ""))]
        if encontrados:
            logger.info(
                "Resultado de %r encontrado en %s (%s días atrás)",
                loteria, dia.strftime("%d-%m-%Y"), atras,
            )
            return (
                f"Último resultado de '{loteria}' — {_fecha_legible(dia, hoy)}.\n"
                f"Este es el sorteo más reciente de esa lotería. El número y la "
                f"fecha ya están verificados: repítelos tal cual, no los cambies "
                f"ni deduzcas el día de la semana por tu cuenta.\n"
                f"OBLIGATORIO: dile al usuario DE QUÉ FECHA es este resultado. Un "
                f"número sin fecha no le sirve, porque no sabe a qué sorteo "
                f"corresponde.\n"
                + "\n".join(_premio(s) for s in encontrados)
            )

    if dias_consultados == 0:
        return (
            "No se pudieron consultar los resultados en este momento. Dile al "
            "usuario que los revise en el apartado de Resultados de la página."
        )

    return (
        f"No se encontró ningún resultado de '{loteria}' en los últimos "
        f"{dias_consultados} días consultados. Puede que el nombre no sea exacto o que esa "
        f"lotería no haya sorteado en ese periodo. NO inventes un número: dile al "
        f"usuario que no encontraste ese resultado y ofrécele consultar el "
        f"apartado de Resultados de la página."
    )
