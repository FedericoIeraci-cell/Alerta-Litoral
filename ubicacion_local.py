"""Selección aproximada de una referencia pública, sin recibir coordenadas.

El navegador calcula la proximidad. Este módulo valida el resultado y sólo
conserva el identificador público, una distancia entera y el estado de la ayuda.
Importar el módulo no importa Streamlit ni realiza consultas.
"""
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
import secrets
import unicodedata
from urllib.parse import urlencode

VERSION_UBICACION = "2026-10-18.1"
FRONTEND = Path(__file__).resolve().parent / "ubicacion_local_frontend"
APPS = {
    "ala": {"nombre": "Alerta Litoral Agro", "url": "https://alerta-litoral-agro.streamlit.app/", "busqueda_km": 50},
    "zc": {"nombre": "Alerta Zárate–Campana", "url": "https://alerta-zarate-campana.streamlit.app/", "busqueda_km": 15},
}
POLITICA = {"max_accuracy_m": 2000, "search_km": {app: info["busqueda_km"] for app, info in APPS.items()}}
ESTADOS = frozenset({"ok", "other_app", "outside", "imprecise", "ambiguous", "permission_denied",
                     "position_unavailable", "timeout", "unsupported", "insecure", "policy_blocked", "pending", "ready"})
ESTADOS_CON_REFERENCIA = frozenset({"ok", "other_app", "outside"})
_COMPONENTE = None


def identificador_referencia(app, localidad):
    texto = unicodedata.normalize("NFKD", str(localidad)).encode("ascii", "ignore").decode().lower()
    return str(app) + "-" + re.sub(r"[^a-z0-9]+", "-", texto).strip("-")


def _numero_publico(valor, minimo, maximo):
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return False
    try:
        return math.isfinite(valor) and minimo <= valor <= maximo
    except OverflowError:
        return False


def _referencias_validas(filas):
    resultado = []
    vistos = set()
    for fila in filas:
        if not isinstance(fila, dict) or fila.get("app") not in APPS:
            raise ValueError("Catálogo de referencias no válido")
        app, nombre = fila["app"], fila.get("localidad")
        if not isinstance(nombre, str) or not nombre.strip():
            raise ValueError("Referencia sin nombre")
        identidad = identificador_referencia(app, nombre)
        if identidad in vistos or fila.get("id") != identidad:
            raise ValueError("Referencia duplicada o incompatible")
        if not _numero_publico(fila.get("lat"), -90, 90) or not _numero_publico(fila.get("lon"), -180, 180):
            raise ValueError("Coordenada pública no válida")
        vistos.add(identidad)
        resultado.append({"id": identidad, "app": app, "localidad": nombre,
                          "provincia": str(fila.get("provincia") or ""), "lat": fila["lat"], "lon": fila["lon"]})
    return resultado


@lru_cache(maxsize=1)
def catalogo_guardado():
    documento = json.loads((FRONTEND / "catalogo.json").read_text(encoding="utf-8"))
    return tuple(_referencias_validas(documento["referencias"]))


def preparar_catalogo(app, nodos, opciones=None):
    """La red actual utiliza exactamente los puntos disponibles en la interfaz."""
    if app not in APPS:
        raise ValueError("Aplicación desconocida")
    permitidas = set(opciones) if opciones is not None else None
    actuales = [{"id": identificador_referencia(app, n["localidad"]), "app": app,
                 "localidad": n["localidad"], "provincia": n.get("provincia", ""), "lat": n["lat"], "lon": n["lon"]}
                for n in nodos if permitidas is None or n.get("localidad") in permitidas]
    otras = [dict(n) for n in catalogo_guardado() if n["app"] != app]
    return _referencias_validas(actuales + otras)


def estado_inicial(seleccion_previa=False):
    return {"session_id": secrets.token_hex(12), "revision": 0, "seen_event": "", "auto_checked": bool(seleccion_previa),
            "manual": bool(seleccion_previa), "result": None, "link_checked": False}


def registrar_eleccion_manual(estado):
    salida = deepcopy(estado)
    salida.update(revision=estado["revision"] + 1, auto_checked=True, manual=True, result=None)
    return salida


