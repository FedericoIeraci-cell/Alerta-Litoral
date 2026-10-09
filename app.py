# ============================================================
# 🌧️ ALERTA LITORAL AGRO
# ============================================================
# Sistema experimental de alerta temprana para riesgo de
# anegamiento agropecuario.
#
# Regiones:
#   Santa Fe
#   Corrientes
#   Entre Ríos
#
# Fuentes:
#   Open-Meteo / ECMWF IFS HRES 9 km
#   SMN — observaciones meteorológicas en directo
#   NOAA GOES-19 ABI (vigilancia satelital)
#
# Integra INA, INTA, ENOS, relieve y planificación territorial.
#
# Versión: V4.0.0
# Requisitos: streamlit>=1.40, requests>=2.31, pandas>=2.0,
# numpy>=1.24, plotly>=6.0 (Pillow se instala con Streamlit).
# Opcional: SMN_API_TOKEN en Secrets, únicamente una credencial
# autorizada por SMN si el acceso público temporal no está disponible.
# Nunca pegar tokens en el código ni guardarlos en GitHub.
# ============================================================

import hashlib
import html
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
import base64
import zlib
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from PIL import Image
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse, unquote
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
import streamlit.components.v1 as components


# ============================================================
# CONFIGURACIÓN
# ============================================================

st.set_page_config(
    page_title="Alerta Litoral Agro",
    page_icon="🌧️",
    layout="wide",
    initial_sidebar_state="expanded",
)

APP_NAME = "Alerta Litoral Agro"
VERSION = "V4.0.0"
MODELO = "ECMWF IFS HRES 9 km"
API_URL = "https://api.open-meteo.com/v1/ecmwf"
TZ = ZoneInfo("America/Argentina/Buenos_Aires")


# ============================================================
# SMN — OBSERVACIONES EN VIVO
# ============================================================
# El sitio público del SMN entrega un token temporal que su propia
# interfaz utiliza para consultar el servicio ws1.smn.gob.ar.
# No se guarda ningún token en el repositorio.

SMN_WEB_URL = "https://www.smn.gob.ar/"
SMN_API_BASE = "https://ws1.smn.gob.ar/v1"
SMN_WEATHER_ZOOM_URL = f"{SMN_API_BASE}/weather/location/zoom/2"
SMN_ALERT_AREA_URL = f"{SMN_API_BASE}/warning/alert/area"


@st.cache_data(ttl=1800, show_spinner=False)
def _pagina_publica_smn(url):
    try:
        response = requests.get(url, headers={"User-Agent": "Alerta-Litoral-Agro/3.10.2",
                               "Accept": "text/html"}, timeout=(4, 8))
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException:
        return ""


@st.cache_data(ttl=1800, show_spinner=False)
def obtener_token_smn():
    """Lee acceso público publicado por el SMN o una credencial autorizada en Secrets."""
    try:
        configurado = str(st.secrets.get("SMN_API_TOKEN", "")).strip()
        if configurado:
            return configurado
    except Exception:
        pass
    for url in (SMN_WEB_URL, "https://www.smn.gob.ar/radar/", "https://ws2.smn.gob.ar/radar/"):
        texto = _pagina_publica_smn(url)
        match = re.search(r"localStorage\.setItem\(\s*['\"]token['\"]\s*,\s*['\"]([^'\"]+)['\"]", texto, re.I)
        if match:
            return match.group(1).strip()
    return ""


def headers_smn(token):
    return {
        "Authorization": f"JWT {token}",
        "User-Agent": "Mozilla/5.0 (Alerta-Litoral-Agro)",
        "Accept": "application/json",
        "Referer": SMN_WEB_URL,
        "Origin": "https://www.smn.gob.ar",
    }


def _recorrer_objetos(obj):
    """Recorre respuestas JSON del SMN aunque cambie su anidamiento."""
    if isinstance(obj, dict):
        yield obj
        for valor in obj.values():
            yield from _recorrer_objetos(valor)
    elif isinstance(obj, list):
        for valor in obj:
            yield from _recorrer_objetos(valor)


def _coord_de_objeto(obj):
    if not isinstance(obj, dict):
        return None

    candidatos = [obj.get("coord"), obj.get("coordinates")]
    location = obj.get("location")
    if isinstance(location, dict):
        candidatos.extend([location.get("coord"), location.get("coordinates")])

    for c in candidatos:
        if isinstance(c, dict):
            lat = c.get("lat", c.get("latitude"))
            lon = c.get("lon", c.get("longitude"))
            if lat is not None and lon is not None:
                try:
                    return float(lat), float(lon)
                except Exception:
                    pass
        elif isinstance(c, (list, tuple)) and len(c) >= 2:
            try:
                return float(c[1]), float(c[0])
            except Exception:
                pass

    lat = obj.get("lat", obj.get("latitude"))
    lon = obj.get("lon", obj.get("longitude"))
    if lat is not None and lon is not None:
        try:
            return float(lat), float(lon)
        except Exception:
            pass

    return None


def _nombre_de_objeto(obj):
    if not isinstance(obj, dict):
        return ""
    location = obj.get("location")
    if isinstance(location, dict):
        return str(location.get("name") or location.get("locality") or "")
    return str(obj.get("name") or obj.get("locality") or "")


def _id_de_objeto(obj):
    if not isinstance(obj, dict):
        return None
    location = obj.get("location")
    if isinstance(location, dict):
        for k in ("id", "location_id", "station_id"):
            if location.get(k) is not None:
                return location.get(k)
    for k in ("id", "location_id", "station_id"):
        if obj.get(k) is not None:
            return obj.get(k)
    return None


@st.cache_data(ttl=300, show_spinner=False)
def obtener_observaciones_smn():
    """Obtiene observaciones actuales del SMN y las normaliza."""
    token = obtener_token_smn()
    if not token:
        return pd.DataFrame(), "No se pudo obtener el token temporal del SMN."

    try:
        r = requests.get(
            SMN_WEATHER_ZOOM_URL,
            headers=headers_smn(token),
            timeout=30,
        )

        if r.status_code == 401:
            _pagina_publica_smn.clear()
            obtener_token_smn.clear()
            token = obtener_token_smn()
            if not token:
                return pd.DataFrame(), "El token del SMN expiró o no pudo renovarse."
            r = requests.get(
                SMN_WEATHER_ZOOM_URL,
                headers=headers_smn(token),
                timeout=30,
            )

        r.raise_for_status()
        payload = r.json()
        registros = []

        for obj in _recorrer_objetos(payload):
            coord = _coord_de_objeto(obj)
            if coord is None:
                continue

            # Solo tomamos objetos que realmente parezcan observaciones.
            tiene_dato = any(
                k in obj
                for k in (
                    "temperature", "humidity", "pressure", "visibility", "wind",
                )
            )
            if not tiene_dato:
                continue

            location = obj.get("location") if isinstance(obj.get("location"), dict) else {}
            weather = obj.get("weather") if isinstance(obj.get("weather"), dict) else {}
            wind = obj.get("wind") if isinstance(obj.get("wind"), dict) else {}

            registros.append({
                "smn_id": _id_de_objeto(obj),
                "estacion": _nombre_de_objeto(obj),
                "lat": coord[0],
                "lon": coord[1],
                "fecha_smn": obj.get("date") or location.get("date"),
                "temperatura_smn": obj.get("temperature"),
                "humedad_smn": obj.get("humidity"),
                "presion_smn": obj.get("pressure"),
                "sensacion_smn": obj.get("feels_like"),
                "visibilidad_smn": obj.get("visibility"),
                "viento_smn": wind.get("speed"),
                "direccion_smn": wind.get("direction"),
                "direccion_grados_smn": wind.get("deg"),
                "tiempo_smn": weather.get("description"),
            })

        if not registros:
            return pd.DataFrame(), "El SMN respondió, pero no se encontraron observaciones utilizables."

        df = pd.DataFrame(registros).drop_duplicates(
            subset=["smn_id", "lat", "lon"], keep="first"
        )
        return df, "OK"

    except Exception as e:
        return pd.DataFrame(), f"Error consultando observaciones del SMN: {e}"


def asociar_smn_a_nodos(df_smn):
    """Asocia cada nodo del proyecto con la observación SMN más cercana."""
    if df_smn.empty:
        return pd.DataFrame()

    filas = []
    for nodo in NODOS:
        tmp = df_smn.copy()
        tmp["distancia_km"] = 2 * 6371.0088 * np.arcsin(np.sqrt(np.clip(
            np.sin(np.radians(tmp["lat"] - nodo["lat"]) / 2) ** 2
            + np.cos(np.radians(nodo["lat"])) * np.cos(np.radians(tmp["lat"]))
            * np.sin(np.radians(tmp["lon"] - nodo["lon"]) / 2) ** 2,
            0, 1,
        )))
        mejor = tmp.sort_values("distancia_km").iloc[0].to_dict()
        filas.append({
            "localidad": nodo["localidad"],
            "provincia": nodo["provincia"],
            "estacion_smn": mejor.get("estacion", "Sin nombre"),
            "smn_id": mejor.get("smn_id"),
            "distancia_km": round(float(mejor.get("distancia_km", np.nan)), 1),
            "fecha_smn": mejor.get("fecha_smn"),
            "temperatura_smn": mejor.get("temperatura_smn"),
            "humedad_smn": mejor.get("humedad_smn"),
            "presion_smn": mejor.get("presion_smn"),
            "sensacion_smn": mejor.get("sensacion_smn"),
            "visibilidad_smn": mejor.get("visibilidad_smn"),
            "viento_smn": mejor.get("viento_smn"),
            "direccion_smn": mejor.get("direccion_smn"),
            "direccion_grados_smn": mejor.get("direccion_grados_smn"),
            "tiempo_smn": mejor.get("tiempo_smn"),
        })

    return pd.DataFrame(filas)


@st.cache_data(ttl=300, show_spinner=False)
def obtener_alertas_smn():
    """Obtiene alertas oficiales del SMN, si el servicio las expone."""
    token = obtener_token_smn()
    if not token:
        return None, "No se pudo obtener el token del SMN."

    try:
        r = requests.get(
            SMN_ALERT_AREA_URL,
            params={"mode": "alert"},
            headers=headers_smn(token),
            timeout=20,
        )
        if r.status_code == 401:
            _pagina_publica_smn.clear()
            obtener_token_smn.clear()
            token = obtener_token_smn()
            r = requests.get(
                SMN_ALERT_AREA_URL,
                params={"mode": "alert"},
                headers=headers_smn(token),
                timeout=20,
            )
        r.raise_for_status()
        return r.json(), "OK"
    except Exception as e:
        return None, f"No se pudieron consultar las alertas del SMN directamente: {e}"


# ============================================================
# RADAR SMN — IMÁGENES OFICIALES DENTRO DE LA APLICACIÓN
# ============================================================
SMN_RADAR_WEB = "https://www.smn.gob.ar/radar/"
SMN_RADAR_STATIC = "https://estaticos.smn.gob.ar/vmsr/radar/"
RADARES_SMN = {
    "Mosaico Argentina": "COMP_ARG",
    "Mosaico Centro": "COMP_CEN",
    "Mosaico Norte": "COMP_NOR",
    "Paraná (Entre Ríos)": "PAR_240",
    "Mercedes (Corrientes)": "RMA8_240",
    "Resistencia (Chaco)": "RMA4_240",
}


@st.cache_data(ttl=1800, show_spinner=False)
def catalogo_radares_smn():
    """Los códigos adicionales (por ejemplo Ituzaingó) se leen del selector oficial."""
    catalogo = dict(RADARES_SMN)
    for pagina in (SMN_RADAR_WEB, "https://ws2.smn.gob.ar/radar/"):
        texto = _pagina_publica_smn(pagina)
        for codigo, etiqueta in re.findall(
            r"<option\b[^>]*value=['\"]([^'\"]+)['\"][^>]*>(.*?)</option>", texto, re.I | re.S
        ):
            if re.fullmatch(r"(?:COMP_(?:ARG|CEN|NOR)|(?:RMA\d+|PAR|ANG|PER)_240)", codigo):
                nombre = html.unescape(re.sub(r"<[^>]+>", "", etiqueta)).strip()
                if nombre and codigo not in catalogo.values():
                    catalogo[nombre] = codigo
        if len(catalogo) > len(RADARES_SMN):
            break
    return catalogo


def _url_imagen_radar(valor):
    """Solo acepta imágenes publicadas por el SMN; no construye fechas ficticias."""
    if not isinstance(valor, str):
        return None
    valor = html.unescape(valor.strip()).replace("\\/", "/")
    if not re.search(r"\.(png|jpg|jpeg|gif)(?:\?.*)?$", valor, re.I):
        return None
    if "no_disponible" in valor.lower() or "sin_datos" in valor.lower():
        return None
    if valor.startswith("//"):
        valor = "https:" + valor
    elif valor.startswith("/"):
        valor = urljoin("https://estaticos.smn.gob.ar/", valor)
    elif not valor.startswith("https://"):
        valor = urljoin(SMN_RADAR_STATIC, valor)
    parsed = urlparse(valor)
    if parsed.scheme != "https" or parsed.hostname != "estaticos.smn.gob.ar":
        return None
    if not parsed.path.startswith("/vmsr/radar/") or ".." in unquote(parsed.path):
        return None
    return valor


