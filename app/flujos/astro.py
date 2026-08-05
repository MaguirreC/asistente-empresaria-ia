"""Flujo guiado para armar una o varias apuestas de Astro.

Recoge, en este orden:

    sorteo (Sol / Luna / los dos) -> [número -> signo -> valor] x N -> ¿otra?

y termina devolviéndole al front un formulario con todo listo para mapear.

Por qué ese orden: el sorteo aplica a TODAS las apuestas del tiquete (no tiene
sentido preguntarlo de nuevo por cada una), y dentro de cada apuesta el número
y el signo van antes que el valor porque no cambian cuánto se puede apostar
(a diferencia del chance, donde el número sí decide qué paga cada modalidad).

A diferencia del chance, en Astro **siempre son 4 cifras** — no existe una
modalidad de 3 o 2 (`knowledge/astro.md`): los premios de menos cifras son por
coincidencia PARCIAL de ese mismo número de 4, no un tiquete más corto. Por
eso `_interpretar_numero` exige exactamente 4 dígitos.

El usuario puede repetir apuestas (otro número, otro signo, otro valor) sin
volver a elegir el sorteo — así funciona la pantalla real. Como el motor
deduce el paso pendiente de los datos ya recogidos, cada apuesta usa claves
numeradas (`numero_1`, `signo_1`, `valor_1`, `numero_2`, ...) en vez de vivir
en una lista, para que `_siguiente_paso` siga siendo una función pura del
dict — igual que el paso de modalidades en `chance.py`.
"""
import re
from datetime import datetime, time

from app.flujos.motor import (
    Flujo,
    FlujoNoDisponible,
    Paso,
    elegir_de,
    formatear_pesos,
    numero_aleatorio,
    pide_aleatorio,
    registrar,
)
from app.tools.loterias import BOGOTA

# Por apuesta (número + signo), no por la suma del tiquete: `astro.md` dice
# "por tiquete", y cada apuesta con su propio número y signo es un tiquete.
VALOR_MINIMO = 500
VALOR_MAXIMO = 10_000

# La tercera etiqueta NO repite "Sol" ni "Luna": si lo hiciera, escribir
# "luna" quedaría ambiguo entre esta opción y "Astro Luna" (las dos
# contendrían la palabra), y `elegir_de` prefiere no adivinar antes que
# arriesgar la opción equivocada.
_SORTEOS: tuple[tuple[str, str], ...] = (
    ("Astro Sol — sorteo del día", "sol"),
    ("Astro Luna — sorteo de la noche", "luna"),
    ("Los dos sorteos", "ambos"),
)

_SOLO_SOL = (("Astro Sol — sorteo del día", "sol"),)
_SOLO_LUNA = (("Astro Luna — sorteo de la noche", "luna"),)

# Horarios oficiales de venta (sorteo, no cierre): Sol lunes a sábado 4:00
# p.m.; Luna lunes a viernes 10:50 p.m., sábado 10:42 p.m., domingo/festivo
# 8:30 p.m. Astro Sol NO juega domingos ni festivos.
#
# El cierre de venta es 10 minutos antes del sorteo — igual para los dos, no
# solo para Sol. Se calcula en código, nunca se le deja al modelo decidir si
# un sorteo sigue abierto.
#
# LIMITACIÓN CONOCIDA: el proyecto no tiene calendario de festivos
# colombianos. Se distingue domingo (`weekday() == 6`), pero un festivo entre
# semana hoy se trata como día normal — Sol se seguiría ofreciendo cuando en
# realidad ese día no juega. Pendiente si hace falta un calendario de
# festivos real.
_CIERRE_SOL = time(15, 50)


def _domingo(momento: datetime) -> bool:
    return momento.weekday() == 6


def _sol_disponible(momento: datetime) -> bool:
    return not _domingo(momento) and momento.time() < _CIERRE_SOL


def _cierre_luna(momento: datetime) -> time:
    if momento.weekday() == 5:  # sábado
        return time(22, 32)
    if _domingo(momento):
        return time(20, 20)
    return time(22, 40)  # lunes a viernes


def _luna_disponible(momento: datetime) -> bool:
    return momento.time() < _cierre_luna(momento)