def validar_evento(evento, estado, catalogo, app):
    """Lista cerrada: no acepta coordenadas, URLs ni mensajes libres del cliente."""
    if not isinstance(evento, dict):
        return None
    campos = {"event_id", "session_id", "revision", "status", "reference_id", "distance_km"}
    if set(evento) - campos or not {"event_id", "session_id", "revision", "status"} <= set(evento):
        return None
    if not isinstance(evento["event_id"], str) or not re.fullmatch(r"[a-f0-9]{24,64}", evento["event_id"]):
        return None
    if evento["session_id"] != estado["session_id"]:
        return None
    if type(evento["revision"]) is not int or evento["revision"] != estado["revision"]:
        return None
    status = evento["status"]
    if not isinstance(status, str) or status not in ESTADOS:
        return None
    limpio = {k: evento[k] for k in ("event_id", "session_id", "revision", "status")}
    if status in ESTADOS_CON_REFERENCIA:
        referencia = next((n for n in catalogo if n["id"] == evento.get("reference_id")), None)
        distancia = evento.get("distance_km")
        if referencia is None or type(distancia) is not int or not 0 <= distancia <= 20040:
            return None
        if status == "ok" and referencia["app"] != app:
            return None
        if status == "other_app" and referencia["app"] == app:
            return None
        if status in {"ok", "other_app"} and distancia > APPS[referencia["app"]]["busqueda_km"]:
            return None
        limpio.update(reference_id=referencia["id"], distance_km=distancia)
    elif "reference_id" in evento or "distance_km" in evento:
        return None
    return limpio


def consumir_evento(evento, estado, catalogo, app, opciones):
    """Cada respuesta cambia la selección una vez; una elección manual invalida respuestas tardías."""
    salida = deepcopy(estado)
    valido = validar_evento(evento, estado, catalogo, app)
    if valido is None or valido["event_id"] == estado["seen_event"]:
        return salida, None
    salida.update(seen_event=valido["event_id"], auto_checked=True)
    if valido["status"] in {"ready", "pending"}:
        return salida, None
    salida["result"] = {k: valido[k] for k in ("status", "reference_id", "distance_km") if k in valido}
    if valido["status"] == "ok":
        referencia = next(n for n in catalogo if n["id"] == valido["reference_id"])
        if referencia["localidad"] in opciones:
            salida["manual"] = False
            return salida, referencia["localidad"]
        salida["result"] = None
    return salida, None


def referencia_desde_enlace(valor, catalogo, app, opciones):
    if not isinstance(valor, str):
        return None
    return next((n["localidad"] for n in catalogo if n["id"] == valor and n["app"] == app
                 and n["localidad"] in opciones), None)


def describir_resultado(resultado, catalogo):
    if not isinstance(resultado, dict):
        return None
    mensajes = {
        "permission_denied": "El navegador no autorizó la ubicación. Podés elegir una referencia manualmente o cambiar el permiso del sitio y volver a pulsar el botón.",
        "position_unavailable": "No se pudo obtener una ubicación utilizable. Podés volver a intentarlo o elegir una referencia manualmente.",
        "timeout": "Se agotó el tiempo de búsqueda de ubicación. Tu selección se conserva; podés reintentar o elegir manualmente.",
        "unsupported": "Este navegador no ofrece geolocalización. Usá el selector manual.",
        "insecure": "La ubicación requiere una conexión segura. Abrí la dirección HTTPS de esta app o usá el selector manual.",
        "policy_blocked": "El navegador o la página que contiene esta app bloquea la ubicación. Abrí la app directamente en otra pestaña o usá el selector manual.",
        "imprecise": "La precisión informada no alcanza para elegir una referencia cercana con suficiente confianza. Tu selección se conserva; elegí manualmente.",
        "ambiguous": "La ubicación aproximada puede corresponder a más de una referencia del catálogo. Tu selección se conserva; elegí la que mejor represente tu consulta.",
    }
    status = resultado.get("status")
    if status in mensajes:
        return {"texto": mensajes[status], "tipo": "info"}
    referencia = next((n for n in catalogo if n["id"] == resultado.get("reference_id")), None)
    if referencia is None or status not in ESTADOS_CON_REFERENCIA:
        return None
    distancia = ("menos de 1 km" if resultado["distance_km"] == 0 else f"unos {resultado['distance_km']} km") + " en línea recta"
    nombre = referencia["localidad"]
    limite = "Es una referencia del modelo, no una medición en tu calle, isla o lote; no confirma acceso ni cobertura exacta."
    if status == "ok":
        return {"tipo": "caption", "texto": f"Referencia cercana: {nombre}, a {distancia} según la ubicación estimada. " + limite}
    if status == "other_app":
        destino = APPS[referencia["app"]]
        return {"tipo": "info", "texto": f"La referencia pública más cercana es {nombre}, a {distancia}, en {destino['nombre']}. "
                "Tu selección actual se conserva. " + limite,
                "url": destino["url"] + "?" + urlencode({"referencia": referencia["id"]}),
                "enlace": "Ver " + nombre + " en " + destino["nombre"]}
    return {"tipo": "info", "texto": f"Según la ubicación estimada, la referencia más cercana del catálogo es {nombre}, a {distancia}. "
            "Está demasiado alejada para elegirla automáticamente como referencia local. Podés consultar el catálogo manualmente; no se confirma cobertura de tu ubicación."}