def _fecha_radar(url, metadata=None):
    # Los nombres oficiales terminados en Z expresan UTC.
    m = re.search(r"(\d{8})[_-](\d{6})Z", url)
    if m:
        try:
            return datetime.strptime("".join(m.groups()), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    if metadata:
        for key in ("date", "datetime", "timestamp", "time", "fecha"):
            value = metadata.get(key)
            if not isinstance(value, str) or not re.search(r"(?:Z|[+-]\d{2}:?\d{2})$", value):
                continue
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                pass
    return None


def normalizar_frames_radar(payload):
    frames = {}
    def recorrer(obj, metadata=None):
        if isinstance(obj, dict):
            for value in obj.values():
                recorrer(value, obj)
        elif isinstance(obj, list):
            for value in obj:
                recorrer(value, metadata)
        elif isinstance(obj, str):
            url = _url_imagen_radar(obj)
            if url:
                frames[url] = {"url": url, "fecha": _fecha_radar(url, metadata)}
    recorrer(payload)
    # Sin hora verificable no se puede elegir ni rotular una imagen como actual.
    return sorted(
        (f for f in frames.values() if f["fecha"] is not None),
        key=lambda f: f["fecha"],
    )


@st.cache_data(ttl=300, show_spinner=False)
def obtener_frames_radar(radar_id):
    if not re.fullmatch(r"(?:COMP_(?:ARG|CEN|NOR)|(?:RMA\d+|PAR|ANG|PER)_240)", radar_id):
        return [], "Identificador de radar no válido."
    token = obtener_token_smn()
    if not token:
        return [], "El SMN no permitió obtener el acceso temporal a sus imágenes."
    try:
        url = f"{SMN_API_BASE}/images/radar/{radar_id}"
        for intento in range(2):
            response = requests.get(url, headers=headers_smn(token), timeout=(5, 20))
            if response.status_code != 401 or intento:
                break
            _pagina_publica_smn.clear()
            obtener_token_smn.clear()
            token = obtener_token_smn()
            if not token:
                return [], "No se pudo renovar el acceso al radar del SMN."
        response.raise_for_status()
        frames = normalizar_frames_radar(response.json())
        if not frames:
            return [], "Este radar no publicó imágenes con hora verificable en esta consulta."
        return frames[-12:], "OK"
    except requests.exceptions.HTTPError as error:
        return [], f"El servicio de imágenes del SMN respondió HTTP {error.response.status_code}."
    except (requests.exceptions.RequestException, ValueError):
        return [], "No se pudo consultar la lista de imágenes del SMN."


@st.cache_data(ttl=300, max_entries=48, show_spinner=False)
def descargar_imagen_radar(url):
    if _url_imagen_radar(url) != url:
        return None
    try:
        response = requests.get(url, headers={"User-Agent": "Alerta-Litoral-Agro/3.10.2"}, timeout=(5, 15))
        response.raise_for_status()
        if not response.content or len(response.content) > 15_000_000:
            return None
        with Image.open(BytesIO(response.content)) as im:
            im.verify()
        return response.content
    except (requests.exceptions.RequestException, OSError, ValueError, Image.DecompressionBombError):
        return None


def _etiqueta_frame(frame):
    return frame["fecha"].astimezone(TZ).strftime("%d/%m/%Y %H:%M:%S ART")


def _animacion_radar(frames, imagenes):
    items = [{"src": "data:image/png;base64," + base64.b64encode(data).decode(),
              "hora": _etiqueta_frame(frame)} for frame, data in zip(frames, imagenes)]
    # Componente local: las imágenes ya fueron descargadas y verificadas.
    document = '''<!doctype html><html lang="es"><meta charset="utf-8">
    <style>body{margin:0;font-family:system-ui;background:#101820;color:white}
    .tools{display:flex;align-items:center;gap:14px;padding:10px;flex-wrap:wrap}
    button{padding:8px 16px;cursor:pointer}input{flex:1;min-width:120px}
    img{display:block;width:100%;height:620px;object-fit:contain}</style>
    <div class="tools"><button id="play">▶ Reproducir</button>
    <input aria-label="Imagen radar" id="range" type="range" min="0">
    <span id="time"></span></div><img id="img" alt="Secuencia radar oficial del SMN">
    <script>const frames=__FRAMES__;let i=frames.length-1,timer=null;
    const slider=document.getElementById('range'),button=document.getElementById('play');
    slider.max=frames.length-1;
    function draw(){slider.value=i;document.getElementById('img').src=frames[i].src;
    document.getElementById('time').textContent=frames[i].hora;}
    function pause(){clearInterval(timer);timer=null;button.textContent='▶ Reproducir';}
    slider.oninput=()=>{pause();i=Number(slider.value);draw();};
    button.onclick=()=>{if(timer){pause();return;}button.textContent='⏸ Pausar';
    timer=setInterval(()=>{i=(i+1)%frames.length;draw();},700);};draw();</script></html>'''
    components.html(document.replace("__FRAMES__", json.dumps(items)), height=700, scrolling=False)


def mostrar_radar_api_smn():
    st.header("📡 Radar meteorológico — SMN / SINARAME")
    st.caption("Imágenes oficiales de vigilancia de lluvia y tormentas. No modifican el índice de anegamiento.")
    a, b = st.columns([3, 1])
    catalogo = catalogo_radares_smn()
    nombre = a.selectbox("Radar o mosaico", list(catalogo), key="radar_smn_selector")
    radar_id = catalogo[nombre]
    if b.button("🔄 Actualizar radar", key="radar_smn_refresh", use_container_width=True):
        _pagina_publica_smn.clear()
        obtener_token_smn.clear()
        catalogo_radares_smn.clear()
        obtener_frames_radar.clear()
        descargar_imagen_radar.clear()
    modo = st.radio("Visualización", ["Última imagen", "Secuencia de imágenes"], horizontal=True, key="radar_smn_modo")
    st.caption("Consulta cada 5 minutos mientras la página está abierta; la frecuencia de publicación depende del SMN.")
    with st.spinner("Consultando imágenes oficiales del radar..."):
        frames, estado = obtener_frames_radar(radar_id)
        imagen = None
        latest = None
        # Una imagen cuyo archivo ya fue retirado no deja el visor en blanco.
        for frame in reversed(frames[-3:]):
            data = descargar_imagen_radar(frame["url"])
            if data:
                latest, imagen = frame, data
                break
    key = "radar_smn_ultimo_" + radar_id
    if imagen:
        st.session_state[key] = {"frame": latest, "imagen": imagen}
    else:
        anterior = st.session_state.get(key)
        st.warning(estado if estado != "OK" else "No se pudo descargar una imagen válida del radar.")
        if anterior:
            latest, imagen = anterior["frame"], anterior["imagen"]
            st.warning("Se muestra la última imagen recibida en esta sesión; no se pudo confirmar una actualización.")
    st.caption("Última consulta: " + ahora().strftime("%d/%m/%Y %H:%M:%S ART"))
    if imagen:
        edad = (datetime.now(timezone.utc) - latest["fecha"]).total_seconds() / 60
        st.write("**Hora de la imagen:** " + _etiqueta_frame(latest))
        if edad > 30:
            st.warning(f"Imagen con {edad:.0f} minutos de antigüedad. Puede haber demoras o un radar fuera de servicio.")
        elif edad < -5:
            st.warning("La hora publicada es posterior a la hora actual; no se pudo confirmar su vigencia.")
        if latest and frames and latest["url"] != frames[-1]["url"]:
            st.warning("La imagen más nueva de la lista no pudo descargarse; se muestra una anterior.")
        if modo == "Secuencia de imágenes" and frames and estado == "OK":
            cantidad = st.select_slider("Cantidad de imágenes", options=[6, 12], key="radar_smn_cantidad")
            elegidos = [f for f in frames if f["fecha"] <= latest["fecha"]][-cantidad:]
            with ThreadPoolExecutor(max_workers=4) as pool:
                datos = list(pool.map(descargar_imagen_radar, [f["url"] for f in elegidos]))
            validos = [(f, d) for f, d in zip(elegidos, datos) if d]
            if len(validos) >= 2:
                _animacion_radar([f for f, _ in validos], [d for _, d in validos])
                st.caption(f"Secuencia de {len(validos)} imágenes recibidas; pueden existir intervalos sin datos.")
            else:
                st.info("No hay suficientes imágenes válidas para reproducir una secuencia.")
                st.image(imagen, caption=nombre, use_container_width=True)
        else:
            st.image(imagen, caption=nombre, use_container_width=True)
    else:
        st.info("Radar sin imagen disponible en esta consulta. Probá otro radar o actualizá más tarde.")
    st.link_button("Abrir visor oficial del SMN", SMN_RADAR_WEB, use_container_width=True)
    st.caption("Fuente: Servicio Meteorológico Nacional / SINARAME. Ausencia de ecos o datos no confirma ausencia de lluvia.")



# ============================================================
# RADAR PÚBLICO SINARAME — IMÁGENES VERIFICADAS SIN TOKEN SMN
# ============================================================
SINARAME_PUBLIC_URL = "https://radares.hidricosargentina.gob.ar/"
SINARAME_RADARES = {
    "Ituzaingó — Corrientes (RMA13)": "RMA13",
    "Mercedes — Corrientes (RMA8)": "RMA8",
    "Resistencia — Chaco (RMA4)": "RMA4",
    "Tostado — Santa Fe (RMA21)": "RMA21",
}
# Extensiones reproducidas del posicionamiento de las imágenes en el visor
# oficial el 08/10/2026. Se derivaron de mosaicos IGN EPSG:3857 a zoom 12,
# con redondeo de un píxel de pantalla (decenas de metros), y se verificaron
# también a otra escala. No son coordenadas elegidas por cercanía a una ciudad.
# Tostado no publicó imágenes en esa consulta: no se inventa su extensión.
SINARAME_BOUNDS = {
    "RMA13": [[-29.7929841355, -59.2750167847], [-25.4519549783, -54.4084167480]],
    "RMA8": [[-31.3665363311, -60.5144119263], [-27.0254891449, -55.5750274658]],
    "RMA4": [[-29.6221164735, -61.4805221558], [-25.2813333878, -56.6214752197]],
}
# Centros de localidades obtenidos de Georef (fuente IGN), 08/10/2026.
REFERENCIAS_RADAR = [
    {"localidad": "Ituzaingó", "lat": -27.5851000792, "lon": -56.6870452397},
    {"localidad": "Resistencia", "lat": -27.4510757112, "lon": -58.9864835287},
]
# El visor oficial publica archivos /cache/RMAn/AAAAMMDDHHMMSS.png.
# Su hora está expresada en ART y sus imágenes observadas tienen pasos de 10 min.
# Cada candidato se descarga y valida: nunca se muestra una URL inventada como dato actual.


@st.cache_data(ttl=300, max_entries=160, show_spinner=False)
def descargar_frame_sinarame(radar_id, marca):
    if radar_id not in SINARAME_RADARES.values() or not re.fullmatch(r"\d{14}", marca):
        return None
    url = f"{SINARAME_PUBLIC_URL}cache/{radar_id}/{marca}.png"
    try:
        r = requests.get(url, headers={"User-Agent": "Alerta-Litoral-Agro/3.10.2"}, timeout=(5, 10))
        r.raise_for_status()
        if len(r.content) > 15_000_000:
            return None
        with Image.open(BytesIO(r.content)) as im:
            if im.format != "PNG":
                return None
            im.verify()
        fecha = datetime.strptime(marca, "%Y%m%d%H%M%S").replace(tzinfo=TZ)
        return {"url": url, "fecha": fecha, "imagen": r.content}
    except (requests.exceptions.RequestException, OSError, ValueError, Image.DecompressionBombError):
        return None


@st.cache_data(ttl=300, show_spinner=False)
def consultar_radar_sinarame(radar_id, cantidad):
    referencia = datetime.now(timezone.utc)
    try:
        # HTTP Date permite calcular la edad respecto del reloj del publicador.
        r = requests.head(SINARAME_PUBLIC_URL, headers={"User-Agent": "Alerta-Litoral-Agro/3.10.2"}, timeout=(5, 10))
        r.raise_for_status()
        servidor = parsedate_to_datetime(r.headers.get("Date", ""))
        if servidor.tzinfo:
            referencia = servidor.astimezone(timezone.utc)
    except (requests.exceptions.RequestException, TypeError, ValueError):
        pass
    local = referencia.astimezone(TZ)
    base = local.replace(minute=(local.minute // 10) * 10, second=0, microsecond=0)
    # Búsqueda acotada: máximo tres horas, solo el radar seleccionado.
    marcas = [(base - timedelta(minutes=10*i)).strftime("%Y%m%d%H%M%S") for i in range(18)]
    frames = []
    for inicio in range(0, len(marcas), 4):
        grupo = marcas[inicio:inicio+4]
        with ThreadPoolExecutor(max_workers=4) as pool:
            resultados = list(pool.map(lambda m: descargar_frame_sinarame(radar_id, m), grupo))
        frames.extend(f for f in resultados if f)
        if len(frames) >= cantidad:
            break
    frames = sorted(frames, key=lambda f: f["fecha"])[-cantidad:]
    return {"frames": frames, "referencia": referencia, "consultado": datetime.now(timezone.utc)}


def _documento_mapa_radar(radar_id, nombre, frames):
    """Mapa local: cartografía independiente, PNG originales y una misma extensión."""
    limites = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"nombre": provincia}, "geometry": geometria}
        for provincia, geometria in GEOMETRIAS_LITORAL.items()
    ]}
    bounds = SINARAME_BOUNDS.get(radar_id)
    # Solo se georreferencian las imágenes cuyo encuadre se pudo verificar.
    items = [{"src": "data:image/png;base64," + base64.b64encode(f["imagen"]).decode(),
              "hora": _etiqueta_frame(f)} for f in frames] if bounds else []
    payload = {"nombre": nombre, "bounds": bounds, "frames": items,
               "limites": limites, "localidades": NODOS + REFERENCIAS_RADAR,
               "centro": [-29.233, -61.769] if radar_id == "RMA21" else [-29, -59]}
    documento = '''<!doctype html><html lang="es"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="">
    <style>
    *{box-sizing:border-box}body{margin:0;background:#f8fafc;color:#172b3a;font:14px system-ui}
    .tools{display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:10px}
    button{border:1px solid #cbd5e1;background:white;color:#172b3a;border-radius:7px;padding:8px 10px;cursor:pointer}
    button:disabled{opacity:.45;cursor:default}input{accent-color:#087e8b}
    #range{flex:1;min-width:120px}#time{font-variant-numeric:tabular-nums;font-weight:600}
    #map{height:560px;background:#edf2f6;border:1px solid #cbd5e1;border-radius:8px}
    .place{background:none;border:0;box-shadow:none;color:#102f40;font-size:12px;font-weight:650;
      text-shadow:0 0 3px white,1px 1px 2px white,-1px -1px 2px white}
    .place:before{display:none}.legend{background:rgba(255,255,255,.94);padding:8px;border-radius:6px;
      box-shadow:0 1px 5px #0003;font:11px system-ui;color:#172b3a}
    .legend .bar{width:16px;height:180px;background:linear-gradient(to top,
      #3d416b 0%,#3b4f78 5.56%,#3d5989 11.11%,#3d6595 16.67%,#3772a6 22.22%,
      #3189bb 27.78%,#25a1cf 33.33%,#4ce132 38.89%,#3ab027 44.44%,#237219 50%,
      #c9d333 55.56%,#d69818 61.11%,#c50017 66.67%,#c1005f 72.22%,#cb00cd 77.78%,
      #e2f5ee 83.33%,#a7ecce 88.89%,#88dfbd 94.44%,#88dfbd 100%);border:1px solid #64748b}
    .legend .scale{display:flex;gap:6px;margin-top:5px}.ticks{height:180px;display:flex;flex-direction:column;justify-content:space-between}
    #status{padding:6px 10px;min-height:26px;font-size:12px;color:#475569}
    .leaflet-control-layers{font-size:12px}#fallback{font-size:12px;padding:10px}
    @media(max-width:500px){.tools{gap:6px}#time{width:100%}.legend .bar,.ticks{height:140px}.place{font-size:11px}}
    </style></head><body>
    <div class="tools"><button id="prev" aria-label="Imagen anterior">◀</button>
    <button id="play">▶ Reproducir</button><button id="next" aria-label="Imagen siguiente">▶</button>
    <input id="range" aria-label="Imagen radar" type="range" min="0" step="1">
    <span id="time" aria-live="polite"></span></div>
    <div class="tools"><button id="home">📍 Centrar radar</button>
    <label for="opacity">Opacidad de los ecos</label><input id="opacity" type="range" min="0" max="1" step="0.05" value="0.65">
    <output id="opacityValue" for="opacity">65%</output></div>
    <div id="map" role="region" aria-label="Mapa geográfico del radar"></div><div id="status" role="status"></div>
    <noscript>Activá JavaScript para ver el mapa interactivo.</noscript>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
      integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
    <script>
    const data=__DATA__,frames=data.frames;
    const status=document.getElementById('status'),time=document.getElementById('time');
    const slider=document.getElementById('range'),play=document.getElementById('play');
    if(!window.L){status.textContent='No se pudo cargar el mapa. Revisá la conexión y actualizá la página.';}
    else{
      const map=L.map('map',{maxZoom:12,scrollWheelZoom:true});
      const ign=L.tileLayer('https://wms.ign.gob.ar/geoserver/gwc/service/wmts?layer=capabaseargenmap&style=&tilematrixset=EPSG:3857&Service=WMTS&Request=GetTile&Version=1.0.0&Format=image/png&TileMatrix=EPSG:3857:{z}&TileCol={x}&TileRow={y}',
        {maxZoom:12,attribution:'&copy; IGN Argentina'});
      const osm=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
        {maxZoom:12,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'});
      ign.addTo(map);
      let tileErrors=0,fallback=false;
      ign.on('tileerror',()=>{if(++tileErrors>=3&&!fallback&&map.hasLayer(ign)){
        fallback=true;map.removeLayer(ign);osm.addTo(map);
        status.textContent='Cartografía OpenStreetMap: el fondo IGN no respondió.';}});
      osm.on('tileerror',()=>{status.textContent='No se pudo cargar toda la cartografía. Los límites y localidades siguen disponibles.';});
      map.createPane('limites');map.getPane('limites').style.zIndex=450;
      map.createPane('lugares');map.getPane('lugares').style.zIndex=460;
      const provinces=L.geoJSON(data.limites,{pane:'limites',style:{color:'#233d4d',weight:1.6,opacity:.8,fill:false},
        onEachFeature:(f,layer)=>layer.bindTooltip(f.properties.nombre)}).addTo(map);
      const places=L.layerGroup().addTo(map);
      data.localidades.forEach(n=>L.circleMarker([n.lat,n.lon],{pane:'lugares',radius:3,color:'#18364c',weight:1,fillColor:'white',fillOpacity:1})
        .bindTooltip(n.localidad,{permanent:true,direction:'right',className:'place'}).addTo(places));
      const bounds=data.bounds?L.latLngBounds(data.bounds):null;
      function center(){if(bounds)map.fitBounds(bounds,{padding:[20,20],maxZoom:8});else map.setView(data.centro,7);}
      center();document.getElementById('home').onclick=center;
      L.control.scale({imperial:false}).addTo(map);
      let index=frames.length-1,timer=null,overlay=null;
      if(frames.length&&bounds){
        overlay=L.imageOverlay(frames[index].src,bounds,{opacity:.65,interactive:false,
          alt:'Reflectividad SINARAME sobre el mapa geográfico',attribution:'Radar: SINARAME / Recursos Hídricos'}).addTo(map);
        overlay.on('error',()=>{pause();status.textContent='No se pudo mostrar esta imagen del radar.';});
        const legend=L.control({position:'bottomright'});
        legend.onAdd=()=>{const e=L.DomUtil.create('div','legend');
          e.innerHTML='<b>Reflectividad · dBZ</b><div class="scale"><div class="bar"></div><div class="ticks"><span>75</span><span>60</span><span>45</span><span>30</span><span>15</span><span>0</span><span>−15</span></div></div>';return e;};legend.addTo(map);
        status.textContent=data.nombre+' · Georreferencia reproducida del visor oficial para orientación regional.';
      }else{time.textContent='Sin imagen georreferenciada disponible';status.textContent='Se muestra la cartografía de referencia.';}
      const layers={'Límites provinciales':provinces,'Localidades':places};if(overlay)layers['Ecos radar']=overlay;
      L.control.layers({'IGN Argentina':ign,'OpenStreetMap':osm},layers,{collapsed:true}).addTo(map);
      slider.max=Math.max(0,frames.length-1);
      function draw(){if(!overlay)return;slider.value=index;overlay.setUrl(frames[index].src);time.textContent=frames[index].hora;}
      function pause(){clearInterval(timer);timer=null;play.textContent='▶ Reproducir';}
      slider.oninput=()=>{pause();index=Number(slider.value);draw();};
      document.getElementById('prev').onclick=()=>{pause();index=(index-1+frames.length)%frames.length;draw();};
      document.getElementById('next').onclick=()=>{pause();index=(index+1)%frames.length;draw();};
      play.onclick=()=>{if(timer){pause();return;}play.textContent='⏸ Pausar';timer=setInterval(()=>{index=(index+1)%frames.length;draw();},800);};
      ['prev','play','next','range'].forEach(id=>document.getElementById(id).disabled=frames.length<2);
      document.getElementById('opacity').disabled=!overlay;
      document.getElementById('opacity').oninput=e=>{const value=Number(e.target.value);
        if(overlay)overlay.setOpacity(value);document.getElementById('opacityValue').textContent=Math.round(value*100)+'%';};
      window.addEventListener('pagehide',pause);window.addEventListener('resize',()=>map.invalidateSize());
      draw();map.invalidateSize();
    }
    </script></body></html>'''
    # Escapa el cierre de script incluso si en el futuro los nombres son externos.
    return documento.replace("__DATA__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))


@st.fragment(run_every="5m")
def mostrar_radar_smn():
    st.header("📡 Radar meteorológico — SINARAME")
    st.caption("Imágenes públicas del Sistema Nacional de Radares Meteorológicos, publicadas por Recursos Hídricos de la Nación.")
    a, b = st.columns([3, 1])
    nombre = a.selectbox("Radar del Litoral", list(SINARAME_RADARES), key="sinarame_radar_selector")
    radar_id = SINARAME_RADARES[nombre]
    if b.button("🔄 Actualizar radar", use_container_width=True, key="sinarame_radar_refresh"):
        consultar_radar_sinarame.clear()
        descargar_frame_sinarame.clear()
    modo = st.radio("Visualización", ["Última imagen", "Secuencia de imágenes"], horizontal=True, key="sinarame_radar_modo")
    cantidad = 1
    if modo == "Secuencia de imágenes":
        cantidad = st.select_slider("Cantidad de imágenes", options=[6, 12], key="sinarame_radar_cantidad")
    with st.spinner("Descargando imágenes públicas del radar SINARAME..."):
        resultado = consultar_radar_sinarame(radar_id, cantidad)
    frames = resultado["frames"]
    guardado = "sinarame_ultimo_" + radar_id
    if frames:
        st.session_state[guardado] = frames[-1]
    elif st.session_state.get(guardado):
        frames = [st.session_state[guardado]]
        st.warning("No se pudo confirmar una actualización. Se conserva la última imagen recibida en esta sesión.")
    st.caption("Última consulta: " + _hora_smn(resultado["consultado"]))
    if frames:
        ultimo = frames[-1]
        edad = (resultado["referencia"] - ultimo["fecha"].astimezone(timezone.utc)).total_seconds() / 60
        st.write("**Hora de la imagen:** " + _etiqueta_frame(ultimo))
        if edad > 30:
            st.warning(f"La última imagen disponible tiene {edad:.0f} minutos de antigüedad. No se confirma actualización en tiempo real.")
        elif edad < -5:
            st.warning("La hora de la imagen es posterior al reloj de consulta. Su actualidad no está confirmada.")
        if cantidad > 1 and len(frames) > 1:
            st.caption(f"Secuencia de {len(frames)} imágenes recibidas. Pueden existir intervalos sin datos.")
        elif cantidad > 1:
            st.info("Se recibió una sola imagen válida para esta secuencia.")
        if radar_id not in SINARAME_BOUNDS:
            st.warning("Todavía no hay una georreferencia verificada para este radar. Su imagen se muestra por separado para evitar ubicar los ecos incorrectamente.")
            st.image(ultimo["imagen"], caption=nombre + " · Reflectividad (dBZ)", width=640)
        components.html(_documento_mapa_radar(radar_id, nombre, frames), height=760, scrolling=False)
        st.caption("Usá +/− para acercarte, arrastrá el mapa y ajustá la opacidad de los ecos para leer las localidades. El botón de capas permite cambiar el fondo y ocultar referencias.")
        st.download_button("⬇️ Descargar imagen radar", ultimo["imagen"],
            file_name=f"radar_{radar_id}_{ultimo['fecha']:%Y%m%d_%H%M}_ART.png",
            mime="image/png", key="sinarame_radar_download")
    else:
        st.warning("No se encontraron imágenes válidas de este radar en las últimas tres horas. Probá otro radar o actualizá más tarde.")
        components.html(_documento_mapa_radar(radar_id, nombre, []), height=760, scrolling=False)
    st.link_button("Abrir mapa oficial SINARAME", SINARAME_PUBLIC_URL, use_container_width=True)
    st.caption("Fuente: SINARAME / Subsecretaría de Recursos Hídricos. Esta visualización no constituye una alerta ni modifica el índice de anegamiento.")
    if st.checkbox("Consultar también el servicio de imágenes del SMN", key="radar_smn_api_opcional"):
        mostrar_radar_api_smn()



HISTORY_FILE = Path("historial_alerta_litoral.csv")
STATE_FILE = Path("telegram_alert_state.json")

MAP_CENTER_LAT = -30.5
MAP_CENTER_LON = -59.8
MAP_ZOOM = 5.6


# ============================================================
# NODOS DE MONITOREO
# ============================================================

NODOS = [
    # --------------------------------------------------------
    # SANTA FE
    # --------------------------------------------------------
    {"localidad": "Reconquista", "provincia": "Santa Fe", "lat": -29.144, "lon": -59.643},
    {"localidad": "Avellaneda", "provincia": "Santa Fe", "lat": -29.117, "lon": -59.658},
    {"localidad": "Vera", "provincia": "Santa Fe", "lat": -29.460, "lon": -60.213},
    {"localidad": "Tostado", "provincia": "Santa Fe", "lat": -29.233, "lon": -61.769},
    {"localidad": "Rafaela", "provincia": "Santa Fe", "lat": -31.250, "lon": -61.486},
    {"localidad": "Santa Fe", "provincia": "Santa Fe", "lat": -31.633, "lon": -60.700},
    {"localidad": "Esperanza", "provincia": "Santa Fe", "lat": -31.448, "lon": -60.932},
    {"localidad": "Rosario", "provincia": "Santa Fe", "lat": -32.946, "lon": -60.639},
    {"localidad": "Venado Tuerto", "provincia": "Santa Fe", "lat": -33.745, "lon": -61.968},

    # --------------------------------------------------------
    # CORRIENTES
    # --------------------------------------------------------
    {"localidad": "Corrientes", "provincia": "Corrientes", "lat": -27.469, "lon": -58.830},
    {"localidad": "Goya", "provincia": "Corrientes", "lat": -29.140, "lon": -59.263},
    {"localidad": "Mercedes", "provincia": "Corrientes", "lat": -29.182, "lon": -58.075},
    {"localidad": "Curuzú Cuatiá", "provincia": "Corrientes", "lat": -29.791, "lon": -58.054},
    {"localidad": "Paso de los Libres", "provincia": "Corrientes", "lat": -29.713, "lon": -57.088},
    {"localidad": "Santo Tomé", "provincia": "Corrientes", "lat": -28.549, "lon": -56.040},

    # --------------------------------------------------------
    # ENTRE RÍOS
    # --------------------------------------------------------
    {"localidad": "Paraná", "provincia": "Entre Ríos", "lat": -31.741, "lon": -60.511},
    {"localidad": "La Paz", "provincia": "Entre Ríos", "lat": -30.744, "lon": -59.645},
    {"localidad": "Concordia", "provincia": "Entre Ríos", "lat": -31.393, "lon": -58.020},
]


# ============================================================
# UTILIDADES
# ============================================================

def ahora():
    return datetime.now(TZ)


def hora_actual_local_redondeada():
    return pd.Timestamp(
        ahora().replace(
            minute=0,
            second=0,
            microsecond=0,
            tzinfo=None,
        )
    )


def clamp(valor, minimo=0.0, maximo=100.0):
    try:
        valor = float(valor)
    except Exception:
        return minimo

    if not np.isfinite(valor):
        return minimo

    return max(minimo, min(maximo, valor))


def normalizar_0_100(valor, minimo, maximo):
    try:
        valor = float(valor)
    except Exception:
        return 0.0

    if not np.isfinite(valor) or maximo <= minimo:
        return 0.0

    resultado = ((valor - minimo) / (maximo - minimo)) * 100.0
    return clamp(resultado)


def array_numerico(valores):
    try:
        return np.asarray(valores, dtype=float)
    except Exception:
        return np.array([], dtype=float)


def ajustar_longitud(array, n):
    if len(array) == n:
        return array

    if len(array) > n:
        return array[:n]

    salida = np.full(n, np.nan, dtype=float)
    salida[:len(array)] = array
    return salida


def suma_segura(array, mascara=None):
    if array is None or len(array) == 0:
        return 0.0

    try:
        datos = array[mascara] if mascara is not None else array

        if len(datos) == 0:
            return 0.0

        return float(np.nansum(datos))

    except Exception:
        return 0.0


def media_segura(array, mascara=None, default=np.nan):
    if array is None or len(array) == 0:
        return default

    try:
        datos = array[mascara] if mascara is not None else array
        datos = datos[np.isfinite(datos)]

        return float(np.mean(datos)) if len(datos) else default

    except Exception:
        return default


def maximo_seguro(array, mascara=None, default=np.nan):
    if array is None or len(array) == 0:
        return default

    try:
        datos = array[mascara] if mascara is not None else array
        datos = datos[np.isfinite(datos)]

        return float(np.max(datos)) if len(datos) else default

    except Exception:
        return default


def ultimo_valor(array, mascara, default=np.nan):
    if array is None or len(array) == 0:
        return default

    try:
        indices = np.where(mascara & np.isfinite(array))[0]

        return float(array[indices[-1]]) if len(indices) else default

    except Exception:
        return default


def ventana_mascara(horas, inicio, fin, incluir_inicio=False):
    if incluir_inicio:
        return (horas >= inicio) & (horas <= fin)

    return (horas > inicio) & (horas <= fin)


def nivel_desde_indice(indice):
    if indice >= 75:
        return "MUY ALTO"

    if indice >= 55:
        return "ALTO"

    if indice >= 35:
        return "MEDIO"

    return "BAJO"


def emoji_nivel(nivel):
    return {
        "MUY ALTO": "🔴",
        "ALTO": "🟠",
        "MEDIO": "🟡",
        "BAJO": "🟢",
        "SIN DATOS": "⚪",
    }.get(nivel, "⚪")


def accion_desde_nivel(nivel):
    acciones = {
        "MUY ALTO": (
            "Priorizar medidas preventivas. Evitar ingreso de maquinaria a "
            "sectores comprometidos y evaluar medidas preventivas sobre "
            "animales, cultivos e insumos."
        ),
        "ALTO": (
            "Reforzar el monitoreo. Evitar tareas que puedan compactar el suelo "
            "y revisar sectores bajos o con antecedentes de anegamiento."
        ),
        "MEDIO": (
            "Mantener vigilancia sobre lluvia, humedad del suelo y evolución "
            "del riesgo antes de realizar tareas sensibles."
        ),
        "BAJO": (
            "Condiciones actualmente favorables según el índice. Mantener el "
            "monitoreo meteorológico."
        ),
    }

    return acciones.get(nivel, "Sin datos suficientes.")


def direccion_compass(grados):
    try:
        grados = float(grados)
    except Exception:
        return "Sin dato"

    if not np.isfinite(grados):
        return "Sin dato"

    direcciones = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"]
    indice = int((grados + 22.5) / 45.0) % 8

    return direcciones[indice]


def descripcion_wmo(codigo):
    try:
        codigo = int(codigo)
    except Exception:
        return "Sin dato"

    tabla = {
        0: "Cielo despejado",
        1: "Principalmente despejado",
        2: "Parcialmente nublado",
        3: "Cubierto",
        45: "Niebla",
        48: "Niebla con escarcha",
        51: "Llovizna ligera",
        53: "Llovizna moderada",
        55: "Llovizna intensa",
        61: "Lluvia ligera",
        63: "Lluvia moderada",
        65: "Lluvia intensa",
        66: "Lluvia helada ligera",
        67: "Lluvia helada intensa",
        71: "Nieve ligera",
        73: "Nieve moderada",
        75: "Nieve intensa",
        77: "Granos de nieve",
        80: "Chaparrones ligeros",
        81: "Chaparrones moderados",
        82: "Chaparrones intensos",
        85: "Nevadas ligeras",
        86: "Nevadas intensas",
        95: "Tormenta",
        96: "Tormenta con granizo ligero",
        99: "Tormenta con granizo intenso",
    }

    return tabla.get(codigo, f"Código WMO {codigo}")


# ============================================================
# OPEN-METEO / ECMWF
# ============================================================

@st.cache_data(ttl=900, show_spinner=False)
def obtener_datos_ecmwf():
    """Consulta los 18 nodos en una única petición multi-coordenada."""

    latitudes = ",".join(
        f'{nodo["lat"]:.3f}' for nodo in NODOS
    )

    longitudes = ",".join(
        f'{nodo["lon"]:.3f}' for nodo in NODOS
    )

    variables_horarias = [
        "temperature_2m",
        "apparent_temperature",
        "dew_point_2m",
        "relative_humidity_2m",
        "pressure_msl",
        "surface_pressure",
        "cloud_cover",
        "cloud_cover_low",
        "cloud_cover_mid",
        "cloud_cover_high",
        "wind_speed_10m",
        "wind_gusts_10m",
        "wind_direction_10m",
        "shortwave_radiation",
        "cape",
        "vapour_pressure_deficit",
        "evapotranspiration",
        "et0_fao_evapotranspiration",
        "precipitation",
        "rain",
        "showers",
        "precipitation_probability",
        "runoff",
        "soil_temperature_0_to_7cm",
        "soil_temperature_7_to_28cm",
        "soil_temperature_28_to_100cm",
        "soil_temperature_100_to_255cm",
        "soil_moisture_0_to_7cm",
        "soil_moisture_7_to_28cm",
        "soil_moisture_28_to_100cm",
        "soil_moisture_100_to_255cm",
        "weather_code",
    ]

    variables_diarias = [
        "weather_code",
        "temperature_2m_min",
        "temperature_2m_max",
        "apparent_temperature_min",
        "apparent_temperature_max",
        "precipitation_sum",
        "rain_sum",
        "showers_sum",
        "precipitation_probability_max",
        "precipitation_hours",
        "wind_speed_10m_max",
        "wind_gusts_10m_max",
        "wind_direction_10m_dominant",
        "cape_max",
        "shortwave_radiation_sum",
        "sunshine_duration",
        "et0_fao_evapotranspiration",
    ]

    params = {
        "latitude": latitudes,
        "longitude": longitudes,
        "hourly": ",".join(variables_horarias),
        "daily": ",".join(variables_diarias),
        "past_days": 8,
        "forecast_days": 15,
        "timezone": "America/Argentina/Buenos_Aires",
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "precipitation_unit": "mm",
        "cell_selection": "land",
    }

    headers = {
        "User-Agent": "Alerta-Litoral-Agro/3.10.2",
    }

    try:
        response = requests.get(
            API_URL,
            params=params,
            headers=headers,
            timeout=60,
        )

        response.raise_for_status()
        payload = response.json()

        if isinstance(payload, list):
            return payload

        if isinstance(payload, dict) and payload.get("error"):
            return [payload for _ in NODOS]

        return [payload]

    except Exception as error:
        return [{"error": str(error)} for _ in NODOS]


# ============================================================
# PROCESAMIENTO DEL NODO
# ============================================================

def resultado_sin_datos(nodo, error="Sin datos suficientes"):

    campos_nan = [
        "indice",
        "temperatura",
        "sensacion",
        "punto_rocio",
        "humedad_relativa",
        "presion_msl",
        "presion_superficie",
        "nubosidad",
        "nubosidad_baja",
        "nubosidad_media",
        "nubosidad_alta",
        "viento",
        "rafaga",
        "direccion_viento",
        "cape",
        "vpd",
        "evapotranspiracion",
        "et0",
        "precipitacion_actual",
        "lluvia_actual",
        "chaparrones_actual",
        "humedad_0_7",
        "humedad_7_28",
        "humedad_28_100",
        "humedad_100_255",
        "humedad_suelo_promedio",
        "temp_suelo_0_7",
        "temp_suelo_7_28",
        "temp_suelo_28_100",
        "temp_suelo_100_255",
        "lluvia_24",
        "lluvia_72",
        "lluvia_7d",
        "lluvia_futura_24",
        "lluvia_futura_72",
        "lluvia_futura_7d",
        "rain_24",
        "rain_72",
        "rain_7d",
        "showers_24",
        "showers_72",
        "showers_7d",
        "runoff_24",
        "runoff_72",
        "runoff_7d",
        "runoff_futuro_24",
        "runoff_futuro_72",
        "runoff_futuro_7d",
        "prob_lluvia",
        "prob_lluvia_24",
        "prob_lluvia_72",
        "cape_max_24",
        "et0_24",
        "temp_min_24",
        "temp_max_24",
        "viento_max_24",
        "rafaga_max_24",
        "codigo_tiempo",
    ]

    salida = {
        **nodo,
        "estado_datos": "ERROR",
        "error_datos": str(error),
        "nivel": "SIN DATOS",
        "tendencia": "SIN DATOS",
        "direccion_hidrologica": "SIN DATOS",
        "velocidad_hidrologica": "SIN DATOS",
        "accion": "Sin datos suficientes.",
        "actualizado": ahora().strftime("%d/%m/%Y %H:%M"),
        "horas_disponibles": 0,
        "condicion_critica": False,
    }

    salida.update({
        campo: np.nan
        for campo in campos_nan
    })

    return salida


def procesar_nodo(nodo, datos, referencia_historica=None):

    if not isinstance(datos, dict) or datos.get("error"):

        error = (
            datos.get("reason")
            or datos.get("error")
            or "Sin datos suficientes"
            if isinstance(datos, dict)
            else "Sin datos suficientes"
        )

        return resultado_sin_datos(nodo, error)

    hourly = datos.get("hourly", {}) or {}
    tiempos = hourly.get("time", [])

    horas_raw = pd.to_datetime(
        tiempos,
        errors="coerce",
    )

    if len(horas_raw) == 0:
        return resultado_sin_datos(
            nodo,
            "La respuesta no contiene horarios",
        )

    horas = pd.DatetimeIndex(horas_raw)
    valid_time = ~horas.isna()
    horas = horas[valid_time]

    n = len(horas)

    def serie(nombre):
        return ajustar_longitud(
            array_numerico(
                hourly.get(nombre, [])
            ),
            len(horas_raw),
        )[valid_time]

    temperatura = serie("temperature_2m")
    sensacion = serie("apparent_temperature")
    punto_rocio = serie("dew_point_2m")
    humedad_relativa = serie("relative_humidity_2m")
    presion_msl = serie("pressure_msl")
    presion_superficie = serie("surface_pressure")
    nubosidad = serie("cloud_cover")
    nubosidad_baja = serie("cloud_cover_low")
    nubosidad_media = serie("cloud_cover_mid")
    nubosidad_alta = serie("cloud_cover_high")
    viento = serie("wind_speed_10m")
    rafaga = serie("wind_gusts_10m")
    direccion_viento = serie("wind_direction_10m")
    cape = serie("cape")
    vpd = serie("vapour_pressure_deficit")
    evapotranspiracion = serie("evapotranspiration")
    et0 = serie("et0_fao_evapotranspiration")
    precipitacion = serie("precipitation")
    rain = serie("rain")
    showers = serie("showers")
    probabilidad = serie("precipitation_probability")
    runoff = serie("runoff")
    codigo_tiempo = serie("weather_code")

    humedad_0_7 = serie("soil_moisture_0_to_7cm")
    humedad_7_28 = serie("soil_moisture_7_to_28cm")
    humedad_28_100 = serie("soil_moisture_28_to_100cm")
    humedad_100_255 = serie("soil_moisture_100_to_255cm")

    temp_suelo_0_7 = serie("soil_temperature_0_to_7cm")
    temp_suelo_7_28 = serie("soil_temperature_7_to_28cm")
    temp_suelo_28_100 = serie("soil_temperature_28_to_100cm")
    temp_suelo_100_255 = serie("soil_temperature_100_to_255cm")

    referencia = (pd.Timestamp(referencia_historica) if referencia_historica is not None
                  else hora_actual_local_redondeada())
    if referencia.tzinfo is not None:
        referencia = referencia.tz_convert("America/Argentina/Buenos_Aires").tz_localize(None)

    pasado_24 = ventana_mascara(
        horas,
        referencia - pd.Timedelta(hours=24),
        referencia,
    )

    pasado_72 = ventana_mascara(
        horas,
        referencia - pd.Timedelta(hours=72),
        referencia,
    )

    pasado_7d = ventana_mascara(
        horas,
        referencia - pd.Timedelta(days=7),
        referencia,
    )

    futuro_24 = ventana_mascara(
        horas,
        referencia,
        referencia + pd.Timedelta(hours=24),
    )

    futuro_72 = ventana_mascara(
        horas,
        referencia,
        referencia + pd.Timedelta(hours=72),
    )

    futuro_7d = ventana_mascara(
        horas,
        referencia,
        referencia + pd.Timedelta(days=7),
    )

    hasta_actual = horas <= referencia

    idx_actuales = np.where(hasta_actual)[0]
    idx_actual = (
        int(idx_actuales[-1])
        if len(idx_actuales)
        else 0
    )

    # ========================================================
    # VARIABLES ACTUALES
    # ========================================================

    temperatura_actual = ultimo_valor(
        temperatura,
        hasta_actual,
    )

    sensacion_actual = ultimo_valor(
        sensacion,
        hasta_actual,
    )

    punto_rocio_actual = ultimo_valor(
        punto_rocio,
        hasta_actual,
    )

    humedad_relativa_actual = ultimo_valor(
        humedad_relativa,
        hasta_actual,
    )

    presion_msl_actual = ultimo_valor(
        presion_msl,
        hasta_actual,
    )

    presion_superficie_actual = ultimo_valor(
        presion_superficie,
        hasta_actual,
    )

    nubosidad_actual = ultimo_valor(
        nubosidad,
        hasta_actual,
    )

    nubosidad_baja_actual = ultimo_valor(
        nubosidad_baja,
        hasta_actual,
    )

    nubosidad_media_actual = ultimo_valor(
        nubosidad_media,
        hasta_actual,
    )

    nubosidad_alta_actual = ultimo_valor(
        nubosidad_alta,
        hasta_actual,
    )

    viento_actual = ultimo_valor(
        viento,
        hasta_actual,
    )

    rafaga_actual = ultimo_valor(
        rafaga,
        hasta_actual,
    )

    direccion_viento_actual = ultimo_valor(
        direccion_viento,
        hasta_actual,
    )

    cape_actual = ultimo_valor(
        cape,
        hasta_actual,
    )

    vpd_actual = ultimo_valor(
        vpd,
        hasta_actual,
    )

    evapotranspiracion_actual = ultimo_valor(
        evapotranspiracion,
        hasta_actual,
    )

    et0_actual = ultimo_valor(
        et0,
        hasta_actual,
    )

    precipitacion_actual = ultimo_valor(
        precipitacion,
        hasta_actual,
    )

    rain_actual = ultimo_valor(
        rain,
        hasta_actual,
    )

    showers_actual = ultimo_valor(
        showers,
        hasta_actual,
    )

    codigo_actual = ultimo_valor(
        codigo_tiempo,
        hasta_actual,
    )

    humedad_capas = [
        ultimo_valor(humedad_0_7, hasta_actual),
        ultimo_valor(humedad_7_28, hasta_actual),
        ultimo_valor(humedad_28_100, hasta_actual),
        ultimo_valor(humedad_100_255, hasta_actual),
    ]

    humedad_validas = [
        x for x in humedad_capas
        if np.isfinite(x)
    ]

    # IMPORTANTE:
    # Open-Meteo entrega humedad de suelo como contenido
    # volumétrico de agua en m³/m³.
    #
    # El sistema mantiene ese valor internamente para que
    # el índice continúe utilizando los umbrales 0.15–0.45.
    #
    # La conversión a porcentaje se realiza únicamente
    # en la interfaz:
    #
    # 0.341 m³/m³ = 34.1 %
    humedad_suelo_promedio = (
        float(np.mean(humedad_validas))
        if humedad_validas
        else np.nan
    )

    temp_suelo_capas = [
        ultimo_valor(
            temp_suelo_0_7,
            hasta_actual,
        ),
        ultimo_valor(
            temp_suelo_7_28,
            hasta_actual,
        ),
        ultimo_valor(
            temp_suelo_28_100,
            hasta_actual,
        ),
        ultimo_valor(
            temp_suelo_100_255,
            hasta_actual,
        ),
    ]

    # ========================================================
    # PRECIPITACIÓN ANTECEDENTE Y FUTURA
    # ========================================================

    lluvia_24 = suma_segura(
        precipitacion,
        pasado_24,
    )

    lluvia_72 = suma_segura(
        precipitacion,
        pasado_72,
    )

    lluvia_7d = suma_segura(
        precipitacion,
        pasado_7d,
    )

    lluvia_futura_24 = suma_segura(
        precipitacion,
        futuro_24,
    )

    lluvia_futura_72 = suma_segura(
        precipitacion,
        futuro_72,
    )

    lluvia_futura_7d = suma_segura(
        precipitacion,
        futuro_7d,
    )

    rain_24 = suma_segura(
        rain,
        pasado_24,
    )

    rain_72 = suma_segura(
        rain,
        pasado_72,
    )

    rain_7d = suma_segura(
        rain,
        pasado_7d,
    )

    showers_24 = suma_segura(
        showers,
        pasado_24,
    )

    showers_72 = suma_segura(
        showers,
        pasado_72,
    )

    showers_7d = suma_segura(
        showers,
        pasado_7d,
    )

    # ========================================================
    # RUNOFF
    # ========================================================

    runoff_24 = suma_segura(
        runoff,
        pasado_24,
    )

    runoff_72 = suma_segura(
        runoff,
        pasado_72,
    )

    runoff_7d = suma_segura(
        runoff,
        pasado_7d,
    )

    runoff_futuro_24 = suma_segura(
        runoff,
        futuro_24,
    )

    runoff_futuro_72 = suma_segura(
        runoff,
        futuro_72,
    )

    runoff_futuro_7d = suma_segura(
        runoff,
        futuro_7d,
    )

    # ========================================================
    # PROBABILIDAD DE PRECIPITACIÓN
    # ========================================================

    prob_lluvia_24 = media_segura(
        probabilidad,
        futuro_24,
    )

    prob_lluvia_72 = media_segura(
        probabilidad,
        futuro_72,
    )

    prob_lluvia = maximo_seguro(
        probabilidad,
        futuro_72,
    )

    # ========================================================
    # EXTREMOS / VARIABLES ADICIONALES
    # ========================================================

    temp_min_24 = (
        np.nanmin(temperatura[futuro_24])
        if np.any(futuro_24)
        and np.any(np.isfinite(temperatura[futuro_24]))
        else np.nan
    )

    temp_max_24 = (
        np.nanmax(temperatura[futuro_24])
        if np.any(futuro_24)
        and np.any(np.isfinite(temperatura[futuro_24]))
        else np.nan
    )

    viento_max_24 = (
        np.nanmax(viento[futuro_24])
        if np.any(futuro_24)
        and np.any(np.isfinite(viento[futuro_24]))
        else np.nan
    )

    rafaga_max_24 = (
        np.nanmax(rafaga[futuro_24])
        if np.any(futuro_24)
        and np.any(np.isfinite(rafaga[futuro_24]))
        else np.nan
    )

    cape_max_24 = (
        np.nanmax(cape[futuro_24])
        if np.any(futuro_24)
        and np.any(np.isfinite(cape[futuro_24]))
        else np.nan
    )

    et0_24 = suma_segura(
        et0,
        futuro_24,
    )

    # ========================================================
    # COMPONENTES DEL ÍNDICE
    # ========================================================

    humedad_score = normalizar_0_100(
        humedad_suelo_promedio,
        0.15,
        0.45,
    )

    lluvia_reciente_score = normalizar_0_100(
        lluvia_72,
        10,
        100,
    )

    lluvia_futura_score = normalizar_0_100(
        lluvia_futura_72,
        10,
        100,
    )

    runoff_score = normalizar_0_100(
        runoff_72,
        1,
        30,
    )

    acumulacion_score = normalizar_0_100(
        lluvia_7d,
        30,
        180,
    )

    lluvia_critica_score = 0.0

    if lluvia_24 >= 50:
        lluvia_critica_score += 40

    elif lluvia_24 >= 30:
        lluvia_critica_score += 25

    elif lluvia_24 >= 20:
        lluvia_critica_score += 15

    if lluvia_72 >= 100:
        lluvia_critica_score += 40

    elif lluvia_72 >= 70:
        lluvia_critica_score += 30

    elif lluvia_72 >= 50:
        lluvia_critica_score += 20

    lluvia_critica_score = clamp(
        lluvia_critica_score
    )

    # ========================================================
    # TENDENCIA HIDROLÓGICA
    # ========================================================

    reciente_12 = ventana_mascara(
        horas,
        referencia - pd.Timedelta(hours=12),
        referencia,
    )

    anterior_12_36 = ventana_mascara(
        horas,
        referencia - pd.Timedelta(hours=36),
        referencia - pd.Timedelta(hours=12),
    )

    runoff_reciente = media_segura(
        runoff,
        reciente_12,
    )

    runoff_anterior = media_segura(
        runoff,
        anterior_12_36,
    )

    if (
        np.isfinite(runoff_reciente)
        and np.isfinite(runoff_anterior)
    ):

        diferencia = (
            runoff_reciente
            - runoff_anterior
        )

        if diferencia > 0.5:

            direccion_hidrologica = "SUBIDA"

            if diferencia >= 2:
                velocidad_hidrologica = "RÁPIDA"
                tendencia = "SUBIDA RÁPIDA"

            else:
                velocidad_hidrologica = "LENTA"
                tendencia = "SUBIDA LENTA"

        elif diferencia < -0.5:

            direccion_hidrologica = "BAJADA"

            if diferencia <= -2:
                velocidad_hidrologica = "RÁPIDA"
                tendencia = "BAJADA RÁPIDA"

            else:
                velocidad_hidrologica = "LENTA"
                tendencia = "BAJADA LENTA"

        else:

            direccion_hidrologica = "ESTABLE"
            velocidad_hidrologica = "NEUTRA"
            tendencia = "ESTABLE"

    else:

        direccion_hidrologica = "SIN DATOS"
        velocidad_hidrologica = "SIN DATOS"
        tendencia = "SIN DATOS"

    tendencia_factor = 0.0

    if direccion_hidrologica == "SUBIDA":

        tendencia_factor = (
            10.0
            if velocidad_hidrologica == "RÁPIDA"
            else 5.0
        )

    elif direccion_hidrologica == "BAJADA":

        tendencia_factor = (
            -8.0
            if velocidad_hidrologica == "RÁPIDA"
            else -4.0
        )

    # ========================================================
    # ÍNDICE FINAL
    # ========================================================

    indice = (
        humedad_score * 0.25
        + lluvia_reciente_score * 0.20
        + lluvia_futura_score * 0.20
        + runoff_score * 0.10
        + acumulacion_score * 0.10
        + lluvia_critica_score * 0.15
    )

    indice += tendencia_factor
    indice = clamp(indice)

    condicion_critica = (
        lluvia_72 >= 120
        or lluvia_7d >= 220
        or runoff_72 >= 50
    )

    if condicion_critica:
        indice = max(
            indice,
            75.0,
        )

    nivel = nivel_desde_indice(
        indice
    )

    return {
        **nodo,
        "estado_datos": "OK",
        "error_datos": "",
        "indice": round(indice, 1),
        "nivel": nivel,
        "cobertura_meteorologica": bool(
            len(pd.DatetimeIndex(horas[pasado_72 & np.isfinite(precipitacion)]).unique()) >= 72
            and len(pd.DatetimeIndex(horas[futuro_72 & np.isfinite(precipitacion)]).unique()) >= 72
            and len(humedad_validas) >= 3
        ),

        "temperatura": (
            round(temperatura_actual, 1)
            if np.isfinite(temperatura_actual)
            else np.nan
        ),

        "sensacion": (
            round(sensacion_actual, 1)
            if np.isfinite(sensacion_actual)
            else np.nan
        ),

        "punto_rocio": (
            round(punto_rocio_actual, 1)
            if np.isfinite(punto_rocio_actual)
            else np.nan
        ),

        "humedad_relativa": (
            round(humedad_relativa_actual, 1)
            if np.isfinite(humedad_relativa_actual)
            else np.nan
        ),

        "presion_msl": (
            round(presion_msl_actual, 1)
            if np.isfinite(presion_msl_actual)
            else np.nan
        ),

        "presion_superficie": (
            round(presion_superficie_actual, 1)
            if np.isfinite(presion_superficie_actual)
            else np.nan
        ),

        "nubosidad": (
            round(nubosidad_actual, 1)
            if np.isfinite(nubosidad_actual)
            else np.nan
        ),

        "nubosidad_baja": (
            round(nubosidad_baja_actual, 1)
            if np.isfinite(nubosidad_baja_actual)
            else np.nan
        ),

        "nubosidad_media": (
            round(nubosidad_media_actual, 1)
            if np.isfinite(nubosidad_media_actual)
            else np.nan
        ),

        "nubosidad_alta": (
            round(nubosidad_alta_actual, 1)
            if np.isfinite(nubosidad_alta_actual)
            else np.nan
        ),

        "viento": (
            round(viento_actual, 1)
            if np.isfinite(viento_actual)
            else np.nan
        ),

        "rafaga": (
            round(rafaga_actual, 1)
            if np.isfinite(rafaga_actual)
            else np.nan
        ),

        "direccion_viento": (
            round(direccion_viento_actual, 0)
            if np.isfinite(direccion_viento_actual)
            else np.nan
        ),

        "direccion_viento_texto":
            direccion_compass(
                direccion_viento_actual
            ),

        "cape": (
            round(cape_actual, 0)
            if np.isfinite(cape_actual)
            else np.nan
        ),

        "vpd": (
            round(vpd_actual, 2)
            if np.isfinite(vpd_actual)
            else np.nan
        ),

        "evapotranspiracion": (
            round(evapotranspiracion_actual, 2)
            if np.isfinite(evapotranspiracion_actual)
            else np.nan
        ),

        "et0": (
            round(et0_actual, 2)
            if np.isfinite(et0_actual)
            else np.nan
        ),

        "precipitacion_actual": (
            round(precipitacion_actual, 2)
            if np.isfinite(precipitacion_actual)
            else np.nan
        ),

        "lluvia_actual": (
            round(rain_actual, 2)
            if np.isfinite(rain_actual)
            else np.nan
        ),

        "chaparrones_actual": (
            round(showers_actual, 2)
            if np.isfinite(showers_actual)
            else np.nan
        ),

        # ====================================================
        # HUMEDAD DE SUELO
        #
        # SE GUARDA INTERNAMENTE EN m³/m³.
        # LA INTERFAZ LA MUESTRA COMO %.
        # ====================================================

        "humedad_0_7": (
            round(humedad_capas[0], 3)
            if np.isfinite(humedad_capas[0])
            else np.nan
        ),

        "humedad_7_28": (
            round(humedad_capas[1], 3)
            if np.isfinite(humedad_capas[1])
            else np.nan
        ),

        "humedad_28_100": (
            round(humedad_capas[2], 3)
            if np.isfinite(humedad_capas[2])
            else np.nan
        ),

        "humedad_100_255": (
            round(humedad_capas[3], 3)
            if np.isfinite(humedad_capas[3])
            else np.nan
        ),

        "humedad_suelo_promedio": (
            round(humedad_suelo_promedio, 3)
            if np.isfinite(humedad_suelo_promedio)
            else np.nan
        ),

        "temp_suelo_0_7": (
            round(temp_suelo_capas[0], 1)
            if np.isfinite(temp_suelo_capas[0])
            else np.nan
        ),

        "temp_suelo_7_28": (
            round(temp_suelo_capas[1], 1)
            if np.isfinite(temp_suelo_capas[1])
            else np.nan
        ),

        "temp_suelo_28_100": (
            round(temp_suelo_capas[2], 1)
            if np.isfinite(temp_suelo_capas[2])
            else np.nan
        ),

        "temp_suelo_100_255": (
            round(temp_suelo_capas[3], 1)
            if np.isfinite(temp_suelo_capas[3])
            else np.nan
        ),

        "lluvia_24": round(lluvia_24, 1),
        "lluvia_72": round(lluvia_72, 1),
        "lluvia_7d": round(lluvia_7d, 1),

        "lluvia_futura_24":
            round(lluvia_futura_24, 1),

        "lluvia_futura_72":
            round(lluvia_futura_72, 1),

        "lluvia_futura_7d":
            round(lluvia_futura_7d, 1),

        "rain_24":
            round(rain_24, 1),

        "rain_72":
            round(rain_72, 1),

        "rain_7d":
            round(rain_7d, 1),

        "showers_24":
            round(showers_24, 1),

        "showers_72":
            round(showers_72, 1),

        "showers_7d":
            round(showers_7d, 1),

        "runoff_24":
            round(runoff_24, 2),

        "runoff_72":
            round(runoff_72, 2),

        "runoff_7d":
            round(runoff_7d, 2),

        "runoff_futuro_24":
            round(runoff_futuro_24, 2),

        "runoff_futuro_72":
            round(runoff_futuro_72, 2),

        "runoff_futuro_7d":
            round(runoff_futuro_7d, 2),

        "prob_lluvia": (
            round(prob_lluvia, 1)
            if np.isfinite(prob_lluvia)
            else np.nan
        ),

        "prob_lluvia_24": (
            round(prob_lluvia_24, 1)
            if np.isfinite(prob_lluvia_24)
            else np.nan
        ),

        "prob_lluvia_72": (
            round(prob_lluvia_72, 1)
            if np.isfinite(prob_lluvia_72)
            else np.nan
        ),

        "temp_min_24": (
            round(temp_min_24, 1)
            if np.isfinite(temp_min_24)
            else np.nan
        ),

        "temp_max_24": (
            round(temp_max_24, 1)
            if np.isfinite(temp_max_24)
            else np.nan
        ),

        "viento_max_24": (
            round(viento_max_24, 1)
            if np.isfinite(viento_max_24)
            else np.nan
        ),

        "rafaga_max_24": (
            round(rafaga_max_24, 1)
            if np.isfinite(rafaga_max_24)
            else np.nan
        ),

        "cape_max_24": (
            round(cape_max_24, 0)
            if np.isfinite(cape_max_24)
            else np.nan
        ),

        "et0_24":
            round(et0_24, 2),

        "codigo_tiempo": (
            int(codigo_actual)
            if np.isfinite(codigo_actual)
            else np.nan
        ),

        "descripcion_tiempo":
            descripcion_wmo(
                codigo_actual
            ),

        "tendencia":
            tendencia,

        "direccion_hidrologica":
            direccion_hidrologica,

        "velocidad_hidrologica":
            velocidad_hidrologica,

        "accion":
            accion_desde_nivel(
                nivel
            ),

        "condicion_critica":
            bool(condicion_critica),

        "actualizado":
            ahora().strftime(
                "%d/%m/%Y %H:%M"
            ),

        "horas_disponibles":
            int(n),
    }


# ============================================================
# OBTENER TODOS LOS NODOS
# ============================================================

def obtener_todos_los_nodos():

    datos_lista = obtener_datos_ecmwf()
    resultados = []

    if len(datos_lista) != len(NODOS):

        datos_lista = list(
            datos_lista
        )

        while len(datos_lista) < len(NODOS):
            datos_lista.append(
                {
                    "error":
                    "Respuesta incompleta de Open-Meteo"
                }
            )

        datos_lista = datos_lista[
            :len(NODOS)
        ]

    for nodo, datos in zip(
        NODOS,
        datos_lista,
    ):
        resultados.append(
            procesar_nodo(
                nodo,
                datos,
            )
        )

    return pd.DataFrame(
        resultados
    )


# ============================================================
# HISTORIAL
# ============================================================

def guardar_historial(df):

    if df.empty:
        return

    columnas = [
        "actualizado",
        "localidad",
        "provincia",
        "indice",
        "nivel",
        "temperatura",
        "humedad_relativa",
        "presion_msl",
        "viento",
        "rafaga",
        "cape",
        "humedad_suelo_promedio",
        "humedad_0_7",
        "humedad_7_28",
        "humedad_28_100",
        "humedad_100_255",
        "lluvia_24",
        "lluvia_72",
        "lluvia_7d",
        "lluvia_futura_24",
        "lluvia_futura_72",
        "lluvia_futura_7d",
        "rain_24",
        "rain_72",
        "rain_7d",
        "showers_24",
        "showers_72",
        "showers_7d",
        "runoff_24",
        "runoff_72",
        "runoff_7d",
        "prob_lluvia",
        "prob_lluvia_24",
        "prob_lluvia_72",
        "tendencia",
    ]

    columnas_validas = [
        columna
        for columna in columnas
        if columna in df.columns
    ]

    nuevo = df[
        columnas_validas
    ].copy()

    try:

        if HISTORY_FILE.exists():

            anterior = pd.read_csv(
                HISTORY_FILE
            )

            combinado = pd.concat(
                [
                    anterior,
                    nuevo,
                ],
                ignore_index=True,
            )

        else:
            combinado = nuevo

        if len(combinado) > 50000:
            combinado = combinado.tail(
                50000
            ).copy()

        combinado.to_csv(
            HISTORY_FILE,
            index=False,
        )

    except Exception:
        pass


# ============================================================
# TELEGRAM
# ============================================================

def obtener_secrets_telegram():

    try:

        token = str(
            st.secrets.get(
                "TELEGRAM_BOT_TOKEN",
                "",
            )
        ).strip()

        chat_id = str(
            st.secrets.get(
                "TELEGRAM_CHAT_ID",
                "",
            )
        ).strip()

        return token, chat_id

    except Exception:
        return "", ""


def cargar_estado_telegram():

    if "telegram_alert_state" in st.session_state:
        return dict(
            st.session_state[
                "telegram_alert_state"
            ]
        )

    if not STATE_FILE.exists():
        estado = {}

    else:

        try:

            with open(
                STATE_FILE,
                "r",
                encoding="utf-8",
            ) as archivo:

                estado = json.load(
                    archivo
                )

        except Exception:
            estado = {}

    st.session_state[
        "telegram_alert_state"
    ] = dict(estado)

    return dict(estado)


def guardar_estado_telegram(estado):

    estado = dict(estado)

    st.session_state[
        "telegram_alert_state"
    ] = estado

    try:

        with open(
            STATE_FILE,
            "w",
            encoding="utf-8",
        ) as archivo:

            json.dump(
                estado,
                archivo,
                ensure_ascii=False,
                indent=2,
            )

    except Exception:
        pass


def enviar_telegram(mensaje):

    token, chat_id = obtener_secrets_telegram()

    if not token or not chat_id:
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{token}/sendMessage"
    )

    payload = {
        "chat_id": chat_id,
        "text": mensaje,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=15,
        )

        response.raise_for_status()

        return True

    except Exception:
        return False