def _sorteos_disponibles() -> tuple[tuple[str, str], ...]:
    ahora = datetime.now(BOGOTA)
    sol, luna = _sol_disponible(ahora), _luna_disponible(ahora)
    if sol and luna:
        return _SORTEOS
    if luna:
        return _SOLO_LUNA
    if sol:
        return _SOLO_SOL
    return ()


_SIGNOS: tuple[tuple[str, str], ...] = (
    ("Aries", "aries"),
    ("Tauro", "tauro"),
    ("Géminis", "geminis"),
    ("Cáncer", "cancer"),
    ("Leo", "leo"),
    ("Virgo", "virgo"),
    ("Libra", "libra"),
    ("Escorpión", "escorpion"),
    ("Sagitario", "sagitario"),
    ("Capricornio", "capricornio"),
    ("Acuario", "acuario"),
    ("Piscis", "piscis"),
)

# Se ofrece como una opción más de la lista, no como una palabra suelta a
# reconocer: así entra gratis al mecanismo de `elegir_de` (número, nombre, o
# un trozo de él) sin necesitar una regex aparte.
_SIGNOS_CON_TODOS = _SIGNOS + (("Todos los signos", "todos"),)


# --- Paso 1: el sorteo ------------------------------------------------------


def _aviso_sorteo(sorteos: tuple[tuple[str, str], ...]) -> str:
    if sorteos == _SORTEOS:
        return ""
    if sorteos == _SOLO_LUNA:
        if _domingo(datetime.now(BOGOTA)):
            return "Los domingos y festivos solo se juega Astro Luna.\n\n"
        return (
            "Astro Sol ya cerró por hoy (cierra 10 minutos antes de su "
            "sorteo), así que solo puedes jugar Astro Luna.\n\n"
        )
    if sorteos == _SOLO_SOL:  # no debería pasar hoy, pero no se descarta
        return "Astro Luna ya cerró por hoy, así que solo puedes jugar Astro Sol.\n\n"
    return ""


def _paso_sorteo() -> Paso:
    sorteos = _sorteos_disponibles()
    if not sorteos:
        raise FlujoNoDisponible(
            "Ya cerraron los dos sorteos de Astro por hoy. 🙏 Escríbeme de "
            "nuevo mañana, o ármalo directamente en la pantalla de Astro."
        )
    return Paso(
        id="sorteo",
        pregunta=(
            f"Vamos a armar tu Astro. 🔮\n\n{_aviso_sorteo(sorteos)}"
            "¿En qué sorteo quieres jugar?"
        ),
        opciones=tuple(etiqueta for etiqueta, _ in sorteos),
        interpretar=lambda texto: elegir_de(texto, list(sorteos)),
        ayuda="Elige Sol, Luna, o los dos. Responde con el número o el nombre.",
    )


# --- Por apuesta: número, signo y valor ------------------------------------

_SOLO_DIGITOS = re.compile(r"\D")


def _interpretar_numero(texto: str) -> str | None:
    digitos = _SOLO_DIGITOS.sub("", texto)
    if len(digitos) == 4:
        return digitos
    if texto.strip() == "1" or pide_aleatorio(texto):
        return numero_aleatorio(4)
    return None


def _paso_numero(indice: int) -> Paso:
    prefijo = "¿A qué número quieres jugar?" if indice == 1 else (
        f"Vamos con la apuesta #{indice}. ¿A qué número quieres jugar esta vez?"
    )
    return Paso(
        id=f"numero_{indice}",
        pregunta=(
            f"{prefijo}\n\n"
            "Siempre son **4 cifras** (0000 a 9999) — en Astro no existe una "
            "modalidad de 3 o 2."
        ),
        opciones=("Elegir un número al azar 🎲",),
        interpretar=_interpretar_numero,
        ayuda="Necesito un número de 4 cifras exactas. Por ejemplo: 1234. También puedes pedirme uno al azar.",
    )


def _paso_signo(indice: int) -> Paso:
    return Paso(
        id=f"signo_{indice}",
        pregunta="¿Con qué signo zodiacal juegas ese número?",
        opciones=tuple(etiqueta for etiqueta, _ in _SIGNOS_CON_TODOS),
        interpretar=lambda texto: elegir_de(texto, list(_SIGNOS_CON_TODOS)),
        ayuda=(
            "No identifiqué ese signo. Elige uno de la lista, responde con su "
            "número, o dime `todos` para jugar los 12."
        ),
    )