def _componente():
    global _COMPONENTE
    if _COMPONENTE is None:
        from streamlit.components.v1 import declare_component
        _COMPONENTE = declare_component("referencia_cercana", path=str(FRONTEND))
    return _COMPONENTE


def seleccionar_localidad(st, *, app, nodos, opciones, destacada=None, key="localidad_detalle"):
    """Selector manual permanente y ayuda geográfica sin alterar el modelo."""
    opciones = list(opciones)
    if not opciones:
        return None
    if st.session_state.get(key) not in opciones:
        st.session_state.pop(key, None)
    meta_key = "_ubicacion_" + app + "_" + key
    if meta_key not in st.session_state:
        st.session_state[meta_key] = estado_inicial(key in st.session_state)
    estado = dict(st.session_state[meta_key])
    catalogo = []
    fallo = False
    try:
        catalogo = preparar_catalogo(app, nodos, opciones)
    except (KeyError, TypeError, ValueError, OSError):
        fallo = True
    if not estado["link_checked"]:
        enlazada = referencia_desde_enlace(st.query_params.get("referencia"), catalogo, app, opciones)
        estado["link_checked"] = True
        if enlazada is not None:
            st.session_state[key] = enlazada
            estado = registrar_eleccion_manual(estado)
        st.session_state[meta_key] = estado

    manual, ubicacion = st.columns([3, 2], vertical_alignment="bottom")
    with ubicacion:
        if not fallo:
            try:
                evento = _componente()(catalog=catalogo, app=app, policy=POLITICA,
                    session_id=estado["session_id"], revision=estado["revision"],
                    auto_allowed=not estado["auto_checked"] and not estado["manual"],
                    catalog_version=hashlib.sha256(json.dumps(catalogo, sort_keys=True).encode()).hexdigest()[:12],
                    default=None, key=meta_key + "_componente")
                estado, elegida = consumir_evento(evento, estado, catalogo, app, opciones)
                st.session_state[meta_key] = estado
                if elegida is not None:
                    st.session_state[key] = elegida
            except Exception:
                fallo = True
        if fallo:
            st.caption("La ayuda de ubicación no está disponible. Elegí una referencia manualmente.")

    def cambio_manual():
        st.session_state[meta_key] = registrar_eleccion_manual(st.session_state[meta_key])

    if key not in st.session_state:
        st.session_state[key] = destacada if destacada in opciones else opciones[0]
    with manual:
        seleccion = st.selectbox("Seleccionar localidad", opciones,
            key=key, on_change=cambio_manual,
            help="Elegí una referencia conocida. Su información no acredita el estado de cada calle, isla o lote.")
    aviso = describir_resultado(st.session_state[meta_key].get("result"), catalogo)
    if aviso:
        getattr(st, aviso["tipo"])(aviso["texto"])
        if aviso.get("url"):
            st.link_button(aviso["enlace"], aviso["url"])
    return seleccion