def generar_mensaje_alerta(row):

    emoji = emoji_nivel(
        row["nivel"]
    )

    prob = row.get(
        "prob_lluvia",
        np.nan,
    )

    prob_texto = (
        f"{prob:.0f}%"
        if pd.notna(prob)
        else "Sin dato"
    )

    # La humedad se convierte de m³/m³ a %
    # únicamente para presentación.
    humedad_suelo_pct = (
        row["humedad_suelo_promedio"] * 100
        if pd.notna(
            row["humedad_suelo_promedio"]
        )
        else np.nan
    )

    humedad_suelo_texto = (
        f"{humedad_suelo_pct:.1f}%"
        if np.isfinite(humedad_suelo_pct)
        else "Sin dato"
    )

    return f"""
{emoji} <b>ALERTA LITORAL AGRO</b>

<b>Nivel:</b> {row["nivel"]}
<b>Localidad:</b> {html.escape(str(row["localidad"]))}
<b>Provincia:</b> {html.escape(str(row["provincia"]))}

<b>Índice de riesgo:</b> {row["indice"]:.1f}/100

<b>Condiciones actuales:</b>
Temperatura: {row["temperatura"]:.1f} °C
Humedad relativa: {row["humedad_relativa"]:.0f}%
Viento: {row["viento"]:.1f} km/h ({row["direccion_viento_texto"]})
Ráfaga: {row["rafaga"]:.1f} km/h
CAPE: {row["cape"]:.0f} J/kg

<b>Humedad del suelo media:</b>
{humedad_suelo_texto}

<b>Precipitación antecedente:</b>
24 h: {row["lluvia_24"]:.1f} mm
72 h: {row["lluvia_72"]:.1f} mm
7 días: {row["lluvia_7d"]:.1f} mm

<b>Pronóstico:</b>
24 h: {row["lluvia_futura_24"]:.1f} mm
72 h: {row["lluvia_futura_72"]:.1f} mm
7 días: {row["lluvia_futura_7d"]:.1f} mm

<b>Runoff antecedente 72 h:</b>
{row["runoff_72"]:.2f} mm

<b>Probabilidad máxima de precipitación en 72 h:</b>
{prob_texto}

<b>Tendencia hidrológica:</b>
{row["tendencia"]}

<b>Acción recomendada:</b>
{row["accion"]}

<i>Actualizado: {row["actualizado"]}</i>

⚠️ Sistema experimental.
No reemplaza avisos oficiales.
""".strip()


def clave_alerta(row):

    contenido = (
        f'{row["localidad"]}|'
        f'{row["nivel"]}|'
        f'{round(float(row["indice"]) / 5)}'
    )

    return hashlib.md5(
        contenido.encode("utf-8")
    ).hexdigest()


def procesar_alertas_telegram(df):

    token, chat_id = obtener_secrets_telegram()

    resultado = {
        "configurado":
            bool(token and chat_id),

        "enviadas":
            0,

        "fallidas":
            0,

        "pendientes":
            0,
    }

    if (
        not token
        or not chat_id
        or df.empty
    ):
        return resultado

    estado = cargar_estado_telegram()

    for _, row in df.iterrows():

        clave_localidad = (
            f'{row["provincia"]}_'
            f'{row["localidad"]}'
        )

        if row["nivel"] not in [
            "ALTO",
            "MUY ALTO",
        ]:

            estado.pop(
                clave_localidad,
                None,
            )

            continue

        resultado["pendientes"] += 1

        clave = clave_alerta(
            row
        )

        if (
            estado.get(
                clave_localidad
            )
            == clave
        ):
            continue

        if enviar_telegram(
            generar_mensaje_alerta(row)
        ):

            estado[
                clave_localidad
            ] = clave

            resultado["enviadas"] += 1

        else:
            resultado["fallidas"] += 1

    guardar_estado_telegram(
        estado
    )

    return resultado


# ============================================================
# MAPA PLOTLY
# ============================================================

def crear_mapa(df):

    colores = {
        "BAJO": "green",
        "MEDIO": "yellow",
        "ALTO": "orange",
        "MUY ALTO": "red",
    }

    hover_data = {
        "provincia": True,
        "indice": True,
        "nivel": True,
        "temperatura": True,
        "humedad_relativa": True,
        "viento": True,
        "rafaga": True,
        "direccion_viento_texto": True,
        "humedad_suelo_promedio": True,
        "lluvia_24": True,
        "lluvia_72": True,
        "lluvia_7d": True,
        "lluvia_futura_24": True,
        "lluvia_futura_72": True,
        "runoff_72": True,
        "cape": True,
        "prob_lluvia": True,
        "tendencia": True,
        "lat": False,
        "lon": False,
    }

    fig = px.scatter_map(
        df,
        lat="lat",
        lon="lon",
        color="nivel",
        size="indice",
        size_max=24,
        hover_name="localidad",
        hover_data=hover_data,
        color_discrete_map=colores,
        center={
            "lat": MAP_CENTER_LAT,
            "lon": MAP_CENTER_LON,
        },
        zoom=MAP_ZOOM,
        height=680,
    )

    fig.update_layout(
        map_style="open-street-map",
        margin={
            "r": 0,
            "t": 0,
            "l": 0,
            "b": 0,
        },
        legend={
            "title": "Nivel de riesgo"
        },
        uirevision="litoral-agro",
    )

    return fig


# ============================================================
# WINDY
# ============================================================

WINDY_CENTER_LAT = -30.5
WINDY_CENTER_LON = -59.8
WINDY_ZOOM = 5.5


def construir_windy_url(
    overlay="wind"
):

    parametros = {
        "type": "map",
        "location": "coordinates",
        "metricRain": "default",
        "metricTemp": "default",
        "metricWind": "default",
        "zoom": WINDY_ZOOM,
        "overlay": overlay,
        "product": "ecmwf",
        "level": "surface",
        "lat": WINDY_CENTER_LAT,
        "lon": WINDY_CENTER_LON,
    }

    return (
        "https://embed.windy.com/embed.html?"
        + urlencode(parametros)
    )


def mostrar_windy(
    overlay="wind"
):

    windy_url = construir_windy_url(
        overlay
    )

    iframe = f'''
    <iframe
        width="100%"
        height="700"
        src="{html.escape(windy_url, quote=True)}"
        frameborder="0"
        style="border:0; border-radius:10px;"
        title="Windy — pronóstico meteorológico del Litoral"
        allowfullscreen>
    </iframe>
    '''

    components.html(
        iframe,
        height=720,
        scrolling=False,
    )


# ============================================================
# GOES-19
# ============================================================

GOES19_URL = (
    "https://www.goes.noaa.gov/sector_band.php"
    "?band=GEOCOLOR&length=24&sat=G19&sector=ssa"
)


def mostrar_goes19():

    iframe = f'''
    <iframe
        src="{html.escape(GOES19_URL, quote=True)}"
        width="100%"
        height="760"
        frameborder="0"
        style="border:0; border-radius:10px;"
        title="GOES-19 — Sudamérica Sur — NOAA"
        allowfullscreen>
    </iframe>
    '''

    components.html(
        iframe,
        height=780,
        scrolling=False,
    )


# ============================================================
# PRONÓSTICO EXTENDIDO
# ============================================================

def extraer_pronostico_diario(
    datos
):

    daily = (
        datos or {}
    ).get(
        "daily",
        {}
    ) or {}

    fechas = daily.get(
        "time",
        []
    )

    if not fechas:
        return pd.DataFrame()

    def arr(nombre):

        valores = daily.get(
            nombre,
            []
        )

        salida = list(valores)[
            :len(fechas)
        ]

        if len(salida) < len(fechas):

            salida += [
                np.nan
            ] * (
                len(fechas)
                - len(salida)
            )

        return salida

    tabla = pd.DataFrame({

        "Fecha":
            pd.to_datetime(
                fechas,
                errors="coerce",
            ),

        "Tiempo":
            arr("weather_code"),

        "Temp. mínima":
            arr("temperature_2m_min"),

        "Temp. máxima":
            arr("temperature_2m_max"),

        "Sensación mínima":
            arr("apparent_temperature_min"),

        "Sensación máxima":
            arr("apparent_temperature_max"),

        "Precipitación":
            arr("precipitation_sum"),

        "Lluvia":
            arr("rain_sum"),

        "Chaparrones":
            arr("showers_sum"),

        "Prob. precipitación":
            arr(
                "precipitation_probability_max"
            ),

        "Horas con precipitación":
            arr("precipitation_hours"),

        "Viento máximo":
            arr("wind_speed_10m_max"),

        "Ráfaga máxima":
            arr("wind_gusts_10m_max"),

        "Dirección viento":
            arr(
                "wind_direction_10m_dominant"
            ),

        "CAPE máximo":
            arr("cape_max"),

        "Radiación solar":
            arr(
                "shortwave_radiation_sum"
            ),

        "Horas de sol":
            arr("sunshine_duration"),

        "ET₀":
            arr(
                "et0_fao_evapotranspiration"
            ),
    })

    tabla["Tiempo"] = tabla[
        "Tiempo"
    ].apply(
        lambda x:
            descripcion_wmo(x)
            if pd.notna(x)
            else "Sin dato"
    )

    tabla[
        "Dirección viento"
    ] = tabla[
        "Dirección viento"
    ].apply(
        lambda x:
            f"{float(x):.0f}° "
            f"{direccion_compass(x)}"
            if pd.notna(x)
            else "Sin dato"
    )

    tabla[
        "Horas de sol"
    ] = (
        tabla["Horas de sol"]
        / 3600.0
    )

    fecha_hoy = pd.Timestamp(
        ahora().date()
    )

    tabla = tabla[
        tabla["Fecha"] >= fecha_hoy
    ].copy()

    return (
        tabla
        .dropna(subset=["Fecha"])
        .head(15)
        .reset_index(drop=True)
    )


# ============================================================
# TELEGRAM — ESTADO
# ============================================================

def mensaje_prueba_telegram():

    return (
        "📡 <b>ALERTA LITORAL AGRO — "
        "PRUEBA TELEGRAM</b>\n\n"
        "✅ El canal de alertas está correctamente conectado.\n"
        f"🕒 {ahora().strftime('%d/%m/%Y %H:%M:%S')}\n\n"
        "Sistema experimental."
    )


def estado_telegram_ui():

    token, chat_id = obtener_secrets_telegram()
    configurado = bool(
        token and chat_id
    )

    st.subheader(
        "📲 Alertas por Telegram"
    )

    if configurado:

        st.success(
            "🟢 Telegram configurado y disponible "
            "para enviar alertas."
        )

        st.caption(
            "Las alertas automáticas se generan para "
            "nodos que alcanzan ALTO o MUY ALTO y se "
            "evita repetir la misma alerta mientras no "
            "cambie el nivel/índice."
        )

        resultado = st.session_state.get(
            "telegram_resultado",
            {
                "enviadas": 0,
                "fallidas": 0,
                "pendientes": 0,
            },
        )

        ta, tb, tc = st.columns(3)

        with ta:
            st.metric(
                "Alertas enviadas",
                resultado.get(
                    "enviadas",
                    0,
                ),
            )

        with tb:
            st.metric(
                "Nodos actualmente ALTO/MUY ALTO",
                resultado.get(
                    "pendientes",
                    0,
                ),
            )

        with tc:
            st.metric(
                "Envíos fallidos",
                resultado.get(
                    "fallidas",
                    0,
                ),
            )

        if (
            resultado.get(
                "fallidas",
                0,
            ) > 0
        ):

            st.error(
                "Hay alertas que no pudieron enviarse "
                "por Telegram. Revisá el bot y las "
                "credenciales."
            )

        col_t1, col_t2 = st.columns(
            [1, 2]
        )

        with col_t1:

            probar = st.button(
                "📨 Enviar prueba a Telegram",
                use_container_width=True,
                key="probar_telegram",
            )

        with col_t2:

            st.caption(
                f"Chat configurado: {chat_id}"
            )

        if probar:

            if enviar_telegram(
                mensaje_prueba_telegram()
            ):

                st.success(
                    "✅ Mensaje de prueba enviado correctamente."
                )

            else:

                st.error(
                    "❌ No se pudo enviar el mensaje. "
                    "Revisá TELEGRAM_BOT_TOKEN, "
                    "TELEGRAM_CHAT_ID y que el bot tenga "
                    "acceso al chat."
                )

    else:

        st.warning(
            "⚪ Alertas Telegram no configuradas. "
            "Agregá TELEGRAM_BOT_TOKEN y "
            "TELEGRAM_CHAT_ID en Settings → Secrets."
        )


def mostrar_observaciones_api_smn():
    st.divider()
    st.header("🇦🇷 Observaciones en directo — Servicio Meteorológico Nacional")
    st.caption(
        "Datos observados por estaciones del SMN. Se muestran como una capa oficial independiente "
        "del índice experimental de Alerta Litoral Agro."
    )

    col_smn_a, col_smn_b = st.columns([1, 3])
    with col_smn_a:
        actualizar_smn = st.button(
            "📡 Actualizar SMN",
            use_container_width=True,
            key="actualizar_smn_live",
        )
    with col_smn_b:
        st.caption(
            "Consulta cada 5 minutos mientras la página está abierta; también podés actualizar manualmente."
        )

    if actualizar_smn:
        _pagina_publica_smn.clear()
        obtener_token_smn.clear()
        obtener_observaciones_smn.clear()
        obtener_alertas_smn.clear()

    with st.spinner("Consultando observaciones actuales del SMN..."):
        df_smn_raw, estado_smn = obtener_observaciones_smn()

    if not df_smn_raw.empty:
        df_smn_nodos = asociar_smn_a_nodos(df_smn_raw)

        ok_count = len(df_smn_nodos)
        st.success(f"🟢 SMN conectado · {ok_count} nodos asociados a la estación más cercana")

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("Estaciones SMN recibidas", len(df_smn_raw))
        with c2:
            st.metric("Nodos asociados", ok_count)
        with c3:
            st.metric("Temperatura media", f"{pd.to_numeric(df_smn_nodos['temperatura_smn'], errors='coerce').mean():.1f} °C")
        with c4:
            st.metric("Humedad media", f"{pd.to_numeric(df_smn_nodos['humedad_smn'], errors='coerce').mean():.0f} %")

        columnas_smn = {
            "localidad": "Localidad",
            "provincia": "Provincia",
            "estacion_smn": "Estación SMN",
            "distancia_km": "Distancia km",
            "temperatura_smn": "Temp. °C",
            "humedad_smn": "HR %",
            "presion_smn": "Presión hPa",
            "sensacion_smn": "ST °C",
            "visibilidad_smn": "Visibilidad",
            "viento_smn": "Viento km/h",
            "direccion_smn": "Dirección",
            "tiempo_smn": "Condición",
            "fecha_smn": "Hora SMN",
        }

        mostrar = df_smn_nodos[list(columnas_smn)].rename(columns=columnas_smn).copy()
        st.dataframe(mostrar, use_container_width=True, hide_index=True)

        st.caption(
            "Importante: estos son datos observados del SMN. No son sustituidos por ECMWF ni ingresan automáticamente "
            "al cálculo del índice de anegamiento. La distancia corresponde a la estación SMN más cercana al nodo."
        )


    else:
        st.warning(
            "🟡 No se pudieron obtener observaciones en directo del SMN en esta actualización. "
            f"{estado_smn}"
        )
        st.link_button("Abrir observaciones oficiales del SMN", "https://www.smn.gob.ar/observaciones")

    st.caption(
        "Fuente: Servicio Meteorológico Nacional · Observaciones oficiales · "
        "Esta capa no reemplaza los avisos oficiales ni modifica por sí sola el nivel de riesgo experimental."
    )



    st.download_button("⬇️ Descargar observaciones SMN CSV", df_smn_nodos.to_csv(index=False).encode("utf-8"),
                       file_name="observaciones_smn_litoral.csv", mime="text/csv", key="smn_csv") if not df_smn_raw.empty else None