def _interpretar_valor(texto: str):
    limpio = re.sub(r"[^\d]", "", texto)
    if not limpio:
        return None
    valor = int(limpio)
    if VALOR_MINIMO <= valor <= VALOR_MAXIMO:
        return valor
    return None


def _paso_valor(indice: int, numero: str, signo: str) -> Paso:
    etiqueta_signo = next(e for e, v in _SIGNOS_CON_TODOS if v == signo)
    return Paso(
        id=f"valor_{indice}",
        pregunta=(
            f"¿Cuánto quieres apostarle al **{numero}** con **{etiqueta_signo}**?\n\n"
            f"Entre {formatear_pesos(VALOR_MINIMO)} y {formatear_pesos(VALOR_MAXIMO)} "
            "por tiquete."
        ),
        interpretar=_interpretar_valor,
        ayuda=(
            f"Dime un valor entre {formatear_pesos(VALOR_MINIMO)} y "
            f"{formatear_pesos(VALOR_MAXIMO)}. Por ejemplo: 1000."
        ),
    )


def _paso_continuar(indice: int) -> Paso:
    pares = [("Sí, otra apuesta", True), ("No, ya terminé", False)]
    return Paso(
        id=f"continuar_{indice}",
        pregunta=(
            "¿Quieres agregar otra apuesta — otro número, otro signo y otro "
            "valor — en el mismo sorteo?"
        ),
        opciones=tuple(etiqueta for etiqueta, _ in pares),
        interpretar=lambda texto: elegir_de(texto, pares),
        ayuda="Responde sí o no, o con el número de la opción.",
    )


# --- Ensamblado del flujo ---------------------------------------------------


def _siguiente_paso(datos: dict) -> Paso | None:
    if "sorteo" not in datos:
        return _paso_sorteo()

    indice = 1
    while True:
        if f"numero_{indice}" not in datos:
            return _paso_numero(indice)
        if f"signo_{indice}" not in datos:
            return _paso_signo(indice)
        if f"valor_{indice}" not in datos:
            return _paso_valor(indice, datos[f"numero_{indice}"], datos[f"signo_{indice}"])
        if f"continuar_{indice}" not in datos:
            return _paso_continuar(indice)
        if not datos[f"continuar_{indice}"]:
            return None
        indice += 1


def _apuestas(datos: dict) -> list[dict]:
    apuestas = []
    indice = 1
    while f"numero_{indice}" in datos:
        apuestas.append(
            {
                "numero": datos[f"numero_{indice}"],
                "signo": datos[f"signo_{indice}"],
                "valor": datos[f"valor_{indice}"],
            }
        )
        indice += 1
    return apuestas


def _formulario(datos: dict) -> dict:
    return {
        "producto": "astro",
        "sorteo": datos["sorteo"],
        "apuestas": _apuestas(datos),
    }


def _etiqueta_signo(clave: str) -> str:
    return next(e for e, v in _SIGNOS_CON_TODOS if v == clave)


def _etiqueta_sorteo(clave: str) -> str:
    return next(e for e, v in _SORTEOS if v == clave)


def _resumen(datos: dict) -> str:
    apuestas = _apuestas(datos)
    detalle = "\n".join(
        f"• **{a['numero']}** con **{_etiqueta_signo(a['signo'])}** — "
        f"{formatear_pesos(a['valor'])}"
        for a in apuestas
    )
    total = sum(a["valor"] for a in apuestas)
    return (
        "¡Listo! Así queda tu Astro:\n\n"
        f"• **Sorteo:** {_etiqueta_sorteo(datos['sorteo'])}\n"
        f"{detalle}\n"
        f"• **Total:** {formatear_pesos(total)}\n\n"
        "Te lo dejo cargado en la pantalla de Astro para que lo revises y "
        "confirmes la compra. 👇"
    )


registrar(
    Flujo(
        producto="astro",
        siguiente_paso=_siguiente_paso,
        formulario=_formulario,
        resumen=_resumen,
    )
)