# ============================================================
# SMN LITORAL — CAP/RSS PÚBLICO Y ARCHIVOS DE OBSERVACIONES
# ============================================================
SMN_CAP_FEED = "https://ssl.smn.gob.ar/CAP/AR.php"
SMN_OBS_DOWNLOAD = "https://ssl.smn.gob.ar/dpd/descarga_opendata.php"
PROVINCIAS_LITORAL = ("Santa Fe", "Corrientes", "Entre Ríos")
# Límites provinciales de Georef (fuente IGN), consultados el 08/10/2026.
# Se conservan sus coordenadas originales; no se usa un rectángulo provincial.
GEOMETRIAS_LITORAL = {'Santa Fe': {'type': 'Polygon', 'coordinates': [[[-61.04639, -27.998001], [-58.884005, -28.000001], [-58.893694, -28.035274], [-58.880822, -28.066825], [-58.945113, -28.133774], [-59.062413, -28.128109], [-59.087635, -28.171772], [-59.086207, -28.199493], [-59.107868, -28.222766], [-59.103458, -28.328816], [-59.076213, -28.365341], [-59.085977, -28.435966], [-59.048857, -28.493311], [-59.100008, -28.576399], [-59.105013, -28.633937], [-59.078601, -28.667487], [-59.160454, -28.794789], [-59.158075, -28.877356], [-59.210899, -28.977531], [-59.199715, -29.031545], [-59.257536, -29.089604], [-59.346766, -29.11435], [-59.407814, -29.221376], [-59.500633, -29.243518], [-59.519788, -29.343189], [-59.604283, -29.394743], [-59.579953, -29.459848], [-59.590185, -29.569779], [-59.613979, -29.668765], [-59.617549, -29.77584], [-59.659665, -29.843179], [-59.6047, -29.902666], [-59.588911, -29.976252], [-59.56639, -30.008076], [-59.616121, -30.11301], [-59.636584, -30.237456], [-59.712013, -30.276241], [-59.693453, -30.313123], [-59.666803, -30.327161], [-59.703439, -30.44261], [-59.65033, -30.500977], [-59.645856, -30.597037], [-59.678406, -30.646912], [-59.633301, -30.713928], [-59.699394, -30.769072], [-59.704867, -30.817376], [-59.734848, -30.876624], [-59.818176, -30.942505], [-59.87793, -31.056213], [-59.925443, -31.105052], [-59.963379, -31.119873], [-60.002686, -31.238586], [-60.105591, -31.289917], [-60.086792, -31.346985], [-60.138428, -31.413391], [-60.143494, -31.443604], [-60.212354, -31.467816], [-60.244001, -31.530395], [-60.332807, -31.547156], [-60.330665, -31.608546], [-60.46759, -31.698303], [-60.584078, -31.708484], [-60.638794, -31.742004], [-60.638567, -31.787482], [-60.675781, -31.811707], [-60.657227, -31.911316], [-60.721849, -31.966179], [-60.720659, -31.985215], [-60.684387, -32.012085], [-60.656006, -32.067078], [-60.700382, -32.149769], [-60.672607, -32.232605], [-60.679018, -32.319529], [-60.718993, -32.334044], [-60.711617, -32.393055], [-60.723816, -32.456116], [-60.776184, -32.507019], [-60.747375, -32.554321], [-60.752075, -32.622192], [-60.723514, -32.671689], [-60.716003, -32.773621], [-60.658875, -32.831177], [-60.681398, -32.871564], [-60.676208, -32.899597], [-60.620246, -32.947945], [-60.605493, -32.999817], [-60.493408, -33.131409], [-60.392055, -33.188984], [-60.335662, -33.176849], [-60.303063, -33.183511], [-60.284504, -33.207068], [-60.297829, -33.242284], [-60.252392, -33.275415], [-60.267107, -33.265935], [-60.272714, -33.263863], [-60.264612, -33.281316], [-60.323963, -33.350753], [-60.335288, -33.412105], [-60.410217, -33.467886], [-60.423841, -33.528429], [-60.413831, -33.545012], [-60.45738, -33.58236], [-60.462817, -33.634187], [-60.556949, -33.646598], [-60.61992, -33.628344], [-60.684582, -33.585157], [-60.781951, -33.578969], [-60.818249, -33.540293], [-60.87909, -33.554734], [-60.918108, -33.58837], [-60.934033, -33.654727], [-61.719753, -34.385555], [-62.883909, -34.38655], [-61.923249, -33.119581], [-61.888748, -33.10599], [-61.787668, -33.006202], [-61.791, -32.958407], [-61.771552, -32.901465], [-61.792243, -32.877228], [-61.77769, -32.830402], [-61.795699, -32.773966], [-61.828402, -32.754511], [-61.857988, -32.694552], [-61.907415, -32.695747], [-61.946626, -32.678481], [-61.946168, -32.651026], [-61.889495, -32.610798], [-61.891829, -32.590534], [-61.920591, -32.594278], [-61.926381, -32.584034], [-61.902477, -32.568619], [-61.905052, -32.552546], [-61.930573, -32.544506], [-61.912075, -32.526474], [-61.914512, -32.494664], [-62.011635, -32.347328], [-62.037889, -32.262914], [-62.145346, -32.193479], [-62.178877, -32.159422], [-62.19678, -32.11477], [-62.167797, -31.98601], [-62.188893, -31.922314], [-62.22349, -31.883934], [-62.218089, -31.740571], [-62.241683, -31.699677], [-62.123495, -31.605408], [-61.842175, -30.744316], [-62.130653, -30.480433], [-61.712045, -27.998552], [-61.04639, -27.998001]]]}, 'Entre Ríos': {'type': 'Polygon', 'coordinates': [[[-58.585342, -30.159018], [-58.496562, -30.207688], [-58.461072, -30.206935], [-58.451527, -30.224856], [-58.437047, -30.221675], [-58.369324, -30.264734], [-58.343726, -30.27125], [-58.28555, -30.244631], [-58.20739, -30.288077], [-58.19195, -30.335628], [-58.142282, -30.405238], [-58.072575, -30.425187], [-58.077347, -30.465802], [-58.057776, -30.496419], [-58.069126, -30.544908], [-58.037025, -30.5679], [-58.033522, -30.597695], [-57.980233, -30.629397], [-57.97485, -30.64734], [-57.894779, -30.669411], [-57.854835, -30.69465], [-57.843748, -30.721658], [-57.821523, -30.719403], [-57.797397, -30.782707], [-57.79979, -30.856979], [-57.824487, -30.91163], [-57.846007, -30.919908], [-57.893289, -30.907073], [-57.914899, -30.920564], [-57.861725, -31.043471], [-57.916687, -31.122593], [-57.91314, -31.212921], [-57.937054, -31.273264], [-57.980907, -31.318799], [-57.996945, -31.360511], [-57.979348, -31.388399], [-58.082394, -31.457827], [-58.078266, -31.488811], [-58.003795, -31.531906], [-57.980639, -31.581048], [-58.012854, -31.686734], [-58.035054, -31.718821], [-58.038148, -31.759064], [-58.086138, -31.821137], [-58.190237, -31.852978], [-58.20681, -31.871645], [-58.191344, -31.920347], [-58.168715, -31.932089], [-58.1383, -32.001843], [-58.143142, -32.060556], [-58.167404, -32.091382], [-58.183326, -32.1592], [-58.099882, -32.260012], [-58.100891, -32.303516], [-58.12299, -32.339999], [-58.183357, -32.376675], [-58.204588, -32.462003], [-58.187387, -32.532869], [-58.160583, -32.57078], [-58.145047, -32.671127], [-58.149629, -32.734879], [-58.119798, -32.815234], [-58.115576, -32.919768], [-58.087324, -32.958903], [-58.08418, -33.000123], [-58.098902, -33.020388], [-58.175963, -33.076893], [-58.361818, -33.120989], [-58.404368, -33.180052], [-58.409848, -33.235555], [-58.39047, -33.279121], [-58.397185, -33.313767], [-58.431714, -33.363329], [-58.44315, -33.44808], [-58.433904, -33.499159], [-58.445011, -33.541052], [-58.494713, -33.586549], [-58.434369, -33.721887], [-58.423586, -33.916318], [-58.344989, -34.038397], [-58.378925, -34.012226], [-58.463196, -34.001892], [-58.60717, -34.037871], [-58.654997, -34.035254], [-58.763739, -33.94531], [-58.830601, -33.955304], [-58.854872, -33.947214], [-58.89978, -33.890991], [-58.97789, -33.870357], [-58.992881, -33.841804], [-59.020482, -33.848942], [-59.055481, -33.832397], [-59.130676, -33.847717], [-59.173584, -33.807007], [-59.24231, -33.807495], [-59.254883, -33.785889], [-59.235413, -33.73999], [-59.254272, -33.72229], [-59.377686, -33.736511], [-59.437012, -33.729004], [-59.467773, -33.704285], [-59.479309, -33.652893], [-59.516737, -33.630834], [-59.593207, -33.689071], [-59.617418, -33.693741], [-59.636037, -33.642701], [-59.65891, -33.620037], [-59.787638, -33.603648], [-59.809381, -33.588509], [-59.80953, -33.551657], [-59.833949, -33.521259], [-59.894744, -33.510284], [-59.933006, -33.474891], [-59.999393, -33.470132], [-60.04484, -33.447527], [-60.111084, -33.372986], [-60.169617, -33.348999], [-60.200684, -33.292175], [-60.297829, -33.242284], [-60.284504, -33.207068], [-60.303063, -33.183511], [-60.335662, -33.176849], [-60.392055, -33.188984], [-60.424178, -33.173994], [-60.513184, -33.112915], [-60.60121, -33.006004], [-60.620246, -32.947945], [-60.676208, -32.899597], [-60.681398, -32.871564], [-60.658875, -32.831177], [-60.716003, -32.773621], [-60.723514, -32.671689], [-60.752075, -32.622192], [-60.747375, -32.554321], [-60.776184, -32.507019], [-60.723816, -32.456116], [-60.711617, -32.393055], [-60.718993, -32.334044], [-60.679018, -32.319529], [-60.672607, -32.232605], [-60.700382, -32.149769], [-60.658555, -32.080155], [-60.658081, -32.05072], [-60.723514, -31.977601], [-60.719707, -31.958803], [-60.671875, -31.930176], [-60.652893, -31.897095], [-60.675781, -31.811707], [-60.640947, -31.791765], [-60.642883, -31.751221], [-60.627622, -31.73323], [-60.568135, -31.704914], [-60.46759, -31.698303], [-60.330665, -31.608546], [-60.332807, -31.547156], [-60.244001, -31.530395], [-60.212354, -31.467816], [-60.143494, -31.443604], [-60.138428, -31.413391], [-60.086792, -31.346985], [-60.105591, -31.289917], [-60.013428, -31.250671], [-59.963379, -31.119873], [-59.902838, -31.084827], [-59.818176, -30.942505], [-59.734848, -30.876624], [-59.704867, -30.817376], [-59.699394, -30.769072], [-59.63269, -30.711304], [-59.678406, -30.646912], [-59.645856, -30.597037], [-59.65033, -30.500977], [-59.703439, -30.44261], [-59.671672, -30.338662], [-59.63682, -30.357754], [-59.615097, -30.419207], [-59.587769, -30.434822], [-59.557735, -30.33265], [-59.490212, -30.344331], [-59.464823, -30.33703], [-59.464028, -30.324323], [-59.44097, -30.329455], [-59.419647, -30.315736], [-59.347148, -30.324657], [-59.323732, -30.354244], [-59.298893, -30.346712], [-59.288267, -30.361884], [-59.273512, -30.352434], [-59.229703, -30.364175], [-59.192139, -30.329546], [-59.136475, -30.311574], [-59.136577, -30.286569], [-59.075638, -30.257267], [-59.081459, -30.249074], [-59.064251, -30.230818], [-58.965202, -30.21834], [-58.912017, -30.248214], [-58.878856, -30.246662], [-58.803753, -30.210834], [-58.748357, -30.213884], [-58.733757, -30.193413], [-58.680701, -30.175028], [-58.681665, -30.163581], [-58.647139, -30.163194], [-58.630015, -30.176859], [-58.585342, -30.159018]], [[-58.110345, -32.971331], [-58.107656, -33.001667], [-58.109117, -33.010383], [-58.11455, -33.024698], [-58.087384, -32.996769], [-58.08891, -32.957851], [-58.099723, -32.951575], [-58.101165, -32.956382], [-58.107781, -32.962243], [-58.110345, -32.971331]], [[-58.114038, -33.021062], [-58.108105, -33.001483], [-58.11574, -32.990354], [-58.128755, -33.025391], [-58.114038, -33.021062]], [[-58.114955, -32.974192], [-58.108589, -32.961814], [-58.101435, -32.954354], [-58.112485, -32.931158], [-58.114955, -32.974192]], [[-58.114383, -32.990141], [-58.111339, -32.980019], [-58.111976, -32.971667], [-58.118078, -32.982034], [-58.114383, -32.990141]], [[-58.125538, -33.028812], [-58.12983, -33.02886], [-58.133199, -33.034628], [-58.12549, -33.029678], [-58.125538, -33.028812]], [[-58.124477, -33.004665], [-58.126438, -33.007848], [-58.125742, -33.011396], [-58.124401, -33.008557], [-58.124477, -33.004665]]]}, 'Corrientes': {'type': 'MultiPolygon', 'coordinates': [[[[-57.011711, -27.487095], [-56.96963, -27.497928], [-56.942233, -27.558315], [-56.896587, -27.588813], [-56.845096, -27.6064], [-56.790758, -27.587161], [-56.743232, -27.605003], [-56.689199, -27.579028], [-56.677864, -27.563271], [-56.717054, -27.496937], [-56.718782, -27.464558], [-56.691029, -27.454341], [-56.649602, -27.460797], [-56.60355, -27.431417], [-56.556735, -27.456781], [-56.487046, -27.563677], [-56.42438, -27.602915], [-56.388842, -27.606019], [-56.356158, -27.58589], [-56.338367, -27.52403], [-56.293688, -27.494141], [-56.287181, -27.410932], [-56.237571, -27.402393], [-56.151922, -27.328739], [-56.086808, -27.30856], [-56.037043, -27.312912], [-56.024264, -27.324262], [-56.039903, -27.34809], [-56.028888, -27.382855], [-56.043216, -27.404992], [-56.036937, -27.415187], [-56.055421, -27.420687], [-56.054612, -27.450946], [-56.026635, -27.507277], [-55.9789, -27.5402], [-55.825537, -27.791656], [-55.846303, -27.832436], [-55.827123, -27.854597], [-55.831762, -27.904133], [-55.81359, -27.921022], [-55.824392, -27.948447], [-55.749427, -28.0271], [-55.758025, -28.06081], [-55.716718, -28.061268], [-55.71242, -28.088318], [-55.620237, -28.137544], [-55.632253, -28.176785], [-55.698109, -28.221033], [-55.782542, -28.253937], [-55.772689, -28.273936], [-55.732866, -28.285822], [-55.669942, -28.330763], [-55.691695, -28.417605], [-55.7175, -28.422158], [-55.732162, -28.384348], [-55.75745, -28.368588], [-55.878303, -28.361162], [-55.90303, -28.408104], [-55.882362, -28.477707], [-56.008646, -28.505999], [-56.025668, -28.535963], [-56.002271, -28.578489], [-56.007602, -28.604952], [-56.118573, -28.681681], [-56.185516, -28.770001], [-56.259155, -28.77832], [-56.29437, -28.797709], [-56.301949, -28.901111], [-56.409915, -28.977648], [-56.415482, -29.000697], [-56.398771, -29.025807], [-56.419525, -29.078981], [-56.507154, -29.092546], [-56.591503, -29.12431], [-56.605498, -29.162404], [-56.64541, -29.199299], [-56.649584, -29.272415], [-56.703944, -29.362244], [-56.767462, -29.382561], [-56.777172, -29.433488], [-56.813834, -29.48199], [-56.899923, -29.533387], [-56.957369, -29.589695], [-56.971205, -29.607736], [-56.970521, -29.641533], [-57.00587, -29.656121], [-57.037709, -29.698964], [-57.119526, -29.763675], [-57.169896, -29.781164], [-57.229949, -29.779164], [-57.289465, -29.825349], [-57.327833, -29.883264], [-57.325337, -29.95893], [-57.336775, -29.990385], [-57.408541, -30.032024], [-57.474243, -30.119768], [-57.54904, -30.166021], [-57.645072, -30.182408], [-57.651858, -30.201794], [-57.61524, -30.254466], [-57.64292, -30.341905], [-57.889609, -30.510684], [-57.887304, -30.587569], [-57.846552, -30.620536], [-57.843369, -30.65955], [-57.808973, -30.695269], [-57.809509, -30.730192], [-57.843748, -30.721658], [-57.854835, -30.69465], [-57.894779, -30.669411], [-57.97485, -30.64734], [-57.980233, -30.629397], [-58.033321, -30.597927], [-58.037025, -30.5679], [-58.069126, -30.544908], [-58.057776, -30.496419], [-58.077347, -30.465802], [-58.072575, -30.425187], [-58.142282, -30.405238], [-58.19195, -30.335628], [-58.20739, -30.288077], [-58.28555, -30.244631], [-58.343726, -30.27125], [-58.369324, -30.264734], [-58.437047, -30.221675], [-58.451527, -30.224856], [-58.462136, -30.206458], [-58.496562, -30.207688], [-58.553486, -30.18521], [-58.576499, -30.160041], [-58.630015, -30.176859], [-58.647139, -30.163194], [-58.681665, -30.163581], [-58.680701, -30.175028], [-58.733757, -30.193413], [-58.748357, -30.213884], [-58.803753, -30.210834], [-58.878856, -30.246662], [-58.912017, -30.248214], [-58.965202, -30.21834], [-59.064251, -30.230818], [-59.081459, -30.249074], [-59.075638, -30.257267], [-59.136577, -30.286569], [-59.136475, -30.311574], [-59.192139, -30.329546], [-59.229703, -30.364175], [-59.273512, -30.352434], [-59.288267, -30.361884], [-59.298893, -30.346712], [-59.323732, -30.354244], [-59.347148, -30.324657], [-59.419647, -30.315736], [-59.44097, -30.329455], [-59.464028, -30.324323], [-59.464823, -30.33703], [-59.490212, -30.344331], [-59.557735, -30.33265], [-59.556837, -30.352302], [-59.565845, -30.347612], [-59.574664, -30.370974], [-59.57244, -30.408418], [-59.587769, -30.434822], [-59.609614, -30.425138], [-59.63682, -30.357754], [-59.671672, -30.338662], [-59.668945, -30.32383], [-59.693453, -30.313123], [-59.712013, -30.276241], [-59.636584, -30.237456], [-59.616121, -30.11301], [-59.56639, -30.008076], [-59.588911, -29.976252], [-59.6047, -29.902666], [-59.659665, -29.843179], [-59.617549, -29.77584], [-59.613979, -29.668765], [-59.590185, -29.569779], [-59.579953, -29.459848], [-59.604283, -29.394743], [-59.519788, -29.343189], [-59.500633, -29.243518], [-59.407814, -29.221376], [-59.346766, -29.11435], [-59.257536, -29.089604], [-59.199715, -29.031545], [-59.210899, -28.977531], [-59.158075, -28.877356], [-59.160454, -28.794789], [-59.078601, -28.667487], [-59.105013, -28.633937], [-59.100008, -28.576399], [-59.048857, -28.493311], [-59.085977, -28.435966], [-59.076213, -28.365341], [-59.103458, -28.328816], [-59.107868, -28.222766], [-59.086207, -28.199493], [-59.087635, -28.171772], [-59.062413, -28.128109], [-58.952965, -28.137106], [-58.859734, -28.050318], [-58.857739, -28.011612], [-58.829262, -27.961873], [-58.844006, -27.880447], [-58.809708, -27.783407], [-58.818775, -27.681178], [-58.835497, -27.646641], [-58.882746, -27.608523], [-58.871019, -27.571271], [-58.881938, -27.499654], [-58.793519, -27.393176], [-58.768318, -27.376276], [-58.74556, -27.382238], [-58.668787, -27.359092], [-58.616488, -27.318632], [-58.515722, -27.291032], [-58.493509, -27.270649], [-58.372854, -27.290794], [-58.249489, -27.261669], [-58.191339, -27.280832], [-58.009875, -27.259381], [-57.970989, -27.274986], [-57.932307, -27.264108], [-57.873551, -27.272966], [-57.827241, -27.327545], [-57.805291, -27.339396], [-57.697013, -27.326884], [-57.609788, -27.395708], [-57.543506, -27.432967], [-57.507518, -27.444201], [-57.439005, -27.438282], [-57.326511, -27.409839], [-57.225409, -27.470378], [-57.120548, -27.493446], [-57.060203, -27.481265], [-57.011711, -27.487095]], [[-57.903349, -27.27382], [-57.910579, -27.274395], [-57.912872, -27.276645], [-57.89824, -27.277518], [-57.862389, -27.294362], [-57.903349, -27.27382]], [[-57.906716, -27.290551], [-57.895896, -27.288114], [-57.894726, -27.280316], [-57.918386, -27.282459], [-57.906716, -27.290551]], [[-57.952914, -27.276011], [-57.939427, -27.277289], [-57.944874, -27.286171], [-57.923928, -27.275242], [-57.952914, -27.276011]], [[-57.942278, -27.268918], [-57.926743, -27.268808], [-57.921479, -27.266981], [-57.930069, -27.265336], [-57.942278, -27.268918]], [[-57.921877, -27.272629], [-57.917728, -27.276162], [-57.913537, -27.273902], [-57.917933, -27.274272], [-57.921877, -27.272629]]], [[[-56.914826, -27.42354], [-56.871761, -27.430442], [-56.764027, -27.502565], [-56.7294, -27.501961], [-56.737973, -27.550577], [-56.840421, -27.596181], [-56.87158, -27.58346], [-56.887897, -27.554936], [-56.903059, -27.537397], [-56.912073, -27.531478], [-56.93278, -27.526781], [-56.943637, -27.517852], [-56.952438, -27.514768], [-56.970105, -27.492732], [-57.032066, -27.480648], [-56.914826, -27.42354]]], [[[-57.566611, -27.385118], [-57.502453, -27.403322], [-57.404192, -27.4166], [-57.4656, -27.431654], [-57.478021, -27.436616], [-57.484351, -27.435521], [-57.492187, -27.438701], [-57.593415, -27.392452], [-57.566611, -27.385118]]], [[[-56.705257, -27.563307], [-56.734235, -27.591428], [-56.783897, -27.582692], [-56.761369, -27.577493], [-56.756046, -27.57477], [-56.741997, -27.563506], [-56.740087, -27.563489], [-56.734045, -27.558731], [-56.716932, -27.519936], [-56.699665, -27.536523], [-56.705765, -27.557421], [-56.705257, -27.563307]]], [[[-56.956935, -27.516053], [-56.946156, -27.525062], [-56.925911, -27.535369], [-56.910732, -27.539054], [-56.894129, -27.551716], [-56.875988, -27.583573], [-56.900854, -27.569427], [-56.91961, -27.562565], [-56.932373, -27.535113], [-56.951989, -27.523312], [-56.956935, -27.516053]]], [[[-57.82595, -27.302602], [-57.775227, -27.314999], [-57.768948, -27.321109], [-57.819451, -27.318697], [-57.82595, -27.302602]]], [[[-56.699595, -27.578159], [-56.706684, -27.580773], [-56.693316, -27.545063], [-56.686068, -27.560886], [-56.699595, -27.578159]]], [[[-57.752547, -27.320163], [-57.74152, -27.323307], [-57.75212, -27.327925], [-57.782765, -27.32782], [-57.798088, -27.325021], [-57.794081, -27.32573], [-57.791364, -27.324007], [-57.787068, -27.325632], [-57.752547, -27.320163]]], [[[-58.223046, -27.256236], [-58.215346, -27.258624], [-58.204121, -27.25965], [-58.1967, -27.259137], [-58.190818, -27.262456], [-58.187877, -27.263104], [-58.184773, -27.263319], [-58.183694, -27.262942], [-58.183093, -27.261355], [-58.170484, -27.269217], [-58.211347, -27.263184], [-58.223046, -27.256236]]], [[[-58.303643, -27.262458], [-58.31784, -27.2725], [-58.335558, -27.273423], [-58.321649, -27.262574], [-58.303643, -27.262458]]], [[[-58.150203, -27.257994], [-58.138566, -27.265546], [-58.178002, -27.259267], [-58.156434, -27.255864], [-58.150203, -27.257994]]], [[[-56.35707, -27.517885], [-56.36473, -27.533633], [-56.3712, -27.538533], [-56.367441, -27.520406], [-56.35707, -27.517885]]], [[[-56.944729, -27.53584], [-56.930999, -27.548674], [-56.930017, -27.557105], [-56.941517, -27.546119], [-56.944729, -27.53584]]], [[[-58.224674, -27.2549], [-58.189397, -27.257697], [-58.183839, -27.261834], [-58.196808, -27.258867], [-58.214726, -27.258435], [-58.224674, -27.2549]]], [[[-57.82147, -27.31831], [-57.837282, -27.307302], [-57.838822, -27.297833], [-57.827021, -27.309075], [-57.82147, -27.31831]]], [[[-56.928489, -27.530362], [-56.911989, -27.5329], [-56.907881, -27.538645], [-56.9246, -27.534686], [-56.928489, -27.530362]]], [[[-57.448871, -27.432221], [-57.451574, -27.436582], [-57.472951, -27.438564], [-57.463196, -27.433926], [-57.448871, -27.432221]]], [[[-58.291622, -27.266431], [-58.301328, -27.271593], [-58.312138, -27.27169], [-58.300387, -27.265814], [-58.291622, -27.266431]]], [[[-57.814601, -27.320683], [-57.787826, -27.320612], [-57.78413, -27.322537], [-57.786963, -27.325336], [-57.790252, -27.323727], [-57.792176, -27.324077], [-57.79459, -27.325266], [-57.818483, -27.320124], [-57.814601, -27.320683]]], [[[-57.221511, -27.464203], [-57.232337, -27.463443], [-57.239298, -27.456655], [-57.232975, -27.457467], [-57.221511, -27.464203]]], [[[-56.921087, -27.563079], [-56.902184, -27.57029], [-56.899074, -27.573137], [-56.9128, -27.570917], [-56.921087, -27.563079]]], [[[-58.423271, -27.270154], [-58.420368, -27.279649], [-58.422009, -27.280627], [-58.431126, -27.268229], [-58.423271, -27.270154]]], [[[-58.332672, -27.266267], [-58.337693, -27.27377], [-58.345599, -27.274808], [-58.341444, -27.269441], [-58.332672, -27.266267]]], [[[-56.938793, -27.524839], [-56.947813, -27.522284], [-56.954815, -27.514896], [-56.94276, -27.520435], [-56.938793, -27.524839]]], [[[-56.329329, -27.501225], [-56.343201, -27.508499], [-56.3443, -27.507991], [-56.320702, -27.495473], [-56.329329, -27.501225]]], [[[-56.77472, -27.576272], [-56.772225, -27.572203], [-56.75618, -27.569443], [-56.75872, -27.571362], [-56.764043, -27.572065], [-56.77472, -27.576272]]], [[[-58.232783, -27.259818], [-58.223997, -27.262112], [-58.244081, -27.253966], [-58.235813, -27.258693], [-58.232783, -27.259818]]], [[[-58.085893, -27.262054], [-58.093676, -27.266257], [-58.100041, -27.266305], [-58.090524, -27.259874], [-58.085893, -27.262054]]], [[[-56.754009, -27.57028], [-56.760082, -27.575118], [-56.772225, -27.576443], [-56.767165, -27.573407], [-56.754009, -27.57028]]], [[[-57.817783, -27.324951], [-57.806309, -27.325546], [-57.802601, -27.327855], [-57.820477, -27.324566], [-57.817783, -27.324951]]], [[[-56.71627, -27.579284], [-56.718509, -27.585508], [-56.732994, -27.592678], [-56.722726, -27.58574], [-56.71627, -27.579284]]], [[[-57.784024, -27.330417], [-57.772207, -27.329422], [-57.769098, -27.330169], [-57.779173, -27.333154], [-57.784833, -27.332221], [-57.786823, -27.328552], [-57.784024, -27.330417]]], [[[-58.197323, -27.269238], [-58.187682, -27.269887], [-58.184533, -27.272516], [-58.200732, -27.268945], [-58.197323, -27.269238]]], [[[-56.731839, -27.532994], [-56.731179, -27.527566], [-56.726104, -27.519706], [-56.725842, -27.525369], [-56.731839, -27.532994]]], [[[-56.759079, -27.591047], [-56.753385, -27.592099], [-56.749424, -27.593461], [-56.749115, -27.593956], [-56.759079, -27.591047]]], [[[-56.939771, -27.535489], [-56.937078, -27.537876], [-56.933892, -27.544083], [-56.946785, -27.532692], [-56.939771, -27.535489]]], [[[-56.701363, -27.55255], [-56.70123, -27.55839], [-56.703737, -27.563565], [-56.705029, -27.558398], [-56.701363, -27.55255]]], [[[-57.543052, -27.424431], [-57.539458, -27.427787], [-57.553169, -27.419738], [-57.551941, -27.419533], [-57.543052, -27.424431]]], [[[-56.730764, -27.542449], [-56.731921, -27.536602], [-56.729446, -27.533384], [-56.727606, -27.537425], [-56.730764, -27.542449]]], [[[-56.874278, -27.582497], [-56.863582, -27.58762], [-56.862235, -27.588762], [-56.871965, -27.586544], [-56.874278, -27.582497]]], [[[-58.329729, -27.263728], [-58.335154, -27.264016], [-58.324073, -27.261362], [-58.324996, -27.262054], [-58.329729, -27.263728]]], [[[-58.293429, -27.256285], [-58.288235, -27.25622], [-58.286417, -27.256577], [-58.291384, -27.260115], [-58.299532, -27.255636], [-58.293429, -27.256285]]], [[[-58.611951, -27.316852], [-58.613093, -27.313283], [-58.607097, -27.312854], [-58.609024, -27.316138], [-58.611951, -27.316852]]], [[[-56.861615, -27.592128], [-56.855197, -27.592904], [-56.855711, -27.594831], [-56.866247, -27.591876], [-56.861615, -27.592128]]], [[[-57.444129, -27.429413], [-57.450031, -27.431948], [-57.454806, -27.430925], [-57.44205, -27.427514], [-57.444129, -27.429413]]], [[[-58.165502, -27.274159], [-58.168732, -27.274042], [-58.169783, -27.2698], [-58.164996, -27.271396], [-58.165502, -27.274159]]], [[[-58.357731, -27.27492], [-58.36128, -27.274703], [-58.349075, -27.271803], [-58.351109, -27.274011], [-58.357731, -27.27492]]], [[[-58.192097, -27.27771], [-58.19411, -27.279431], [-58.19979, -27.277061], [-58.197778, -27.275957], [-58.192097, -27.27771]]], [[[-57.481254, -27.437477], [-57.479921, -27.438493], [-57.48784, -27.439153], [-57.484118, -27.436117], [-57.481254, -27.437477]]], [[[-56.781625, -27.572992], [-56.788961, -27.576244], [-56.789467, -27.575666], [-56.784516, -27.571655], [-56.781625, -27.572992]]], [[[-56.710336, -27.58133], [-56.711821, -27.581949], [-56.706684, -27.575079], [-56.706065, -27.57774], [-56.710336, -27.58133]]], [[[-57.541051, -27.429531], [-57.542869, -27.429697], [-57.548394, -27.426491], [-57.541232, -27.427582], [-57.541051, -27.429531]]], [[[-56.903637, -27.541573], [-56.905886, -27.540545], [-56.907363, -27.535663], [-56.903509, -27.537847], [-56.903637, -27.541573]]], [[[-56.765927, -27.569867], [-56.768081, -27.569071], [-56.761047, -27.567587], [-56.762446, -27.568803], [-56.765927, -27.569867]]], [[[-58.420597, -27.280978], [-58.413948, -27.281097], [-58.41312, -27.281593], [-58.418207, -27.283202], [-58.420597, -27.280978]]], [[[-56.945215, -27.530746], [-56.93928, -27.532507], [-56.937885, -27.534432], [-56.945622, -27.531965], [-56.945215, -27.530746]]], [[[-56.739175, -27.562097], [-56.741974, -27.561672], [-56.736843, -27.558564], [-56.736126, -27.560066], [-56.739175, -27.562097]]], [[[-57.563128, -27.410461], [-57.557194, -27.414144], [-57.554601, -27.416941], [-57.56456, -27.410529], [-57.563128, -27.410461]]], [[[-58.437706, -27.267721], [-58.436534, -27.270747], [-58.441708, -27.267037], [-58.441058, -27.265638], [-58.437706, -27.267721]]], [[[-57.610778, -27.383325], [-57.610586, -27.381637], [-57.60579, -27.381215], [-57.606365, -27.382251], [-57.610778, -27.383325]]], [[[-58.457404, -27.272953], [-58.4626, -27.271824], [-58.463321, -27.270848], [-58.45788, -27.2717], [-58.457404, -27.272953]]], [[[-56.689817, -27.569795], [-56.690297, -27.572176], [-56.693674, -27.574418], [-56.690861, -27.570039], [-56.689817, -27.569795]]], [[[-56.93127, -27.527496], [-56.928909, -27.527833], [-56.927512, -27.528749], [-56.934065, -27.527544], [-56.93127, -27.527496]]], [[[-58.262908, -27.259689], [-58.263817, -27.261247], [-58.267713, -27.26129], [-58.265635, -27.259689], [-58.262908, -27.259689]]], [[[-57.476975, -27.439656], [-57.47868, -27.439997], [-57.479294, -27.438837], [-57.475474, -27.438087], [-57.476975, -27.439656]]]]}}
ESTACIONES_LITORAL = {
    "CERES AERO": "Santa Fe", "RAFAELA AERO": "Santa Fe",
    "RECONQUISTA AERO": "Santa Fe", "ROSARIO AERO": "Santa Fe",
    "SAUCE VIEJO AERO": "Santa Fe",
    "SUNCHALES AERO": "Santa Fe", "VENADO TUERTO AERO": "Santa Fe",
    "CORRIENTES AERO": "Corrientes",
    "ITUZAINGO": "Corrientes", "MERCEDES AERO (CTES)": "Corrientes",
    "MONTE CASEROS AERO": "Corrientes", "PASO DE LOS LIBRES AERO": "Corrientes",
    "CONCORDIA AERO": "Entre Ríos", "GUALEGUAYCHU AERO": "Entre Ríos",
    "PARANA AERO": "Entre Ríos",
}
CAP_NS = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}


def _normalizar_nombre_smn(texto):
    return " ".join(unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode().upper().split())


def _fecha_cap(texto):
    try:
        fecha = datetime.fromisoformat(str(texto).strip().replace("Z", "+00:00"))
        return fecha.astimezone(timezone.utc) if fecha.tzinfo else None
    except (ValueError, TypeError):
        return None


def _url_cap_oficial(url, base=SMN_CAP_FEED):
    candidato = urljoin(base, str(url).strip())
    p = urlparse(candidato)
    if (p.scheme == "https" and p.hostname == "ssl.smn.gob.ar"
            and p.path.startswith(("/feeds/CAP/", "/CAP/"))
            and p.path.lower().endswith(".xml") and ".." not in unquote(p.path)):
        return candidato
    return None


def _xml_seguro(contenido):
    if len(contenido) > 3_000_000 or b"<!DOCTYPE" in contenido.upper() or b"<!ENTITY" in contenido.upper():
        raise ValueError("Documento XML no admitido")
    return ET.fromstring(contenido)


def _poligono_cap(texto):
    puntos = []
    try:
        for par in str(texto).split():
            lat, lon = map(float, par.split(","))
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                return []
            puntos.append((lon, lat))
    except ValueError:
        return []
    if len(puntos) < 3:
        return []
    if puntos[0] != puntos[-1]:
        puntos.append(puntos[0])
    return puntos


def _anillos_provincia(geo):
    return [geo["coordinates"]] if geo["type"] == "Polygon" else geo["coordinates"]


def _punto_en_anillo(punto, anillo):
    x, y = punto
    dentro = False
    for (x1, y1), (x2, y2) in zip(anillo, anillo[1:]):
        if (y1 > y) != (y2 > y):
            corte = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < corte:
                dentro = not dentro
    return dentro


def _punto_en_poligono(punto, anillos):
    return _punto_en_anillo(punto, anillos[0]) and not any(
        _punto_en_anillo(punto, hole) for hole in anillos[1:]
    )


def _poligonos_se_cruzan(alerta, provincia):
    """Prueba de intersección en lon/lat: interiores y cruces de segmentos."""
    exterior = provincia[0]
    ax = [p[0] for p in alerta]; ay = [p[1] for p in alerta]
    bx = [p[0] for p in exterior]; by = [p[1] for p in exterior]
    if max(ax) < min(bx) or min(ax) > max(bx) or max(ay) < min(by) or min(ay) > max(by):
        return False
    if any(_punto_en_poligono(p, provincia) for p in alerta[:-1]):
        return True
    if any(_punto_en_anillo(p, alerta) for p in exterior[:-1]):
        return True
    # Cruces interiores; el mero contacto de límites no se cuenta como cobertura.
    a = np.asarray(alerta, dtype=float)
    b = np.asarray(exterior, dtype=float)
    p, r = a[:-1], np.diff(a, axis=0)
    q, t = b[:-1], np.diff(b, axis=0)
    cross = r[:, None, 0] * t[None, :, 1] - r[:, None, 1] * t[None, :, 0]
    qp = q[None, :, :] - p[:, None, :]
    safe = np.where(np.abs(cross) > 1e-12, cross, np.nan)
    u = (qp[:, :, 0] * t[None, :, 1] - qp[:, :, 1] * t[None, :, 0]) / safe
    v = (qp[:, :, 0] * r[:, None, 1] - qp[:, :, 1] * r[:, None, 0]) / safe
    return bool(np.any((u > 0) & (u < 1) & (v > 0) & (v < 1)))


def _provincias_cap(areas, poligonos):
    provincias = set()
    nombres = _normalizar_nombre_smn(" ".join(areas))
    for provincia, geometria in GEOMETRIAS_LITORAL.items():
        if _normalizar_nombre_smn(provincia) in nombres:
            provincias.add(provincia)
        elif any(_poligonos_se_cruzan(p, rings) for p in poligonos for rings in _anillos_provincia(geometria)):
            provincias.add(provincia)
    return sorted(provincias)


def parsear_cap_smn(contenido, url):
    root = _xml_seguro(contenido)
    if root.tag != "{urn:oasis:names:tc:emergency:cap:1.2}alert":
        raise ValueError("La respuesta no es una alerta CAP")
    def txt(elemento, nombre):
        return (elemento.findtext("cap:" + nombre, default="", namespaces=CAP_NS) or "").strip()
    referencias = [r.split(",")[1] for r in txt(root, "references").split() if len(r.split(",")) >= 2]
    filas = []
    for info in root.findall("cap:info", CAP_NS):
        idioma = txt(info, "language")
        if idioma and not idioma.lower().startswith("es"):
            continue
        areas = [txt(a, "areaDesc") for a in info.findall("cap:area", CAP_NS)]
        poligonos = [p for node in info.findall("cap:area/cap:polygon", CAP_NS)
                     if (p := _poligono_cap(node.text))]
        provincias = _provincias_cap(areas, poligonos)
        nodos = [n["localidad"] for n in NODOS if any(
            _punto_en_anillo((n["lon"], n["lat"]), p) for p in poligonos)]
        severidad = txt(info, "severity")
        filas.append({
            "id": txt(root, "identifier"), "emision": _fecha_cap(txt(root, "sent")),
            "status": txt(root, "status"), "tipo_mensaje": txt(root, "msgType"),
            "referencias": referencias, "fenomeno": txt(info, "event"),
            "titulo": txt(info, "headline"), "severidad_cap": severidad,
            "urgencia": txt(info, "urgency"), "certeza": txt(info, "certainty"),
            "desde": _fecha_cap(txt(info, "onset") or txt(info, "effective")),
            "hasta": _fecha_cap(txt(info, "expires")),
            "descripcion": txt(info, "description"), "recomendaciones": txt(info, "instruction"),
            "areas": [a for a in areas if a], "poligonos": poligonos,
            "provincias": provincias, "nodos": nodos, "url": url,
            "producto": "Aviso a muy corto plazo" if "/avisocortoplazo/" in url else "Alerta / advertencia",
        })
    # Las cancelaciones pueden omitir el bloque info y aun así deben invalidar referencias.
    return {"id": txt(root, "identifier"), "tipo_mensaje": txt(root, "msgType"),
            "status": txt(root, "status"), "referencias": referencias, "filas": filas}


@st.cache_data(ttl=300, max_entries=200, show_spinner=False)
def descargar_cap_smn(url):
    if _url_cap_oficial(url) != url:
        return None, "Enlace CAP no admitido."
    try:
        r = requests.get(url, headers={"User-Agent": "Alerta-Litoral-Agro/3.10.2"}, timeout=(5, 20))
        r.raise_for_status()
        return parsear_cap_smn(r.content, url), ""
    except (requests.exceptions.RequestException, ET.ParseError, ValueError, TypeError):
        return None, "No se pudo leer un documento oficial."


@st.cache_data(ttl=300, show_spinner=False)
def obtener_cap_litoral():
    consultado = datetime.now(timezone.utc)
    try:
        response = requests.get(SMN_CAP_FEED, headers={"User-Agent": "Alerta-Litoral-Agro/3.10.2"}, timeout=(5, 25))
        response.raise_for_status()
        root = _xml_seguro(response.content)
        channel = root.find("channel")
        if channel is None:
            raise ValueError("Respuesta sin canal RSS")
        fecha_feed = None
        for tag in ("lastBuildDate", "pubDate"):
            try:
                value = channel.findtext(tag)
                if value:
                    fecha_feed = parsedate_to_datetime(value).astimezone(timezone.utc)
                    break
            except (ValueError, TypeError):
                pass
        links = []
        invalidos = 0
        for item in channel.findall("item"):
            url = _url_cap_oficial(item.findtext("link", default=""))
            if url:
                if url not in links:
                    links.append(url)
            else:
                invalidos += 1
        limite = 160
        seleccion = links[:limite]
        with ThreadPoolExecutor(max_workers=8) as pool:
            resultados = list(pool.map(descargar_cap_smn, seleccion))
        docs = [d for d, _ in resultados if d]
        reemplazados = set(r for d in docs if d["status"] == "Actual" for r in d["referencias"])
        filas = []
        for d in docs:
            if d["id"] in reemplazados or d["tipo_mensaje"] == "Cancel":
                continue
            for f in d["filas"]:
                if f["status"] == "Actual" and f["provincias"]:
                    filas.append(f)
        # Un documento con varias áreas se conserva una sola vez por bloque info.
        unicos = {(f["id"], f["desde"], f["hasta"], f["fenomeno"], tuple(f["provincias"])): f for f in filas}
        return {"filas": list(unicos.values()), "fecha_feed": fecha_feed, "consultado": consultado,
                "total": len(links), "leidos": len(docs),
                "fallos": sum(d is None for d, _ in resultados) + invalidos,
                "truncado": len(links) > limite, "error": ""}
    except (requests.exceptions.RequestException, ET.ParseError, ValueError):
        return {"filas": [], "fecha_feed": None, "consultado": consultado,
                "total": 0, "leidos": 0, "fallos": 0, "truncado": False,
                "error": "No se pudo consultar el canal público de alertas del SMN."}


def estado_vigencia_cap(fila, instante):
    if fila["hasta"] is None:
        return "Vigencia sin confirmar"
    if fila["hasta"] <= instante:
        return "Finalizado"
    desde = fila["desde"] or fila["emision"]
    if desde is None:
        return "Vigencia sin confirmar"
    return "Previsto" if desde > instante else "Vigente"


def _hora_smn(fecha):
    return fecha.astimezone(TZ).strftime("%d/%m/%Y %H:%M ART") if fecha else "Sin fecha publicada"


def mostrar_alertas_cap(provincias):
    with st.spinner("Consultando alertas y avisos oficiales del Litoral..."):
        resultado = obtener_cap_litoral()
    st.caption("Consulta de datos: " + _hora_smn(resultado["consultado"]))
    if resultado["error"]:
        st.warning(resultado["error"])
        st.link_button("Consultar alertas en el SMN", "https://www.smn.gob.ar/alertas", key="cap_fallback")
        return
    feed_date = resultado["fecha_feed"]
    antiguedad = (datetime.now(timezone.utc) - feed_date).total_seconds() / 3600 if feed_date else None
    incompleto = resultado["fallos"] > 0 or resultado["truncado"]
    desactualizado = antiguedad is None or antiguedad > 24 or antiguedad < -1
    st.caption("Publicación del canal SMN: " + _hora_smn(feed_date)
               + f" · Documentos leídos: {resultado['leidos']} de {resultado['total']}")
    if desactualizado:
        st.warning("La actualidad del canal no está confirmada. Los avisos recibidos pueden estar incompletos o desactualizados.")
    if incompleto:
        st.warning("La consulta fue parcial: algunos documentos no pudieron leerse. No permite confirmar ausencia de alertas.")
    instante = datetime.now(timezone.utc)
    filas = [dict(f, vigencia=estado_vigencia_cap(f, instante)) for f in resultado["filas"]
             if set(f["provincias"]) & set(provincias)]
    ver_hist = st.checkbox("Incluir avisos finalizados", key="cap_historial")
    visibles = [f for f in filas if ver_hist or f["vigencia"] != "Finalizado"]
    columnas = st.columns(len(provincias)) if provincias else []
    for provincia, col in zip(provincias, columnas):
        relevantes = [f for f in filas if provincia in f["provincias"] and f["vigencia"] in ("Vigente", "Previsto")]
        col.metric(provincia, f"{len(relevantes)} avisos publicados")
        if not relevantes:
            col.caption("Cobertura por confirmar" if (incompleto or desactualizado) else "Sin avisos vigentes o previstos en el canal consultado")
    st.caption("La provincia indicada puede estar afectada solo parcialmente. Revisá el área y la vigencia de cada aviso.")
    if not visibles:
        st.info("No se encontraron avisos vigentes o previstos del Litoral en los documentos recibidos. Consultá el SMN para confirmar la situación oficial.")
        return
    tabla = pd.DataFrame([{
        "Provincia": ", ".join(f["provincias"]), "Producto": f["producto"],
        "Fenómeno": f["fenomeno"], "Severidad CAP": f["severidad_cap"],
        "Estado": f["vigencia"], "Desde": _hora_smn(f["desde"] or f["emision"]),
        "Hasta": _hora_smn(f["hasta"]), "Emisión": _hora_smn(f["emision"]),
        "Nodos dentro del área": ", ".join(f["nodos"]) or "Ninguno de los 18 nodos",
    } for f in visibles])
    st.dataframe(tabla, hide_index=True, use_container_width=True)
    st.caption("Severidad CAP: Moderate = moderada; Severe = severa; Extreme = extrema. Es la clasificación publicada en cada documento.")
    elegido = st.selectbox("Detalle del aviso", range(len(visibles)),
        format_func=lambda i: f"{visibles[i]['fenomeno']} · {', '.join(visibles[i]['provincias'])} · {_hora_smn(visibles[i]['desde'] or visibles[i]['emision'])}",
        key="cap_detalle")
    f = visibles[elegido]
    st.subheader(f["titulo"] or f["fenomeno"])
    st.write(f["descripcion"] or "Descripción no publicada.")
    if f["areas"]:
        st.write("**Zonas publicadas por el SMN:** " + " · ".join(f["areas"]))
    else:
        st.caption("El SMN publicó la zona como polígono, sin descripción textual. El mapa muestra su cobertura.")
    if f["recomendaciones"]:
        st.markdown("**Recomendaciones del SMN**")
        st.write(f["recomendaciones"])
    if f["poligonos"]:
        fig = go.Figure()
        for provincia in provincias:
            for rings in _anillos_provincia(GEOMETRIAS_LITORAL[provincia]):
                ring = rings[0]
                fig.add_trace(go.Scattermap(lon=[p[0] for p in ring], lat=[p[1] for p in ring],
                    mode="lines", line={"color": "#64748b", "width": 1}, name=provincia,
                    showlegend=False, hoverinfo="skip"))
        colores = {"Moderate": "#eab308", "Severe": "#f97316", "Extreme": "#ef4444"}
        color = colores.get(f["severidad_cap"], "#6366f1")
        for ring in f["poligonos"]:
            fig.add_trace(go.Scattermap(lon=[p[0] for p in ring], lat=[p[1] for p in ring],
                mode="lines", fill="toself", line={"color": color, "width": 2},
                name=f["fenomeno"], showlegend=False))
        fig.update_layout(map={"style": "open-street-map", "center": {"lat": -30.5, "lon": -59.8}, "zoom": 5},
                          height=450, margin={"t": 0, "l": 0, "r": 0, "b": 0})
        st.plotly_chart(fig, use_container_width=True)
    st.link_button("Abrir documento oficial del aviso", f["url"], key="cap_documento")
    st.download_button("⬇️ Descargar avisos SMN CSV", tabla.to_csv(index=False).encode("utf-8"),
                       file_name="avisos_smn_litoral.csv", mime="text/csv", key="cap_csv")


def parsear_observaciones_diarias(contenido):
    try:
        texto = contenido.decode("utf-8")
    except UnicodeDecodeError:
        texto = contenido.decode("latin-1")
    if "FECHA" not in texto[:150] or "NOMBRE" not in texto[:150]:
        return pd.DataFrame()
    filas = []
    specs = ((14, 20), (20, 25), (25, 33), (33, 38), (38, 43))
    for linea in texto.splitlines()[2:]:
        if not re.match(r"^\d{8}", linea):
            continue
        nombre = _normalizar_nombre_smn(linea[43:].strip())
        provincia = ESTACIONES_LITORAL.get(nombre)
        if provincia is None:
            continue
        try:
            fecha = datetime.strptime(linea[:8], "%d%m%Y").replace(tzinfo=TZ)
            hora = int(linea[8:14].strip())
            if not 0 <= hora <= 23:
                continue
            fecha += timedelta(hours=hora)
        except ValueError:
            continue
        valores = [pd.to_numeric(linea[a:b].strip().replace(",", "."), errors="coerce") for a, b in specs]
        filas.append({"Provincia": provincia, "Estación SMN": nombre, "fecha": fecha,
                      "Temperatura °C": valores[0], "HR %": valores[1], "Presión hPa": valores[2],
                      "Dirección °": valores[3], "Viento km/h": valores[4]})
    return pd.DataFrame(filas)


@st.cache_data(ttl=900, show_spinner=False)
def obtener_archivo_observaciones_smn(fecha_hoy):
    dia = datetime.strptime(fecha_hoy, "%Y%m%d").date()
    for atraso in range(3):
        target = dia - timedelta(days=atraso)
        filename = f"observaciones/datohorario{target:%Y%m%d}.txt"
        try:
            r = requests.get(SMN_OBS_DOWNLOAD, params={"file": filename}, timeout=(5, 20))
            r.raise_for_status()
            df = parsear_observaciones_diarias(r.content)
            if not df.empty and all(f.date() == target for f in df["fecha"]):
                return df, r.url, target.isoformat()
        except requests.exceptions.RequestException:
            continue
    return pd.DataFrame(), "", ""


def mostrar_archivo_observaciones(provincias):
    st.subheader("🌡️ Últimas observaciones publicadas del Litoral")
    st.caption("Archivos horarios oficiales de publicación diaria: pueden corresponder al día anterior. La fecha del dato aparece en cada fila.")
    with st.spinner("Buscando el último archivo horario publicado por el SMN..."):
        datos, fuente, fecha_archivo = obtener_archivo_observaciones_smn(ahora().strftime("%Y%m%d"))
    if datos.empty:
        st.warning("No se pudo descargar un archivo horario utilizable del SMN en los últimos tres días.")
        st.link_button("Abrir descarga oficial de datos", "https://www.smn.gob.ar/descarga-de-datos", key="obs_archivo_fallback")
        return
    datos = datos[datos["Provincia"].isin(provincias)].copy()
    if datos.empty:
        st.info("El archivo no contiene estaciones de las provincias seleccionadas.")
        return
    datos = datos.sort_values("fecha")
    ultimas = datos.groupby("Estación SMN", as_index=False).tail(1).copy()
    ultimas["Hora de observación ART"] = ultimas["fecha"].map(lambda x: x.strftime("%d/%m/%Y %H:%M"))
    ultimas["Antigüedad horas"] = ultimas["fecha"].map(lambda x: round((ahora() - x).total_seconds()/3600, 1))
    st.caption(f"Archivo SMN: {fecha_archivo} · {len(ultimas)} estaciones con datos · Fuente: SMN, descarga pública de datos horarios.")
    st.dataframe(ultimas.drop(columns="fecha"), hide_index=True, use_container_width=True)
    nombres = sorted(datos["Estación SMN"].unique())
    estacion = st.selectbox("Estación para ver registros horarios", nombres, key="smn_archivo_estacion")
    registros = datos[datos["Estación SMN"] == estacion].copy()
    fig = px.line(registros, x="fecha", y="Temperatura °C", markers=True,
                  title=f"Temperatura observada — {estacion}", labels={"fecha": "Hora ART"})
    st.plotly_chart(fig, use_container_width=True)
    st.download_button("⬇️ Descargar observaciones oficiales CSV", datos.to_csv(index=False).encode("utf-8"),
                       file_name="observaciones_horarias_smn_litoral.csv", mime="text/csv", key="smn_archivo_csv")
    st.link_button("Abrir archivo de origen SMN", fuente, key="smn_archivo_fuente")


@st.fragment(run_every="5m")
def mostrar_datos_smn():
    st.header("🇦🇷 SMN — Información oficial del Litoral")
    st.caption("Santa Fe · Corrientes · Entre Ríos. Alertas, avisos y observaciones oficiales, independientes del índice experimental.")
    provincias = st.multiselect("Provincias", PROVINCIAS_LITORAL, default=list(PROVINCIAS_LITORAL), key="smn_provincias")
    if st.button("🔄 Actualizar información SMN", key="smn_litoral_refresh"):
        obtener_cap_litoral.clear()
        descargar_cap_smn.clear()
        obtener_archivo_observaciones_smn.clear()
        _pagina_publica_smn.clear()
        obtener_token_smn.clear()
        obtener_observaciones_smn.clear()
    if not provincias:
        st.info("Seleccioná al menos una provincia.")
        return
    tab_avisos, tab_obs, tab_productos = st.tabs(["🚨 Alertas y avisos", "🌡️ Observaciones", "📚 Productos oficiales"])
    with tab_avisos:
        mostrar_alertas_cap(provincias)
    with tab_obs:
        mostrar_archivo_observaciones(provincias)
        if st.checkbox("Consultar también observaciones actuales del servicio web SMN", key="smn_consultar_api"):
            mostrar_observaciones_api_smn()
    with tab_productos:
        st.write("Productos complementarios para planificación agropecuaria y vigilancia meteorológica.")
        productos = [
            ("Pronóstico agropecuario", "https://www.smn.gob.ar/pronostico_agropecuario"),
            ("Pronóstico semanal", "https://www.smn.gob.ar/clima/perspectiva"),
            ("Pronóstico climático trimestral", "https://www.smn.gob.ar/pronostico-trimestral"),
            ("Resumen de alertas por provincia", "https://www.smn.gob.ar/resumen_por_provincia"),
            ("Alertas oficiales", "https://www.smn.gob.ar/alertas"),
            ("Avisos a muy corto plazo", "https://www.smn.gob.ar/avisos_a_muy_corto_plazo"),
        ]
        for nombre, enlace in productos:
            st.link_button(nombre, enlace)
        st.caption("Estos accesos abren los productos del SMN; no se presentan como datos descargados dentro de la app.")
    st.caption("Fuente: Servicio Meteorológico Nacional, República Argentina. La disponibilidad de datos no garantiza cobertura completa; consultá los avisos oficiales para tomar decisiones.")



# ============================================================
# FICHA DEL PROBLEMA — HIDROLOGÍA, TERRITORIO Y EVALUACIÓN
# ============================================================
FICHA_FECHA_COPIA = '2026-10-09'
INA_BASE = 'https://alerta.ina.gob.ar/pub/datos/'
INTA_BASE = 'https://sepa.inta.gob.ar'
RONI_URL = 'https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso/roni/'
VIALIDAD_URL = 'https://www.argentina.gob.ar/transporte/vialidad-nacional/estado-de-rutas'
SALUD_SF_URL = 'https://www.santafe.gob.ar/maparecursos/index.php?action=consultar'
IGN_SALUD_URL = 'https://wms.ign.gob.ar/geoserver/ows'
CASO_VERA_URL = 'https://www.santafe.gob.ar/noticias/noticia/283139/'
CASO_2016_URL = 'https://repositorio.smn.gob.ar/bitstream/handle/20.500.12160/429/0054AM2016.pdf?isAllowed=y&sequence=1'
ESTACIONES_INA = {'Corrientes': (19, 868), 'Goya': (23, None),
                  'Reconquista': (24, None), 'Paraná': (29, 878),
                  'Santa Fe': (30, 879), 'Concordia': (79, 928)}
PRODUCTOS_INTA = {
    'Agua en suelo / recarga del perfil · PJ': ('agua_en_suelo', 'pj', 'decada', 'pj'),
    'Excedentes hídricos': ('agua_en_suelo', 'dr', 'decada', 'dr'),
    'Vegetación NDVI · MODIS 250 m': ('indices_de_vegetacion', 'compuesto_16d_ndvi', 'juliano', 'iv'),
}
# Copias reproducibles de fuentes públicas, conservadas para el caso histórico.
# Se insertan los datos verificados al construir esta versión; nunca simulan datos en vivo.
FICHA_COPIAS = {'ina_estaciones': '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', 'roni': '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', 'salud_ign': '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', 'refugios_vera': 'eNrlkM1Kw0AUhV9lmHXNpJOmSbqTgnYjFAtuRMpNOmkvTGfK/FBQfBgfwJULwaV5MW+whbrQB6izOvdvzuG7f+LGbmun+ITPrN9hAM1u1RqtIbFS7E454AMecGdpxak20owaGgKfXMgqGeWjUUG1NVSP00TKtKgGXPkAq/5kqkxwlsWAGh+pxZRmshBpLmQqc/b5zjZQ0yxAg92bYdCESNYeDWusadFtwZFfG+mfPuUmhJ2fCLHf7xMPJkCrkrWtE3DC2IANgj8KIctsmFWCzn1Uzi5pHW2LK2z6ZGSxPLU4RKw12bSgvXoenNCZXs4XbAGG3YAL3avp4cytc8r/jadIi0ymZZkX5Q9K9DJZjfNhdo6wdKzZLDpouhfzKx8TtT4w+ZZnx+EatwY8ArvqPkJtNevB/DMeD18Qx4Gx', 'inta_pj': 'eNrVz7kRgEAMA8BeFDuQfQ9wrTBUcuPeeULUAARKdhRIE8GoGBNsGDschrhScBjYlRalVWkTciq5Urwo7R7YnoFaZygVpar04bP9V2czT4vpbR8=', 'inta_dr': 'eNrVz7kRgEAMA8BeFDuQfQ9wrTBUcuPeeULUAARKdhRIE8GoGBNsGDschrhScBjYlRalVWkTciq5Urwo7R7YnoFaZygVpar04bP9V2czT4vpbR8=', 'inta_compuesto_16d_ndvi': 'eNrN1D0KgkEQg+G7TG0xyeyvV5HvJOLdJdvaWIikSfOw8LLFPIOJFvdHZCJukZjaKm3b2tG16+iWAlJQiibFkGIe3VKmlJSySdmlnEeXtFJalFZJq1dceoz+RdJnzM8zht/PTL+k5Ze07ZKYfknwS6JfUvkl+V1sdr+kv13v6/UG2/FJdQ==', 'concordia_2016': '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', 'ina_2016': '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', 'vera_hist': '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', 'vera_2025-05-24': 'eNrtmcluGzkQQP9FZy9NNoss+ua5zGluvhmC4DidpAEtgZYAM4G/av5gvmzYbMnN6Eka25kdOvDBLm5FPXbJkr+Opg/rdr1534xuLm28crGqa3cxmi7mH3dhX11ZZ4MPF6OPzbxZpgmL+bqdNZPZanQj/iq6OgRnbV2nUWnyZv04WXz4sGrWk1XzuJi/T8MuTaVVdTHq5v2ymKd1R7ezZtk+PlzfLtOy63b+cP3DppkvVpPbdtmsRsPYycO7d8vmS5s3ThN//Onusk79zbT5so2lHNPinxab5fTnyWbertOWX/MCaXy7WqivTJrxedk8tp/b9W6l2SwFV4t2Opkt2tV6s2wm1WS9mITHWdf726/XqWFI6IZYPTnGajfIVNXJUak/ryWyN2y5madXsM/waXew4Uj3I1tZuazk0tZ31txUVZpThixD9V7I3VUVQ4Yhy9CBtRxDwpBnKDCkDEWEDLM3zN4we8PsDbM3zN4we8PsDbM3zN4ye8vsLbOHR6FHoUehR6FHoUehR6FHoUehR6FHoUehR6FHoUehR6FHoUehR6FHoUehR6FHoUehR0+Pnh49PXp69PTo6dHTo6dHT4+eHj09enr09Ojp0dOjp0dPj54ePT16evT06OnR06Onx0CPgR4DPQZ6DPQY6DHQY6DHQI+BHgM9BnoM9BjoMdBjoMdAj4EeAz0Gegz0GOgx0GOgR6VHpUelR6VHpUelR6VHpUelR6VHpUelR6VHpUelR6VHpUelR6VHpUelR6VHpUelx0iPkR4jPUZ6jPQY6THSY6THSI+RHiM9RnqM9BjpMdJjpMdIj5EeIz1Geoz0GOkx0mOEx7qCxy5kGLIMHVjLMSQMeYYCQ8pQRMgwe8PsDbM3zN4we8PsDbM3zN4we8Pse49jfIS6n2+m04vu01ffYmp1amb7u2ybS80X8a7Z1MK2f9e36zfb/nL8W5vN++vzui+ZY4q87V6f28v1j9eVtIakeeb5XNWFzevbA+v/Wec+t9TGxz/g36erGrrL+g3VZkqmHxhNZndhXZWvlMnoaQt2yzib47ab6mpzknVBe4TH5h6AjQU1Mww/53lS5bvdHybmHTT35mM77SMdQs98jOAy8ybBFvF4eLgMndt4KJbp4HvmgM8HyjMlzxEtmOuEzannA/SncLunNOYZUQvmlWJeOtYF+7P1KeftNY9RU+QpxUHNkG2e5LXIPG/ipaAD6yO0w6m3B85rSp6VD+hcjve79y+Nk5P0YHgly11yJq6/gDmTOo+p+0udx9Su+Pk0/RGGk/TFLna44+W99gWl5PjEN3f3z0/qAdr/FOu30NbFq6ZDxaiL4lIWoLJIOVBOWtWC8XvZl93tk6fPD2cYapYOz/OLOFSviIpV1q1Q1DaPOidFbSsLZVkuS5q96lKWmLLQlOWmLDosPS8vQBY04H41PlyTv6U/STlCB9agBQ34LVwcn/xSPr/75/f6fynrM19J92LKK+lfwPAm6l/G+PdR/1GYkuPT/2O7H/6SP/PMM//nHA//S++/qjrjjDO+B+Onp98BTULxeA==', 'vera_2025-05-25': 'eNrtmctu4zYUht/FayfhISkeMrt001V32QWGkclopgJ8GfgyQDvIU/UN+mSVKDvm+Itct0EGReGFvghHvPziZ1Gw8200e9w0m+3HenR7ZdO1T8Y5Px7NlovP+3Iw19ZbDToefa4X9artsFxsmnk9na9Hty5eG2ucWpeMejVtq+3mabr89Gldb6br+mm5+Ng2uxITjRmPun6/LxftuKO7eb1qnh5v7lbtsJtm8Xjz07ZeLNfTu2ZVr0eHttPHDx9W9dcmT9x2/PmX+yvXXq9n9dddrc3YDv7rcrua/TbdLppNO+W3PEDbvlkvYzDS9viyqp+aL81mP9J83hbXy2Y2nS+b9Wa7qqdmullO9WneXf3zj5v2QBPtmth4so2NXSMx5mSr9noeq6qOmq22i3YF+4TP+xs73NLDyBpbXZnqyvp7K7fGtH3KkmXJHZWqe2NYEpYsS6+M5VmqWAosKUuRpYSSML0wvTC9ML0wvTC9ML0wvTC9ML1lesv0lunhMdBjoMdAj4EeAz0Gegz0GOgx0GOgx0CPgR4DPQZ6DPQY6DHQY6DHQI+BHgM9BnoM9BjoUelR6VHpUelR6VHpUelR6VHpUelR6VHpUelR6VHpUelR6VHpUelR6VHpUelR6VHpMdJjpMdIj5EeIz1Geoz0GOkx0mOkx0iPkR4jPUZ6jPQY6THSY6THSI+RHiM9RnqM9BjpMdFjosdEj4keEz0mekz0mOgx0WOix0SPiR4TPSZ6TPSY6DHRY6LHRI+JHhM9JnpM9Jjg0Rl47ErCkmXplbE8SxVLgSVlKbKUUBKmF6YXphemF6YXphemF6YXphemt0xvmd4yPTwKPQo9Cj0KPQo9Cj0KPQo9Cj0KPQo9Cj0KPQo9Cj0KPQo9Cj0KPQo9ys7jBF+hHhbb2WzcffviIe1hd3+P6+YNRzeme2UOLa7t56hOjhVyHz+217H9m8aS+1cDucv599e7PmY3d2rHcbtzczn+G8dk+Av+Q6s+he4DkFxmhxgzczlWmR7MzaPNFJx30DyY9uf5Q6Z5SM1Dah5GXUELSkFb9CpZHUbehfID8UNxbouE+mqqKj8HRg6n3Z1UUmX2lZQZMt2heV7JfN9Ji/P0cr7rkxtLT1fQF4Pn6Yw9GrZXlsOmfCupX/fSnw4sgzt4+m4B/GGxQ66HXA+5b/DvzJwq5HvJkat8E1WevMoXswWfF+3vaM+gO8lirv6h8HkxfM7jcxuf2/i+TXE6RFvQDdCD5VQdXE7jchqX07i+nhs6W1B6Tk78eNc9/1V6WfQgBS3ozuBb7L8H3/CZ7LeleHh0+odst1NWxYNIVgV9QTdA/9J+v5OUtEd7S8IOo5ib8xUbRTps9udsGdzyjzf7cscv9/2Id4DifVC+Fc55N5RvhRfstqyeEdQzGMBqYAN02LJKykm+gv4J/I4RVDCAFegnJ3+Wb59/1w3/Nsi70b4b3T+kB6sBBlAHGMF0YAEpaEE3QD/A6myGM6j/ivHdmH4Y/Y848qb/wsnp/549HH9YLrzwwv8tJ4f/kvc/Ql1wwQVvweT5+S+acuvV', 'vera_2025-05-26': 'eNrtmc1u20YUhd9Fa0meO3/3jnfupqvuvBMEwVGYlIAkBvoJ0AZ+qr5Bn6yjS9ma8Iis4hZBWsgAj+gzd2YO+Q0HEPVltHra1/vD+2p0P7Fp6pNxzo9Hq2bz8cWOZmq95cjj0cdqU21zh2azr9fVYr0b3bs4tTEFH/IfkWcfx6PDfrloPnzYVfvFrlo2m/e5bkJGjBmPjh1/bzZ54NHDutrWy6e7h20ed19vnu5+OlSbZrd4qLfVbnSuXTy9e7etPtc6c+748y+PE5fbq1X1+eTlkHnwX5vDdvXb4rCp93nKLzpArq93jURDucenbbWsP9X7l5HW62zumnq1WDf1bn/YVguz2DcLXq6PrX/+cZcPKOFjiZXBGivHIjJmsCq361ghdMq2h02+g23C55cLO1/SbGSNDRMTJjY8Wro3JvcpLYuW61jx0Ri0CC2L1oWxPFoBrYgWoyVoJbAI0xOmJ0xPmJ4wPWF6wvSE6QnTE6a3mN5ieovpgSMjR0aOjBwZOTJyZOTIyJGRIyNHRo6MHBk5MnJk5MjIkZEjI0dGjowcGTkycmTkyMiRkaMgR0GOghwFOQpyFOQoyFGQoyBHQY6CHAU5CnIU5CjIUZCjIEdBjoIcBTkKchTkKMhRkGNCjgk5JuSYkGNCjgk5JuSYkGNCjgk5JuSYkGNCjgk5JuSYkGNCjgk5JuSYkGNCjgk5JuSYgKMzwPFoEVoWrQtjebQCWhEtRkvQSmARpidMT5ieMD1hesL0hOkJ0xOmJ0xvMb3F9BbTA0dCjoQcCTkSciTkSMiRkCMhR0KOhBwJORJyJORIyJGQIyFHQo6EHAk5EnIk5EjIkZAjdTjGielybC1Cy6J1YSyPVkArosVoCVoJLML0hOkJ0xOmJ0xPmJ4wPWF6wvSE6VuOc/gKNdscVquxmXI+7Pj4LcxMXT7S6ZxOn93jWONPfezpnDr19vR/0E8L9W5gjmN7zH14TPp/yJ9OPdLx3ClzXzbb03Y7/mPHvP8L/iwvE9GFmOiskvT8uACCaX1WDaquWymiGou+xWipc/pVdQA91wTSpW+CqlP153MdsB1cx0q+SGdf8+pBrKq9qTi1RSOdHcPFZFRM1N4GKbSc2vbcGC4uT+OJ1rDWsLaytrKOwzoOE2ifxHSFSqFtL40edcqoU6oRtCRoSWidMKjxTco9s+j9UUbea5I2lddWr2G9G1R/hYZBLWfRG6OG0zhO4zgtdK3vivNrNIC2Y3KPljO2c+ntscrVairLhcZS5wOv7mZn/q2GQmOxUsq1U6yp3gV5Ye0OqwVtnwcNwXJ+WuQsdLVysQO5YiPj1yc7vD7pJy2e+VQ87Vw886HQcutx3b2g3PtwW+Ce3dAX20WrttDy8l7ltKGUKoVyobHQABuQK9R+w5b0N9uO9GxE5XYUYSGGcXeZtupALSiBDkpIhQrshtyzl/XtjP5qdT1qQQn0a/FpPvgiPj/zTtfQW9X9Y/Vv0nC1xkFlUCk0dbUrBGp71PWov1rD1Rq/UflfUvmOmn4o9d/zmA//aja7vDBvetOb/g91fv51/OXl0+1Fx+34MV8+PT//BZBBJq8='}


def copia_ficha(nombre):
    return json.loads(zlib.decompress(base64.b64decode(FICHA_COPIAS[nombre])))


def fecha_fuente(valor):
    t = pd.Timestamp(valor)
    if pd.isna(t):
        raise ValueError('Fecha ausente')
    return t.tz_localize(TZ) if t.tzinfo is None else t.tz_convert(TZ)


def edad_fuente(valor, referencia=None):
    try:
        return ((fecha_fuente(referencia or ahora()) - fecha_fuente(valor)).total_seconds() / 3600)
    except (ValueError, TypeError):
        return np.nan


def dato_vigente(valor, max_horas, referencia=None):
    e = edad_fuente(valor, referencia)
    return bool(np.isfinite(e) and 0 <= e <= max_horas)


def texto_sin_html(valor):
    return html.unescape(re.sub(r'<[^>]*>', '', str(valor or ''))).strip()


def json_finito(valor):
    """Exportaciones JSON estándar: un dato ausente es null, nunca NaN."""
    if isinstance(valor, dict):
        return {str(k): json_finito(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [json_finito(v) for v in valor]
    if isinstance(valor, (float, np.floating)):
        return float(valor) if np.isfinite(valor) else None
    if isinstance(valor, (bool, np.bool_)):
        return bool(valor)
    if isinstance(valor, np.integer):
        return int(valor)
    if isinstance(valor, (datetime, pd.Timestamp)):
        return valor.isoformat()
    return valor


def descargar_publico(url, params=None):
    r = requests.get(url, params=params, timeout=(4, 18),
                     headers={'User-Agent': 'Alerta-Litoral-Agro/4.0 (fuentes publicas)'})
    r.raise_for_status()
    return r


@st.cache_data(ttl=3600, show_spinner=False)
def consultar_ina(serie_id, desde, hasta):
    # La API INA usa separadores & en el PATH, no una query iniciada por ?.
    url = INA_BASE + 'datos&' + urlencode({'seriesId': int(serie_id),
          'timeStart': str(desde), 'timeEnd': str(hasta), 'format': 'json'})
    try:
        d = descargar_publico(url).json()
        if not isinstance(d, dict) or not isinstance(d.get('data'), list) or not d['data']:
            raise ValueError(d.get('mensaje', 'La serie no tiene observaciones en ese período'))
        meta = d.get('responseHeader', {}).get('seriesmetadata', {})
        if int(meta.get('seriesId', -1)) != int(serie_id):
            raise ValueError('La respuesta no corresponde a la serie solicitada')
        filas = []
        for obs in d['data']:
            try:
                valor = float(obs['valor'])
                if np.isfinite(valor):
                    filas.append({'fecha': fecha_fuente(obs['timestart']), 'valor': valor})
            except (KeyError, TypeError, ValueError):
                continue
        if not filas:
            raise ValueError('No hay observaciones numéricas válidas')
        return {'df': pd.DataFrame(filas).sort_values('fecha').drop_duplicates('fecha', keep='last'),
                'meta': meta, 'sitio': d['responseHeader'].get('sitemetadata', {}), 'url': url, 'error': ''}
    except Exception as exc:
        return {'error': str(exc) or type(exc).__name__, 'df': pd.DataFrame(), 'url': url}


@st.cache_data(ttl=86400, show_spinner=False)
def estaciones_ina_publicas():
    try:
        d = descargar_publico(INA_BASE + 'estaciones&redId=10&format=json').json()
        if not isinstance(d.get('data'), list) or not d['data']:
            raise ValueError('Catálogo vacío')
        return d['data'], 'Consulta pública INA'
    except Exception:
        return copia_ficha('ina_estaciones'), f'Copia del {FICHA_FECHA_COPIA}; umbrales por confirmar si cambiaron'


def clasificar_rio(altura, estacion):
    if not np.isfinite(altura):
        return 'SIN DATOS'
    for campo, estado in [('nivel_de_evacuacion', 'EVACUACIÓN'), ('nivel_de_alerta', 'ALERTA')]:
        try:
            limite = float(estacion[campo])
            if np.isfinite(limite) and altura >= limite:
                return estado
        except (KeyError, TypeError, ValueError):
            pass
    return 'BAJO UMBRALES' if estacion.get('nivel_de_alerta') is not None else 'SIN UMBRAL'


def mostrar_ina_ficha():
    st.subheader('Ríos · observaciones y umbrales INA')
    st.write('Altura de los ríos y caudal, con estaciones de referencia para el Paraná y el Uruguay.')
    nombre = st.selectbox('Estación hidrológica', list(ESTACIONES_INA), key='ficha_ina_estacion')
    dias = st.select_slider('Período de observaciones', [7, 30, 90, 365], value=30, key='ficha_ina_dias')
    if st.button('Consultar observaciones INA', key='ficha_ina_cargar'):
        hasta = ahora().date()
        h_id, q_id = ESTACIONES_INA[nombre]
        estaciones, origen = estaciones_ina_publicas()
        estacion = next((x for x in estaciones if int(x.get('sitecode', -1)) == h_id), {})
        series = [h_id] + ([q_id] if q_id else [])
        with st.spinner('Consultando series del INA…'):
            with ThreadPoolExecutor(max_workers=2) as ex:
                respuestas = list(ex.map(lambda sid: consultar_ina(sid, hasta-timedelta(days=dias), hasta), series))
        st.session_state['ficha_ina_resultado'] = {'nombre': nombre, 'dias': dias, 'estacion': estacion,
                                                 'origen': origen, 'series': respuestas}
    resultado = st.session_state.get('ficha_ina_resultado')
    if not resultado or resultado['nombre'] != nombre or resultado['dias'] != dias:
        st.info('Elegí una estación y consultá sus observaciones. No se asigna cero a una serie ausente.')
    else:
        est = resultado['estacion']
        for i, r in enumerate(resultado['series']):
            unidad = 'm' if i == 0 else 'm³/s'
            etiqueta = 'Altura sobre el cero de la escala' if i == 0 else 'Caudal calculado por curva de aforo'
            if r['error']:
                st.warning(f'{etiqueta}: {r["error"]}')
                continue
            esperada = 11 if i == 0 else 10
            if int(r['meta'].get('unitId', -1)) != esperada:
                st.warning('La unidad de la fuente cambió; se suspende la interpretación de esta serie.')
                continue
            df = r['df']; ultimo = df.iloc[-1]
            st.metric(etiqueta, f'{ultimo.valor:.2f} {unidad}')
            st.caption(f'Última observación: {ultimo.fecha:%d/%m/%Y %H:%M} ART · serie {r["meta"]["seriesId"]}')
            if not dato_vigente(ultimo.fecha, 48):
                st.warning('Serie desactualizada o con fecha inválida: no se usa como condición actual.')
            fig = px.line(df, x='fecha', y='valor', labels={'valor': unidad, 'fecha': 'Fecha ART'})
            if i == 0:
                estado = clasificar_rio(ultimo.valor, est)
                st.write(f'**Comparación con umbrales de la estación: {estado}.**')
                for campo, texto, color in [('nivel_de_alerta','Alerta','orange'), ('nivel_de_evacuacion','Evacuación','red')]:
                    if est.get(campo) is not None:
                        fig.add_hline(y=float(est[campo]), line_color=color, annotation_text=f'{texto} · {est[campo]} m')
                st.caption(resultado['origen'])
            st.plotly_chart(fig, use_container_width=True, key=f'ficha_ina_chart_{i}')
            st.download_button(f'Descargar {etiqueta.lower()} CSV', df.to_csv(index=False).encode('utf-8-sig'),
                               f'INA_{nombre}_{i}.csv', 'text/csv', key=f'ficha_ina_csv_{i}')
            st.link_button('Serie pública utilizada', r['url'])
        if est.get('lat') is not None:
            st.map(pd.DataFrame([{'lat': float(est['lat']), 'lon': float(est['lon'])}]), zoom=9)
    st.caption('La altura de escala no es una cota del terreno sobre el nivel del mar. Una estación fluvial representa su tramo, no todos los lotes de la localidad.')
    st.link_button('Aplicación y documentación del INA', 'https://www.argentina.gob.ar/ina/recursos/aplicacionweb-datos-hidrologicos')


@st.cache_data(ttl=21600, show_spinner=False)
def catalogo_inta(seccion, producto, tipo):
    try:
        url = INTA_BASE + '/productos/geosepa/componentes/listar_archivos.php'
        d = descargar_publico(url, {'seccion': seccion, 'producto': producto, 'tipo': tipo}).json()
        if not isinstance(d, dict) or not d:
            raise ValueError('Catálogo INTA vacío')
        return d, 'Catálogo público consultado'
    except Exception:
        return copia_ficha('inta_' + producto), f'Copia del catálogo del {FICHA_FECHA_COPIA}'


def fechas_producto_inta(catalogo, tipo):
    filas = []
    for year, valores in catalogo.items():
        if not re.fullmatch(r'\d{4}', str(year)):
            continue
        if tipo == 'decada' and isinstance(valores, dict):
            for mes, decadas in valores.items():
                for dec in decadas:
                    try:
                        dec = int(dec)
                        if dec not in (1, 2, 3):
                            continue
                        inicio = datetime(int(year), int(mes), 1 + 10 * (dec-1))
                        siguiente = (inicio.replace(day=28)+timedelta(days=4)).replace(day=1)
                        fin = inicio.replace(day=10*dec) if dec < 3 else siguiente-timedelta(days=1)
                        filas.append({'inicio': inicio, 'fin': fin, 'sufijo': f'{int(year):04}{int(mes):02}_{dec}'})
                    except (ValueError, TypeError):
                        continue
        elif tipo == 'juliano' and isinstance(valores, list):
            for jul in valores:
                try:
                    j = int(jul)
                    if not 1 <= j <= 366:
                        continue
                    inicio = datetime(int(year), 1, 1) + timedelta(days=j-1)
                    fin = min(inicio+timedelta(days=15), datetime(int(year),12,31))
                    filas.append({'inicio': inicio, 'fin': fin, 'sufijo': f'{year}{j:03}'})
                except (ValueError, TypeError):
                    continue
    return sorted(filas, key=lambda x: x['inicio'], reverse=True)


@st.cache_data(ttl=21600, show_spinner=False)
def imagen_inta_publica(seccion, producto, nombre):
    base = f'{INTA_BASE}/productos/geosepa/{seccion}/{producto}/jpg/{nombre}'
    errores = []
    extensiones = ('gif', 'jpg', 'png') if producto == 'compuesto_16d_ndvi' else ('jpg', 'png', 'gif')
    for ext in extensiones:
        try:
            r = descargar_publico(base+'.'+ext)
            if len(r.content) > 12_000_000:
                raise ValueError('Imagen demasiado grande')
            Image.open(BytesIO(r.content)).verify()
            return {'bytes': r.content, 'url': base+'.'+ext, 'error': ''}
        except Exception as exc:
            errores.append(type(exc).__name__)
    return {'error': 'No se pudo recuperar la imagen pública ('+', '.join(errores)+')'}


def calcular_indices_espectrales(df):
    requeridas = {'localidad', 'lat', 'lon', 'fecha', 'fuente', 'verde', 'rojo', 'nir', 'valido'}
    if not requeridas.issubset(df.columns):
        raise ValueError('Faltan columnas: ' + ', '.join(sorted(requeridas-set(df.columns))))
    if len(df) > 10000 or df.empty:
        raise ValueError('Se admiten de 1 a 10.000 muestras')
    d = df.copy()
    for col in ['lat', 'lon', 'verde', 'rojo', 'nir'] + (['swir'] if 'swir' in d else []):
        d[col] = pd.to_numeric(d[col], errors='coerce')
    d['fecha'] = d.fecha.map(fecha_fuente)
    d['valido'] = d.valido.astype(str).str.lower().isin(['1','true','sí','si'])
    if not d.lat.between(-35,-26).all() or not d.lon.between(-64,-54).all():
        raise ValueError('Coordenadas fuera del entorno del Litoral o no válidas')
    if not d.fuente.astype(str).str.match(r'^https?://\S+$').all():
        raise ValueError('Cada muestra necesita la URL de su fuente')
    for salida, a, b in [('NDWI','verde','nir'),('NDVI','nir','rojo'),('MNDWI','verde','swir')]:
        if b not in d:
            continue
        validas = d.valido & d[a].between(0,1) & d[b].between(0,1) & ((d[a]+d[b]) > 0)
        d[salida] = np.nan
        d.loc[validas, salida] = (d.loc[validas,a]-d.loc[validas,b])/(d.loc[validas,a]+d.loc[validas,b])
    return d


def mostrar_inta_ficha():
    st.subheader('INTA · suelo, vegetación y agua superficial')
    st.write('Productos oficiales con su período de observación y leyenda original. El agua útil del perfil (%) y la humedad volumétrica del modelo (m³/m³) son variables diferentes.')
    producto = st.selectbox('Producto SEPA', list(PRODUCTOS_INTA), key='ficha_inta_producto')
    seccion, codigo, tipo, prefijo = PRODUCTOS_INTA[producto]
    cat, origen = catalogo_inta(seccion, codigo, tipo)
    fechas = fechas_producto_inta(cat, tipo)
    if fechas:
        ind = st.selectbox('Período publicado', range(len(fechas)),
            format_func=lambda i: f'{fechas[i]["inicio"]:%d/%m/%Y} – {fechas[i]["fin"]:%d/%m/%Y}', key='ficha_inta_fecha_'+codigo)
        f = fechas[ind]
        st.caption(f'{origen}. Actualización cada diez días para suelo; compuesto de 16 días para NDVI. Un compuesto puede conservar observaciones anteriores dentro de su ventana.')
        if edad_fuente(f['fin']) > (20 if tipo == 'decada' else 32)*24:
            st.warning('Producto histórico o atrasado; no representa automáticamente la situación de hoy.')
        if st.button('Mostrar mapa INTA dentro de la app', key='ficha_inta_mostrar'):
            st.session_state['ficha_inta_imagen'] = (codigo, f['sufijo'], imagen_inta_publica(seccion,codigo,prefijo+'_'+f['sufijo']))
        guardada = st.session_state.get('ficha_inta_imagen')
        if guardada and guardada[:2] == (codigo,f['sufijo']):
            r = guardada[2]
            if r.get('error'):
                st.warning(r['error'])
            else:
                st.image(r['bytes'], caption=f'Fuente INTA SEPA · {producto} · {f["inicio"]:%d/%m/%Y} a {f["fin"]:%d/%m/%Y}', use_container_width=True)
                st.link_button('Imagen pública original', r['url'])
    else:
        st.warning('No hay períodos válidos publicados para este producto.')
    st.link_button('Descripción del producto y descargas autorizadas', f'{INTA_BASE}/productos/geosepa/{seccion}/{codigo}/')
    with st.expander('Situación hídrica observada · archivo INTA del Litoral'):
        st.write('Comparación satelital MODIS del 29/12/2022 y el 26/12/2023, con agua y vegetación visibles en la composición de bandas 1–2–7. El archivo fue publicado con nombre del 29/12/2023; se conservan las fechas de adquisición indicadas en la imagen. No es una clasificación automática ni una capa de inundación actual.')
        url = INTA_BASE+'/productos/sepaproductos/inundaciones/in_20231229-litotal.gif'
        if st.button('Cargar comparación hídrica INTA', key='ficha_agua_publica'):
            try:
                contenido = descargar_publico(url).content
                Image.open(BytesIO(contenido)).verify()
                st.session_state['ficha_agua_imagen'] = contenido
            except Exception as exc:
                st.warning('Imagen no disponible: '+str(exc))
        if st.session_state.get('ficha_agua_imagen'):
            st.image(st.session_state['ficha_agua_imagen'], caption='INTA SEPA · Litoral · adquisiciones 29/12/2022 y 26/12/2023 · MODIS 250 m', use_container_width=True)
        st.link_button('Archivo de inundaciones y metodología INTA', INTA_BASE+'/productos/eventos_extremos/inundaciones/index-inundaciones.php')
    with st.expander('Calcular NDWI / NDVI en muestras de reflectancia'):
        st.write('Cargá reflectancias de superficie normalizadas a 0–1, corregidas por el factor/offset del producto y con nubes, sombras y NoData marcados valido=0. Cada muestra debe identificar localidad, fecha y fuente. La app no descarga ni inventa bandas satelitales.')
        plantilla = 'localidad,lat,lon,fecha,fuente,verde,rojo,nir,swir,valido\n'
        st.download_button('Plantilla de muestras espectrales', plantilla, 'muestras_satelitales.csv', 'text/csv', key='ficha_ndwi_plantilla')
        archivo = st.file_uploader('CSV de muestras satelitales', type=['csv'], key='ficha_espectral_csv')
        if archivo is not None:
            try:
                d = calcular_indices_espectrales(pd.read_csv(archivo))
                st.session_state['ficha_indices'] = d
                st.dataframe(d, use_container_width=True)
                st.download_button('Descargar índices calculados', d.to_csv(index=False).encode('utf-8-sig'), 'indices_espectrales.csv','text/csv',key='ficha_indices_descarga')
            except Exception as exc:
                st.session_state.pop('ficha_indices', None)
                st.error('Muestras rechazadas: '+str(exc))
        else:
            st.session_state.pop('ficha_indices', None)
        st.caption('NDWI de agua = (verde−NIR)/(verde+NIR), McFeeters. NDVI = (NIR−rojo)/(NIR+rojo). MNDWI = (verde−SWIR)/(verde+SWIR). NDWI > 0 es una detección exploratoria: puede confundir superficies urbanas y requiere contraste visual/campo.')
        st.link_button('Método NDWI · McFeeters (1996)', 'https://doi.org/10.1080/01431169608948714')
        st.link_button('Método MNDWI · Xu (2006)', 'https://doi.org/10.1080/01431160600589179')


def parsear_roni(texto):
    filas = []
    estaciones = ['DJF','JFM','FMA','MAM','AMJ','MJJ','JJA','JAS','ASO','SON','OND','NDJ']
    for fila in re.findall(r'<tr\b[^>]*>(.*?)</tr>', texto, re.S|re.I):
        celdas = [texto_sin_html(x) for x in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>',fila,re.S|re.I)]
        if celdas and re.fullmatch(r'\d{4}',celdas[0]) and 1950 <= int(celdas[0]) <= ahora().year:
            for mes, valor in enumerate(celdas[1:13], 1):
                try:
                    v = float(valor)
                    if np.isfinite(v) and abs(v) <= 10:
                        filas.append({'año':int(celdas[0]),'trimestre':estaciones[mes-1],
                                      'mes_central':mes,'RONI':v})
                except ValueError:
                    pass
    if len(filas) < 12:
        raise ValueError('Tabla RONI no reconocida')
    return pd.DataFrame(filas).drop_duplicates(['año','mes_central']).sort_values(['año','mes_central'])


@st.cache_data(ttl=86400, show_spinner=False)
def obtener_roni():
    try:
        return parsear_roni(descargar_publico(RONI_URL).text), 'Consulta pública NOAA CPC'
    except Exception:
        return pd.DataFrame(copia_ficha('roni')), f'Copia del {FICHA_FECHA_COPIA}; consulta en vivo no disponible'


def fase_roni(df):
    if df.empty:
        return 'SIN DATOS'
    vals = df.RONI.to_numpy(dtype=float)
    if len(vals) >= 5 and np.all(vals[-5:] >= .5):
        return 'Secuencia cálida ≥ 5 trimestres'
    if len(vals) >= 5 and np.all(vals[-5:] <= -.5):
        return 'Secuencia fría ≥ 5 trimestres'
    return 'Sin secuencia completa de 5 trimestres al último dato'


def mostrar_enos_ficha():
    st.subheader('El Niño / La Niña · contexto estacional')
    d, origen = obtener_roni(); ultimo = d.iloc[-1]
    st.metric('RONI · anomalía relativa Niño 3.4', f'{ultimo.RONI:+.1f} °C')
    st.caption(f'{origen} · trimestre {ultimo.trimestre} {int(ultimo["año"])} · {fase_roni(d)}')
    st.write('El RONI resume el Pacífico tropical. No determina por sí solo una inundación local: se contrasta con lluvia, suelo, ríos y territorio. Las tendencias trimestrales del SMN están en Datos oficiales SMN.')
    desde = st.slider('Año inicial de la serie ENOS', 1950, ahora().year-1, 2014, key='ficha_roni_inicio')
    vista = d[d['año']>=desde].copy()
    vista['fecha'] = pd.to_datetime(dict(year=vista['año'],month=vista.mes_central,day=15))
    fig = px.line(vista,x='fecha',y='RONI',hover_data=['trimestre'])
    fig.add_hline(y=.5,line_color='red'); fig.add_hline(y=-.5,line_color='blue')
    st.plotly_chart(fig,use_container_width=True,key='ficha_roni_chart')
    st.caption('NOAA publica medias móviles de tres meses. Cinco trimestres consecutivos por encima de +0,5 °C o debajo de −0,5 °C delimitan episodios históricos; la confirmación operacional también considera la atmósfera. Los valores recientes pueden revisarse. RONI y ONI no son intercambiables.')
    st.download_button('Descargar serie RONI',d.to_csv(index=False).encode('utf-8-sig'),'NOAA_RONI.csv','text/csv',key='ficha_roni_csv')
    st.link_button('Fuente y criterio NOAA CPC',RONI_URL)


@st.cache_data(ttl=1800, show_spinner=False)
def consultar_vialidad():
    try:
        pagina = descargar_publico(VIALIDAD_URL).text
        m = re.search(r'"idSpread"\s*:\s*"([\w-]+)"',pagina)
        hoja = re.search(r'"hojaNombre"\s*:\s*"([\w-]+)"',pagina)
        if not m or not hoja:
            raise ValueError('La página oficial cambió la ubicación de su tabla')
        url = f'https://docs.google.com/spreadsheets/d/{m[1]}/gviz/tq?'+urlencode({'tqx':'out:csv','sheet':hoja[1]})
        from io import StringIO
        d = pd.read_csv(StringIO(descargar_publico(url).text),dtype=str).fillna('')
        cols = ['filtro-provincia','filtro-ruta','filtro-tramo','estado','calzada','observaciones','actualizado']
        if not set(cols).issubset(d.columns):
            raise ValueError('Columnas de Vialidad no reconocidas')
        d = d[cols].copy()
        for col in cols:
            d[col] = d[col].map(texto_sin_html)
        d = d[d['filtro-provincia'].map(normalizar_nombre).isin(['SANTA FE','CORRIENTES','ENTRE RIOS'])]
        d = d.rename(columns={'filtro-provincia':'provincia','filtro-ruta':'ruta','filtro-tramo':'tramo'})
        d['fecha'] = pd.to_datetime(d.actualizado,dayfirst=True,errors='coerce').map(lambda x: x.tz_localize(TZ) if pd.notna(x) else pd.NaT)
        if d.empty:
            raise ValueError('La tabla no contiene tramos del Litoral')
        d['vigencia'] = d.fecha.map(lambda t: 'Reciente · ≤ 24 h' if pd.notna(t) and dato_vigente(t,24) else 'Reconfirmar · reporte antiguo/sin fecha')
        return {'df': d, 'consulta': ahora().isoformat(), 'error': ''}
    except Exception as exc:
        return {'error':str(exc) or type(exc).__name__,'df':pd.DataFrame()}


def normalizar_nombre(s):
    return ''.join(c for c in unicodedata.normalize('NFD',str(s).upper().strip()) if unicodedata.category(c) != 'Mn')


def distancia_km(lat1,lon1,lat2,lon2):
    a,b,c,d = map(np.radians,[lat1,lon1,lat2,lon2])
    h = np.sin((c-a)/2)**2 + np.cos(a)*np.cos(c)*np.sin((d-b)/2)**2
    return 6371*2*np.arcsin(np.sqrt(np.clip(h,0,1)))


@st.cache_data(ttl=604800, show_spinner=False)
def consultar_relieve(lat, lon, radio_km):
    # Malla de muestreo: no representa un raster continuo ni resolución de 90 m.
    dy = radio_km/111.32; dx = radio_km/(111.32*np.cos(np.radians(lat)))
    yy,xx = np.meshgrid(np.linspace(lat-dy,lat+dy,15),np.linspace(lon-dx,lon+dx,15),indexing='ij')
    coords = list(zip(yy.ravel(),xx.ravel()))
    def lote(puntos):
        d = descargar_publico('https://api.open-meteo.com/v1/elevation',
            {'latitude':','.join(f'{p[0]:.6f}' for p in puntos),'longitude':','.join(f'{p[1]:.6f}' for p in puntos)}).json()
        if len(d.get('elevation',[])) != len(puntos):
            raise ValueError('DEM incompleto')
        return d['elevation']
    try:
        lotes = [coords[i:i+100] for i in range(0,len(coords),100)]
        with ThreadPoolExecutor(max_workers=3) as ex:
            elev = [v for parte in ex.map(lote,lotes) for v in parte]
        df = pd.DataFrame({'lat':yy.ravel(),'lon':xx.ravel(),'cota_m':pd.to_numeric(elev,errors='coerce')})
        df = df[df.cota_m.between(-100,5000)].copy()
        if len(df)<200:
            raise ValueError('Cobertura insuficiente de la malla')
        return {'df':df,'radio':radio_km,'consulta':ahora().isoformat(),'error':''}
    except Exception as exc:
        return {'error':str(exc) or type(exc).__name__,'df':pd.DataFrame()}


@st.cache_data(ttl=604800, show_spinner=False)
def consultar_salud_ign():
    try:
        d = descargar_publico(IGN_SALUD_URL,{'service':'WFS','version':'1.0.0','request':'GetFeature',
            'typeName':'ign:salud_020801','outputFormat':'application/json','srsName':'EPSG:4326',
            'bbox':'-63,-34.5,-55,-26.8,EPSG:4326'}).json()
        filas = []
        for feat in d.get('features',[]):
            geom = feat.get('geometry') or {}; p = feat.get('properties') or {}
            if geom.get('type')!='Point':
                continue
            lon,lat = geom['coordinates'][:2]
            prov = next((nombre for nombre,g in GEOMETRIAS_LITORAL.items()
                if any(_punto_en_poligono((lon,lat),a) for a in _anillos_provincia(g))),None)
            if prov:
                filas.append({'nombre':p.get('fna') or p.get('nam') or 'Efector sin nombre',
                    'provincia':prov,'lat':lat,'lon':lon,'tipo':'salud','fuente':'IGN/SISA',
                    'url_fuente':'https://www.ign.gob.ar/NuestrasActividades/ServiciosWeb',
                    'estado':'Ubicación de catálogo · atención/stock sin confirmar'})
        if not filas:
            raise ValueError('Sin puntos de salud válidos')
        return pd.DataFrame(filas), 'Consulta pública IGN/SISA · fecha de relevamiento no informada'
    except Exception:
        return pd.DataFrame(copia_ficha('salud_ign')), f'Copia IGN/SISA del {FICHA_FECHA_COPIA} · relevamiento sin fecha'


def validar_recursos(df):
    cols = {'nombre','tipo','lat','lon','fecha_verificacion','valido_hasta','autoridad','fuente','estado','suero_antiofidico'}
    if not cols.issubset(df.columns):
        raise ValueError('Faltan columnas: '+', '.join(sorted(cols-set(df.columns))))
    if df.empty or len(df)>2000:
        raise ValueError('Se requieren de 1 a 2.000 puntos')
    d = df.copy()
    for c in ['lat','lon']:
        d[c] = pd.to_numeric(d[c],errors='coerce')
    if not d.lat.between(-35,-26).all() or not d.lon.between(-64,-54).all():
        raise ValueError('Coordenadas fuera del entorno del Litoral')
    if not d.tipo.isin(['refugio','salud','terreno_alto','limpieza_canal']).all():
        raise ValueError('Tipo de recurso no reconocido')
    if not d.estado.isin(['habilitado','cerrado','sin_confirmar']).all():
        raise ValueError('Estado no reconocido')
    if not d.suero_antiofidico.isin(['confirmado','sin_confirmar','no']).all():
        raise ValueError('Estado de suero no reconocido')
    if not d.fuente.astype(str).str.match(r'^https?://\S+$').all() or not d.autoridad.astype(str).str.strip().ne('').all():
        raise ValueError('Cada punto necesita autoridad y URL de evidencia')
    d['fecha_verificacion'] = d.fecha_verificacion.map(fecha_fuente)
    d['valido_hasta'] = d.valido_hasta.map(fecha_fuente)
    if (d.valido_hasta < d.fecha_verificacion).any():
        raise ValueError('La vigencia termina antes de la verificación')
    # Una declaración subida por el usuario conserva su procedencia; no se hace oficial automáticamente.
    d['utilizable'] = [(e=='habilitado' and dato_vigente(f,24) and fin>=fecha_fuente(ahora()))
                      for e,f,fin in zip(d.estado,d.fecha_verificacion,d.valido_hasta)]
    return d


def validar_rutas_geojson(d):
    if not isinstance(d,dict) or d.get('type')!='FeatureCollection' or not 1 <= len(d.get('features',[]))<=200:
        raise ValueError('Se requiere FeatureCollection con 1–200 rutas')
    rutas = []
    for f in d['features']:
        g = f.get('geometry') or {}; p = f.get('properties') or {}
        if g.get('type')!='LineString' or not 2<=len(g.get('coordinates',[]))<=10000:
            raise ValueError('Cada ruta debe ser un LineString de 2–10.000 vértices')
        for pt in g['coordinates']:
            if len(pt)<2 or not -64<=float(pt[0])<=-54 or not -35<=float(pt[1])<=-26:
                raise ValueError('Ruta fuera del entorno del Litoral')
        for c in ['nombre','autoridad','fuente','fecha_verificacion','valido_hasta','estado']:
            if not p.get(c):
                raise ValueError('Falta propiedad de ruta: '+c)
        if not re.fullmatch(r'https?://\S+',str(p['fuente'])) or p['estado'] not in ['habilitado','cerrado','sin_confirmar']:
            raise ValueError('Fuente o estado de ruta no válido')
        inicio,fin = fecha_fuente(p['fecha_verificacion']),fecha_fuente(p['valido_hasta'])
        if fin<inicio:
            raise ValueError('Vigencia de ruta inválida')
        # Ninguna ruta calculada se interpreta como segura para evacuar.
        estado = 'Confirmación declarada · revisar en terreno' if p['estado']=='habilitado' and dato_vigente(inicio,24) and fin>=fecha_fuente(ahora()) else 'Sin habilitación vigente'
        rutas.append({'type':'Feature','geometry':g,'properties':{**p,'interpretacion':estado}})
    return {'type':'FeatureCollection','features':rutas}


def documento_territorio(nodo, relieve, recursos, rutas):
    puntos = []
    if relieve is not None and not relieve.empty:
        minimo,maximo = float(relieve.cota_m.min()),float(relieve.cota_m.max())
        for r in relieve.to_dict('records'):
            frac = (r['cota_m']-minimo)/max(1,maximo-minimo)
            puntos.append({**r,'color':f'hsl({int(210*(1-frac))},70%,45%)'})
    datos = {'nodo':nodo,'dem':puntos,'recursos':recursos,'rutas':rutas,'provincias':GEOMETRIAS_LITORAL}
    payload = json.dumps(json_finito(datos),ensure_ascii=False,default=str,allow_nan=False).replace('</', '<\\/')
    return '''<!doctype html><html lang="es"><head><meta charset="utf-8">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>html,body{margin:0;font:14px Arial}#map{height:550px}.leyenda{background:white;padding:10px;max-width:260px;border-radius:8px;box-shadow:0 2px 8px #999}</style></head>
<body><div id="map" aria-label="Mapa de relieve, puntos de salud, refugios y rutas"></div><script>
const D=PAYLOAD; const M=L.map('map').setView([D.nodo.lat,D.nodo.lon],11);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ign=L.tileLayer('https://wms.ign.gob.ar/geoserver/gwc/service/tms/1.0.0/capabaseargenmap@EPSG%3A3857@png/{z}/{x}/{y}.png',{tms:true,maxZoom:18,attribution:'IGN Argenmap'}).addTo(M);
const osm=L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap'});
let errores=0;ign.on('tileerror',()=>{if(++errores===3){M.removeLayer(ign);osm.addTo(M)}});
const dem=L.layerGroup(),salud=L.layerGroup(),rec=L.layerGroup(),rutas=L.layerGroup(),prov=L.layerGroup();
Object.entries(D.provincias).forEach(([n,g])=>L.geoJSON({type:'Feature',geometry:g},{style:{weight:1,color:'#34495e',fillOpacity:0}}).bindTooltip(esc(n)).addTo(prov));prov.addTo(M);
D.dem.forEach(p=>L.circleMarker([p.lat,p.lon],{radius:5,color:p.color,fillOpacity:.8,weight:0}).bindTooltip(esc(p.cota_m.toFixed(1)+' m · muestra DEM')).addTo(dem));dem.addTo(M);
D.recursos.forEach(p=>{const destino=p.tipo==='salud'?salud:rec;L.circleMarker([p.lat,p.lon],{radius:p.tipo==='salud'?6:8,color:p.tipo==='salud'?'#7c3aed':p.utilizable?'#15803d':'#6b7280',fillOpacity:.8}).bindPopup('<b>'+esc(p.nombre)+'</b><br>'+esc(p.tipo)+'<br>'+esc(p.estado)+'<br>'+esc(p.fuente)+'<br>Antiofídico: '+esc(p.suero_antiofidico||'sin confirmar')).addTo(destino)});salud.addTo(M);rec.addTo(M);
L.geoJSON(D.rutas,{style:f=>({color:f.properties.estado==='cerrado'?'#dc2626':'#f59e0b',weight:5,dashArray:'8 6'}),onEachFeature:(f,l)=>l.bindPopup('<b>'+esc(f.properties.nombre)+'</b><br>'+esc(f.properties.interpretacion)+'<br>'+esc(f.properties.autoridad))}).addTo(rutas);rutas.addTo(M);
L.marker([D.nodo.lat,D.nodo.lon]).bindPopup(esc(D.nodo.localidad)+' · referencia meteorológica').addTo(M);
L.control.layers({'IGN':ign,'OpenStreetMap':osm},{'Muestras de relieve':dem,'Salud · catálogo':salud,'Recursos declarados':rec,'Rutas verificadas por el usuario':rutas,'Provincias':prov}).addTo(M);L.control.scale({imperial:false}).addTo(M);
const ley=L.control({position:'bottomleft'});ley.onAdd=()=>{const e=L.DomUtil.create('div','leyenda');e.innerHTML='<b>Planificación territorial</b><br>DEM: azul bajo → rojo alto, respecto de esta malla.<br>Violeta: salud; gris: recurso sin vigencia; verde: habilitación declarada vigente.<br>Una cota mayor no certifica un refugio. Confirmar acceso, capacidad y atención.';return e};ley.addTo(M);
setTimeout(()=>M.invalidateSize(),0);</script></body></html>'''.replace('PAYLOAD',payload)


def fusion_territorial(modelo, agua=None, suelo=None, cota_baja=None, rio=None):
    indice = float(modelo.get('indice',np.nan)); lluvia = float(modelo.get('lluvia_futura_72',np.nan))
    valido_modelo = bool(np.isfinite(indice) and modelo.get('cobertura_meteorologica', True))
    if modelo.get('actualizado'):
        try:
            stamp = datetime.strptime(modelo['actualizado'], '%d/%m/%Y %H:%M')
            valido_modelo = valido_modelo and dato_vigente(stamp, 12)
        except (ValueError, TypeError):
            valido_modelo = False
    umbral = st.session_state.get('ficha_umbral_local',55.0)
    nivel = (2 if indice>=umbral else 1 if indice>=35 else 0) if valido_modelo else None
    motivos = ([f'Índice meteorológico {indice:.1f}/100; umbral rojo experimental {umbral:g}']
               if valido_modelo else ['Meteorología ausente, incompleta o calculada hace más de 12 horas; no se interpreta como riesgo bajo'])
    cobertura = int(valido_modelo)
    if agua is not None and dato_vigente(agua.get('fecha'),72) and np.isfinite(agua.get('NDWI',np.nan)):
        cobertura += 1
        if agua['NDWI']>0 and valido_modelo and np.isfinite(lluvia) and lluvia>=30:
            nivel = 2; motivos.append('NDWI positivo reciente y lluvia prevista ≥ 30 mm/72 h; contrastar detección de agua')
    if suelo is not None and dato_vigente(suelo.get('fecha'),10*24):
        cobertura += 1
        if suelo.get('agua_util_pct',0)>=90 and valido_modelo and np.isfinite(lluvia) and lluvia>=30:
            nivel = max(nivel,1); motivos.append('Agua útil ≥ 90 % y lluvia prevista ≥ 30 mm/72 h')
            if cota_baja:
                nivel = 2; motivos.append('Además, cota en el quintil inferior de la malla local')
    if cota_baja is not None:
        cobertura += 1
    if rio and dato_vigente(rio.get('fecha'),48):
        estado = clasificar_rio(float(rio['altura']),rio['estacion'])
        if estado=='EVACUACIÓN':
            nivel=2; motivos.append('Estación fluvial de referencia sobre umbral INA de evacuación; evaluar área de influencia')
        elif estado=='ALERTA':
            nivel=max(nivel or 0,1); motivos.append('Estación fluvial de referencia sobre umbral INA de alerta')
    return {'semaforo': ['VERDE','AMARILLO','ROJO'][nivel] if nivel is not None else 'SIN DATOS',
            'motivos':motivos,'cobertura':min(cobertura,4)}


@st.cache_data(ttl=86400, show_spinner=False)
def recorrido_orientativo(lat1,lon1,lat2,lon2):
    url = f'https://router.project-osrm.org/route/v1/driving/{lon1:.6f},{lat1:.6f};{lon2:.6f},{lat2:.6f}'
    try:
        d = descargar_publico(url,{'overview':'full','geometries':'geojson','alternatives':'false'}).json()
        if d.get('code')!='Ok' or not d.get('routes'):
            raise ValueError('Sin recorrido en la red vial')
        ruta = d['routes'][0]
        return {'geometry':ruta['geometry'],'distancia_km':ruta['distance']/1000,'error':''}
    except Exception as exc:
        return {'error':str(exc) or type(exc).__name__}


def mostrar_territorio_ficha():
    st.subheader('Semáforo operativo y planificación territorial')
    nod = st.selectbox('Localidad para planificar',range(len(NODOS)),
                       format_func=lambda i:NODOS[i]['localidad']+' · '+NODOS[i]['provincia'],key='ficha_localidad')
    nodo = NODOS[nod]
    radio = st.select_slider('Entorno de muestreo de relieve (km)',[2,5,10,20],value=5,key='ficha_radio')
    if st.button('Consultar relieve del entorno',key='ficha_dem_cargar'):
        with st.spinner('Consultando cotas del DEM Copernicus…'):
            st.session_state['ficha_relieve'] = (nod,radio,consultar_relieve(nodo['lat'],nodo['lon'],radio))
    dem = pd.DataFrame(); baja = None
    rd = st.session_state.get('ficha_relieve')
    if rd and rd[:2]==(nod,radio):
        if rd[2]['error']:
            st.warning('Relieve no disponible: '+rd[2]['error'])
        else:
            dem = rd[2]['df']
            nearest = distancia_km(nodo['lat'],nodo['lon'],dem.lat,dem.lon).argmin()
            cota = float(dem.iloc[nearest].cota_m); baja = bool(cota<=dem.cota_m.quantile(.2))
            a,b,c=st.columns(3)
            a.metric('Cota de la muestra central',f'{cota:.1f} m')
            b.metric('Rango de cotas del entorno',f'{dem.cota_m.min():.0f} – {dem.cota_m.max():.0f} m')
            c.metric('Posición local','Quintil inferior' if baja else 'Fuera del quintil inferior')
            st.download_button('Descargar muestreo del relieve',dem.to_csv(index=False).encode('utf-8-sig'),
                f'DEM_{nodo["localidad"]}.csv','text/csv',key='ficha_dem_csv')
    st.caption('Fuente: Copernicus DEM GLO-90 vía Open-Meteo. Se consultan 225 muestras en una malla de 15×15; su separación depende del radio. La malla no resuelve canales, defensas, alcantarillas ni garantiza terrenos libres de inundación.')
    st.link_button('Fuente y resolución del DEM','https://open-meteo.com/en/docs/elevation-api')
    salud = pd.DataFrame(copia_ficha('salud_ign'))
    origen_salud = f'Ubicaciones IGN/SISA · copia consultada {FICHA_FECHA_COPIA}; relevamiento sin fecha'
    if st.button('Actualizar catálogo de puntos de salud',key='ficha_salud_actualizar'):
        st.session_state['ficha_salud'] = consultar_salud_ign()
    if 'ficha_salud' in st.session_state:
        salud,origen_salud=st.session_state['ficha_salud']
    salud=salud.copy();salud['distancia_km']=distancia_km(nodo['lat'],nodo['lon'],salud.lat,salud.lon)
    salud=salud[salud.distancia_km<=80].sort_values('distancia_km').head(50)
    extras = pd.DataFrame()
    with st.expander('Refugios, terrenos altos, salud y limpieza de canales'):
        st.write('Registro de planificación aportado por municipio, Defensa Civil o comité de cuenca. La habilitación debe tener responsable, evidencia, hora de verificación y fin de vigencia. Los datos cargados siguen siendo declaraciones de su fuente.')
        plantilla='nombre,tipo,lat,lon,fecha_verificacion,valido_hasta,autoridad,fuente,estado,suero_antiofidico\n'
        st.download_button('Plantilla de recursos territoriales',plantilla,'recursos_territoriales.csv','text/csv',key='ficha_recursos_plantilla')
        st.caption('Tipos: refugio, salud, terreno_alto, limpieza_canal. Estados: habilitado, cerrado, sin_confirmar. Suero: confirmado, sin_confirmar, no. Las fechas deben incluir hora y zona; sin zona se interpretan en ART. Vigencia operativa máxima: 24 horas desde la verificación.')
        archivo=st.file_uploader('Registro territorial CSV',type=['csv'],key='ficha_recursos_csv')
        if archivo is not None:
            try:
                extras=validar_recursos(pd.read_csv(archivo,dtype={'fuente':str,'autoridad':str}).fillna(''))
                st.dataframe(extras,use_container_width=True)
                st.download_button('Guardar registro para volver a cargarlo',extras.to_csv(index=False).encode('utf-8-sig'),'registro_territorial.csv','text/csv',key='ficha_recursos_guardar')
            except Exception as exc:
                st.error('Registro rechazado: '+str(exc))
        st.caption('El registro queda en esta sesión. Descargalo para conservarlo y cargarlo en una próxima sesión.')
        hist=pd.DataFrame(copia_ficha('refugios_vera'))
        st.write('**Antecedente documentado · Vera, 27/05/2025**')
        st.dataframe(hist[['nombre','tipo','estado','fuente']],use_container_width=True)
        st.caption('Cuatro centros estuvieron habilitados en ese evento. Sólo se cartografían los dos puntos cuya ubicación está en el catálogo provincial de salud. Habilitación actual y disponibilidad de antiofídicos: sin confirmar.')
        st.link_button('Parte oficial del operativo de Vera',CASO_VERA_URL)
    recursos=salud.to_dict('records')
    recursos+=extras.to_dict('records') if not extras.empty else []
    if nodo['localidad']=='Vera':
        recursos+=[p for p in copia_ficha('refugios_vera') if p.get('lat') is not None]
    rutas={'type':'FeatureCollection','features':[]}
    with st.expander('Rutas, cortes y desvíos · Vialidad Nacional'):
        if st.button('Consultar estado de rutas del Litoral',key='ficha_vialidad_cargar'):
            with st.spinner('Consultando tabla pública de Vialidad…'):
                st.session_state['ficha_vialidad']=consultar_vialidad()
        vial=st.session_state.get('ficha_vialidad')
        if vial:
            if vial['error']:
                st.warning('No se pudo consultar Vialidad: '+vial['error'])
            else:
                vd=vial['df'].copy()
                # Recalcular vigencia en cada render, incluso si la respuesta viene de caché.
                vd['vigencia']=vd.fecha.map(lambda t:'Reciente · ≤ 24 h' if pd.notna(t) and dato_vigente(t,24) else 'Reconfirmar · reporte antiguo/sin fecha')
                st.caption('Consulta: '+vial['consulta']+'. Cada fila conserva su fecha original; cache de consulta: 30 min.')
                provincia=st.selectbox('Provincia del reporte vial',['Todas','Santa Fe','Corrientes','Entre Ríos'],key='ficha_vial_prov')
                if provincia!='Todas':
                    vd=vd[vd.provincia.map(normalizar_nombre)==normalizar_nombre(provincia)]
                st.dataframe(vd[['provincia','ruta','tramo','estado','calzada','observaciones','actualizado','vigencia']],use_container_width=True,hide_index=True)
                st.download_button('Descargar reportes viales',vd.to_csv(index=False).encode('utf-8-sig'),'Vialidad_Litoral.csv','text/csv',key='ficha_vial_csv')
        st.link_button('Fuente oficial de Vialidad Nacional',VIALIDAD_URL)
        st.caption('Cobertura: rutas nacionales publicadas. Los caminos rurales y provinciales pueden no estar incluidos. Un tramo ausente en la tabla no equivale a transitable.')
        st.write('**Cartografiar trazados revisados**')
        ejemplo={'type':'FeatureCollection','features':[]}
        st.download_button('Estructura GeoJSON para rutas',json.dumps(ejemplo,indent=2),'rutas_verificadas.geojson','application/geo+json',key='ficha_rutas_plantilla')
        st.caption('Cada Feature: LineString en WGS84 [longitud,latitud] y properties con nombre, autoridad, fuente, fecha_verificacion, valido_hasta y estado (habilitado/cerrado/sin_confirmar).')
        archivo=st.file_uploader('Rutas revisadas por la autoridad (GeoJSON)',type=['geojson','json'],key='ficha_rutas_archivo')
        if archivo is not None:
            try:
                rutas=validar_rutas_geojson(json.load(archivo))
            except Exception as exc:
                st.error('Rutas rechazadas: '+str(exc))
    with st.expander('Proponer un recorrido para revisar'):
        st.write('Trazado sobre la red OpenStreetMap mediante OSRM. No incorpora cortes ni inundaciones y necesita revisión con los reportes viales y la autoridad local antes de usarse.')
        candidatos=recursos
        if candidatos:
            dest=st.selectbox('Destino de referencia',range(len(candidatos)),format_func=lambda i:candidatos[i]['nombre'],key='ficha_ruta_destino')
            if st.button('Calcular trazado orientativo',key='ficha_ruta_calcular'):
                p=candidatos[dest]
                st.session_state['ficha_ruta_calculada']=(nod,p['nombre'],recorrido_orientativo(nodo['lat'],nodo['lon'],float(p['lat']),float(p['lon'])))
            guardada=st.session_state.get('ficha_ruta_calculada')
            if guardada and guardada[:2]==(nod,candidatos[dest]['nombre']):
                r=guardada[2]
                if r['error']:
                    st.warning('Sin trazado disponible: '+r['error'])
                else:
                    st.metric('Longitud del trazado orientativo',f'{r["distancia_km"]:.1f} km')
                    rutas['features'].append({'type':'Feature','geometry':r['geometry'],'properties':{
                        'nombre':'Trazado OSRM · sin verificar','estado':'sin_confirmar','autoridad':'OpenStreetMap / OSRM',
                        'interpretacion':'No incorpora cortes ni anegamientos'}})
        else:
            st.info('No hay destinos con coordenadas en el entorno seleccionado.')
    components.html(documento_territorio(nodo,dem,recursos,rutas),height=555,scrolling=False)
    st.caption(origen_salud+'. Los puntos de salud no certifican atención disponible, centro de evacuación ni existencias de suero antiofídico.')
    if not salud.empty:
        st.dataframe(salud[['nombre','provincia','distancia_km','estado']],use_container_width=True,hide_index=True)
    with st.expander('Observaciones de suelo para la fusión'):
        st.write('Podés cargar valores de agua útil del perfil extraídos del producto INTA o de un relevamiento autorizado. Identificá el método de extracción en la fuente. No se toman valores de un mapa nacional como si fueran mediciones de un lote.')
        st.download_button('Plantilla de agua útil', 'localidad,fecha,fuente,agua_util_pct\n','agua_util.csv','text/csv',key='ficha_suelo_plantilla')
        archivo=st.file_uploader('Agua útil por localidad (CSV)',type=['csv'],key='ficha_suelo_csv')
        if archivo is not None:
            try:
                ds=pd.read_csv(archivo)
                if not {'localidad','fecha','fuente','agua_util_pct'}.issubset(ds):
                    raise ValueError('Columnas incompletas')
                ds['fecha']=ds.fecha.map(fecha_fuente);ds['agua_util_pct']=pd.to_numeric(ds.agua_util_pct,errors='coerce')
                if not ds.agua_util_pct.between(0,100).all() or not ds.fuente.astype(str).str.match(r'^https?://\S+$').all():
                    raise ValueError('Agua útil fuera de 0–100 % o fuente ausente')
                st.session_state['ficha_suelo']=ds
                st.dataframe(ds,use_container_width=True)
            except Exception as exc:
                st.session_state.pop('ficha_suelo',None);st.error('Suelo rechazado: '+str(exc))
        else:
            st.session_state.pop('ficha_suelo',None)
    df=st.session_state.get('df_alerta',pd.DataFrame())
    modelo=df[df.localidad==nodo['localidad']].iloc[0].to_dict() if not df.empty and 'localidad' in df else {}
    agua=None; suelo=None; rio=None
    indices=st.session_state.get('ficha_indices',pd.DataFrame())
    if not indices.empty:
        match=indices[(indices.localidad.map(normalizar_nombre)==normalizar_nombre(nodo['localidad'])) &
                      (distancia_km(nodo['lat'],nodo['lon'],indices.lat,indices.lon)<=10)]
        if not match.empty:
            agua=match.sort_values('fecha').iloc[-1].to_dict()
    suelos=st.session_state.get('ficha_suelo',pd.DataFrame())
    if not suelos.empty:
        match=suelos[suelos.localidad.map(normalizar_nombre)==normalizar_nombre(nodo['localidad'])]
        if not match.empty:
            suelo=match.sort_values('fecha').iloc[-1].to_dict()
    ri=st.session_state.get('ficha_ina_resultado')
    if ri and ri['estacion'].get('lat') is not None and distancia_km(nodo['lat'],nodo['lon'],ri['estacion']['lat'],ri['estacion']['lon'])<=25:
        r=ri['series'][0]
        if not r['error'] and int(r['meta'].get('unitId',-1))==11:
            obs=r['df'].iloc[-1];rio={'fecha':obs.fecha,'altura':obs.valor,'estacion':ri['estacion']}
    fusion=fusion_territorial(modelo,agua,suelo,baja,rio)
    st.subheader('Semáforo integrado · '+nodo['localidad'])
    colores={'VERDE':'🟢','AMARILLO':'🟡','ROJO':'🔴','SIN DATOS':'⚪'}
    st.markdown(f'### {colores[fusion["semaforo"]]} {fusion["semaforo"]}')
    st.caption(f'Cobertura territorial: {fusion["cobertura"]}/4 componentes (meteorología, suelo de fuente externa, agua superficial, relieve). La información incompleta mantiene incertidumbre; no certifica ausencia de riesgo.')
    for motivo in fusion['motivos']:
        st.write('• '+motivo)
    acciones={
        'VERDE':'Mantener vigilancia; revisar ventanas de siembra/cosecha con el pronóstico diario y verificar portancia del suelo antes de ingresar maquinaria.',
        'AMARILLO':'Preparar traslado de ganado a terrenos altos verificados, revisar desagües y limpieza de canales con la autoridad y confirmar accesos a establecimientos de salud.',
        'ROJO':'Priorizar evaluación en terreno y coordinación con Defensa Civil; considerar traslado de ganado y protección de insumos, suspender tareas en áreas anegadas y reconfirmar cada acceso.',
        'SIN DATOS':'Consultar avisos SMN, reportes INA y la autoridad local; completar datos antes de planificar movimientos.'}
    st.info(acciones[fusion['semaforo']])
    st.caption('Reglas de fusión exploratorias: modelo rojo ≥ 55/100 (o umbral local evaluado), amarillo ≥ 35; NDWI > 0 de ≤ 72 h + lluvia ≥ 30 mm/72 h → rojo; agua útil ≥ 90 % de ≤ 10 días + lluvia ≥ 30 → amarillo y, en quintil inferior de relieve, rojo. Un umbral INA vigente puede elevar el estado sólo en el entorno de su estación. ENOS aporta contexto, sin sumar puntos automáticamente.')
    informe={'version':VERSION,'fecha':ahora().isoformat(),'localidad':nodo['localidad'],
             'semaforo':fusion,'modelo':modelo,'agua':agua,'suelo':suelo,'rio':rio,
             'relieve_consultado':not dem.empty,'rutas':rutas,'recursos_declarados':extras.to_dict('records'),
             'limitaciones':'Prototipo experimental; confirmar áreas de influencia, accesos, refugios y atención con autoridad local.'}
    st.download_button('Descargar informe de planificación',json.dumps(json_finito(informe),ensure_ascii=False,indent=2,default=str,allow_nan=False),
                       f'planificacion_{nodo["localidad"]}.json','application/json',key='ficha_plan_descarga')


def reconstruir_vera():
    pasado=copia_ficha('vera_hist');resultados=[]
    variables=['precipitation','runoff','soil_moisture_0_to_7cm','soil_moisture_7_to_28cm','soil_moisture_28_to_100cm','soil_moisture_100_to_255cm']
    for dia in ['2025-05-24','2025-05-25','2025-05-26']:
        run=copia_ficha('vera_'+dia);referencia=pd.Timestamp(dia+'T06:00:00Z').tz_convert(TZ).tz_localize(None)
        th=pd.DatetimeIndex(pd.to_datetime(pasado['hourly']['time']));tr=pd.DatetimeIndex(pd.to_datetime(run['hourly']['time']))
        hp=(th>referencia-pd.Timedelta(days=7)) & (th<referencia)
        hr=(tr>=referencia) & (tr<=referencia+pd.Timedelta(days=7))
        horas=list(th[hp])+list(tr[hr]);hourly={'time':[t.isoformat() for t in horas]}
        for var in variables:
            a=np.array(pasado['hourly'].get(var,[None]*len(th)),dtype=float)
            b=np.array(run['hourly'].get(var,[None]*len(tr)),dtype=float)
            hourly[var]=np.r_[a[hp],b[hr]].tolist()
        r=procesar_nodo(next(n for n in NODOS if n['localidad']=='Vera'),{'hourly':hourly},referencia)
        resultados.append({'inicializacion_UTC':dia+'T00:00Z','corte_ART':referencia.isoformat(),
            'indice':r['indice'],'nivel':r['nivel'],'lluvia_previa_72_mm':r['lluvia_72'],
            'lluvia_prevista_72_mm':r['lluvia_futura_72'],'humedad_modelo_m3_m3':r['humedad_suelo_promedio'],
            'escorrentia_previa_72_mm':r['runoff_72']})
    return pd.DataFrame(resultados)


def metricas_clasificacion(y,p):
    y=np.asarray(y,dtype=int);p=np.asarray(p,dtype=int)
    tp=int(np.sum((y==1)&(p==1)));tn=int(np.sum((y==0)&(p==0)))
    fp=int(np.sum((y==0)&(p==1)));fn=int(np.sum((y==1)&(p==0)))
    return {'VP':tp,'VN':tn,'FP':fp,'FN':fn,'sensibilidad':tp/(tp+fn) if tp+fn else None,
            'especificidad':tn/(tn+fp) if tn+fp else None,
            'precision':tp/(tp+fp) if tp+fp else None,
            'F1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0.0}


def evaluar_umbral_historico(df):
    cols={'evento_id','fecha_evento','emision','indice','anegamiento','fuente'}
    if not cols.issubset(df.columns):
        raise ValueError('Faltan columnas: '+', '.join(sorted(cols-set(df.columns))))
    d=df.copy()
    if d.evento_id.astype(str).str.strip().eq('').any() or d.evento_id.duplicated().any():
        raise ValueError('Usá una fila por evento independiente; IDs únicos y no vacíos')
    if len(d)<10:
        raise ValueError('Se requieren al menos 10 episodios independientes, incluyendo controles con ausencia verificada de anegamiento')
    d['fecha_evento']=d.fecha_evento.map(fecha_fuente);d['emision']=d.emision.map(fecha_fuente)
    d['indice']=pd.to_numeric(d.indice,errors='coerce');d['anegamiento']=pd.to_numeric(d.anegamiento,errors='coerce')
    if not d.indice.between(0,100).all() or not d.anegamiento.isin([0,1]).all():
        raise ValueError('Índice 0–100 y anegamiento observado 0/1 son obligatorios')
    if not d.fuente.astype(str).str.match(r'^https?://\S+$').all():
        raise ValueError('Cada etiqueta, incluida ausencia de anegamiento, necesita evidencia')
    if ((d.fecha_evento-d.emision).dt.total_seconds()<6*3600).any():
        raise ValueError('El índice debe provenir de información disponible al menos 6 horas antes del evento')
    d=d.sort_values('fecha_evento').reset_index(drop=True);corte=int(len(d)*.7)
    train,test=d.iloc[:corte],d.iloc[corte:]
    if train.fecha_evento.max()>=test.fecha_evento.min():
        raise ValueError('La fecha del corte se comparte entre entrenamiento y prueba; separar los episodios')
    if train.anegamiento.nunique()<2 or test.anegamiento.nunique()<2:
        raise ValueError('Cada tramo temporal necesita eventos y controles observados; no se infieren negativos por falta de noticias')
    if train.fecha_evento.max()>=test.emision.min():
        raise ValueError('La calibración debe finalizar antes de emitir los índices del período de prueba')
    opciones=[]
    for umbral in range(35,81):
        m=metricas_clasificacion(train.anegamiento,train.indice>=umbral)
        opciones.append((m['F1'],m['sensibilidad'] or 0,-abs(umbral-55),umbral))
    umbral=max(opciones)[-1]
    salida={'umbral_rojo':umbral,'n_entrenamiento':len(train),'n_prueba':len(test),
            'criterio':'F1 de entrenamiento; desempate por sensibilidad y cercanía a 55',
            'entrenamiento_hasta':train.fecha_evento.max().isoformat(),
            'prueba_desde':test.fecha_evento.min().isoformat(),
            'metricas_prueba':metricas_clasificacion(test.anegamiento,test.indice>=umbral),
            'modelo':VERSION,'estado':'Evaluación local exploratoria; no validación institucional'}
    test=test.copy();test['prediccion_rojo']=(test.indice>=umbral).astype(int)
    return salida,test


def mostrar_historia_ficha():
    st.subheader('Caso histórico, evaluación y ficha del proyecto')
    st.write('Problema: anticipar anegamiento en Corrientes, Entre Ríos y Santa Fe para productores, comités de cuenca, municipios y Defensa Civil. Integra acumulados y pronósticos, suelo, agua superficial y relieve para apoyar decisiones agropecuarias y territoriales.')
    caso2016,casovera,calibracion,inventario=st.tabs(['Litoral · abril 2016','Vera · mayo 2025','Evaluar umbrales','Datos, vacíos y decisiones'])
    with caso2016:
        st.write('**Episodio de El Niño y anegamientos del Litoral · abril de 2016.** El SMN documentó lluvias excepcionales, suelos y caminos anegados y dificultades de cosecha. En Concordia registró 605,5 mm mensuales y 21 días con lluvia. El boletín incluye mapas de agua en suelo e índices de vegetación.')
        st.link_button('Boletín agrometeorológico SMN · evidencia del caso',CASO_2016_URL)
        d=copia_ficha('concordia_2016');h=d['hourly']
        df=pd.DataFrame({'fecha':pd.to_datetime(h['time']),'precipitacion_mm':h['precipitation'],
            'humedad_0_7_m3_m3':h['soil_moisture_0_to_7cm'],'humedad_7_28_m3_m3':h['soil_moisture_7_to_28cm'],
            'humedad_28_100_m3_m3':h['soil_moisture_28_to_100cm']})
        abril=df[df.fecha.dt.month==4];diaria=abril.groupby(abril.fecha.dt.date).precipitacion_mm.sum(min_count=24).reset_index()
        a,b,c=st.columns(3);a.metric('SMN Concordia · acumulado observado','605,5 mm');b.metric('ERA5 · estimación de celda en abril',f'{diaria.precipitacion_mm.sum():.1f} mm')
        roni=pd.DataFrame(copia_ficha('roni'));roni2016=roni[(roni['año']==2016)&(roni.trimestre=='MAM')]
        c.metric('RONI MAM 2016 · NOAA',f'{roni2016.iloc[0].RONI:+.1f} °C')
        st.plotly_chart(px.bar(diaria,x='fecha',y='precipitacion_mm',labels={'precipitacion_mm':'Lluvia estimada ERA5 (mm/día)'}),use_container_width=True,key='ficha_2016_rain')
        st.plotly_chart(px.line(abril,x='fecha',y=['humedad_0_7_m3_m3','humedad_7_28_m3_m3','humedad_28_100_m3_m3'],labels={'value':'Contenido volumétrico m³/m³'}),use_container_width=True,key='ficha_2016_soil')
        ina=copia_ficha('ina_2016');rio=pd.DataFrame(ina['data'])
        rio['fecha']=rio.timestart.map(fecha_fuente);rio['altura_m']=pd.to_numeric(rio.valor,errors='coerce')
        st.plotly_chart(px.line(rio,x='fecha',y='altura_m',title='INA · escala de Santa Fe · marzo–abril 2016'),use_container_width=True,key='ficha_2016_river')
        st.caption('ERA5 es un reanálisis de aproximadamente 0,25°, no un pronóstico emitido antes del evento ni una medición de la estación SMN. Celda devuelta: '+f'{d["latitude"]}, {d["longitude"]}. Los acumulados puntuales y de celda no son equivalentes. La escala INA Santa Fe aporta contexto fluvial en otro tramo; no mide agua en los lotes de Concordia. ENOS es contexto y no prueba una causa única.')
        st.download_button('Descargar reconstrucción horaria 2016',df.to_csv(index=False).encode('utf-8-sig'),'Concordia_ERA5_2016.csv','text/csv',key='ficha_2016_csv')
        st.link_button('Método y fuente de reanálisis','https://open-meteo.com/en/docs/historical-weather-api')
        st.link_button('Serie INA Santa Fe del caso',INA_BASE+'datos&seriesId=30&timeStart=2016-03-25&timeEnd=2016-05-01&format=json')
        st.info('Conclusión del caso: lluvia persistente, suelo húmedo y niveles fluviales requieren evaluación conjunta. El episodio fundamenta la vigilancia agropecuaria; no permite certificar por sí solo la precisión del semáforo.')
    with casovera:
        st.write('**Vera · 26–27/05/2025.** El parte provincial del 27/05 reportó más de 400 mm en pocas horas, 117 evacuados y cuatro centros de asistencia. Hay evidencia positiva de impacto; no se atribuye ausencia de anegamiento a los días sin parte.')
        st.link_button('Fuente provincial del impacto observado',CASO_VERA_URL)
        resultados=reconstruir_vera();st.dataframe(resultados,use_container_width=True,hide_index=True)
        st.plotly_chart(px.bar(resultados,x='corte_ART',y='lluvia_prevista_72_mm',labels={'lluvia_prevista_72_mm':'Lluvia estimada a 72 h (mm)'}),use_container_width=True,key='ficha_vera_chart')
        st.caption('Datos: ECMWF IFS HRES vía Open-Meteo Single Runs, inicializaciones 00 UTC del 24, 25 y 26 de mayo. Se fija el corte a 06 UTC (03 ART) como demora de disponibilidad supuesta. Sólo se usa el futuro de esa corrida; los antecedentes provienen del archivo horario y son una reconstrucción retrospectiva. El archivo puede incluir hindcasts del ciclo IFS 49R1: no certifica qué información recibió un usuario en 2025. Malla devuelta: '+str(copia_ficha('vera_2025-05-26')['latitude'])+', '+str(copia_ficha('vera_2025-05-26')['longitude'])+'.')
        st.warning('El modelo de celda subestima fuertemente la lluvia puntual documentada. Este caso muestra una omisión potencial del semáforo y la necesidad de incorporar observaciones, avisos SMN y vigilancia radar. No se corrige el umbral usando el mismo evento como prueba de éxito.')
        st.download_button('Descargar evaluación de Vera',resultados.to_csv(index=False).encode('utf-8-sig'),'Evaluacion_Vera_2025.csv','text/csv',key='ficha_vera_csv')
        st.link_button('Documentación de corridas archivadas','https://open-meteo.com/en/docs/single-runs-api')
    with calibracion:
        st.write('La ficha propone calibrar el semáforo. Este módulo evalúa un umbral local con episodios independientes y controles documentados, separando el 70 % más antiguo para ajuste y el 30 % posterior para prueba. Los dos casos incluidos todavía no aportan suficientes controles para validar un umbral.')
        plantilla='evento_id,fecha_evento,emision,indice,anegamiento,fuente\n'
        st.download_button('Plantilla de eventos para evaluación',plantilla,'eventos_evaluacion.csv','text/csv',key='ficha_cal_plantilla')
        archivo=st.file_uploader('Episodios y controles observados',type=['csv'],key='ficha_cal_csv')
        st.caption('Mínimo: 10 eventos independientes, una fila por episodio, ambas clases en ajuste y prueba. anegamiento=0 necesita ausencia verificada; no basta que falte un reporte. emision identifica cuándo estuvieron disponibles TODOS los datos del índice y debe preceder al evento al menos 6 horas. Separá episodios de la misma tormenta bajo un único ID antes de cargar.')
        if archivo is not None:
            try:
                datos=pd.read_csv(archivo);res,prueba=evaluar_umbral_historico(datos)
                st.metric('Umbral rojo propuesto',f'{res["umbral_rojo"]}/100')
                st.dataframe(pd.DataFrame([res['metricas_prueba']]),use_container_width=True)
                st.dataframe(prueba,use_container_width=True)
                st.download_button('Descargar protocolo y métricas',json.dumps(res,ensure_ascii=False,indent=2),'evaluacion_umbral.json','application/json',key='ficha_cal_export')
                if st.button('Aplicar umbral evaluado a la fusión local',key='ficha_cal_aplicar'):
                    st.session_state['ficha_umbral_local']=res['umbral_rojo'];st.session_state['ficha_cal_protocolo']=res
                    st.rerun()
            except Exception as exc:
                st.error('Evaluación no disponible: '+str(exc))
        if 'ficha_umbral_local' in st.session_state:
            st.info(f'Umbral local activo en esta sesión: {st.session_state["ficha_umbral_local"]}/100. El índice meteorológico conserva su formulación; el cambio afecta la fusión territorial.')
            if st.button('Volver al umbral experimental de 55',key='ficha_cal_reset'):
                st.session_state.pop('ficha_umbral_local',None);st.session_state.pop('ficha_cal_protocolo',None);st.rerun()
    with inventario:
        inventario_datos=[
            {'Fuente':'SMN','Dato':'Lluvia, observaciones, alertas CAP y tendencia trimestral','Uso':'Datos oficiales SMN; radar para vigilancia','Frecuencia / cobertura':'Horaria/diaria y trimestre; cobertura por estación/área','Limitación':'Avisos oficiales y red rural incompleta; tendencia no es lluvia de un día'},
            {'Fuente':'INTA SEPA','Dato':'Recarga del perfil, excedentes, NDVI y archivo de situación hídrica','Uso':'Mapas dentro de la app; contraste con modelo y muestras NDWI','Frecuencia / cobertura':'Suelo S-NPP 500 m, decadal; NDVI MODIS 250 m, compuesto 16 días','Limitación':'Nubes y ventana compuesta; JPG no da un valor automático por lote; composición hídrica no es clasificación; NDWI requiere bandas/muestras'},
            {'Fuente':'INA','Dato':'Altura, caudal y umbrales por estación','Uso':'Series actuales/históricas y comparación con umbral','Frecuencia / cobertura':'Observaciones diarias según serie; revisar último dato','Limitación':'Datum de escala y representatividad del tramo; caudal por curva; series atrasadas'},
            {'Fuente':'Open-Meteo / ECMWF','Dato':'Lluvia 7–14 días, humedad volumétrica, escorrentía','Uso':'Índice meteorológico, reconstrucción y corridas archivadas','Frecuencia / cobertura':'Horaria; modelo de celda ~9 km','Limitación':'Subestimación convectiva; no equivale a medición de campo'},
            {'Fuente':'Vialidad Nacional','Dato':'Estado, tramo, observaciones y fecha de reporte','Uso':'Tabla en la app; contrastar trazados de planificación','Frecuencia / cobertura':'Consulta bajo demanda, caché 30 min','Limitación':'No cubre todos los caminos rurales/provinciales; confirmar reportes >24 h'},
            {'Fuente':'Copernicus / IGN / Defensa Civil','Dato':'DEM, salud, refugios, terrenos altos y rutas','Uso':'Mapa Leaflet, muestreo de cotas y registros verificables','Frecuencia / cobertura':'DEM estático; registros operativos con vigencia declarada','Limitación':'Cota no certifica refugio; habilitación y suero requieren autoridad'},
            {'Fuente':'NOAA CPC','Dato':'RONI y episodios ENOS','Uso':'Contexto estacional e histórico del Pacífico','Frecuencia / cobertura':'Trimestral móvil, publicación mensual desde 1950','Limitación':'Valores revisables; no determina riesgo local por sí solo'},
        ]
        st.dataframe(pd.DataFrame(inventario_datos),use_container_width=True,hide_index=True)
        st.write('**Vacíos y representatividad:** pocas mediciones rurales; humedad y lluvia del modelo representan una celda; sensores satelitales sujetos a nubes; accesos rurales y alcantarillas sin inventario completo; refugios, capacidad, horarios de atención y stock de antiofídicos requieren verificación local vigente.')
        st.write('**Decisiones apoyadas:** preparar traslado de ganado a terrenos altos confirmados; revisar ventanas de siembra/cosecha; priorizar inspección y limpieza de canales; coordinar logística vial, refugios y atención sanitaria con Defensa Civil. El prototipo muestra evidencia y acciones sugeridas, con cobertura y antigüedad.')
        st.write('**Tecnología y método:** Python/Streamlit para integrar series y reglas; Leaflet para territorio; exportaciones CSV/GeoJSON para QGIS. Dos casos con fuentes verificables y módulo de evaluación temporal sin inventar controles negativos. Queda pendiente reunir más episodios independientes y un registro operativo oficial de refugios/accesos para validación institucional.')
        st.download_button('Descargar inventario de datos',pd.DataFrame(inventario_datos).to_csv(index=False).encode('utf-8-sig'),'Inventario_Alerta_Litoral.csv','text/csv',key='ficha_inv_csv')


def mostrar_paneles_ficha():
    """Las fuentes externas siguen disponibles si falla el modelo principal."""
    with tab_fuentes:
        sub_inta, sub_ina = st.tabs(["INTA · suelo y satélite", "INA · ríos"])
        with sub_inta:
            mostrar_inta_ficha()
        with sub_ina:
            mostrar_ina_ficha()
    with tab_enos:
        mostrar_enos_ficha()
    with tab_territorio:
        mostrar_territorio_ficha()
    with tab_historia:
        mostrar_historia_ficha()


# ============================================================
# INTERFAZ
# ============================================================

st.title(
    "🌧️ Alerta Litoral Agro"
)

st.subheader(
    "Sistema experimental de alerta temprana "
    "para riesgo de anegamiento agropecuario"
)

st.caption(
    f"Versión {VERSION} · Santa Fe · Corrientes · "
    f"Entre Ríos · {MODELO}"
)


# ============================================================
# SMN — DATOS EN DIRECTO
# ============================================================

tab_agro, tab_radar, tab_smn, tab_fuentes, tab_enos, tab_territorio, tab_historia = st.tabs([
    "🌧️ Alerta Litoral Agro", "📡 Radar SINARAME", "🇦🇷 Datos oficiales SMN",
    "🌱 Suelo y ríos", "🌊 El Niño / La Niña", "🗺️ Territorio y rutas", "📘 Caso histórico y ficha",
])
with tab_radar:
    mostrar_radar_smn()
with tab_smn:
    mostrar_datos_smn()

# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header(
        "⚙️ Control"
    )

    actualizar = st.button(
        "🔄 Actualizar datos",
        use_container_width=True,
    )

    if actualizar:

        obtener_datos_ecmwf.clear()
        for consulta_ficha in (consultar_ina, estaciones_ina_publicas, catalogo_inta,
                               imagen_inta_publica, obtener_roni, consultar_vialidad,
                               consultar_relieve, consultar_salud_ign, recorrido_orientativo):
            consulta_ficha.clear()
        for clave_ficha in ('ficha_ina_resultado', 'ficha_inta_imagen', 'ficha_relieve',
                            'ficha_vialidad', 'ficha_ruta_calculada', 'ficha_salud'):
            st.session_state.pop(clave_ficha, None)

        st.session_state.pop(
            "df_alerta",
            None,
        )

    st.divider()

    st.markdown(
        """
### Fuentes

**Meteorología principal**
- Open-Meteo
- ECMWF IFS HRES 9 km
- Precipitación / lluvia / chaparrones
- Humedad y temperatura del suelo
- Runoff
- Probabilidad de precipitación
- Viento y ráfagas
- CAPE / VPD
- Presión / nubosidad

**Vigilancia satelital**
- NOAA / GOES-19 ABI
- GeoColor
- Sector Sudamérica Sur

**Suelo e hidrología**
- INTA SEPA: suelo, excedentes y NDVI
- INA: alturas, caudal y umbrales
- NOAA CPC: ENOS (RONI)

**Territorio**
- DEM Copernicus / IGN
- Salud IGN/SISA y registros locales
- Vialidad Nacional: estado de rutas
"""
    )

    st.divider()

    st.markdown(
        """
### Niveles

🟢 **BAJO**

🟡 **MEDIO**

🟠 **ALTO**

🔴 **MUY ALTO**
"""
    )


with tab_agro:
    # ============================================================
    # DATOS
    # ============================================================

    if (
        actualizar
        or "df_alerta" not in st.session_state
    ):

        with st.spinner(
            "Consultando datos meteorológicos de ECMWF..."
        ):

            raw_ecmwf = obtener_datos_ecmwf()

            raw_ecmwf = (
                list(raw_ecmwf)
                if isinstance(
                    raw_ecmwf,
                    list,
                )
                else []
            )

            if len(raw_ecmwf) < len(NODOS):

                raw_ecmwf.extend(
                    [
                        {
                            "error":
                            "Respuesta incompleta de Open-Meteo"
                        }
                    ]
                    * (
                        len(NODOS)
                        - len(raw_ecmwf)
                    )
                )

            raw_ecmwf = raw_ecmwf[
                :len(NODOS)
            ]

            resultados = [
                procesar_nodo(
                    nodo,
                    datos,
                )
                for nodo, datos
                in zip(
                    NODOS,
                    raw_ecmwf,
                )
            ]

            df_alerta = pd.DataFrame(
                resultados
            )

            st.session_state[
                "df_alerta"
            ] = df_alerta

            st.session_state[
                "raw_ecmwf"
            ] = raw_ecmwf

            guardar_historial(
                df_alerta
            )

    else:

        df_alerta = st.session_state[
            "df_alerta"
        ]

        if (
            "raw_ecmwf"
            not in st.session_state
        ):

            st.session_state[
                "raw_ecmwf"
            ] = obtener_datos_ecmwf()

        raw_ecmwf = st.session_state[
            "raw_ecmwf"
        ]


    # ============================================================
    # TELEGRAM AUTOMÁTICO
    # ============================================================

    telegram_resultado = (
        procesar_alertas_telegram(
            df_alerta
        )
    )

    st.session_state[
        "telegram_resultado"
    ] = telegram_resultado


    # ============================================================
    # DATOS VÁLIDOS
    # ============================================================

    df_validos = df_alerta[
        df_alerta["estado_datos"]
        == "OK"
    ].copy()

    if df_validos.empty:

        st.error(
            "No se pudieron obtener datos meteorológicos válidos."
        )

        errores = df_alerta[
            [
                "localidad",
                "provincia",
                "error_datos",
            ]
        ].copy()

        st.dataframe(
            errores,
            use_container_width=True,
            hide_index=True,
        )

        mostrar_paneles_ficha()
        st.stop()


    # ============================================================
    # MÉTRICAS
    # ============================================================

    maximo = df_validos[
        "indice"
    ].max()

    promedio = df_validos[
        "indice"
    ].mean()

    cantidad_alto = int(
        (
            df_validos["nivel"]
            == "ALTO"
        ).sum()
    )

    cantidad_muy_alto = int(
        (
            df_validos["nivel"]
            == "MUY ALTO"
        ).sum()
    )

    cantidad_medio = int(
        (
            df_validos["nivel"]
            == "MEDIO"
        ).sum()
    )

    col1, col2, col3, col4 = st.columns(4)

    with col1:

        st.metric(
            "🔴 Muy alto",
            cantidad_muy_alto,
        )

    with col2:

        st.metric(
            "🟠 Alto",
            cantidad_alto,
        )

    with col3:

        st.metric(
            "📊 Riesgo máximo",
            f"{maximo:.1f}/100",
        )

    with col4:

        st.metric(
            "📈 Riesgo promedio",
            f"{promedio:.1f}/100",
        )


    # ============================================================
    # ESTADO GENERAL
    # ============================================================

    if cantidad_muy_alto > 0:

        st.error(
            f"🔴 Se detectan {cantidad_muy_alto} "
            "nodos en nivel MUY ALTO."
        )

    elif cantidad_alto > 0:

        st.warning(
            f"🟠 Se detectan {cantidad_alto} "
            "nodos en nivel ALTO."
        )

    elif cantidad_medio > 0:

        st.info(
            f"🟡 Se detectan {cantidad_medio} "
            "nodos en nivel MEDIO; se recomienda vigilancia."
        )

    else:

        st.success(
            "🟢 No se detectan nodos en nivel "
            "ALTO o MUY ALTO según el índice actual."
        )


    # ============================================================
    # TELEGRAM VISIBLE
    # ============================================================

    estado_telegram_ui()


    # ============================================================
    # MAPAS
    # ============================================================

    st.header(
        "🗺️ Mapa de riesgo del Litoral"
    )

    st.caption(
        "El encuadre inicial está fijado sobre Santa Fe, "
        "Corrientes y Entre Ríos."
    )

    fig_mapa = crear_mapa(
        df_validos
    )

    st.plotly_chart(
        fig_mapa,
        use_container_width=True,
        config={
            "scrollZoom": True,
            "displaylogo": False,
        },
    )


    st.header(
        "🌐 Windy — pronóstico meteorológico interactivo"
    )

    st.caption(
        "Windy se muestra como herramienta complementaria "
        "de visualización. El mapa arranca centrado sobre "
        "Santa Fe, Corrientes y Entre Ríos."
    )

    windy_overlay = st.selectbox(
        "Variable de Windy",
        options=[
            "wind",
            "rain",
            "temp",
            "clouds",
            "pressure",
        ],
        format_func=lambda x: {
            "wind": "💨 Viento",
            "rain": "🌧️ Lluvia",
            "temp": "🌡️ Temperatura",
            "clouds": "☁️ Nubosidad",
            "pressure": "🌀 Presión",
        }[x],
        key="windy_overlay",
    )

    mostrar_windy(
        windy_overlay
    )

    st.link_button(
        "Abrir Windy",
        construir_windy_url(
            windy_overlay
        ),
        use_container_width=True,
    )


    st.header(
        "🛰️ GOES-19 — vigilancia satelital del Litoral"
    )

    st.caption(
        "Imagen GeoColor del GOES-19 / ABI. Sector oficial "
        "de NOAA para Sudamérica Sur. La imagen es una "
        "herramienta de observación y no alimenta directamente "
        "el índice de riesgo."
    )

    mostrar_goes19()

    st.link_button(
        "Abrir GOES-19 en NOAA",
        GOES19_URL,
        use_container_width=True,
    )


    # ============================================================
    # LOCALIDAD SELECCIONADA
    # ============================================================

    localidades = sorted(
        df_validos[
            "localidad"
        ].tolist()
    )

    if localidades:

        localidad_seleccionada = st.selectbox(
            "Seleccionar localidad",
            localidades,
            key="localidad_detalle",
        )

    else:
        localidad_seleccionada = None


    # ============================================================
    # PRONÓSTICO EXTENDIDO 15 DÍAS
    # ============================================================

    st.header(
        "📅 Pronóstico extendido — 15 días"
    )

    st.caption(
        "Pronóstico diario del ECMWF IFS HRES. El horizonte "
        "extendido sirve para planificación y tendencia general; "
        "la incertidumbre aumenta con el plazo."
    )

    try:

        indice_nodo = next(
            i
            for i, nodo in enumerate(NODOS)
            if nodo["localidad"]
            == localidad_seleccionada
        )

        datos_seleccionados = (
            raw_ecmwf[indice_nodo]
        )

    except Exception:

        datos_seleccionados = {}


    pronostico_15 = extraer_pronostico_diario(
        datos_seleccionados
    )

    if pronostico_15.empty:

        st.warning(
            "No hay pronóstico extendido disponible para esta localidad."
        )

    else:

        horizonte = st.slider(
            "Mostrar horizonte",
            min_value=5,
            max_value=min(
                15,
                len(pronostico_15),
            ),
            value=min(
                15,
                len(pronostico_15),
            ),
            key="horizonte_pronostico",
        )

        pronostico_mostrar = (
            pronostico_15
            .head(horizonte)
            .copy()
        )

        pronostico_mostrar[
            "Fecha"
        ] = pronostico_mostrar[
            "Fecha"
        ].dt.strftime("%d/%m")

        c1, c2 = st.columns(2)

        with c1:

            fig_lp_prec = px.bar(
                pronostico_mostrar,
                x="Fecha",
                y="Precipitación",
                title=(
                    f"Precipitación diaria — "
                    f"{localidad_seleccionada}"
                ),
                labels={
                    "Precipitación": "mm",
                    "Fecha": "Fecha",
                },
            )

            st.plotly_chart(
                fig_lp_prec,
                use_container_width=True,
            )

        with c2:

            temp_largo = pronostico_mostrar.melt(
                id_vars=["Fecha"],
                value_vars=[
                    "Temp. mínima",
                    "Temp. máxima",
                ],
                var_name="Variable",
                value_name="Temperatura",
            )

            fig_lp_temp = px.line(
                temp_largo,
                x="Fecha",
                y="Temperatura",
                color="Variable",
                markers=True,
                title=(
                    f"Temperatura mínima y máxima — "
                    f"{localidad_seleccionada}"
                ),
                labels={
                    "Temperatura": "°C",
                    "Fecha": "Fecha",
                },
            )

            st.plotly_chart(
                fig_lp_temp,
                use_container_width=True,
            )

        tabla_lp = (
            pronostico_mostrar
            .copy()
        )

        tabla_lp = tabla_lp.round({
            "Temp. mínima": 1,
            "Temp. máxima": 1,
            "Sensación mínima": 1,
            "Sensación máxima": 1,
            "Precipitación": 1,
            "Lluvia": 1,
            "Chaparrones": 1,
            "Prob. precipitación": 0,
            "Horas con precipitación": 1,
            "Viento máximo": 1,
            "Ráfaga máxima": 1,
            "CAPE máximo": 0,
            "Radiación solar": 1,
            "Horas de sol": 1,
            "ET₀": 2,
        })

        st.dataframe(
            tabla_lp,
            use_container_width=True,
            hide_index=True,
        )

        csv_lp = (
            tabla_lp
            .to_csv(index=False)
            .encode("utf-8")
        )

        st.download_button(
            "⬇️ Descargar pronóstico extendido CSV",
            csv_lp,
            file_name=(
                "pronostico_15_dias_"
                f"{localidad_seleccionada.lower().replace(' ', '_')}.csv"
            ),
            mime="text/csv",
        )


    # ============================================================
    # TABLA RESUMEN
    # ============================================================

    st.header(
        "📋 Estado de los nodos"
    )

    tabla = df_validos[
        [
            "localidad",
            "provincia",
            "indice",
            "nivel",
            "temperatura",
            "humedad_relativa",
            "viento",
            "rafaga",
            "direccion_viento_texto",
            "humedad_suelo_promedio",
            "lluvia_24",
            "lluvia_72",
            "lluvia_7d",
            "lluvia_futura_24",
            "lluvia_futura_72",
            "lluvia_futura_7d",
            "runoff_72",
            "prob_lluvia",
            "cape",
            "tendencia",
        ]
    ].copy()

    # ------------------------------------------------------------
    # Conversión SOLO PARA PRESENTACIÓN.
    # Internamente humedad_suelo_promedio continúa siendo m³/m³.
    # ------------------------------------------------------------
    tabla[
        "humedad_suelo_promedio"
    ] = (
        tabla[
            "humedad_suelo_promedio"
        ] * 100
    )

    tabla.columns = [
        "Localidad",
        "Provincia",
        "Riesgo",
        "Nivel",
        "Temp. °C",
        "HR %",
        "Viento km/h",
        "Ráfaga km/h",
        "Dirección",
        "Humedad suelo (%)",
        "Lluvia ant. 24h",
        "Lluvia ant. 72h",
        "Lluvia ant. 7d",
        "Pronóstico 24h",
        "Pronóstico 72h",
        "Pronóstico 7d",
        "Runoff ant. 72h",
        "Prob. lluvia 72h",
        "CAPE",
        "Tendencia",
    ]

    st.dataframe(
        tabla,
        use_container_width=True,
        hide_index=True,
    )


    # ============================================================
    # DETALLE
    # ============================================================

    st.header(
        "🔎 Análisis por localidad"
    )

    if not localidad_seleccionada:

        st.warning(
            "No hay localidades disponibles para mostrar el detalle."
        )

        mostrar_paneles_ficha()
        st.stop()


    row = df_validos[
        df_validos[
            "localidad"
        ]
        == localidad_seleccionada
    ].iloc[0]


    st.markdown(
        f"""
    ## {emoji_nivel(row["nivel"])} {row["nivel"]}

    ### {row["localidad"]}, {row["provincia"]}

    ### Índice: {row["indice"]:.1f}/100

    **Condición meteorológica:** {row["descripcion_tiempo"]}

    **Tendencia hidrológica:** {row["tendencia"]}

    **Dirección hidrológica:** {row["direccion_hidrologica"]}

    **Velocidad hidrológica:** {row["velocidad_hidrologica"]}
    """
    )

    if bool(
        row.get(
            "condicion_critica",
            False,
        )
    ):

        st.error(
            "⚠️ Se activó una condición crítica por "
            "acumulación de precipitación y/o runoff."
        )


    # ------------------------------------------------------------
    # Meteorología actual
    # ------------------------------------------------------------

    st.subheader(
        "🌡️ Condiciones meteorológicas actuales"
    )

    m1, m2, m3, m4, m5 = st.columns(5)

    with m1:
        st.metric(
            "Temperatura",
            f'{row["temperatura"]:.1f} °C',
        )

    with m2:
        st.metric(
            "Sensación",
            f'{row["sensacion"]:.1f} °C',
        )

    with m3:
        st.metric(
            "Humedad relativa",
            f'{row["humedad_relativa"]:.0f}%',
        )

    with m4:
        st.metric(
            "Viento",
            f'{row["viento"]:.1f} km/h',
        )

    with m5:
        st.metric(
            "Ráfaga",
            f'{row["rafaga"]:.1f} km/h',
        )


    m6, m7, m8, m9, m10 = st.columns(5)

    with m6:
        st.metric(
            "Dirección",
            f'{row["direccion_viento"]:.0f}° '
            f'{row["direccion_viento_texto"]}',
        )

    with m7:
        st.metric(
            "Punto de rocío",
            f'{row["punto_rocio"]:.1f} °C',
        )

    with m8:
        st.metric(
            "Presión MSL",
            f'{row["presion_msl"]:.1f} hPa',
        )

    with m9:
        st.metric(
            "Nubosidad",
            f'{row["nubosidad"]:.0f}%',
        )

    with m10:
        st.metric(
            "CAPE",
            f'{row["cape"]:.0f} J/kg',
        )


    m11, m12, m13, m14 = st.columns(4)

    with m11:
        st.metric(
            "VPD",
            f'{row["vpd"]:.2f} kPa',
        )

    with m12:
        st.metric(
            "ET",
            f'{row["evapotranspiracion"]:.2f} mm',
        )

    with m13:
        st.metric(
            "ET₀",
            f'{row["et0"]:.2f} mm',
        )

    with m14:
        st.metric(
            "Tiempo",
            row["descripcion_tiempo"],
        )


    # ------------------------------------------------------------
    # Suelo
    # ------------------------------------------------------------

    st.subheader(
        "🌱 Estado del suelo"
    )

    st.caption(
        "Contenido volumétrico de agua del suelo. "
        "Los datos originales del modelo están expresados "
        "en m³/m³ y se muestran aquí como porcentaje."
    )

    s1, s2, s3, s4, s5 = st.columns(5)

    with s1:

        st.metric(
            "0–7 cm",
            (
                f'{row["humedad_0_7"] * 100:.1f}%'
                if pd.notna(
                    row["humedad_0_7"]
                )
                else "Sin dato"
            ),
        )

    with s2:

        st.metric(
            "7–28 cm",
            (
                f'{row["humedad_7_28"] * 100:.1f}%'
                if pd.notna(
                    row["humedad_7_28"]
                )
                else "Sin dato"
            ),
        )

    with s3:

        st.metric(
            "28–100 cm",
            (
                f'{row["humedad_28_100"] * 100:.1f}%'
                if pd.notna(
                    row["humedad_28_100"]
                )
                else "Sin dato"
            ),
        )

    with s4:

        st.metric(
            "100–255 cm",
            (
                f'{row["humedad_100_255"] * 100:.1f}%'
                if pd.notna(
                    row["humedad_100_255"]
                )
                else "Sin dato"
            ),
        )

    with s5:

        st.metric(
            "Promedio",
            (
                f'{row["humedad_suelo_promedio"] * 100:.1f}%'
                if pd.notna(
                    row["humedad_suelo_promedio"]
                )
                else "Sin dato"
            ),
        )


    temp_suelo_df = pd.DataFrame({

        "Profundidad": [
            "0–7 cm",
            "7–28 cm",
            "28–100 cm",
            "100–255 cm",
        ],

        "Temperatura °C": [
            row["temp_suelo_0_7"],
            row["temp_suelo_7_28"],
            row["temp_suelo_28_100"],
            row["temp_suelo_100_255"],
        ],
    })


    fig_temp_suelo = px.bar(
        temp_suelo_df,
        x="Profundidad",
        y="Temperatura °C",
        title="Temperatura del suelo por profundidad",
    )

    st.plotly_chart(
        fig_temp_suelo,
        use_container_width=True,
    )


    # ------------------------------------------------------------
    # Precipitación y escorrentía
    # ------------------------------------------------------------

    st.subheader(
        "🌧️ Precipitación y runoff"
    )

    precipitacion_df = pd.DataFrame({

        "Periodo": [
            "Antecedente 24h",
            "Antecedente 72h",
            "Antecedente 7d",
            "Pronóstico 24h",
            "Pronóstico 72h",
            "Pronóstico 7d",
        ],

        "mm": [
            row["lluvia_24"],
            row["lluvia_72"],
            row["lluvia_7d"],
            row["lluvia_futura_24"],
            row["lluvia_futura_72"],
            row["lluvia_futura_7d"],
        ],
    })


    fig_prec = px.bar(
        precipitacion_df,
        x="Periodo",
        y="mm",
        title="Precipitación antecedente y prevista",
    )

    st.plotly_chart(
        fig_prec,
        use_container_width=True,
    )


    runoff_df = pd.DataFrame({

        "Periodo": [
            "Antecedente 24h",
            "Antecedente 72h",
            "Antecedente 7d",
            "Futuro 24h",
            "Futuro 72h",
            "Futuro 7d",
        ],

        "Runoff mm": [
            row["runoff_24"],
            row["runoff_72"],
            row["runoff_7d"],
            row["runoff_futuro_24"],
            row["runoff_futuro_72"],
            row["runoff_futuro_7d"],
        ],
    })


    fig_runoff = px.bar(
        runoff_df,
        x="Periodo",
        y="Runoff mm",
        title="Escorrentía / runoff antecedente y prevista",
    )

    st.plotly_chart(
        fig_runoff,
        use_container_width=True,
    )


    # ------------------------------------------------------------
    # Variables atmosféricas y convectivas
    # ------------------------------------------------------------

    st.subheader(
        "⛈️ Variables atmosféricas y convectivas"
    )

    convectiva_df = pd.DataFrame({

        "Variable": [
            "CAPE actual",
            "CAPE máximo próximo 24h",
            "Prob. precipitación 24h",
            "Prob. precipitación 72h",
            "Viento máximo 24h",
            "Ráfaga máxima 24h",
            "Nubosidad total",
            "Nubosidad baja",
            "Nubosidad media",
            "Nubosidad alta",
        ],

        "Valor": [
            row["cape"],
            row["cape_max_24"],
            row["prob_lluvia_24"],
            row["prob_lluvia_72"],
            row["viento_max_24"],
            row["rafaga_max_24"],
            row["nubosidad"],
            row["nubosidad_baja"],
            row["nubosidad_media"],
            row["nubosidad_alta"],
        ],
    })


    fig_conv = px.bar(
        convectiva_df,
        x="Variable",
        y="Valor",
        title="Variables meteorológicas destacadas",
    )

    fig_conv.update_xaxes(
        tickangle=-35
    )

    st.plotly_chart(
        fig_conv,
        use_container_width=True,
    )


    # ------------------------------------------------------------
    # Componentes del riesgo
    # ------------------------------------------------------------

    st.subheader(
        "📊 Componentes del riesgo"
    )

    componentes = pd.DataFrame({

        "Componente": [
            "Humedad del suelo",
            "Lluvia reciente",
            "Lluvia futura",
            "Runoff",
            "Acumulación",
            "Lluvia crítica",
        ],

        "Valor": [
            normalizar_0_100(
                row["humedad_suelo_promedio"],
                0.15,
                0.45,
            ),

            normalizar_0_100(
                row["lluvia_72"],
                10,
                100,
            ),

            normalizar_0_100(
                row["lluvia_futura_72"],
                10,
                100,
            ),

            normalizar_0_100(
                row["runoff_72"],
                1,
                30,
            ),

            normalizar_0_100(
                row["lluvia_7d"],
                30,
                180,
            ),

            min(
                100,
                (
                    (
                        40
                        if row["lluvia_24"] >= 50
                        else 25
                        if row["lluvia_24"] >= 30
                        else 15
                        if row["lluvia_24"] >= 20
                        else 0
                    )
                    +
                    (
                        40
                        if row["lluvia_72"] >= 100
                        else 30
                        if row["lluvia_72"] >= 70
                        else 20
                        if row["lluvia_72"] >= 50
                        else 0
                    )
                ),
            ),
        ],
    })


    fig_componentes = px.bar(
        componentes,
        x="Componente",
        y="Valor",
        range_y=[0, 100],
        title="Componentes normalizados del riesgo",
    )

    st.plotly_chart(
        fig_componentes,
        use_container_width=True,
    )


    st.info(
        f'💡 **Acción recomendada:** '
        f'{row["accion"]}'
    )


    # ============================================================
    # HISTORIAL
    # ============================================================

    st.header(
        "📈 Historial"
    )

    if HISTORY_FILE.exists():

        try:

            historial = pd.read_csv(
                HISTORY_FILE
            )

            if (
                not historial.empty
                and "localidad"
                in historial.columns
            ):

                localidades_historial = sorted(
                    historial[
                        "localidad"
                    ]
                    .dropna()
                    .unique()
                    .tolist()
                )

                localidad_hist = st.selectbox(
                    "Localidad para historial",
                    localidades_historial,
                    key="hist_localidad",
                )

                hist_local = historial[
                    historial["localidad"]
                    == localidad_hist
                ].copy()

                if (
                    not hist_local.empty
                    and "indice"
                    in hist_local.columns
                ):

                    hist_local[
                        "actualizado"
                    ] = pd.to_datetime(
                        hist_local[
                            "actualizado"
                        ],
                        dayfirst=True,
                        errors="coerce",
                    )

                    hist_local = (
                        hist_local
                        .dropna(
                            subset=[
                                "actualizado"
                            ]
                        )
                        .sort_values(
                            "actualizado"
                        )
                    )

                    if not hist_local.empty:

                        fig_hist = px.line(
                            hist_local,
                            x="actualizado",
                            y="indice",
                            markers=True,
                            title=(
                                f"Evolución del riesgo — "
                                f"{localidad_hist}"
                            ),
                        )

                        fig_hist.update_yaxes(
                            range=[0, 100]
                        )

                        st.plotly_chart(
                            fig_hist,
                            use_container_width=True,
                        )

                    csv_hist = (
                        hist_local
                        .to_csv(index=False)
                        .encode("utf-8")
                    )

                    st.download_button(
                        "⬇️ Descargar historial CSV",
                        csv_hist,
                        file_name=(
                            "historial_alerta_litoral.csv"
                        ),
                        mime="text/csv",
                    )

        except Exception as error:

            st.warning(
                f"No se pudo cargar el historial: {error}"
            )

    else:

        st.info(
            "El historial comenzará a generarse cuando "
            "la aplicación procese datos."
        )


    # ============================================================
    # EXPORTAR
    # ============================================================

    st.header(
        "⬇️ Exportar estado actual"
    )

    csv_actual = (
        df_validos
        .to_csv(index=False)
        .encode("utf-8")
    )

    st.download_button(
        "Descargar estado actual CSV",
        csv_actual,
        file_name="alerta_litoral_agro_actual.csv",
        mime="text/csv",
    )


    # ============================================================
    # METODOLOGÍA
    # ============================================================

    with st.expander(
        "📚 Metodología y limitaciones"
    ):

        st.markdown(
            """
    ### ¿Qué evalúa el sistema?

    El índice experimental de anegamiento integra seis componentes principales:

    - humedad del suelo;
    - precipitación antecedente de 72 horas;
    - precipitación prevista para 72 horas;
    - runoff antecedente de 72 horas;
    - acumulación de precipitación antecedente de 7 días;
    - componente de lluvia crítica por acumulación intensa.

    Las restantes variables meteorológicas —temperatura, viento, ráfagas, presión,
    nubosidad, CAPE, VPD, etc.— se muestran para ampliar el diagnóstico y no se
    incorporan automáticamente al índice mientras no exista una calibración histórica.

    ### Humedad del suelo

    La humedad del suelo proviene de las variables de contenido volumétrico de agua
    del modelo ECMWF consultadas mediante Open-Meteo.

    Los datos originales se expresan como **m³/m³**.

    Para facilitar la interpretación, la interfaz los presenta como porcentaje:

    **0.341 m³/m³ = 34.1 %**

    La conversión a porcentaje es únicamente de presentación. El cálculo interno
    del índice conserva los valores originales en m³/m³ y utiliza actualmente el
    intervalo experimental de **0.15–0.45 m³/m³** para normalizar el componente
    de humedad del suelo.

    ### Ventanas temporales

    Las ventanas de lluvia y runoff se calculan utilizando los timestamps horarios
    devueltos por Open-Meteo:

    - **Antecedente:** período previo a la hora actual.
    - **Pronóstico:** período posterior a la hora actual.

    De esta forma, "24 h", "72 h" y "7 días" no dependen de la posición arbitraria
    dentro del array de respuesta.

    ### Tendencia hidrológica

    Se compara el runoff medio de las últimas 12 horas con el período comprendido
    entre 12 y 36 horas antes.

    **SUBIDA:** aumenta el riesgo.

    **ESTABLE:** efecto neutro.

    **BAJADA:** reduce el riesgo.

    También se diferencia entre subida rápida, subida lenta, bajada rápida,
    bajada lenta y estado estable.

    ### Índice de riesgo

    | Índice | Nivel |
    |---:|---|
    | 0–34.9 | 🟢 BAJO |
    | 35–54.9 | 🟡 MEDIO |
    | 55–74.9 | 🟠 ALTO |
    | 75–100 | 🔴 MUY ALTO |

    ### Pesos

    | Componente | Peso |
    |---|---:|
    | Humedad del suelo | 25% |
    | Lluvia reciente | 20% |
    | Lluvia futura | 20% |
    | Runoff | 10% |
    | Acumulación | 10% |
    | Lluvia crítica | 15% |

    La tendencia hidrológica funciona como ajuste posterior.

    ### Condición crítica

    El índice se eleva como mínimo a 75 cuando se cumple alguna de estas condiciones
    internas del prototipo:

    - precipitación antecedente de 72 h ≥ 120 mm;
    - precipitación antecedente de 7 días ≥ 220 mm;
    - runoff antecedente de 72 h ≥ 50 mm.

    Estos valores son **umbrales experimentales del proyecto**, no umbrales oficiales.

    ### Fuentes

    **Open-Meteo / ECMWF:** fuente meteorológica principal del índice y de las variables
    mostradas en las fichas de los nodos.

    **NOAA / GOES-19 ABI:** vigilancia satelital complementaria mediante GeoColor
    en el sector Sudamérica Sur. Se mantiene separada del índice porque la imagen
    satelital es una observación/visualización y no un predictor numérico calibrado
    dentro de la fórmula actual.

    ### Pronóstico extendido

    La aplicación muestra hasta **15 días** de pronóstico diario del ECMWF IFS HRES 9 km.
    Este horizonte es útil para planificación y tendencia general, pero la incertidumbre
    aumenta con el plazo y no debe interpretarse con la misma precisión que el corto plazo.

    ### Limitaciones

    Es un sistema experimental. No constituye una alerta oficial, pronóstico
    hidrológico oficial ni reemplaza la información de organismos competentes.

    Los datos meteorológicos son salidas de modelos numéricos; los valores de
    precipitación/runoff no deben presentarse como mediciones directas de un
    pluviómetro local.

    La calibración de pesos, umbrales y desempeño debe validarse contra episodios
    históricos reales de anegamiento antes de un uso institucional operativo.
    """
        )


    # ============================================================
    # ESTADO DEL SISTEMA
    # ============================================================

    st.header(
        "🟢 Estado del sistema"
    )

    st.write(
        "Última actualización de interfaz: "
        f"**{ahora().strftime('%d/%m/%Y %H:%M:%S')}**"
    )

    st.write(
        "Nodos procesados: "
        f"**{len(df_validos)} / {len(NODOS)}**"
    )

    st.write(
        "Fuente meteorológica principal: "
        f"**Open-Meteo / {MODELO}**"
    )

    telegram_token, telegram_chat = (
        obtener_secrets_telegram()
    )

    if telegram_token and telegram_chat:

        st.write(
            "Alertas Telegram: **🟢 configuradas**"
        )

    else:

        st.write(
            "Alertas Telegram: **⚪ no configuradas**"
        )

    st.write(
        "Satélite: **🛰️ NOAA GOES-19 / ABI — Sudamérica Sur**"
    )

    st.caption(
        f"{APP_NAME} · {VERSION} · "
        "Sistema experimental · Fuentes y límites en Caso histórico y ficha"
    )


mostrar_paneles_ficha()
