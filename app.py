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
#   Chaco
#   Formosa
#   Misiones
#
# Fuentes:
#   Open-Meteo / ECMWF IFS HRES 9 km
#   SMN — observaciones meteorológicas en directo
#   NOAA GOES-19 ABI (vigilancia satelital)
#
# Integra INA, INTA, ENOS, relieve y planificación territorial.
#
# Versión: V4.4.2
# Requisitos: streamlit>=1.40, requests>=2.31, pandas>=2.0,
# numpy>=1.24, plotly>=6.0, rasterio>=1.4, PyMuPDF>=1.24.
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
import csv
import zlib
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO, StringIO
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
VERSION = "V4.4.2"
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
    <button id="region">🌎 Ver todo el Litoral</button>
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
      document.getElementById('region').onclick=()=>map.fitBounds(provinces.getBounds(),{padding:[20,20]});
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
    st.caption("Fuente: SINARAME / Subsecretaría de Recursos Hídricos. Cada radar tiene cobertura parcial; no cubre por sí solo las seis provincias. Ver todo el Litoral muestra el alcance sobre los límites oficiales. Ausencia de imagen no confirma ausencia de lluvia.")
    if st.checkbox("Consultar también el servicio de imágenes del SMN", key="radar_smn_api_opcional"):
        mostrar_radar_api_smn()



HISTORY_FILE = Path("historial_alerta_litoral.csv")
STATE_FILE = Path("telegram_alert_state.json")

MAP_CENTER_LAT = -28.4
MAP_CENTER_LON = -58.6
MAP_ZOOM = 4.7


# ============================================================
# NODOS DE MONITOREO
# ============================================================

NODOS = [
    {'localidad': 'Reconquista', 'provincia': 'Santa Fe', 'lat': -29.144, 'lon': -59.643},
    {'localidad': 'Avellaneda', 'provincia': 'Santa Fe', 'lat': -29.117, 'lon': -59.658},
    {'localidad': 'Vera', 'provincia': 'Santa Fe', 'lat': -29.46, 'lon': -60.213},
    {'localidad': 'Tostado', 'provincia': 'Santa Fe', 'lat': -29.233, 'lon': -61.769},
    {'localidad': 'Rafaela', 'provincia': 'Santa Fe', 'lat': -31.25, 'lon': -61.486},
    {'localidad': 'Santa Fe', 'provincia': 'Santa Fe', 'lat': -31.633, 'lon': -60.7},
    {'localidad': 'Esperanza', 'provincia': 'Santa Fe', 'lat': -31.448, 'lon': -60.932},
    {'localidad': 'Rosario', 'provincia': 'Santa Fe', 'lat': -32.946, 'lon': -60.639},
    {'localidad': 'Venado Tuerto', 'provincia': 'Santa Fe', 'lat': -33.745, 'lon': -61.968},
    {'localidad': 'Corrientes', 'provincia': 'Corrientes', 'lat': -27.469, 'lon': -58.83},
    {'localidad': 'Goya', 'provincia': 'Corrientes', 'lat': -29.14, 'lon': -59.263},
    {'localidad': 'Mercedes', 'provincia': 'Corrientes', 'lat': -29.182, 'lon': -58.075},
    {'localidad': 'Curuzú Cuatiá', 'provincia': 'Corrientes', 'lat': -29.791, 'lon': -58.054},
    {'localidad': 'Paso de los Libres', 'provincia': 'Corrientes', 'lat': -29.713, 'lon': -57.088},
    {'localidad': 'Santo Tomé', 'provincia': 'Corrientes', 'lat': -28.549, 'lon': -56.04},
    {'localidad': 'Paraná', 'provincia': 'Entre Ríos', 'lat': -31.741, 'lon': -60.511},
    {'localidad': 'La Paz', 'provincia': 'Entre Ríos', 'lat': -30.744, 'lon': -59.645},
    {'localidad': 'Concordia', 'provincia': 'Entre Ríos', 'lat': -31.393, 'lon': -58.02},
    {'localidad': 'Resistencia', 'provincia': 'Chaco', 'lat': -27.451075711, 'lon': -58.986483529},
    {'localidad': 'General José de San Martín', 'provincia': 'Chaco', 'lat': -26.53630236, 'lon': -59.341321005},
    {'localidad': 'Presidencia Roque Sáenz Peña', 'provincia': 'Chaco', 'lat': -26.790971775, 'lon': -60.441248325},
    {'localidad': 'Charata', 'provincia': 'Chaco', 'lat': -27.217922303, 'lon': -61.187347968},
    {'localidad': 'Villa Ángela', 'provincia': 'Chaco', 'lat': -27.576877556, 'lon': -60.711125666},
    {'localidad': 'Nueva Pompeya', 'provincia': 'Chaco', 'lat': -24.933074618, 'lon': -61.483145173},
    {'localidad': 'Formosa', 'provincia': 'Formosa', 'lat': -26.185008983, 'lon': -58.174896483},
    {'localidad': 'Clorinda', 'provincia': 'Formosa', 'lat': -25.286080881, 'lon': -57.7224082},
    {'localidad': 'Pirané', 'provincia': 'Formosa', 'lat': -25.732649819, 'lon': -59.109808063},
    {'localidad': 'El Colorado', 'provincia': 'Formosa', 'lat': -26.309061021, 'lon': -59.372308046},
    {'localidad': 'Las Lomitas', 'provincia': 'Formosa', 'lat': -24.708135606, 'lon': -60.591376883},
    {'localidad': 'Ingeniero Guillermo N. Juárez', 'provincia': 'Formosa', 'lat': -23.90373915, 'lon': -61.842429269},
    {'localidad': 'Posadas', 'provincia': 'Misiones', 'lat': -27.36642598, 'lon': -55.893980275},
    {'localidad': 'Oberá', 'provincia': 'Misiones', 'lat': -27.486790956, 'lon': -55.120056983},
    {'localidad': 'Eldorado', 'provincia': 'Misiones', 'lat': -26.408876964, 'lon': -54.60705304},
    {'localidad': 'Puerto Iguazú', 'provincia': 'Misiones', 'lat': -25.597210098, 'lon': -54.577223001},
    {'localidad': 'San Vicente', 'provincia': 'Misiones', 'lat': -26.993297387, 'lon': -54.486618972},
    {'localidad': 'El Soberbio', 'provincia': 'Misiones', 'lat': -27.297511036, 'lon': -54.197545618},
    {'localidad': 'Gualeguaychú', 'provincia': 'Entre Ríos', 'lat': -33.007797697, 'lon': -58.510770086},
    {'localidad': 'Gualeguay', 'provincia': 'Entre Ríos', 'lat': -33.15043008, 'lon': -59.310575586},
    {'localidad': 'Victoria', 'provincia': 'Entre Ríos', 'lat': -32.621605823, 'lon': -60.157953952},
    {'localidad': 'Villaguay', 'provincia': 'Entre Ríos', 'lat': -31.867637571, 'lon': -59.026885213},
    {'localidad': 'Federal', 'provincia': 'Entre Ríos', 'lat': -30.956214281, 'lon': -58.779587505},
    {'localidad': 'Chajarí', 'provincia': 'Entre Ríos', 'lat': -30.754724203, 'lon': -57.981789368},
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
    if not np.isfinite(indice): return "SIN DATOS"
    return "ROJO" if indice >= 55 else "AMARILLO" if indice >= 35 else "VERDE"



def emoji_nivel(nivel):
    return {
        "ROJO": "🔴",
        "ROJO": "🟠",
        "AMARILLO": "🟡",
        "VERDE": "🟢",
        "SIN DATOS": "⚪",
    }.get(nivel, "⚪")


def accion_desde_nivel(nivel):
    acciones = {
        "ROJO": (
            "Priorizar medidas preventivas. Evitar ingreso de maquinaria a "
            "sectores comprometidos y evaluar medidas preventivas sobre "
            "animales, cultivos e insumos."
        ),
        "ROJO": (
            "Reforzar el monitoreo. Evitar tareas que puedan compactar el suelo "
            "y revisar sectores bajos o con antecedentes de anegamiento."
        ),
        "AMARILLO": (
            "Mantener vigilancia sobre lluvia, humedad del suelo y evolución "
            "del riesgo antes de realizar tareas sensibles."
        ),
        "VERDE": (
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
    """Consulta los nodos configurados por lotes, preservando su orden."""

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

    def consultar_lote(lote):
        solicitud={**params,'latitude':','.join(f'{n["lat"]:.6f}' for n in lote),
                   'longitude':','.join(f'{n["lon"]:.6f}' for n in lote)}
        try:
            response=requests.get(API_URL,params=solicitud,headers=headers,timeout=(5,45))
            response.raise_for_status()
            payload=response.json()
            if isinstance(payload,dict) and payload.get('error'):
                return [payload.copy() for _ in lote]
            filas=payload if isinstance(payload,list) else [payload]
            if len(filas)!=len(lote):
                return [{'error':'Cantidad de coordenadas distinta de la solicitada'} for _ in lote]
            return filas
        except Exception:
            return [{'error':'No se pudo completar la consulta meteorológica del lote'} for _ in lote]
    lotes=[NODOS[i:i+8] for i in range(0,len(NODOS),8)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        return [fila for lote in pool.map(consultar_lote,lotes) for fila in lote]


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
        "direccion_viento_texto": "Sin datos",
        "descripcion_tiempo": "Sin datos",
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

    cobertura_completa = bool(
        len(pd.DatetimeIndex(horas[pasado_7d & np.isfinite(precipitacion) & (precipitacion>=0)]).unique()) >= 168
        and len(pd.DatetimeIndex(horas[futuro_72 & np.isfinite(precipitacion) & (precipitacion>=0)]).unique()) >= 72
        and len(pd.DatetimeIndex(horas[pasado_72 & np.isfinite(runoff) & (runoff>=0)]).unique()) >= 72
        and len(humedad_validas) >= 3
    )
    indice_parcial = round(indice,1) if not cobertura_completa else np.nan
    if not cobertura_completa:
        indice = np.nan
        nivel = 'SIN DATOS'

    return {
        **nodo,
        "estado_datos": "OK" if cobertura_completa else "INCOMPLETO",
        "error_datos": "" if cobertura_completa else "Índice completo no disponible: revisar lluvia de 7 días, pronóstico de 72 h, escorrentía de 72 h y humedad de al menos tres capas",
        "indice": round(indice, 1),
        "indice_parcial": indice_parcial,
        "nivel": nivel,
        "cobertura_meteorologica": cobertura_completa,

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
            round(runoff_72, 2) if np.sum(pasado_72 & np.isfinite(runoff))>=72 else np.nan,

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
        "nivel_meteorologico",
        "cobertura_territorial",
        "motivos_semaforo",
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


def enviar_telegram_detallado(mensaje):
    """Valida la respuesta de Bot API sin exportar credenciales ni el chat."""
    token, chat_id = obtener_secrets_telegram()
    resultado = {'fecha': ahora().isoformat(), 'ok': False, 'http': None,
                 'message_id': None, 'detalle': 'Credenciales no configuradas'}
    if not token or not chat_id:
        return resultado
    def detalle_seguro(valor):
        texto = str(valor)
        for secreto in (str(token), str(chat_id)):
            if secreto:
                texto = texto.replace(secreto, '[oculto]')
        return texto[:300]
    try:
        response = requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat_id, 'text': mensaje, 'parse_mode': 'HTML',
                  'disable_web_page_preview': True},
            timeout=15,
        )
        resultado['http'] = response.status_code
        datos = response.json()
        if not isinstance(datos, dict):
            raise ValueError('Bot API no devolvió un objeto JSON')
        enviado = datos.get('result') or {}
        message_id = enviado.get('message_id') if isinstance(enviado, dict) else None
        resultado['ok'] = bool(200 <= response.status_code < 300 and datos.get('ok') is True
                               and isinstance(message_id, int) and message_id > 0)
        if resultado['ok']:
            resultado.update(message_id=message_id, detalle='Bot API confirmó el envío; recepción en el chat pendiente de comprobar')
        else:
            resultado['detalle'] = detalle_seguro(datos.get('description', 'Respuesta sin confirmación válida de envío'))
    except requests.exceptions.RequestException:
        resultado['detalle'] = 'Fallo de conexión o tiempo de espera; envío no confirmado'
    except (ValueError, TypeError):
        resultado['detalle'] = 'Respuesta de Bot API inválida; envío no confirmado'
    return resultado


def enviar_telegram(mensaje):
    return enviar_telegram_detallado(mensaje)['ok']


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

<b>Semáforo:</b> {row["nivel"]}
<b>Cobertura territorial:</b> {row.get("cobertura_territorial", 1)}/4
<b>Evidencia:</b> {html.escape(str(row.get("motivos_semaforo", "Índice meteorológico")))}
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
{acciones_telegram_html(row)}

<i>Actualizado: {row["actualizado"]}</i>

⚠️ Sistema experimental.
No reemplaza avisos oficiales.
""".strip()


def clave_alerta(row):
    contenido = f'{row["localidad"]}|{row["nivel"]}|{round(float(row["indice"]) / 5)}|{row.get("motivos_semaforo", "")}'
    return hashlib.md5(contenido.encode("utf-8")).hexdigest()



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
            "ROJO",
            "ROJO",
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
    df=df.copy()
    # Un índice ausente conserva un punto visible; su tamaño no expresa riesgo.
    df['_tamano_mapa']=12.0
    colores = {
        "VERDE": "green",
        "AMARILLO": "yellow",
        "ROJO": "red",
        "SIN DATOS": "#64748b",
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
        "accion_prioritaria": True,
        "lat": False,
        "lon": False,
    }

    fig = px.scatter_map(
        df,
        lat="lat",
        lon="lon",
        color="nivel",
        size="_tamano_mapa",
        size_max=14,
        hover_name="localidad",
        hover_data={**{k:v for k,v in hover_data.items() if k in df},'_tamano_mapa':False},
        labels={'accion_prioritaria':'Primera acción'},
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
        uirevision=firma_cobertura(),
    )
    for provincia,geometria in GEOMETRIAS_LITORAL.items():
        for poligono in _anillos_provincia(geometria):
            exterior=poligono[0]
            fig.add_trace(go.Scattermap(lon=[p[0] for p in exterior],lat=[p[1] for p in exterior],
                mode='lines',line={'color':'#475569','width':1},name=provincia,
                showlegend=False,hoverinfo='skip'))
    return fig


# ============================================================
# WINDY
# ============================================================

WINDY_CENTER_LAT = MAP_CENTER_LAT
WINDY_CENTER_LON = MAP_CENTER_LON
WINDY_ZOOM = 5


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
        "Mensaje de prueba. No es una alerta meteorológica.\n"
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
            "🟢 Credenciales de Telegram configuradas. "
            "La conexión requiere una prueba de envío."
        )

        st.caption(
            "Las alertas automáticas se generan para "
            "nodos que alcanzan ROJO y se "
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
                "Nodos actualmente ROJO",
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
            st.caption('Destino configurado; credenciales y chat no se muestran.')

        if probar:
            prueba = enviar_telegram_detallado(mensaje_prueba_telegram())
            st.session_state['ficha_prueba_telegram'] = prueba
            st.session_state.pop('ficha_recepcion_telegram', None)
            if prueba['ok']:
                st.success(
                    "✅ Bot API confirmó el envío. Comprobá la recepción en el chat."
                )
            else:
                st.error(prueba['detalle'])

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
PROVINCIAS_LITORAL = ("Santa Fe", "Corrientes", "Entre Ríos", "Chaco", "Formosa", "Misiones")
# Límites oficiales GeoRef v2 (fuente IGN), descargados el 10/10/2026.
# Se conservan sus coordenadas originales; no se usa un rectángulo provincial.
GEOMETRIAS_LITORAL = {'Santa Fe': {'type': 'Polygon', 'coordinates': [[[-61.04639, -27.998001], [-58.884005, -28.000001], [-58.893694, -28.035274], [-58.880822, -28.066825], [-58.945113, -28.133774], [-59.062413, -28.128109], [-59.087635, -28.171772], [-59.086207, -28.199493], [-59.107868, -28.222766], [-59.103458, -28.328816], [-59.076213, -28.365341], [-59.085977, -28.435966], [-59.048857, -28.493311], [-59.100008, -28.576399], [-59.105013, -28.633937], [-59.078601, -28.667487], [-59.160454, -28.794789], [-59.158075, -28.877356], [-59.210899, -28.977531], [-59.199715, -29.031545], [-59.257536, -29.089604], [-59.346766, -29.11435], [-59.407814, -29.221376], [-59.500633, -29.243518], [-59.519788, -29.343189], [-59.604283, -29.394743], [-59.579953, -29.459848], [-59.590185, -29.569779], [-59.613979, -29.668765], [-59.617549, -29.77584], [-59.659665, -29.843179], [-59.6047, -29.902666], [-59.588911, -29.976252], [-59.56639, -30.008076], [-59.616121, -30.11301], [-59.636584, -30.237456], [-59.712013, -30.276241], [-59.693453, -30.313123], [-59.666803, -30.327161], [-59.703439, -30.44261], [-59.65033, -30.500977], [-59.645856, -30.597037], [-59.678406, -30.646912], [-59.633301, -30.713928], [-59.699394, -30.769072], [-59.704867, -30.817376], [-59.734848, -30.876624], [-59.818176, -30.942505], [-59.87793, -31.056213], [-59.925443, -31.105052], [-59.963379, -31.119873], [-60.002686, -31.238586], [-60.105591, -31.289917], [-60.086792, -31.346985], [-60.138428, -31.413391], [-60.143494, -31.443604], [-60.212354, -31.467816], [-60.244001, -31.530395], [-60.332807, -31.547156], [-60.330665, -31.608546], [-60.46759, -31.698303], [-60.584078, -31.708484], [-60.638794, -31.742004], [-60.638567, -31.787482], [-60.675781, -31.811707], [-60.657227, -31.911316], [-60.721849, -31.966179], [-60.720659, -31.985215], [-60.684387, -32.012085], [-60.656006, -32.067078], [-60.700382, -32.149769], [-60.672607, -32.232605], [-60.679018, -32.319529], [-60.718993, -32.334044], [-60.711617, -32.393055], [-60.723816, -32.456116], [-60.776184, -32.507019], [-60.747375, -32.554321], [-60.752075, -32.622192], [-60.723514, -32.671689], [-60.716003, -32.773621], [-60.658875, -32.831177], [-60.681398, -32.871564], [-60.676208, -32.899597], [-60.620246, -32.947945], [-60.605493, -32.999817], [-60.493408, -33.131409], [-60.392055, -33.188984], [-60.335662, -33.176849], [-60.303063, -33.183511], [-60.284504, -33.207068], [-60.297829, -33.242284], [-60.252392, -33.275415], [-60.267107, -33.265935], [-60.272714, -33.263863], [-60.264612, -33.281316], [-60.323963, -33.350753], [-60.335288, -33.412105], [-60.410217, -33.467886], [-60.423841, -33.528429], [-60.413831, -33.545012], [-60.45738, -33.58236], [-60.462817, -33.634187], [-60.556949, -33.646598], [-60.61992, -33.628344], [-60.684582, -33.585157], [-60.781951, -33.578969], [-60.818249, -33.540293], [-60.87909, -33.554734], [-60.918108, -33.58837], [-60.934033, -33.654727], [-61.719753, -34.385555], [-62.883909, -34.38655], [-61.923249, -33.119581], [-61.888748, -33.10599], [-61.787668, -33.006202], [-61.791, -32.958407], [-61.771552, -32.901465], [-61.792243, -32.877228], [-61.77769, -32.830402], [-61.795699, -32.773966], [-61.828402, -32.754511], [-61.857988, -32.694552], [-61.907415, -32.695747], [-61.946626, -32.678481], [-61.946168, -32.651026], [-61.889495, -32.610798], [-61.891829, -32.590534], [-61.920591, -32.594278], [-61.926381, -32.584034], [-61.902477, -32.568619], [-61.905052, -32.552546], [-61.930573, -32.544506], [-61.912075, -32.526474], [-61.914512, -32.494664], [-62.011635, -32.347328], [-62.037889, -32.262914], [-62.145346, -32.193479], [-62.178877, -32.159422], [-62.19678, -32.11477], [-62.167797, -31.98601], [-62.188893, -31.922314], [-62.22349, -31.883934], [-62.218089, -31.740571], [-62.241683, -31.699677], [-62.123495, -31.605408], [-61.842175, -30.744316], [-62.130653, -30.480433], [-61.712045, -27.998552], [-61.04639, -27.998001]]]}, 'Corrientes': {'type': 'MultiPolygon', 'coordinates': [[[[-57.011711, -27.487095], [-56.96963, -27.497928], [-56.942233, -27.558315], [-56.896587, -27.588813], [-56.845096, -27.6064], [-56.790758, -27.587161], [-56.743232, -27.605003], [-56.689199, -27.579028], [-56.677864, -27.563271], [-56.717054, -27.496937], [-56.718782, -27.464558], [-56.691029, -27.454341], [-56.649602, -27.460797], [-56.60355, -27.431417], [-56.556735, -27.456781], [-56.487046, -27.563677], [-56.42438, -27.602915], [-56.388842, -27.606019], [-56.356158, -27.58589], [-56.338367, -27.52403], [-56.293688, -27.494141], [-56.287181, -27.410932], [-56.237571, -27.402393], [-56.151922, -27.328739], [-56.086808, -27.30856], [-56.037043, -27.312912], [-56.024264, -27.324262], [-56.039903, -27.34809], [-56.028888, -27.382855], [-56.043216, -27.404992], [-56.036937, -27.415187], [-56.055421, -27.420687], [-56.054612, -27.450946], [-56.026635, -27.507277], [-55.9789, -27.5402], [-55.825537, -27.791656], [-55.846303, -27.832436], [-55.827123, -27.854597], [-55.831762, -27.904133], [-55.81359, -27.921022], [-55.824392, -27.948447], [-55.749427, -28.0271], [-55.758025, -28.06081], [-55.716718, -28.061268], [-55.71242, -28.088318], [-55.620237, -28.137544], [-55.632253, -28.176785], [-55.698109, -28.221033], [-55.782542, -28.253937], [-55.772689, -28.273936], [-55.732866, -28.285822], [-55.669942, -28.330763], [-55.691695, -28.417605], [-55.7175, -28.422158], [-55.732162, -28.384348], [-55.75745, -28.368588], [-55.878303, -28.361162], [-55.90303, -28.408104], [-55.882362, -28.477707], [-56.008646, -28.505999], [-56.025668, -28.535963], [-56.002271, -28.578489], [-56.007602, -28.604952], [-56.118573, -28.681681], [-56.185516, -28.770001], [-56.259155, -28.77832], [-56.29437, -28.797709], [-56.301949, -28.901111], [-56.409915, -28.977648], [-56.415482, -29.000697], [-56.398771, -29.025807], [-56.419525, -29.078981], [-56.507154, -29.092546], [-56.591503, -29.12431], [-56.605498, -29.162404], [-56.64541, -29.199299], [-56.649584, -29.272415], [-56.703944, -29.362244], [-56.767462, -29.382561], [-56.777172, -29.433488], [-56.813834, -29.48199], [-56.899923, -29.533387], [-56.957369, -29.589695], [-56.971205, -29.607736], [-56.970521, -29.641533], [-57.00587, -29.656121], [-57.037709, -29.698964], [-57.119526, -29.763675], [-57.169896, -29.781164], [-57.229949, -29.779164], [-57.289465, -29.825349], [-57.327833, -29.883264], [-57.325337, -29.95893], [-57.336775, -29.990385], [-57.408541, -30.032024], [-57.474243, -30.119768], [-57.54904, -30.166021], [-57.645072, -30.182408], [-57.651858, -30.201794], [-57.61524, -30.254466], [-57.64292, -30.341905], [-57.889609, -30.510684], [-57.887304, -30.587569], [-57.846552, -30.620536], [-57.843369, -30.65955], [-57.808973, -30.695269], [-57.809509, -30.730192], [-57.843748, -30.721658], [-57.854835, -30.69465], [-57.894779, -30.669411], [-57.97485, -30.64734], [-57.980233, -30.629397], [-58.033321, -30.597927], [-58.037025, -30.5679], [-58.069126, -30.544908], [-58.057776, -30.496419], [-58.077347, -30.465802], [-58.072575, -30.425187], [-58.142282, -30.405238], [-58.19195, -30.335628], [-58.20739, -30.288077], [-58.28555, -30.244631], [-58.343726, -30.27125], [-58.369324, -30.264734], [-58.437047, -30.221675], [-58.451527, -30.224856], [-58.462136, -30.206458], [-58.496562, -30.207688], [-58.553486, -30.18521], [-58.576499, -30.160041], [-58.630015, -30.176859], [-58.647139, -30.163194], [-58.681665, -30.163581], [-58.680701, -30.175028], [-58.733757, -30.193413], [-58.748357, -30.213884], [-58.803753, -30.210834], [-58.878856, -30.246662], [-58.912017, -30.248214], [-58.965202, -30.21834], [-59.064251, -30.230818], [-59.081459, -30.249074], [-59.075638, -30.257267], [-59.136577, -30.286569], [-59.136475, -30.311574], [-59.192139, -30.329546], [-59.229703, -30.364175], [-59.273512, -30.352434], [-59.288267, -30.361884], [-59.298893, -30.346712], [-59.323732, -30.354244], [-59.347148, -30.324657], [-59.419647, -30.315736], [-59.44097, -30.329455], [-59.464028, -30.324323], [-59.464823, -30.33703], [-59.490212, -30.344331], [-59.557735, -30.33265], [-59.556837, -30.352302], [-59.565845, -30.347612], [-59.574664, -30.370974], [-59.57244, -30.408418], [-59.587769, -30.434822], [-59.609614, -30.425138], [-59.63682, -30.357754], [-59.671672, -30.338662], [-59.668945, -30.32383], [-59.693453, -30.313123], [-59.712013, -30.276241], [-59.636584, -30.237456], [-59.616121, -30.11301], [-59.56639, -30.008076], [-59.588911, -29.976252], [-59.6047, -29.902666], [-59.659665, -29.843179], [-59.617549, -29.77584], [-59.613979, -29.668765], [-59.590185, -29.569779], [-59.579953, -29.459848], [-59.604283, -29.394743], [-59.519788, -29.343189], [-59.500633, -29.243518], [-59.407814, -29.221376], [-59.346766, -29.11435], [-59.257536, -29.089604], [-59.199715, -29.031545], [-59.210899, -28.977531], [-59.158075, -28.877356], [-59.160454, -28.794789], [-59.078601, -28.667487], [-59.105013, -28.633937], [-59.100008, -28.576399], [-59.048857, -28.493311], [-59.085977, -28.435966], [-59.076213, -28.365341], [-59.103458, -28.328816], [-59.107868, -28.222766], [-59.086207, -28.199493], [-59.087635, -28.171772], [-59.062413, -28.128109], [-58.952965, -28.137106], [-58.859734, -28.050318], [-58.857739, -28.011612], [-58.829262, -27.961873], [-58.844006, -27.880447], [-58.809708, -27.783407], [-58.818775, -27.681178], [-58.835497, -27.646641], [-58.882746, -27.608523], [-58.871019, -27.571271], [-58.881938, -27.499654], [-58.793519, -27.393176], [-58.768318, -27.376276], [-58.74556, -27.382238], [-58.668787, -27.359092], [-58.616488, -27.318632], [-58.515722, -27.291032], [-58.493509, -27.270649], [-58.372854, -27.290794], [-58.249489, -27.261669], [-58.191339, -27.280832], [-58.009875, -27.259381], [-57.970989, -27.274986], [-57.932307, -27.264108], [-57.873551, -27.272966], [-57.827241, -27.327545], [-57.805291, -27.339396], [-57.697013, -27.326884], [-57.609788, -27.395708], [-57.543506, -27.432967], [-57.507518, -27.444201], [-57.439005, -27.438282], [-57.326511, -27.409839], [-57.225409, -27.470378], [-57.120548, -27.493446], [-57.060203, -27.481265], [-57.011711, -27.487095]], [[-57.903349, -27.27382], [-57.910579, -27.274395], [-57.912872, -27.276645], [-57.89824, -27.277518], [-57.862389, -27.294362], [-57.903349, -27.27382]], [[-57.906716, -27.290551], [-57.895896, -27.288114], [-57.894726, -27.280316], [-57.918386, -27.282459], [-57.906716, -27.290551]], [[-57.952914, -27.276011], [-57.939427, -27.277289], [-57.944874, -27.286171], [-57.923928, -27.275242], [-57.952914, -27.276011]], [[-57.942278, -27.268918], [-57.926743, -27.268808], [-57.921479, -27.266981], [-57.930069, -27.265336], [-57.942278, -27.268918]], [[-57.921877, -27.272629], [-57.917728, -27.276162], [-57.913537, -27.273902], [-57.917933, -27.274272], [-57.921877, -27.272629]]], [[[-56.914826, -27.42354], [-56.871761, -27.430442], [-56.764027, -27.502565], [-56.7294, -27.501961], [-56.737973, -27.550577], [-56.840421, -27.596181], [-56.87158, -27.58346], [-56.887897, -27.554936], [-56.903059, -27.537397], [-56.912073, -27.531478], [-56.93278, -27.526781], [-56.943637, -27.517852], [-56.952438, -27.514768], [-56.970105, -27.492732], [-57.032066, -27.480648], [-56.914826, -27.42354]]], [[[-57.566611, -27.385118], [-57.502453, -27.403322], [-57.404192, -27.4166], [-57.4656, -27.431654], [-57.478021, -27.436616], [-57.484351, -27.435521], [-57.492187, -27.438701], [-57.593415, -27.392452], [-57.566611, -27.385118]]], [[[-56.705257, -27.563307], [-56.734235, -27.591428], [-56.783897, -27.582692], [-56.761369, -27.577493], [-56.756046, -27.57477], [-56.741997, -27.563506], [-56.740087, -27.563489], [-56.734045, -27.558731], [-56.716932, -27.519936], [-56.699665, -27.536523], [-56.705765, -27.557421], [-56.705257, -27.563307]]], [[[-56.956935, -27.516053], [-56.946156, -27.525062], [-56.925911, -27.535369], [-56.910732, -27.539054], [-56.894129, -27.551716], [-56.875988, -27.583573], [-56.900854, -27.569427], [-56.91961, -27.562565], [-56.932373, -27.535113], [-56.951989, -27.523312], [-56.956935, -27.516053]]], [[[-57.82595, -27.302602], [-57.775227, -27.314999], [-57.768948, -27.321109], [-57.819451, -27.318697], [-57.82595, -27.302602]]], [[[-56.699595, -27.578159], [-56.706684, -27.580773], [-56.693316, -27.545063], [-56.686068, -27.560886], [-56.699595, -27.578159]]], [[[-57.752547, -27.320163], [-57.74152, -27.323307], [-57.75212, -27.327925], [-57.782765, -27.32782], [-57.798088, -27.325021], [-57.794081, -27.32573], [-57.791364, -27.324007], [-57.787068, -27.325632], [-57.752547, -27.320163]]], [[[-58.223046, -27.256236], [-58.215346, -27.258624], [-58.204121, -27.25965], [-58.1967, -27.259137], [-58.190818, -27.262456], [-58.187877, -27.263104], [-58.184773, -27.263319], [-58.183694, -27.262942], [-58.183093, -27.261355], [-58.170484, -27.269217], [-58.211347, -27.263184], [-58.223046, -27.256236]]], [[[-58.303643, -27.262458], [-58.31784, -27.2725], [-58.335558, -27.273423], [-58.321649, -27.262574], [-58.303643, -27.262458]]], [[[-58.150203, -27.257994], [-58.138566, -27.265546], [-58.178002, -27.259267], [-58.156434, -27.255864], [-58.150203, -27.257994]]], [[[-56.35707, -27.517885], [-56.36473, -27.533633], [-56.3712, -27.538533], [-56.367441, -27.520406], [-56.35707, -27.517885]]], [[[-56.944729, -27.53584], [-56.930999, -27.548674], [-56.930017, -27.557105], [-56.941517, -27.546119], [-56.944729, -27.53584]]], [[[-58.224674, -27.2549], [-58.189397, -27.257697], [-58.183839, -27.261834], [-58.196808, -27.258867], [-58.214726, -27.258435], [-58.224674, -27.2549]]], [[[-57.82147, -27.31831], [-57.837282, -27.307302], [-57.838822, -27.297833], [-57.827021, -27.309075], [-57.82147, -27.31831]]], [[[-56.928489, -27.530362], [-56.911989, -27.5329], [-56.907881, -27.538645], [-56.9246, -27.534686], [-56.928489, -27.530362]]], [[[-57.448871, -27.432221], [-57.451574, -27.436582], [-57.472951, -27.438564], [-57.463196, -27.433926], [-57.448871, -27.432221]]], [[[-58.291622, -27.266431], [-58.301328, -27.271593], [-58.312138, -27.27169], [-58.300387, -27.265814], [-58.291622, -27.266431]]], [[[-57.814601, -27.320683], [-57.787826, -27.320612], [-57.78413, -27.322537], [-57.786963, -27.325336], [-57.790252, -27.323727], [-57.792176, -27.324077], [-57.79459, -27.325266], [-57.818483, -27.320124], [-57.814601, -27.320683]]], [[[-57.221511, -27.464203], [-57.232337, -27.463443], [-57.239298, -27.456655], [-57.232975, -27.457467], [-57.221511, -27.464203]]], [[[-56.921087, -27.563079], [-56.902184, -27.57029], [-56.899074, -27.573137], [-56.9128, -27.570917], [-56.921087, -27.563079]]], [[[-58.423271, -27.270154], [-58.420368, -27.279649], [-58.422009, -27.280627], [-58.431126, -27.268229], [-58.423271, -27.270154]]], [[[-58.332672, -27.266267], [-58.337693, -27.27377], [-58.345599, -27.274808], [-58.341444, -27.269441], [-58.332672, -27.266267]]], [[[-56.938793, -27.524839], [-56.947813, -27.522284], [-56.954815, -27.514896], [-56.94276, -27.520435], [-56.938793, -27.524839]]], [[[-56.329329, -27.501225], [-56.343201, -27.508499], [-56.3443, -27.507991], [-56.320702, -27.495473], [-56.329329, -27.501225]]], [[[-56.77472, -27.576272], [-56.772225, -27.572203], [-56.75618, -27.569443], [-56.75872, -27.571362], [-56.764043, -27.572065], [-56.77472, -27.576272]]], [[[-58.232783, -27.259818], [-58.223997, -27.262112], [-58.244081, -27.253966], [-58.235813, -27.258693], [-58.232783, -27.259818]]], [[[-58.085893, -27.262054], [-58.093676, -27.266257], [-58.100041, -27.266305], [-58.090524, -27.259874], [-58.085893, -27.262054]]], [[[-56.754009, -27.57028], [-56.760082, -27.575118], [-56.772225, -27.576443], [-56.767165, -27.573407], [-56.754009, -27.57028]]], [[[-57.817783, -27.324951], [-57.806309, -27.325546], [-57.802601, -27.327855], [-57.820477, -27.324566], [-57.817783, -27.324951]]], [[[-56.71627, -27.579284], [-56.718509, -27.585508], [-56.732994, -27.592678], [-56.722726, -27.58574], [-56.71627, -27.579284]]], [[[-57.784024, -27.330417], [-57.772207, -27.329422], [-57.769098, -27.330169], [-57.779173, -27.333154], [-57.784833, -27.332221], [-57.786823, -27.328552], [-57.784024, -27.330417]]], [[[-58.197323, -27.269238], [-58.187682, -27.269887], [-58.184533, -27.272516], [-58.200732, -27.268945], [-58.197323, -27.269238]]], [[[-56.731839, -27.532994], [-56.731179, -27.527566], [-56.726104, -27.519706], [-56.725842, -27.525369], [-56.731839, -27.532994]]], [[[-56.759079, -27.591047], [-56.753385, -27.592099], [-56.749424, -27.593461], [-56.749115, -27.593956], [-56.759079, -27.591047]]], [[[-56.939771, -27.535489], [-56.937078, -27.537876], [-56.933892, -27.544083], [-56.946785, -27.532692], [-56.939771, -27.535489]]], [[[-56.701363, -27.55255], [-56.70123, -27.55839], [-56.703737, -27.563565], [-56.705029, -27.558398], [-56.701363, -27.55255]]], [[[-57.543052, -27.424431], [-57.539458, -27.427787], [-57.553169, -27.419738], [-57.551941, -27.419533], [-57.543052, -27.424431]]], [[[-56.730764, -27.542449], [-56.731921, -27.536602], [-56.729446, -27.533384], [-56.727606, -27.537425], [-56.730764, -27.542449]]], [[[-56.874278, -27.582497], [-56.863582, -27.58762], [-56.862235, -27.588762], [-56.871965, -27.586544], [-56.874278, -27.582497]]], [[[-58.329729, -27.263728], [-58.335154, -27.264016], [-58.324073, -27.261362], [-58.324996, -27.262054], [-58.329729, -27.263728]]], [[[-58.293429, -27.256285], [-58.288235, -27.25622], [-58.286417, -27.256577], [-58.291384, -27.260115], [-58.299532, -27.255636], [-58.293429, -27.256285]]], [[[-58.611951, -27.316852], [-58.613093, -27.313283], [-58.607097, -27.312854], [-58.609024, -27.316138], [-58.611951, -27.316852]]], [[[-56.861615, -27.592128], [-56.855197, -27.592904], [-56.855711, -27.594831], [-56.866247, -27.591876], [-56.861615, -27.592128]]], [[[-57.444129, -27.429413], [-57.450031, -27.431948], [-57.454806, -27.430925], [-57.44205, -27.427514], [-57.444129, -27.429413]]], [[[-58.165502, -27.274159], [-58.168732, -27.274042], [-58.169783, -27.2698], [-58.164996, -27.271396], [-58.165502, -27.274159]]], [[[-58.357731, -27.27492], [-58.36128, -27.274703], [-58.349075, -27.271803], [-58.351109, -27.274011], [-58.357731, -27.27492]]], [[[-58.192097, -27.27771], [-58.19411, -27.279431], [-58.19979, -27.277061], [-58.197778, -27.275957], [-58.192097, -27.27771]]], [[[-57.481254, -27.437477], [-57.479921, -27.438493], [-57.48784, -27.439153], [-57.484118, -27.436117], [-57.481254, -27.437477]]], [[[-56.781625, -27.572992], [-56.788961, -27.576244], [-56.789467, -27.575666], [-56.784516, -27.571655], [-56.781625, -27.572992]]], [[[-56.710336, -27.58133], [-56.711821, -27.581949], [-56.706684, -27.575079], [-56.706065, -27.57774], [-56.710336, -27.58133]]], [[[-57.541051, -27.429531], [-57.542869, -27.429697], [-57.548394, -27.426491], [-57.541232, -27.427582], [-57.541051, -27.429531]]], [[[-56.903637, -27.541573], [-56.905886, -27.540545], [-56.907363, -27.535663], [-56.903509, -27.537847], [-56.903637, -27.541573]]], [[[-56.765927, -27.569867], [-56.768081, -27.569071], [-56.761047, -27.567587], [-56.762446, -27.568803], [-56.765927, -27.569867]]], [[[-58.420597, -27.280978], [-58.413948, -27.281097], [-58.41312, -27.281593], [-58.418207, -27.283202], [-58.420597, -27.280978]]], [[[-56.945215, -27.530746], [-56.93928, -27.532507], [-56.937885, -27.534432], [-56.945622, -27.531965], [-56.945215, -27.530746]]], [[[-56.739175, -27.562097], [-56.741974, -27.561672], [-56.736843, -27.558564], [-56.736126, -27.560066], [-56.739175, -27.562097]]], [[[-57.563128, -27.410461], [-57.557194, -27.414144], [-57.554601, -27.416941], [-57.56456, -27.410529], [-57.563128, -27.410461]]], [[[-58.437706, -27.267721], [-58.436534, -27.270747], [-58.441708, -27.267037], [-58.441058, -27.265638], [-58.437706, -27.267721]]], [[[-57.610778, -27.383325], [-57.610586, -27.381637], [-57.60579, -27.381215], [-57.606365, -27.382251], [-57.610778, -27.383325]]], [[[-58.457404, -27.272953], [-58.4626, -27.271824], [-58.463321, -27.270848], [-58.45788, -27.2717], [-58.457404, -27.272953]]], [[[-56.689817, -27.569795], [-56.690297, -27.572176], [-56.693674, -27.574418], [-56.690861, -27.570039], [-56.689817, -27.569795]]], [[[-56.93127, -27.527496], [-56.928909, -27.527833], [-56.927512, -27.528749], [-56.934065, -27.527544], [-56.93127, -27.527496]]], [[[-58.262908, -27.259689], [-58.263817, -27.261247], [-58.267713, -27.26129], [-58.265635, -27.259689], [-58.262908, -27.259689]]], [[[-57.476975, -27.439656], [-57.47868, -27.439997], [-57.479294, -27.438837], [-57.475474, -27.438087], [-57.476975, -27.439656]]]]}, 'Entre Ríos': {'type': 'Polygon', 'coordinates': [[[-58.585342, -30.159018], [-58.496562, -30.207688], [-58.461072, -30.206935], [-58.451527, -30.224856], [-58.437047, -30.221675], [-58.369324, -30.264734], [-58.343726, -30.27125], [-58.28555, -30.244631], [-58.20739, -30.288077], [-58.19195, -30.335628], [-58.142282, -30.405238], [-58.072575, -30.425187], [-58.077347, -30.465802], [-58.057776, -30.496419], [-58.069126, -30.544908], [-58.037025, -30.5679], [-58.033522, -30.597695], [-57.980233, -30.629397], [-57.97485, -30.64734], [-57.894779, -30.669411], [-57.854835, -30.69465], [-57.843748, -30.721658], [-57.821523, -30.719403], [-57.797397, -30.782707], [-57.79979, -30.856979], [-57.824487, -30.91163], [-57.846007, -30.919908], [-57.893289, -30.907073], [-57.914899, -30.920564], [-57.861725, -31.043471], [-57.916687, -31.122593], [-57.91314, -31.212921], [-57.937054, -31.273264], [-57.980907, -31.318799], [-57.996945, -31.360511], [-57.979348, -31.388399], [-58.082394, -31.457827], [-58.078266, -31.488811], [-58.003795, -31.531906], [-57.980639, -31.581048], [-58.012854, -31.686734], [-58.035054, -31.718821], [-58.038148, -31.759064], [-58.086138, -31.821137], [-58.190237, -31.852978], [-58.20681, -31.871645], [-58.191344, -31.920347], [-58.168715, -31.932089], [-58.1383, -32.001843], [-58.143142, -32.060556], [-58.167404, -32.091382], [-58.183326, -32.1592], [-58.099882, -32.260012], [-58.100891, -32.303516], [-58.12299, -32.339999], [-58.183357, -32.376675], [-58.204588, -32.462003], [-58.187387, -32.532869], [-58.160583, -32.57078], [-58.145047, -32.671127], [-58.149629, -32.734879], [-58.119798, -32.815234], [-58.115576, -32.919768], [-58.087324, -32.958903], [-58.08418, -33.000123], [-58.098902, -33.020388], [-58.175963, -33.076893], [-58.361818, -33.120989], [-58.404368, -33.180052], [-58.409848, -33.235555], [-58.39047, -33.279121], [-58.397185, -33.313767], [-58.431714, -33.363329], [-58.44315, -33.44808], [-58.433904, -33.499159], [-58.445011, -33.541052], [-58.494713, -33.586549], [-58.434369, -33.721887], [-58.423586, -33.916318], [-58.344989, -34.038397], [-58.378925, -34.012226], [-58.463196, -34.001892], [-58.60717, -34.037871], [-58.654997, -34.035254], [-58.763739, -33.94531], [-58.830601, -33.955304], [-58.854872, -33.947214], [-58.89978, -33.890991], [-58.97789, -33.870357], [-58.992881, -33.841804], [-59.020482, -33.848942], [-59.055481, -33.832397], [-59.130676, -33.847717], [-59.173584, -33.807007], [-59.24231, -33.807495], [-59.254883, -33.785889], [-59.235413, -33.73999], [-59.254272, -33.72229], [-59.377686, -33.736511], [-59.437012, -33.729004], [-59.467773, -33.704285], [-59.479309, -33.652893], [-59.516737, -33.630834], [-59.593207, -33.689071], [-59.617418, -33.693741], [-59.636037, -33.642701], [-59.65891, -33.620037], [-59.787638, -33.603648], [-59.809381, -33.588509], [-59.80953, -33.551657], [-59.833949, -33.521259], [-59.894744, -33.510284], [-59.933006, -33.474891], [-59.999393, -33.470132], [-60.04484, -33.447527], [-60.111084, -33.372986], [-60.169617, -33.348999], [-60.200684, -33.292175], [-60.297829, -33.242284], [-60.284504, -33.207068], [-60.303063, -33.183511], [-60.335662, -33.176849], [-60.392055, -33.188984], [-60.424178, -33.173994], [-60.513184, -33.112915], [-60.60121, -33.006004], [-60.620246, -32.947945], [-60.676208, -32.899597], [-60.681398, -32.871564], [-60.658875, -32.831177], [-60.716003, -32.773621], [-60.723514, -32.671689], [-60.752075, -32.622192], [-60.747375, -32.554321], [-60.776184, -32.507019], [-60.723816, -32.456116], [-60.711617, -32.393055], [-60.718993, -32.334044], [-60.679018, -32.319529], [-60.672607, -32.232605], [-60.700382, -32.149769], [-60.658555, -32.080155], [-60.658081, -32.05072], [-60.723514, -31.977601], [-60.719707, -31.958803], [-60.671875, -31.930176], [-60.652893, -31.897095], [-60.675781, -31.811707], [-60.640947, -31.791765], [-60.642883, -31.751221], [-60.627622, -31.73323], [-60.568135, -31.704914], [-60.46759, -31.698303], [-60.330665, -31.608546], [-60.332807, -31.547156], [-60.244001, -31.530395], [-60.212354, -31.467816], [-60.143494, -31.443604], [-60.138428, -31.413391], [-60.086792, -31.346985], [-60.105591, -31.289917], [-60.013428, -31.250671], [-59.963379, -31.119873], [-59.902838, -31.084827], [-59.818176, -30.942505], [-59.734848, -30.876624], [-59.704867, -30.817376], [-59.699394, -30.769072], [-59.63269, -30.711304], [-59.678406, -30.646912], [-59.645856, -30.597037], [-59.65033, -30.500977], [-59.703439, -30.44261], [-59.671672, -30.338662], [-59.63682, -30.357754], [-59.615097, -30.419207], [-59.587769, -30.434822], [-59.557735, -30.33265], [-59.490212, -30.344331], [-59.464823, -30.33703], [-59.464028, -30.324323], [-59.44097, -30.329455], [-59.419647, -30.315736], [-59.347148, -30.324657], [-59.323732, -30.354244], [-59.298893, -30.346712], [-59.288267, -30.361884], [-59.273512, -30.352434], [-59.229703, -30.364175], [-59.192139, -30.329546], [-59.136475, -30.311574], [-59.136577, -30.286569], [-59.075638, -30.257267], [-59.081459, -30.249074], [-59.064251, -30.230818], [-58.965202, -30.21834], [-58.912017, -30.248214], [-58.878856, -30.246662], [-58.803753, -30.210834], [-58.748357, -30.213884], [-58.733757, -30.193413], [-58.680701, -30.175028], [-58.681665, -30.163581], [-58.647139, -30.163194], [-58.630015, -30.176859], [-58.585342, -30.159018]], [[-58.110345, -32.971331], [-58.107656, -33.001667], [-58.109117, -33.010383], [-58.11455, -33.024698], [-58.087384, -32.996769], [-58.08891, -32.957851], [-58.099723, -32.951575], [-58.101165, -32.956382], [-58.107781, -32.962243], [-58.110345, -32.971331]], [[-58.114038, -33.021062], [-58.108105, -33.001483], [-58.11574, -32.990354], [-58.128755, -33.025391], [-58.114038, -33.021062]], [[-58.114955, -32.974192], [-58.108589, -32.961814], [-58.101435, -32.954354], [-58.112485, -32.931158], [-58.114955, -32.974192]], [[-58.114383, -32.990141], [-58.111339, -32.980019], [-58.111976, -32.971667], [-58.118078, -32.982034], [-58.114383, -32.990141]], [[-58.125538, -33.028812], [-58.12983, -33.02886], [-58.133199, -33.034628], [-58.12549, -33.029678], [-58.125538, -33.028812]], [[-58.124477, -33.004665], [-58.126438, -33.007848], [-58.125742, -33.011396], [-58.124401, -33.008557], [-58.124477, -33.004665]]]}, 'Chaco': {'type': 'Polygon', 'coordinates': [[[-62.32434, -24.123184], [-62.301522, -24.116203], [-62.243528, -24.145432], [-62.228317, -24.135399], [-62.216433, -24.151103], [-62.182682, -24.155029], [-62.171749, -24.177707], [-62.180781, -24.19515], [-62.136097, -24.196022], [-62.138474, -24.218694], [-62.076428, -24.201301], [-62.082857, -24.230464], [-62.055286, -24.211282], [-62.041025, -24.240489], [-62.017733, -24.226976], [-61.989211, -24.250514], [-61.979229, -24.245284], [-61.984652, -24.227095], [-61.945003, -24.239182], [-61.95451, -24.26141], [-61.907838, -24.259432], [-61.904008, -24.27777], [-61.920264, -24.283899], [-61.908516, -24.306762], [-61.883681, -24.266639], [-61.879321, -24.307321], [-61.839948, -24.301497], [-61.798592, -24.336345], [-61.761514, -24.306725], [-61.760563, -24.328505], [-61.720633, -24.33983], [-61.728239, -24.359427], [-61.694013, -24.365959], [-61.713978, -24.383375], [-61.694488, -24.415154], [-61.639698, -24.434575], [-61.61035, -24.421683], [-61.60417, -24.455627], [-61.597515, -24.438221], [-61.582844, -24.441594], [-61.596403, -24.479293], [-61.518605, -24.51914], [-61.518764, -24.547014], [-61.477249, -24.57523], [-61.481052, -24.595225], [-61.453956, -24.612175], [-61.382725, -24.612757], [-61.381226, -24.637812], [-61.361836, -24.621647], [-61.346107, -24.638663], [-61.284728, -24.624342], [-61.276062, -24.639155], [-61.255731, -24.621735], [-61.244323, -24.653453], [-61.230062, -24.635205], [-61.22721, -24.655191], [-61.197262, -24.661272], [-61.199164, -24.674304], [-61.217703, -24.665616], [-61.201065, -24.682557], [-61.208074, -24.698026], [-61.180149, -24.722509], [-61.170642, -24.699929], [-61.156857, -24.703837], [-61.170167, -24.709916], [-61.171593, -24.734231], [-61.150202, -24.74986], [-61.156381, -24.785016], [-61.133089, -24.786752], [-61.144022, -24.804977], [-61.092683, -24.834913], [-61.091257, -24.852697], [-61.067965, -24.857468], [-61.075102, -24.872696], [-61.04277, -24.897363], [-61.024231, -24.889125], [-61.031837, -24.901265], [-61.01615, -24.909502], [-61.020235, -24.921316], [-60.994186, -24.916385], [-60.99571, -24.933778], [-60.963973, -24.948386], [-60.953522, -24.972647], [-60.921554, -24.97928], [-60.941519, -24.990979], [-60.940093, -25.004408], [-60.927258, -24.992711], [-60.921078, -25.010473], [-60.892082, -25.011339], [-60.86974, -25.031263], [-60.821728, -25.034295], [-60.83694, -25.038192], [-60.814123, -25.074565], [-60.806992, -25.064174], [-60.748523, -25.069369], [-60.767537, -25.090583], [-60.738065, -25.097509], [-60.721447, -25.12521], [-60.700036, -25.119582], [-60.676268, -25.135594], [-60.686251, -25.113956], [-60.653451, -25.119582], [-60.670564, -25.149008], [-60.644419, -25.1542], [-60.65155, -25.165448], [-60.630634, -25.177561], [-60.608292, -25.177128], [-60.60544, -25.159392], [-60.58595, -25.177993], [-60.571214, -25.169774], [-60.568362, -25.198755], [-60.541002, -25.167448], [-60.529648, -25.171784], [-60.510212, -25.1883], [-60.50831, -25.213816], [-60.489771, -25.209492], [-60.485017, -25.235869], [-60.44176, -25.265266], [-60.427499, -25.315397], [-60.413714, -25.30762], [-60.388995, -25.335271], [-60.354294, -25.322742], [-60.338607, -25.365507], [-60.346213, -25.380191], [-60.312937, -25.388396], [-60.333853, -25.391418], [-60.327198, -25.407394], [-60.307708, -25.405667], [-60.32292, -25.424231], [-60.295824, -25.409984], [-60.300103, -25.427684], [-60.282514, -25.433296], [-60.274433, -25.466528], [-60.252567, -25.463939], [-60.224521, -25.485083], [-60.241158, -25.508811], [-60.226897, -25.501477], [-60.220242, -25.534259], [-60.241633, -25.544609], [-60.237831, -25.559271], [-60.205031, -25.573931], [-60.226422, -25.586002], [-60.184115, -25.611434], [-60.203129, -25.613589], [-60.175083, -25.630396], [-60.192671, -25.635998], [-60.176985, -25.659696], [-60.142283, -25.637291], [-60.115663, -25.663143], [-60.101402, -25.657542], [-60.102829, -25.670036], [-60.034852, -25.689419], [-60.011559, -25.729469], [-59.97971, -25.723011], [-59.986841, -25.743247], [-59.954992, -25.769936], [-59.942632, -25.759606], [-59.943108, -25.773379], [-59.921717, -25.778975], [-59.92647, -25.791886], [-59.877508, -25.802213], [-59.879063, -25.841987], [-59.859207, -25.838028], [-59.871339, -25.845802], [-59.843676, -25.856047], [-59.836128, -25.875978], [-59.827161, -25.869637], [-59.81572, -25.884432], [-59.826803, -25.889937], [-59.78118, -25.896681], [-59.788284, -25.909712], [-59.764571, -25.916001], [-59.768644, -25.925682], [-59.753458, -25.920721], [-59.75452, -25.955628], [-59.743572, -25.949521], [-59.751428, -25.963237], [-59.740805, -25.975545], [-59.72787, -25.97015], [-59.725094, -25.999758], [-59.708057, -25.996323], [-59.707787, -26.009779], [-59.671168, -26.003304], [-59.685435, -26.009403], [-59.668182, -26.024916], [-59.678641, -26.054133], [-59.663611, -26.06246], [-59.68251, -26.089486], [-59.678304, -26.115002], [-59.649268, -26.119455], [-59.660456, -26.143402], [-59.626247, -26.13434], [-59.639353, -26.116772], [-59.608874, -26.123712], [-59.605861, -26.114629], [-59.597208, -26.130961], [-59.584773, -26.121826], [-59.574229, -26.152815], [-59.535484, -26.141322], [-59.477396, -26.162347], [-59.438301, -26.149132], [-59.408793, -26.179131], [-59.420658, -26.1783], [-59.428885, -26.198811], [-59.410298, -26.207102], [-59.415862, -26.222161], [-59.397935, -26.258624], [-59.381307, -26.261717], [-59.396067, -26.273781], [-59.386937, -26.29473], [-59.403288, -26.299484], [-59.357506, -26.33797], [-59.312988, -26.31895], [-59.311118, -26.339427], [-59.25241, -26.348706], [-59.254734, -26.340487], [-59.192587, -26.329734], [-59.181965, -26.304718], [-59.140226, -26.299443], [-59.1388, -26.312724], [-59.153872, -26.31686], [-59.110278, -26.326004], [-59.128817, -26.343994], [-59.106, -26.344851], [-59.068475, -26.374233], [-59.042898, -26.365348], [-59.024081, -26.38122], [-58.985715, -26.379999], [-58.95556, -26.399876], [-58.909809, -26.462557], [-58.865752, -26.475083], [-58.845614, -26.519216], [-58.792258, -26.519425], [-58.795034, -26.536524], [-58.772284, -26.536917], [-58.744377, -26.602646], [-58.689424, -26.594151], [-58.681546, -26.642494], [-58.611001, -26.681494], [-58.572181, -26.687517], [-58.569117, -26.727702], [-58.545387, -26.751587], [-58.5446, -26.738628], [-58.530815, -26.740762], [-58.534142, -26.756125], [-58.485656, -26.794952], [-58.473296, -26.828222], [-58.450479, -26.817133], [-58.443824, -26.83206], [-58.396763, -26.832487], [-58.398665, -26.864892], [-58.379175, -26.861908], [-58.380321, -26.887965], [-58.484137, -26.935354], [-58.472606, -27.003836], [-58.519433, -27.001951], [-58.502301, -27.056031], [-58.517206, -27.062656], [-58.523488, -27.039642], [-58.556039, -27.035701], [-58.554897, -27.108913], [-58.62571, -27.113881], [-58.652207, -27.130556], [-58.66163, -27.186578], [-58.592479, -27.229028], [-58.616488, -27.318632], [-58.668787, -27.359092], [-58.781012, -27.381778], [-58.881402, -27.497892], [-58.871019, -27.571271], [-58.882746, -27.608523], [-58.835497, -27.646641], [-58.818775, -27.681178], [-58.809708, -27.783407], [-58.844894, -27.885572], [-58.829262, -27.961873], [-58.857739, -28.011612], [-58.865299, -28.056653], [-58.880822, -28.066825], [-58.892415, -28.045669], [-58.884005, -28.000001], [-61.712045, -27.998552], [-61.711816, -25.654744], [-63.422412, -25.64922], [-62.342352, -24.392113], [-62.342157, -24.110249], [-62.326716, -24.078676], [-62.309603, -24.105295], [-62.32434, -24.123184]]]}, 'Formosa': {'type': 'Polygon', 'coordinates': [[[-62.312005, -22.48666], [-62.290366, -22.474434], [-62.28707, -22.522657], [-62.280918, -22.51143], [-62.252286, -22.5153], [-62.254639, -22.532051], [-62.234095, -22.54433], [-62.233477, -22.57049], [-62.26421, -22.600209], [-62.235794, -22.615725], [-62.242354, -22.628359], [-62.22574, -22.622642], [-62.207208, -22.645761], [-62.188956, -22.622548], [-62.20102, -22.683658], [-62.186291, -22.678685], [-62.177224, -22.693607], [-62.201074, -22.695128], [-62.196987, -22.714277], [-62.18491, -22.705103], [-62.108199, -22.819527], [-62.095241, -22.806137], [-62.072169, -22.846668], [-62.040548, -22.869415], [-62.024822, -22.927711], [-61.998988, -22.93051], [-62.010376, -22.975686], [-61.996766, -22.966075], [-62.005953, -22.988472], [-61.992498, -22.992033], [-61.99712, -23.010175], [-61.989467, -23.002924], [-61.982782, -23.022899], [-61.951611, -23.033761], [-61.955336, -23.048513], [-61.940064, -23.046541], [-61.934727, -23.067687], [-61.89183, -23.069633], [-61.87265, -23.093127], [-61.860562, -23.085719], [-61.818255, -23.127097], [-61.806596, -23.15699], [-61.754424, -23.171093], [-61.734665, -23.199464], [-61.734897, -23.235754], [-61.663075, -23.27665], [-61.670797, -23.284517], [-61.636656, -23.27543], [-61.572954, -23.30403], [-61.566557, -23.339381], [-61.514961, -23.352151], [-61.522207, -23.377273], [-61.494122, -23.424436], [-61.44949, -23.419048], [-61.384661, -23.459012], [-61.384664, -23.447246], [-61.37642, -23.457566], [-61.35207, -23.449954], [-61.292669, -23.495426], [-61.280861, -23.488671], [-61.2921, -23.509286], [-61.273786, -23.506186], [-61.27761, -23.516992], [-61.263155, -23.516334], [-61.246901, -23.540801], [-61.215117, -23.551752], [-61.189738, -23.540672], [-61.195368, -23.549969], [-61.176573, -23.547538], [-61.146599, -23.580215], [-61.130166, -23.578505], [-61.131921, -23.598014], [-61.12189, -23.584904], [-61.089515, -23.613861], [-61.101149, -23.626982], [-61.090499, -23.639054], [-61.095178, -23.666661], [-61.081145, -23.670904], [-61.086722, -23.679858], [-61.039989, -23.743022], [-61.015144, -23.758246], [-61.020403, -23.772625], [-60.986809, -23.812867], [-60.964946, -23.808633], [-60.958946, -23.820574], [-60.933932, -23.800326], [-60.857758, -23.849203], [-60.847339, -23.871548], [-60.742974, -23.87272], [-60.694212, -23.902591], [-60.612822, -23.903819], [-60.587355, -23.917024], [-60.592763, -23.943038], [-60.580016, -23.952996], [-60.589928, -23.958839], [-60.509661, -23.970865], [-60.477794, -23.950936], [-60.389508, -23.994954], [-60.374233, -24.023722], [-60.3355, -24.0216], [-60.295069, -24.041431], [-60.177525, -24.044022], [-60.039006, -24.011656], [-59.475908, -24.331139], [-59.456599, -24.373513], [-59.421276, -24.408879], [-59.374104, -24.421867], [-59.350488, -24.483049], [-59.293446, -24.515487], [-59.257605, -24.519681], [-59.241276, -24.546063], [-59.185538, -24.563641], [-59.127806, -24.617952], [-59.07983, -24.621284], [-59.023883, -24.660099], [-58.972971, -24.662056], [-58.807361, -24.772409], [-58.726992, -24.774331], [-58.708566, -24.810518], [-58.68875, -24.810176], [-58.673502, -24.831515], [-58.573584, -24.821026], [-58.526185, -24.850817], [-58.510899, -24.843507], [-58.473704, -24.860906], [-58.430017, -24.893304], [-58.428627, -24.923438], [-58.40834, -24.923057], [-58.391374, -24.953019], [-58.348316, -24.971731], [-58.333677, -25.000018], [-58.255365, -24.931865], [-58.224653, -24.927106], [-58.224438, -24.942025], [-58.202, -24.949663], [-58.193467, -24.968033], [-58.14978, -24.975214], [-58.13716, -25.011677], [-58.113154, -25.019626], [-58.090575, -25.00875], [-58.068674, -25.045712], [-58.005742, -25.035062], [-57.999517, -25.074166], [-57.975375, -25.086273], [-57.941768, -25.071682], [-57.861132, -25.08159], [-57.837014, -25.131425], [-57.753504, -25.176473], [-57.748621, -25.20674], [-57.759062, -25.220274], [-57.735677, -25.234637], [-57.741026, -25.253254], [-57.722685, -25.253054], [-57.722428, -25.277534], [-57.711835, -25.273051], [-57.705096, -25.291734], [-57.694446, -25.285967], [-57.707228, -25.300063], [-57.70285, -25.322201], [-57.642351, -25.383355], [-57.596518, -25.401064], [-57.560602, -25.452714], [-57.579709, -25.512534], [-57.572647, -25.556052], [-57.580414, -25.569416], [-57.60457, -25.568787], [-57.616848, -25.616072], [-57.674983, -25.596998], [-57.678067, -25.661072], [-57.719926, -25.646339], [-57.742785, -25.651878], [-57.76665, -25.700131], [-57.727122, -25.715039], [-57.773565, -25.757607], [-57.826545, -25.754442], [-57.796564, -25.824855], [-57.816266, -25.847413], [-57.858469, -25.858001], [-57.863037, -25.882201], [-57.836767, -25.915599], [-57.878969, -25.932274], [-57.895302, -25.960713], [-57.854014, -25.987439], [-57.854756, -26.008455], [-57.919515, -26.032325], [-57.946413, -26.066019], [-58.015404, -26.101668], [-58.090928, -26.11628], [-58.118638, -26.154354], [-58.11966, -26.186495], [-58.159009, -26.181187], [-58.104227, -26.229894], [-58.163825, -26.264373], [-58.159666, -26.348817], [-58.210453, -26.378972], [-58.221727, -26.399495], [-58.1809, -26.457397], [-58.233603, -26.473542], [-58.207457, -26.498438], [-58.224932, -26.536072], [-58.199805, -26.560513], [-58.194094, -26.597804], [-58.210199, -26.622132], [-58.191296, -26.646345], [-58.21511, -26.654911], [-58.260224, -26.645089], [-58.246005, -26.750337], [-58.259768, -26.762273], [-58.293347, -26.757761], [-58.283981, -26.795395], [-58.334863, -26.809557], [-58.317895, -26.852734], [-58.325272, -26.869674], [-58.370213, -26.864266], [-58.378236, -26.879842], [-58.382503, -26.858924], [-58.398665, -26.864892], [-58.390584, -26.841015], [-58.401042, -26.829928], [-58.404369, -26.840589], [-58.409123, -26.827369], [-58.428137, -26.833766], [-58.422433, -26.824384], [-58.443824, -26.83206], [-58.450479, -26.817133], [-58.473296, -26.828222], [-58.485656, -26.794952], [-58.534142, -26.756125], [-58.530815, -26.740762], [-58.5446, -26.738628], [-58.545387, -26.751587], [-58.569117, -26.727702], [-58.572181, -26.687517], [-58.611001, -26.681494], [-58.681546, -26.642494], [-58.689424, -26.594151], [-58.744377, -26.602646], [-58.772284, -26.536917], [-58.795034, -26.536524], [-58.792258, -26.519425], [-58.845614, -26.519216], [-58.865752, -26.475083], [-58.909809, -26.462557], [-58.95556, -26.399876], [-58.985715, -26.379999], [-59.024081, -26.38122], [-59.042898, -26.365348], [-59.068475, -26.374233], [-59.106, -26.344851], [-59.128817, -26.343994], [-59.110278, -26.326004], [-59.153872, -26.31686], [-59.1388, -26.312724], [-59.140226, -26.299443], [-59.181965, -26.304718], [-59.192587, -26.329734], [-59.254734, -26.340487], [-59.25241, -26.348706], [-59.311118, -26.339427], [-59.312988, -26.31895], [-59.357506, -26.33797], [-59.403288, -26.299484], [-59.386937, -26.29473], [-59.396067, -26.273781], [-59.381307, -26.261717], [-59.397935, -26.258624], [-59.415862, -26.222161], [-59.410298, -26.207102], [-59.428885, -26.198811], [-59.420658, -26.1783], [-59.408793, -26.179131], [-59.438301, -26.149132], [-59.477396, -26.162347], [-59.535484, -26.141322], [-59.574229, -26.152815], [-59.584773, -26.121826], [-59.597208, -26.130961], [-59.605861, -26.114629], [-59.608874, -26.123712], [-59.639353, -26.116772], [-59.626247, -26.13434], [-59.660456, -26.143402], [-59.649268, -26.119455], [-59.678304, -26.115002], [-59.68251, -26.089486], [-59.663611, -26.06246], [-59.678641, -26.054133], [-59.668182, -26.024916], [-59.685435, -26.009403], [-59.671168, -26.003304], [-59.707787, -26.009779], [-59.708057, -25.996323], [-59.725094, -25.999758], [-59.72787, -25.97015], [-59.740805, -25.975545], [-59.751428, -25.963237], [-59.743572, -25.949521], [-59.75452, -25.955628], [-59.753458, -25.920721], [-59.768644, -25.925682], [-59.764571, -25.916001], [-59.788284, -25.909712], [-59.78118, -25.896681], [-59.826803, -25.889937], [-59.81572, -25.884432], [-59.827161, -25.869637], [-59.836128, -25.875978], [-59.843676, -25.856047], [-59.871339, -25.845802], [-59.859207, -25.838028], [-59.879063, -25.841987], [-59.877508, -25.802213], [-59.92647, -25.791886], [-59.921717, -25.778975], [-59.943108, -25.773379], [-59.942632, -25.759606], [-59.954992, -25.769936], [-59.986841, -25.743247], [-59.97971, -25.723011], [-60.011559, -25.729469], [-60.034852, -25.689419], [-60.102829, -25.670036], [-60.101402, -25.657542], [-60.115663, -25.663143], [-60.142283, -25.637291], [-60.176985, -25.659696], [-60.192671, -25.635998], [-60.175083, -25.630396], [-60.203129, -25.613589], [-60.184115, -25.611434], [-60.226422, -25.586002], [-60.205031, -25.573931], [-60.237831, -25.559271], [-60.241633, -25.544609], [-60.220242, -25.534259], [-60.226897, -25.501477], [-60.241158, -25.508811], [-60.224521, -25.485083], [-60.252567, -25.463939], [-60.274433, -25.466528], [-60.282514, -25.433296], [-60.300103, -25.427684], [-60.295824, -25.409984], [-60.32292, -25.424231], [-60.307708, -25.405667], [-60.327198, -25.407394], [-60.333853, -25.391418], [-60.312937, -25.388396], [-60.346213, -25.380191], [-60.338607, -25.365507], [-60.354294, -25.322742], [-60.388995, -25.335271], [-60.413714, -25.30762], [-60.427499, -25.315397], [-60.44176, -25.265266], [-60.485017, -25.235869], [-60.489771, -25.209492], [-60.50831, -25.213816], [-60.510212, -25.1883], [-60.529648, -25.171784], [-60.541002, -25.167448], [-60.568362, -25.198755], [-60.571214, -25.169774], [-60.58595, -25.177993], [-60.60544, -25.159392], [-60.608292, -25.177128], [-60.630634, -25.177561], [-60.65155, -25.165448], [-60.644419, -25.1542], [-60.670564, -25.149008], [-60.653451, -25.119582], [-60.686251, -25.113956], [-60.676268, -25.135594], [-60.700036, -25.119582], [-60.721447, -25.12521], [-60.738065, -25.097509], [-60.767537, -25.090583], [-60.748523, -25.069369], [-60.806992, -25.064174], [-60.814123, -25.074565], [-60.83694, -25.038192], [-60.821728, -25.034295], [-60.86974, -25.031263], [-60.892082, -25.011339], [-60.921078, -25.010473], [-60.927258, -24.992711], [-60.940093, -25.004408], [-60.941519, -24.990979], [-60.921554, -24.97928], [-60.953522, -24.972647], [-60.963973, -24.948386], [-60.99571, -24.933778], [-60.994186, -24.916385], [-61.020235, -24.921316], [-61.01615, -24.909502], [-61.031837, -24.901265], [-61.024231, -24.889125], [-61.04277, -24.897363], [-61.075102, -24.872696], [-61.067965, -24.857468], [-61.091257, -24.852697], [-61.092683, -24.834913], [-61.144022, -24.804977], [-61.133089, -24.786752], [-61.156381, -24.785016], [-61.150202, -24.74986], [-61.171593, -24.734231], [-61.170167, -24.709916], [-61.156857, -24.703837], [-61.170642, -24.699929], [-61.180149, -24.722509], [-61.208074, -24.698026], [-61.201065, -24.682557], [-61.217703, -24.665616], [-61.199164, -24.674304], [-61.197262, -24.661272], [-61.22721, -24.655191], [-61.230062, -24.635205], [-61.244323, -24.653453], [-61.255731, -24.621735], [-61.276062, -24.639155], [-61.284728, -24.624342], [-61.346107, -24.638663], [-61.361836, -24.621647], [-61.381226, -24.637812], [-61.382725, -24.612757], [-61.453956, -24.612175], [-61.481052, -24.595225], [-61.477249, -24.57523], [-61.518764, -24.547014], [-61.518605, -24.51914], [-61.596403, -24.479293], [-61.582844, -24.441594], [-61.597515, -24.438221], [-61.60417, -24.455627], [-61.61035, -24.421683], [-61.639698, -24.434575], [-61.694488, -24.415154], [-61.713978, -24.383375], [-61.694013, -24.365959], [-61.728239, -24.359427], [-61.720633, -24.33983], [-61.760563, -24.328505], [-61.761514, -24.306725], [-61.798592, -24.336345], [-61.839948, -24.301497], [-61.879321, -24.307321], [-61.883681, -24.266639], [-61.908516, -24.306762], [-61.920264, -24.283899], [-61.904008, -24.27777], [-61.907838, -24.259432], [-61.95451, -24.26141], [-61.945003, -24.239182], [-61.984652, -24.227095], [-61.979229, -24.245284], [-61.989211, -24.250514], [-62.017733, -24.226976], [-62.041025, -24.240489], [-62.055286, -24.211282], [-62.082857, -24.230464], [-62.076428, -24.201301], [-62.138474, -24.218694], [-62.136097, -24.196022], [-62.180781, -24.19515], [-62.171749, -24.177707], [-62.182682, -24.155029], [-62.216433, -24.151103], [-62.228317, -24.135399], [-62.243528, -24.145432], [-62.301522, -24.116203], [-62.32434, -24.123184], [-62.309603, -24.105295], [-62.326716, -24.078676], [-62.342157, -24.110249], [-62.342256, -22.460941], [-62.334461, -22.478732], [-62.312005, -22.48666]]]}, 'Misiones': {'type': 'MultiPolygon', 'coordinates': [[[[-54.109057, -25.539824], [-54.123582, -25.571637], [-54.100406, -25.618192], [-54.081095, -25.55825], [-54.049077, -25.585373], [-54.016363, -25.563655], [-53.970144, -25.601017], [-53.978109, -25.61029], [-53.965441, -25.612693], [-53.962259, -25.643477], [-53.951263, -25.646724], [-53.943499, -25.61103], [-53.932799, -25.62261], [-53.916625, -25.616216], [-53.912246, -25.636309], [-53.890714, -25.622984], [-53.894969, -25.643188], [-53.876414, -25.645103], [-53.889994, -25.661277], [-53.862074, -25.658983], [-53.859404, -25.679381], [-53.842236, -25.688759], [-53.85518, -25.691436], [-53.851492, -25.706189], [-53.872444, -25.693431], [-53.865576, -25.705407], [-53.879256, -25.708031], [-53.859015, -25.72335], [-53.866337, -25.744245], [-53.834109, -25.751333], [-53.857125, -25.766329], [-53.833392, -25.777498], [-53.843324, -25.790604], [-53.822694, -25.792113], [-53.819449, -25.814951], [-53.838408, -25.815888], [-53.850081, -25.839601], [-53.823358, -25.872082], [-53.849899, -25.883774], [-53.819575, -25.898437], [-53.835241, -25.911833], [-53.818801, -25.912606], [-53.820605, -25.927608], [-53.833806, -25.920625], [-53.842917, -25.933681], [-53.828082, -25.96364], [-53.83607, -25.971906], [-53.813777, -25.980118], [-53.817883, -25.989982], [-53.802864, -25.984617], [-53.77164, -26.03151], [-53.737276, -26.041433], [-53.726722, -26.064822], [-53.742585, -26.081772], [-53.744293, -26.116237], [-53.710403, -26.130531], [-53.641272, -26.213878], [-53.653514, -26.235097], [-53.637383, -26.249682], [-53.646187, -26.24837], [-53.645624, -26.286073], [-53.710055, -26.387959], [-53.696623, -26.402093], [-53.705451, -26.416381], [-53.687331, -26.430848], [-53.688402, -26.443424], [-53.704249, -26.441865], [-53.698276, -26.469598], [-53.715337, -26.471121], [-53.695873, -26.475237], [-53.695945, -26.490442], [-53.705213, -26.509002], [-53.729493, -26.501476], [-53.716862, -26.527659], [-53.723097, -26.542326], [-53.741752, -26.544611], [-53.725295, -26.554281], [-53.735927, -26.566606], [-53.714255, -26.55646], [-53.707544, -26.560915], [-53.722059, -26.562133], [-53.724619, -26.58903], [-53.732786, -26.58648], [-53.739829, -26.644082], [-53.759531, -26.64114], [-53.726342, -26.658777], [-53.724924, -26.647717], [-53.720089, -26.650953], [-53.731606, -26.677775], [-53.717367, -26.684685], [-53.72876, -26.692185], [-53.743465, -26.681525], [-53.734442, -26.717017], [-53.754115, -26.709745], [-53.757954, -26.720287], [-53.742722, -26.731084], [-53.750099, -26.740707], [-53.731758, -26.730894], [-53.714102, -26.750691], [-53.743541, -26.763854], [-53.698788, -26.769517], [-53.717043, -26.782109], [-53.700187, -26.797899], [-53.698122, -26.814318], [-53.7108, -26.81131], [-53.688414, -26.83777], [-53.69481, -26.846983], [-53.679562, -26.842338], [-53.660174, -26.858395], [-53.696751, -26.859822], [-53.679971, -26.869731], [-53.695609, -26.886891], [-53.670891, -26.894667], [-53.672671, -26.912951], [-53.696856, -26.929217], [-53.671196, -26.942485], [-53.709039, -26.93409], [-53.703176, -26.960179], [-53.726076, -26.958818], [-53.736955, -26.974303], [-53.717852, -26.990188], [-53.743569, -26.991968], [-53.731739, -27.004389], [-53.740333, -27.014602], [-53.763524, -27.008394], [-53.747248, -27.032176], [-53.764, -27.045359], [-53.785569, -27.026347], [-53.760062, -27.065096], [-53.802178, -27.040088], [-53.797015, -27.060064], [-53.771697, -27.073627], [-53.797395, -27.066084], [-53.805402, -27.078612], [-53.776349, -27.103429], [-53.818644, -27.102156], [-53.804438, -27.122667], [-53.824692, -27.132359], [-53.798266, -27.145836], [-53.827933, -27.145746], [-53.836696, -27.169661], [-53.873405, -27.125934], [-53.910634, -27.176807], [-53.951805, -27.152829], [-53.960752, -27.196128], [-54.008431, -27.199287], [-54.020053, -27.246755], [-54.055134, -27.263661], [-54.08446, -27.299776], [-54.15412, -27.29688], [-54.157252, -27.261842], [-54.173278, -27.254989], [-54.189732, -27.27569], [-54.214752, -27.383516], [-54.262793, -27.398756], [-54.26779, -27.437054], [-54.281567, -27.44719], [-54.338281, -27.403039], [-54.34517, -27.425596], [-54.334421, -27.453133], [-54.353337, -27.466267], [-54.413157, -27.406519], [-54.469871, -27.42772], [-54.442246, -27.461877], [-54.44635, -27.474155], [-54.504278, -27.481829], [-54.530191, -27.506564], [-54.581656, -27.453407], [-54.622276, -27.542041], [-54.64221, -27.54446], [-54.658682, -27.510918], [-54.677908, -27.510252], [-54.67134, -27.561029], [-54.683666, -27.574973], [-54.742962, -27.562267], [-54.774466, -27.58511], [-54.794025, -27.537473], [-54.812871, -27.532619], [-54.846802, -27.620278], [-54.905051, -27.638647], [-54.902814, -27.721024], [-54.936241, -27.771868], [-54.985489, -27.771525], [-55.008277, -27.797622], [-55.055931, -27.770049], [-55.083936, -27.785968], [-55.025023, -27.832573], [-55.029164, -27.853941], [-55.108495, -27.847421], [-55.125202, -27.8588], [-55.134383, -27.896153], [-55.171451, -27.861077], [-55.197201, -27.85713], [-55.264207, -27.928894], [-55.322599, -27.924421], [-55.339017, -27.966299], [-55.384417, -27.982955], [-55.368652, -28.019076], [-55.376044, -28.034973], [-55.422781, -28.060974], [-55.447751, -28.099058], [-55.498155, -28.080989], [-55.513099, -28.113873], [-55.541326, -28.124555], [-55.554787, -28.162795], [-55.587766, -28.146139], [-55.58848, -28.11849], [-55.607563, -28.1163], [-55.630211, -28.138839], [-55.712579, -28.088184], [-55.716718, -28.061268], [-55.758025, -28.06081], [-55.749427, -28.0271], [-55.824392, -27.948447], [-55.81359, -27.921022], [-55.831762, -27.904133], [-55.827123, -27.854597], [-55.846303, -27.832436], [-55.825537, -27.791656], [-55.9789, -27.5402], [-56.026635, -27.507277], [-56.054612, -27.450946], [-56.055421, -27.420687], [-56.036937, -27.415187], [-56.043216, -27.404992], [-56.028888, -27.382855], [-56.039906, -27.349216], [-56.024622, -27.338424], [-56.030459, -27.330259], [-55.975489, -27.355629], [-55.896346, -27.342718], [-55.838908, -27.407476], [-55.787112, -27.44021], [-55.732774, -27.443108], [-55.683316, -27.378756], [-55.589636, -27.331687], [-55.602394, -27.284618], [-55.574386, -27.270945], [-55.574081, -27.250257], [-55.611594, -27.212998], [-55.618761, -27.187177], [-55.607731, -27.161304], [-55.56112, -27.160948], [-55.561323, -27.101324], [-55.455545, -27.106255], [-55.44574, -27.025378], [-55.375741, -26.966166], [-55.306663, -26.963879], [-55.257052, -26.937701], [-55.203121, -26.969877], [-55.141464, -26.953458], [-55.130078, -26.938667], [-55.15029, -26.895954], [-55.141566, -26.866335], [-55.057543, -26.798985], [-54.95626, -26.781356], [-54.942158, -26.68238], [-54.880297, -26.655999], [-54.831507, -26.674098], [-54.812421, -26.667842], [-54.784778, -26.623262], [-54.807719, -26.560957], [-54.800544, -26.536115], [-54.706353, -26.449134], [-54.696645, -26.377714], [-54.647569, -26.31147], [-54.678585, -26.262537], [-54.618016, -26.214246], [-54.672518, -26.156282], [-54.643107, -26.093607], [-54.679692, -26.003556], [-54.667414, -25.981499], [-54.610735, -25.977787], [-54.622013, -25.912399], [-54.586719, -25.83062], [-54.631169, -25.765533], [-54.655242, -25.67038], [-54.637793, -25.659829], [-54.583674, -25.666263], [-54.594925, -25.597435], [-54.555332, -25.587703], [-54.534945, -25.598625], [-54.527678, -25.62832], [-54.500018, -25.614584], [-54.468829, -25.637186], [-54.428923, -25.694065], [-54.42476, -25.657993], [-54.394838, -25.637553], [-54.382441, -25.596332], [-54.347344, -25.606058], [-54.328387, -25.571481], [-54.288417, -25.55573], [-54.250519, -25.597716], [-54.235172, -25.59304], [-54.230556, -25.563012], [-54.178779, -25.584886], [-54.174673, -25.57278], [-54.205005, -25.557774], [-54.205346, -25.540092], [-54.164055, -25.542255], [-54.102558, -25.494315], [-54.092637, -25.509034], [-54.109057, -25.539824]]], [[[-55.927544, -27.333644], [-55.928887, -27.338344], [-55.960362, -27.34187], [-55.9471, -27.335239], [-55.927544, -27.333644]]], [[[-56.003001, -27.337085], [-55.997125, -27.338596], [-55.996202, -27.340275], [-56.005099, -27.337421], [-56.003001, -27.337085]]]]}}
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
    "RESISTENCIA AERO": "Chaco", "PRESIDENCIA ROQUE SAENZ PE": "Chaco",
    "FORMOSA AERO": "Formosa", "LAS LOMITAS": "Formosa",
    "IGUAZU AERO": "Misiones", "POSADAS AERO": "Misiones",
    "BERNARDO DE IRIGOYEN AERO": "Misiones", "OBERA": "Misiones",
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


def limites_litoral():
    puntos=[p for g in GEOMETRIAS_LITORAL.values() for rings in _anillos_provincia(g) for ring in rings for p in ring]
    return min(p[0] for p in puntos),min(p[1] for p in puntos),max(p[0] for p in puntos),max(p[1] for p in puntos)


def provincia_de_coordenadas(lat,lon):
    try:
        lat,lon=float(lat),float(lon)
    except (ValueError,TypeError):
        return None
    if not np.isfinite([lat,lon]).all():
        return None
    return next((nombre for nombre,g in GEOMETRIAS_LITORAL.items()
                 if any(_punto_en_poligono((lon,lat),rings) for rings in _anillos_provincia(g))),None)


def firma_cobertura():
    return hashlib.sha256(json.dumps({'version':VERSION,'provincias':PROVINCIAS_LITORAL,'nodos':NODOS},
                       sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def cobertura_observaciones_smn(df,referencia=None):
    referencia=referencia or ahora()
    filas=[]
    for provincia in PROVINCIAS_LITORAL:
        registros=df[df['Provincia'].eq(provincia)] if not df.empty and {'Provincia','fecha'}.issubset(df.columns) else pd.DataFrame()
        ultima=registros['fecha'].max() if not registros.empty else None
        filas.append({'Provincia':provincia,'registros':len(registros),'fecha_ultimo':ultima,
                      'vigente':bool(ultima is not None and pd.notna(ultima) and dato_vigente(ultima,24,referencia))})
    return pd.DataFrame(filas)


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
        "Nodos dentro del área": ", ".join(f["nodos"]) or f"Ninguno de los {len(NODOS)} nodos",
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
        fig.update_layout(map={"style": "open-street-map", "center": {"lat": MAP_CENTER_LAT, "lon": MAP_CENTER_LON}, "zoom": MAP_ZOOM},
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
    st.caption(' · '.join(PROVINCIAS_LITORAL)+'. Alertas, avisos y observaciones oficiales, independientes del índice experimental.')
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
        mostrar_trimestral_integrado()
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
FECHA_REVISION_EVIDENCIA = '2026-10-10'
INA_BASE = 'https://alerta.ina.gob.ar/pub/datos/'
INTA_BASE = 'https://sepa.inta.gob.ar'
RONI_URL = 'https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso/roni/'
VIALIDAD_URL = 'https://www.argentina.gob.ar/transporte/vialidad-nacional/estado-de-rutas'
SALUD_SF_URL = 'https://www.santafe.gob.ar/maparecursos/index.php?action=consultar'
IGN_SALUD_URL = 'https://wms.ign.gob.ar/geoserver/ows'
CASO_VERA_URL = 'https://www.santafe.gob.ar/noticias/noticia/283139/'
CASO_2016_URL = 'https://repositorio.smn.gob.ar/bitstream/handle/20.500.12160/429/0054AM2016.pdf?isAllowed=y&sequence=1'


# Controles negativos relevados en partes oficiales. La etiqueta 0 se usa sólo
# cuando el organismo declara explícitamente ausencia de anegamiento/inundación
# en una localidad o zona y conserva la fuente y la fecha del parte.
CONTROLES_NEGATIVOS_DOCUMENTADOS = [
    {
        'evento_id': 'control_negativo_01', 'tipo_registro': 'control_negativo',
        'fecha_evento': '2026-10-08', 'localidad': 'Corrientes capital',
        'provincia': 'Corrientes', 'anegamiento': 0,
        'fuente': 'https://ciudaddecorrientes.gov.ar/content/lluvias-en-la-ciudad-sin-anegamientos-y-con-operativo-preventivo-en-distintos-sectores',
        'observacion_etiqueta': 'Parte municipal: 17 mm hasta las 09:00; no se reportaron barrios ni calles inundadas; transporte normal.',
        'fecha_evidencia': '2026-10-08T11:00:00-03:00',
        'autoridad': 'Municipalidad de Corrientes · Gestión Integral de Riesgos',
        'alcance_espacial': 'Barrios y calles de Corrientes capital informados en el parte',
        'alcance_temporal': 'Mañana del 08/10/2026; lluvia desde 05:45 y balance hasta 09:00. No cubre el resto del día.',
        'estado_validacion': 'ausencia_documentada_sin_indice_historico',
        'nota_calibracion': 'Control negativo válido como etiqueta observada. Falta vincular una corrida histórica y su emisión para usarlo en métricas.',
    },
    {
        'evento_id': 'control_negativo_02', 'tipo_registro': 'control_negativo',
        'fecha_evento': '2018-03-24', 'localidad': 'Corrientes capital',
        'provincia': 'Corrientes', 'anegamiento': 0,
        'fuente': 'https://ciudaddecorrientes.gov.ar/content/pesar-de-la-lluvia-corrientes-no-se-inund',
        'observacion_etiqueta': 'Parte municipal: tras lluvia intensa, los desagües respondieron; no se recibieron denuncias y el agua circuló con normalidad en los puntos recorridos.',
        'fecha_evidencia': '2018-03-24T20:58:00-03:00',
        'autoridad': 'Municipalidad de Corrientes · COE municipal',
        'alcance_espacial': 'Puntos recorridos por la autoridad municipal; no representa todos los lotes rurales',
        'alcance_temporal': 'Temporal de la tarde del 24/03/2018 y situación informada a las 20:58',
        'estado_validacion': 'ausencia_documentada_sin_indice_historico',
        'nota_calibracion': 'La fuente también informa caída de un árbol; el control es sólo para anegamiento/inundación, no para ausencia de otros daños.',
    },
    {
        'evento_id': 'control_negativo_03', 'tipo_registro': 'control_negativo',
        'fecha_evento': '2018-05-05', 'localidad': 'Corrientes · Chacabuco/Ferré · España/3 de Abril · Alfonsín/Laprida',
        'provincia': 'Corrientes', 'anegamiento': 0,
        'fuente': 'https://ciudaddecorrientes.gov.ar/content/el-plan-h-drico-con-buenos-resultados-en-diversos-puntos-de-la-ciudad',
        'observacion_etiqueta': 'Parte municipal: 47,5 mm hasta medianoche y 11,7 mm hasta las 08:30; los cruces y barrios intervenidos no se inundaron ni tuvieron inconvenientes de drenaje.',
        'fecha_evidencia': '2018-05-05T15:34:00-03:00',
        'autoridad': 'Municipalidad de Corrientes · Gestión Integral de Riesgo y Catástrofes',
        'alcance_espacial': 'Sólo Chacabuco/Ferré, España/3 de Abril y Alfonsín/Laprida; otros sectores sí fueron afectados',
        'alcance_temporal': 'Lluvia de la tarde-noche del 04/05 y madrugada del 05/05; balance publicado el 05/05 a las 15:34',
        'estado_validacion': 'ausencia_documentada_sin_indice_historico',
        'nota_calibracion': 'Control espacial de zonas declaradas sin inundación; no generalizar a los sectores del mismo parte donde sí hubo acumulación de agua.',
    },
    {
        'evento_id': 'control_negativo_04', 'tipo_registro': 'control_negativo',
        'fecha_evento': '2021-03-25', 'localidad': 'Santa Fe capital',
        'provincia': 'Santa Fe', 'anegamiento': 0,
        'fuente': 'https://www.santafe.gob.ar/noticias/noticia/270427/',
        'observacion_etiqueta': 'Parte provincial: para Santa Fe (departamento La Capital) no se reportó afectación y se indicó “sin afectación por lluvias”.',
        'fecha_evidencia': '2021-03-25',
        'autoridad': 'Gobierno de Santa Fe · Secretaría de Protección Civil',
        'alcance_espacial': 'Santa Fe capital (departamento La Capital); no las otras localidades del parte',
        'alcance_temporal': 'Situación relevada en el parte del 25/03/2021; hora exacta no publicada',
        'estado_validacion': 'ausencia_documentada_sin_indice_historico',
        'nota_calibracion': 'Control municipal dentro de un parte provincial con otras localidades afectadas; no se extrapola la ausencia fuera de Santa Fe capital.',
    },
]

# Catálogo de episodios reales documentados por fuentes oficiales.
# Son antecedentes observados, no entradas de calibración: se mantienen sin
# inventar índices de emisión de los episodios que no tienen corrida archivada.
EPISODIOS_REALES_DOCUMENTADOS = [
    {
        'evento_id': '2016-04-concordia', 'inicio': '2016-04', 'fin': '2016-04',
        'localidad': 'Concordia', 'provincia': 'Entre Ríos',
        'evidencia_observada': '605,5 mm mensuales y 21 días con lluvia; serie INA del río Uruguay; imagen INTA del episodio',
        'impacto_documentado': 'Crecida regional del río Uruguay y anegamientos asociados',
        'fuente_principal': CASO_2016_URL,
        'fuentes_contraste': 'INA estación 79 + INTA SEPA Terra/MODIS',
        'estado_calibracion': 'Multifuente documentado; falta fecha puntual e índice de emisión',
    },
    {
        'evento_id': '2023-09-01-ituzaingo', 'inicio': '2023-09-01', 'fin': '2023-09-01',
        'localidad': 'Ituzaingó', 'provincia': 'Corrientes',
        'evidencia_observada': '267 mm de precipitación diaria reportados por SMN',
        'impacto_documentado': 'Precipitación extrema; impacto local a verificar con municipio/Defensa Civil',
        'fuente_principal': 'https://repositorio.smn.gob.ar/bitstream/handle/20.500.12160/2740/Reporte%20final%20clima%20arg%202023.pdf?isAllowed=y&sequence=6',
        'fuentes_contraste': 'SMN Estado del clima 2023',
        'estado_calibracion': 'Episodio documentado; falta índice de emisión e impacto local confirmado',
    },
    {
        'evento_id': '2023-11-litoral-norte', 'inicio': '2023-11', 'fin': '2023-11',
        'localidad': 'Norte del Litoral', 'provincia': 'Santa Fe · Entre Ríos · Corrientes',
        'evidencia_observada': 'Informe SMN registra lluvias intensas y episodios de inundación/evacuación regional',
        'impacto_documentado': 'Impacto regional; ventana y localidad puntual requieren validación local',
        'fuente_principal': 'https://repositorio.smn.gob.ar/bitstream/handle/20.500.12160/2740/Reporte%20final%20clima%20arg%202023.pdf?isAllowed=y&sequence=6',
        'fuentes_contraste': 'SMN Estado del clima 2023',
        'estado_calibracion': 'Antecedente regional; falta desagregación espacial e índice de emisión',
    },
    {
        'evento_id': '2024-01-11-vera-reconquista', 'inicio': '2024-01-11', 'fin': '2024-01-12',
        'localidad': 'Vera · Reconquista · Malabrigo', 'provincia': 'Santa Fe',
        'evidencia_observada': '>400 mm en 24–48 h; aproximadamente 700 evacuados; dos bases operativas; relevamiento de caminos rurales y limpieza de canales/alcantarillas',
        'impacto_documentado': 'Evacuación y afectación de accesos, drenajes y zonas rurales',
        'fuente_principal': 'https://www.santafe.gob.ar/noticias/noticia/279557/',
        'fuentes_contraste': 'Gobierno de Santa Fe · emergencia hídrica norte',
        'estado_calibracion': 'Impacto real documentado; falta índice de emisión',
    },
    {
        'evento_id': '2024-03-03-corrientes', 'inicio': '2024-03-03', 'fin': '2024-03-03',
        'localidad': 'Corrientes capital', 'provincia': 'Corrientes',
        'evidencia_observada': '>200 mm en 4 h; 996 personas evacuadas; declaración de emergencia municipal',
        'impacto_documentado': 'Inundación urbana y evacuación masiva',
        'fuente_principal': 'https://repositorio.smn.gob.ar/bitstream/handle/20.500.12160/2989/InformeFinal_EstadoDelClimaEnArgentina_2024.pdf?isAllowed=y&sequence=4',
        'fuentes_contraste': 'SMN Estado del clima 2024',
        'estado_calibracion': 'Impacto real documentado; falta índice de emisión',
    },
    {
        'evento_id': '2024-03-20-gualeguaychu-santafe', 'inicio': '2024-03-20', 'fin': '2024-03-21',
        'localidad': 'Santa Fe · Gualeguaychú', 'provincia': 'Santa Fe · Entre Ríos',
        'evidencia_observada': 'Tormentas intensas, desborde del Gualeguaychú, drenajes colapsados, calles anegadas y cortes de energía',
        'impacto_documentado': 'Inundación urbana y fallas de drenaje',
        'fuente_principal': 'https://repositorio.smn.gob.ar/bitstream/handle/20.500.12160/2989/InformeFinal_EstadoDelClimaEnArgentina_2024.pdf?isAllowed=y&sequence=4',
        'fuentes_contraste': 'SMN Estado del clima 2024',
        'estado_calibracion': 'Impacto real documentado; falta índice de emisión y desagregación por localidad',
    },
    {
        'evento_id': '2025-05-27-vera', 'inicio': '2025-05-27', 'fin': '2025-05-27',
        'localidad': 'Vera', 'provincia': 'Santa Fe',
        'evidencia_observada': '>400 mm en horas y 157 mm/h; cuatro centros de evacuación; 117 personas evacuadas; Ruta 11 casi intransitable',
        'impacto_documentado': 'Anegamiento urbano, evacuación y afectación de Ruta 11',
        'fuente_principal': CASO_VERA_URL,
        'fuentes_contraste': 'Gobierno de Santa Fe · emergencia Vera',
        'estado_calibracion': 'Tres corridas reconstruidas del mismo episodio; prueba independiente pendiente',
    },
    {
        'evento_id': '2020-11-norte-santa-fe', 'inicio': '2020-11', 'fin': '2020-11',
        'localidad': 'Norte de Santa Fe', 'provincia': 'Santa Fe',
        'evidencia_observada': 'Tormenta severa con dos centros de evacuación y nueve familias asistidas',
        'impacto_documentado': 'Evacuación local documentada por la provincia',
        'fuente_principal': 'https://www.santafe.gob.ar/noticias/noticia/269259/',
        'fuentes_contraste': 'Gobierno de Santa Fe',
        'estado_calibracion': 'Antecedente local; falta serie de precipitación e índice de emisión',
    },
]

# Inventario trazable de fuentes para caminos, refugios y recursos sanitarios.
# “Fuente oficial conectable” no significa que la vigencia operativa esté confirmada.
REGISTROS_LOCALES_FUENTES = [
    {'registro': 'Estado de rutas', 'jurisdiccion': 'Nacional', 'tipo': 'caminos/cortes', 'autoridad': 'Vialidad Nacional', 'url': VIALIDAD_URL, 'estado': 'Fuente oficial conectable; vigencia se valida por fecha del reporte', 'uso': 'Contrastar rutas nacionales y derivar cortes publicados'},
    {'registro': 'Vialidad provincial Santa Fe', 'jurisdiccion': 'Santa Fe', 'tipo': 'caminos/cortes', 'autoridad': 'Dirección Provincial de Vialidad / APSV', 'url': 'https://www.santafe.gov.ar/index.php/web/content/view/full/239909', 'estado': 'Portal oficial; no publica un feed uniforme de cortes en esta versión', 'uso': 'Reconfirmar rutas provinciales y caminos rurales con la autoridad antes de circular'},
    {'registro': 'Vialidad provincial Corrientes', 'jurisdiccion': 'Corrientes', 'tipo': 'caminos/cortes', 'autoridad': 'Dirección Provincial de Vialidad', 'url': 'https://vialidad.corrientes.gob.ar/', 'estado': 'Portal oficial; estado operativo no descargable automáticamente', 'uso': 'Reconfirmar transitabilidad y cortes con Vialidad/Defensa Civil'},
    {'registro': 'Vialidad provincial Entre Ríos', 'jurisdiccion': 'Entre Ríos', 'tipo': 'caminos/cortes', 'autoridad': 'Dirección Provincial de Vialidad', 'url': 'https://www.dpver.gov.ar/', 'estado': 'Portal oficial actualizado; no confirma el estado actual de cada tramo', 'uso': 'Reconfirmar transitabilidad y cortes con Vialidad/Defensa Civil'},
    {'registro': 'Mapa de Recursos', 'jurisdiccion': 'Santa Fe', 'tipo': 'salud/refugios', 'autoridad': 'Gobierno de Santa Fe', 'url': 'https://www.santafe.gob.ar/maparecursos/', 'estado': 'Catálogo geográfico oficial; capacidad/guardia actual no publicada en el mapa', 'uso': 'Ubicar efectores y recursos públicos georreferenciados'},
    {'registro': 'Catálogo sanitario IGN', 'jurisdiccion': 'Litoral · seis provincias', 'tipo': 'salud', 'autoridad': 'IGN · procedencia original por registro', 'url': IGN_SALUD_URL, 'estado': '2.077 puntos conservados el 10/10/2026; atención, guardia, capacidad y stock sin confirmar', 'uso': 'Ubicaciones de catálogo; no acredita establecimientos únicos ni exhaustividad de la red sanitaria'},
    {'registro': 'Mapas hidrológicos IDESF', 'jurisdiccion': 'Santa Fe', 'tipo': 'drenaje/riesgo', 'autoridad': 'IDESF · Gobierno de Santa Fe', 'url': 'https://www.santafe.gov.ar/idesf/', 'estado': 'Fuente oficial conectable; no es estado vial en tiempo real', 'uso': 'Capas de cursos, canales, riesgo y apoyo a caminos rurales'},
    {'registro': 'Centros de salud Entre Ríos', 'jurisdiccion': 'Entre Ríos', 'tipo': 'salud', 'autoridad': 'Ministerio de Salud de Entre Ríos', 'url': 'https://datos.entrerios.gov.ar/dataset/centros-de-salud', 'estado': 'Descarga conservada del 10/10/2026: 212 registros legibles y una fila inconsistente excluida; atención y horario sin confirmar', 'uso': 'Nombres, teléfonos y domicilios para georreferenciación local'},
    {'registro': 'Hospitales Entre Ríos', 'jurisdiccion': 'Entre Ríos', 'tipo': 'salud', 'autoridad': 'Ministerio de Salud de Entre Ríos', 'url': 'https://datos.entrerios.gov.ar/dataset/listado-de-hospitales-de-entre-rios', 'estado': 'Descarga conservada del 10/10/2026: 65 registros; capacidad y guardia sin confirmar', 'uso': 'Red hospitalaria y contactos para derivación'},
    {'registro': 'Centros de evacuación Vera 2025', 'jurisdiccion': 'Vera, Santa Fe', 'tipo': 'refugio', 'autoridad': 'Municipio/Provincia de Santa Fe', 'url': CASO_VERA_URL, 'estado': 'Antecedente histórico; habilitación, capacidad y suero actuales sin confirmar', 'uso': 'Cuatro centros usados el 27/05/2025: Hospital Regional, CAPS San Martín de Porres, Club Huracán y Gimnasia Fútbol Club'},
    {'registro': 'Operativo norte Santa Fe 2024', 'jurisdiccion': 'Vera · Reconquista · Malabrigo', 'tipo': 'caminos/drenaje/refugios', 'autoridad': 'Gobierno de Santa Fe', 'url': 'https://www.santafe.gob.ar/noticias/noticia/279557/', 'estado': 'Antecedente operativo; caminos, refugios y capacidades actuales sin confirmar', 'uso': 'Documenta bases, caminos rurales, canales y alcantarillas relevados durante la emergencia'},
    {'registro':'SIGVIAL Chaco','jurisdiccion':'Chaco','tipo':'caminos/cartografía','autoridad':'Dirección de Vialidad Provincial','url':'https://vialidadchaco.com.ar/sigvial','estado':'Portal oficial revisado 10/10/2026; inventario geográfico, transitabilidad sin confirmar','uso':'Mapas de caminos, puentes y zonas; KML/KMZ/GeoPDF'},
    {'registro':'Defensa Civil Chaco','jurisdiccion':'Chaco','tipo':'refugios/coordinación','autoridad':'Dirección de Defensa Civil','url':'https://mapadelestado.chaco.gob.ar/dependencia/ver/279','estado':'Funciones y contacto publicados; refugios abiertos y capacidades sin confirmar','uso':'Coordinación local y consulta a responsables de evacuación'},
    {'registro':'Salud Chaco','jurisdiccion':'Chaco','tipo':'salud','autoridad':'Ministerio de Salud','url':'https://chaco.gob.ar/ministerio/ministerio-de-salud','estado':'Portal institucional revisado; atención y capacidad por efector sin confirmar','uso':'Red de Servicios de Salud y contactos institucionales'},
    {'registro':'Vialidad Formosa','jurisdiccion':'Formosa','tipo':'caminos','autoridad':'Dirección Provincial de Vialidad','url':'https://www.formosa.gob.ar/vialidad','estado':'Fuente oficial identificada; apertura no completada en la revisión, transitabilidad sin confirmar','uso':'Consulta institucional de rutas y caminos provinciales'},
    {'registro':'IDEF Formosa','jurisdiccion':'Formosa','tipo':'caminos/salud/cartografía','autoridad':'Infraestructura de Datos Espaciales de Formosa','url':'https://idef.formosa.gob.ar/geoportal/servicios','estado':'Servicios WMS identificados; inventario geográfico, operación sin confirmar','uso':'Capas de Vialidad y Desarrollo Humano para referencia territorial'},
    {'registro':'Defensa Civil Formosa','jurisdiccion':'Formosa','tipo':'refugios/coordinación','autoridad':'Dirección de Defensa Civil','url':'https://m.formosa.gob.ar/defensacivil/historia','estado':'Fuente institucional identificada; refugios, capacidades y contactos actuales pendientes','uso':'Organización provincial de respuesta y articulación local'},
    {'registro':'Hospitales Formosa','jurisdiccion':'Formosa','tipo':'salud','autoridad':'Ministerio de Desarrollo Humano','url':'https://m.formosa.gob.ar/salud/mapashospitales','estado':'Directorio oficial revisado; cupos, guardia y acceso actuales sin confirmar','uso':'Mapa y búsqueda de hospitales por localidad'},
    {'registro':'Vialidad Misiones','jurisdiccion':'Misiones','tipo':'caminos','autoridad':'Dirección Provincial de Vialidad','url':'https://www.dpv.misiones.gov.ar/contacto.php','estado':'Fuente oficial identificada; apertura no completada en la revisión, transitabilidad sin confirmar','uso':'Información institucional y contactos viales'},
    {'registro':'Protección Civil Misiones','jurisdiccion':'Misiones','tipo':'refugios/coordinación','autoridad':'Ministerio de Gobierno','url':'https://gobierno.misiones.gob.ar/guia-de-autoridades/','estado':'Guía institucional revisada; contactos publicados, refugios y capacidades sin confirmar','uso':'Coordinación provincial y Defensa Civil'},
    {'registro':'Hospitales Misiones','jurisdiccion':'Misiones','tipo':'salud','autoridad':'Ministerio de Salud Pública','url':'https://salud.misiones.gob.ar/hospitales/','estado':'Directorio institucional revisado; guardia, capacidad y stock sin confirmar','uso':'Hospitales por nivel, domicilios y teléfonos'},
    {'registro':'Mapa sanitario IPEC Misiones','jurisdiccion':'Misiones','tipo':'salud/cartografía','autoridad':'IPEC · Salud Pública de Misiones','url':'https://www.ipec.misiones.gov.ar/el-ipec/servicios-herramientas/mapas-ipec-misiones/mapa-hospitales-misiones-zonas-sanitarias/','estado':'Inventario fechado; ubicación no acredita funcionamiento actual','uso':'Mapa de hospitales y zonas sanitarias'},
]
ESTACIONES_INA = {'Corrientes': (19, 868), 'Goya': (23, None),
                  'Reconquista': (24, None), 'Paraná': (29, 878),
                  'Santa Fe': (30, 879), 'Concordia': (79, 928),
                  'Barranqueras · Chaco': (20, None), 'Isla del Cerrito · Chaco': (59, None),
                  'Puerto Formosa · Formosa': (57, None), 'Puerto Pilcomayo · Formosa': (55, None),
                  'Posadas · Misiones': (14, None), 'Puerto Iguazú · Misiones': (9, None),
                  'Andresito · Misiones': (8, None)}
PRODUCTOS_INTA = {
    'Agua en suelo / recarga del perfil · PJ': ('agua_en_suelo', 'pj', 'decada', 'pj'),
    'Excedentes hídricos': ('agua_en_suelo', 'dr', 'decada', 'dr'),
    'Vegetación NDVI · MODIS 250 m': ('indices_de_vegetacion', 'compuesto_16d_ndvi', 'juliano', 'iv'),
}
# Copias reproducibles de fuentes públicas, conservadas para el caso histórico.
# Se insertan los datos verificados al construir esta versión; nunca simulan datos en vivo.
FICHA_COPIAS = {'ina_estaciones': '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', 'roni': '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', 'salud_ign': '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', 'refugios_vera': 'eNrlkM1Kw0AUhV9lmHXNpJOmSbqTgnYjFAtuRMpNOmkvTGfK/FBQfBgfwJULwaV5MW+whbrQB6izOvdvzuG7f+LGbmun+ITPrN9hAM1u1RqtIbFS7E454AMecGdpxak20owaGgKfXMgqGeWjUUG1NVSP00TKtKgGXPkAq/5kqkxwlsWAGh+pxZRmshBpLmQqc/b5zjZQ0yxAg92bYdCESNYeDWusadFtwZFfG+mfPuUmhJ2fCLHf7xMPJkCrkrWtE3DC2IANgj8KIctsmFWCzn1Uzi5pHW2LK2z6ZGSxPLU4RKw12bSgvXoenNCZXs4XbAGG3YAL3avp4cytc8r/jadIi0ymZZkX5Q9K9DJZjfNhdo6wdKzZLDpouhfzKx8TtT4w+ZZnx+EatwY8ArvqPkJtNevB/DMeD18Qx4Gx', 'inta_pj': 'eNrVz7kRgEAMA8BeFDuQfQ9wrTBUcuPeeULUAARKdhRIE8GoGBNsGDschrhScBjYlRalVWkTciq5Urwo7R7YnoFaZygVpar04bP9V2czT4vpbR8=', 'inta_dr': 'eNrVz7kRgEAMA8BeFDuQfQ9wrTBUcuPeeULUAARKdhRIE8GoGBNsGDschrhScBjYlRalVWkTciq5Urwo7R7YnoFaZygVpar04bP9V2czT4vpbR8=', 'inta_compuesto_16d_ndvi': 'eNrN1D0KgkEQg+G7TG0xyeyvV5HvJOLdJdvaWIikSfOw8LLFPIOJFvdHZCJukZjaKm3b2tG16+iWAlJQiibFkGIe3VKmlJSySdmlnEeXtFJalFZJq1dceoz+RdJnzM8zht/PTL+k5Ze07ZKYfknwS6JfUvkl+V1sdr+kv13v6/UG2/FJdQ==', 'concordia_2016': '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', 'ina_2016': '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', 'vera_hist': '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', 'vera_2025-05-24': 'eNrtmcluGzkQQP9FZy9NNoss+ua5zGluvhmC4DidpAEtgZYAM4G/av5gvmzYbMnN6Eka25kdOvDBLm5FPXbJkr+Opg/rdr1534xuLm28crGqa3cxmi7mH3dhX11ZZ4MPF6OPzbxZpgmL+bqdNZPZanQj/iq6OgRnbV2nUWnyZv04WXz4sGrWk1XzuJi/T8MuTaVVdTHq5v2ymKd1R7ezZtk+PlzfLtOy63b+cP3DppkvVpPbdtmsRsPYycO7d8vmS5s3ThN//Onusk79zbT5so2lHNPinxab5fTnyWbertOWX/MCaXy7WqivTJrxedk8tp/b9W6l2SwFV4t2Opkt2tV6s2wm1WS9mITHWdf726/XqWFI6IZYPTnGajfIVNXJUak/ryWyN2y5madXsM/waXew4Uj3I1tZuazk0tZ31txUVZpThixD9V7I3VUVQ4Yhy9CBtRxDwpBnKDCkDEWEDLM3zN4we8PsDbM3zN4we8PsDbM3zN4ye8vsLbOHR6FHoUehR6FHoUehR6FHoUehR6FHoUehR6FHoUehR6FHoUehR6FHoUehR6FHoUehR0+Pnh49PXp69PTo6dHTo6dHT4+eHj09enr09Ojp0dOjp0dPj54ePT16evT06OnR06Onx0CPgR4DPQZ6DPQY6DHQY6DHQI+BHgM9BnoM9BjoMdBjoMdAj4EeAz0Gegz0GOgx0GOgR6VHpUelR6VHpUelR6VHpUelR6VHpUelR6VHpUelR6VHpUelR6VHpUelR6VHpUelx0iPkR4jPUZ6jPQY6THSY6THSI+RHiM9RnqM9BjpMdJjpMdIj5EeIz1Geoz0GOkx0mOEx7qCxy5kGLIMHVjLMSQMeYYCQ8pQRMgwe8PsDbM3zN4we8PsDbM3zN4we8Pse49jfIS6n2+m04vu01ffYmp1amb7u2ybS80X8a7Z1MK2f9e36zfb/nL8W5vN++vzui+ZY4q87V6f28v1j9eVtIakeeb5XNWFzevbA+v/Wec+t9TGxz/g36erGrrL+g3VZkqmHxhNZndhXZWvlMnoaQt2yzib47ab6mpzknVBe4TH5h6AjQU1Mww/53lS5bvdHybmHTT35mM77SMdQs98jOAy8ybBFvF4eLgMndt4KJbp4HvmgM8HyjMlzxEtmOuEzannA/SncLunNOYZUQvmlWJeOtYF+7P1KeftNY9RU+QpxUHNkG2e5LXIPG/ipaAD6yO0w6m3B85rSp6VD+hcjve79y+Nk5P0YHgly11yJq6/gDmTOo+p+0udx9Su+Pk0/RGGk/TFLna44+W99gWl5PjEN3f3z0/qAdr/FOu30NbFq6ZDxaiL4lIWoLJIOVBOWtWC8XvZl93tk6fPD2cYapYOz/OLOFSviIpV1q1Q1DaPOidFbSsLZVkuS5q96lKWmLLQlOWmLDosPS8vQBY04H41PlyTv6U/STlCB9agBQ34LVwcn/xSPr/75/f6fynrM19J92LKK+lfwPAm6l/G+PdR/1GYkuPT/2O7H/6SP/PMM//nHA//S++/qjrjjDO+B+Onp98BTULxeA==', 'vera_2025-05-25': 'eNrtmctu4zYUht/FayfhISkeMrt001V32QWGkclopgJ8GfgyQDvIU/UN+mSVKDvm+Itct0EGReGFvghHvPziZ1Gw8200e9w0m+3HenR7ZdO1T8Y5Px7NlovP+3Iw19ZbDToefa4X9artsFxsmnk9na9Hty5eG2ucWpeMejVtq+3mabr89Gldb6br+mm5+Ng2uxITjRmPun6/LxftuKO7eb1qnh5v7lbtsJtm8Xjz07ZeLNfTu2ZVr0eHttPHDx9W9dcmT9x2/PmX+yvXXq9n9dddrc3YDv7rcrua/TbdLppNO+W3PEDbvlkvYzDS9viyqp+aL81mP9J83hbXy2Y2nS+b9Wa7qqdmullO9WneXf3zj5v2QBPtmth4so2NXSMx5mSr9noeq6qOmq22i3YF+4TP+xs73NLDyBpbXZnqyvp7K7fGtH3KkmXJHZWqe2NYEpYsS6+M5VmqWAosKUuRpYSSML0wvTC9ML0wvTC9ML0wvTC9ML1lesv0lunhMdBjoMdAj4EeAz0Gegz0GOgx0GOgx0CPgR4DPQZ6DPQY6DHQY6DHQI+BHgM9BnoM9BjoUelR6VHpUelR6VHpUelR6VHpUelR6VHpUelR6VHpUelR6VHpUelR6VHpUelR6VHpMdJjpMdIj5EeIz1Geoz0GOkx0mOkx0iPkR4jPUZ6jPQY6THSY6THSI+RHiM9RnqM9BjpMdFjosdEj4keEz0mekz0mOgx0WOix0SPiR4TPSZ6TPSY6DHRY6LHRI+JHhM9JnpM9Jjg0Rl47ErCkmXplbE8SxVLgSVlKbKUUBKmF6YXphemF6YXphemF6YXphemt0xvmd4yPTwKPQo9Cj0KPQo9Cj0KPQo9Cj0KPQo9Cj0KPQo9Cj0KPQo9Cj0KPQo9ys7jBF+hHhbb2WzcffviIe1hd3+P6+YNRzeme2UOLa7t56hOjhVyHz+217H9m8aS+1cDucv599e7PmY3d2rHcbtzczn+G8dk+Av+Q6s+he4DkFxmhxgzczlWmR7MzaPNFJx30DyY9uf5Q6Z5SM1Dah5GXUELSkFb9CpZHUbehfID8UNxbouE+mqqKj8HRg6n3Z1UUmX2lZQZMt2heV7JfN9Ji/P0cr7rkxtLT1fQF4Pn6Yw9GrZXlsOmfCupX/fSnw4sgzt4+m4B/GGxQ66HXA+5b/DvzJwq5HvJkat8E1WevMoXswWfF+3vaM+gO8lirv6h8HkxfM7jcxuf2/i+TXE6RFvQDdCD5VQdXE7jchqX07i+nhs6W1B6Tk78eNc9/1V6WfQgBS3ozuBb7L8H3/CZ7LeleHh0+odst1NWxYNIVgV9QTdA/9J+v5OUtEd7S8IOo5ib8xUbRTps9udsGdzyjzf7cscv9/2Id4DifVC+Fc55N5RvhRfstqyeEdQzGMBqYAN02LJKykm+gv4J/I4RVDCAFegnJ3+Wb59/1w3/Nsi70b4b3T+kB6sBBlAHGMF0YAEpaEE3QD/A6myGM6j/ivHdmH4Y/Y848qb/wsnp/549HH9YLrzwwv8tJ4f/kvc/Ql1wwQVvweT5+S+acuvV', 'vera_2025-05-26': 'eNrtmc1u20YUhd9Fa0meO3/3jnfupqvuvBMEwVGYlIAkBvoJ0AZ+qr5Bn6yjS9ma8Iis4hZBWsgAj+gzd2YO+Q0HEPVltHra1/vD+2p0P7Fp6pNxzo9Hq2bz8cWOZmq95cjj0cdqU21zh2azr9fVYr0b3bs4tTEFH/IfkWcfx6PDfrloPnzYVfvFrlo2m/e5bkJGjBmPjh1/bzZ54NHDutrWy6e7h20ed19vnu5+OlSbZrd4qLfVbnSuXTy9e7etPtc6c+748y+PE5fbq1X1+eTlkHnwX5vDdvXb4rCp93nKLzpArq93jURDucenbbWsP9X7l5HW62zumnq1WDf1bn/YVguz2DcLXq6PrX/+cZcPKOFjiZXBGivHIjJmsCq361ghdMq2h02+g23C55cLO1/SbGSNDRMTJjY8Wro3JvcpLYuW61jx0Ri0CC2L1oWxPFoBrYgWoyVoJbAI0xOmJ0xPmJ4wPWF6wvSE6QnTE6a3mN5ieovpgSMjR0aOjBwZOTJyZOTIyJGRIyNHRo6MHBk5MnJk5MjIkZEjI0dGjowcGTkycmTkyMiRkaMgR0GOghwFOQpyFOQoyFGQoyBHQY6CHAU5CnIU5CjIUZCjIEdBjoIcBTkKchTkKMhRkGNCjgk5JuSYkGNCjgk5JuSYkGNCjgk5JuSYkGNCjgk5JuSYkGNCjgk5JuSYkGNCjgk5JuSYgKMzwPFoEVoWrQtjebQCWhEtRkvQSmARpidMT5ieMD1hesL0hOkJ0xOmJ0xvMb3F9BbTA0dCjoQcCTkSciTkSMiRkCMhR0KOhBwJORJyJORIyJGQIyFHQo6EHAk5EnIk5EjIkZAjdTjGielybC1Cy6J1YSyPVkArosVoCVoJLML0hOkJ0xOmJ0xPmJ4wPWF6wvSE6VuOc/gKNdscVquxmXI+7Pj4LcxMXT7S6ZxOn93jWONPfezpnDr19vR/0E8L9W5gjmN7zH14TPp/yJ9OPdLx3ClzXzbb03Y7/mPHvP8L/iwvE9GFmOiskvT8uACCaX1WDaquWymiGou+xWipc/pVdQA91wTSpW+CqlP153MdsB1cx0q+SGdf8+pBrKq9qTi1RSOdHcPFZFRM1N4GKbSc2vbcGC4uT+OJ1rDWsLaytrKOwzoOE2ifxHSFSqFtL40edcqoU6oRtCRoSWidMKjxTco9s+j9UUbea5I2lddWr2G9G1R/hYZBLWfRG6OG0zhO4zgtdK3vivNrNIC2Y3KPljO2c+ntscrVairLhcZS5wOv7mZn/q2GQmOxUsq1U6yp3gV5Ye0OqwVtnwcNwXJ+WuQsdLVysQO5YiPj1yc7vD7pJy2e+VQ87Vw886HQcutx3b2g3PtwW+Ce3dAX20WrttDy8l7ltKGUKoVyobHQABuQK9R+w5b0N9uO9GxE5XYUYSGGcXeZtupALSiBDkpIhQrshtyzl/XtjP5qdT1qQQn0a/FpPvgiPj/zTtfQW9X9Y/Vv0nC1xkFlUCk0dbUrBGp71PWov1rD1Rq/UflfUvmOmn4o9d/zmA//aja7vDBvetOb/g91fv51/OXl0+1Fx+34MV8+PT//BZBBJq8='}


# Descargas oficiales de los catálogos sanitarios de Entre Ríos.
SALUD_ER_DESCARGAS = {
    'Centros de salud': 'https://datos.entrerios.gov.ar/dataset/597fb25c-839e-40c5-936f-046d650b700a/resource/4c20ffdc-88f4-467c-87e2-08f07192d03c/download/centros-de-salud-entre-rios.csv',
    'Hospitales': 'https://datos.entrerios.gov.ar/dataset/753a6290-196e-4e3c-b8bf-6bc89475e8a2/resource/8cf3e033-0240-49d2-929b-0f50ea1c94f1/download/listado-de-hospitales-de-er.csv',
}

# Plantilla de validación: deja separadas las etiquetas positivas y los controles.
# Las celdas vacías son intencionales: no se rellenan con ausencia inferida.
def episodios_documentados():
    """Suma antecedentes del norte sin convertirlos en pronósticos validados."""
    filas = list(EPISODIOS_REALES_DOCUMENTADOS)
    path = Path(__file__).resolve().parent/'assets'/'validacion_norte'/'episodios_documentados.json'
    if not path.exists():
        return filas
    nuevos = json.loads(path.read_text(encoding='utf-8'))
    ids = {f['evento_id'] for f in filas}
    if not isinstance(nuevos, list):
        raise ValueError('El catálogo regional debe ser una lista de episodios.')
    for fila in nuevos:
        if not isinstance(fila, dict) or not fila.get('evento_id') or fila['evento_id'] in ids:
            raise ValueError('Episodio regional vacío o repetido.')
        if fila.get('provincia') not in PROVINCIAS_LITORAL:
            raise ValueError('Provincia no reconocida en el catálogo regional.')
        for campo in ('inicio', 'fin'):
            valor = str(fila.get(campo, ''))
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', valor):
                raise ValueError('Los nuevos episodios necesitan fechas puntuales documentadas.')
            fecha_fuente(valor)
        if fecha_fuente(fila['fin']) < fecha_fuente(fila['inicio']):
            raise ValueError('Ventana de episodio invertida.')
        if fila.get('etiqueta_observada') != 1 or not fila.get('impacto_documentado'):
            raise ValueError('El impacto debe estar documentado para asignar anegamiento=1.')
        if not re.fullmatch(r'https?://\S+', str(fila.get('fuente_principal', ''))):
            raise ValueError('Falta la fuente del impacto regional.')
        ids.add(fila['evento_id'])
        filas.append(dict(fila))
    return filas


def controles_documentados():
    filas=list(CONTROLES_NEGATIVOS_DOCUMENTADOS)
    path=Path(__file__).resolve().parent/'assets'/'validacion_norte'/'controles_documentados.json'
    if path.exists():
        nuevos=json.loads(path.read_text(encoding='utf-8'))
        ids={c['evento_id'] for c in filas}
        for fila in nuevos:
            if fila.get('evento_id') in ids or fila.get('anegamiento')!=0 or not fila.get('observacion_etiqueta'):
                raise ValueError('Control regional sin ausencia documentada o ID repetido.')
            if fila.get('provincia') not in PROVINCIAS_LITORAL or not re.fullmatch(r'https?://\S+',str(fila.get('fuente',''))):
                raise ValueError('Control regional sin provincia o fuente reconocible.')
            ids.add(fila['evento_id']);filas.append(dict(fila))
    return filas


def plantilla_validacion_historica():
    filas=[]
    for c in episodios_documentados():
        filas.append({
            'evento_id': c['evento_id'], 'tipo_registro': 'episodio_real',
            'fecha_evento': c['inicio'], 'emision': '', 'indice': '', 'anegamiento': c.get('etiqueta_observada',''),
            'fuente': c['fuente_principal'], 'fuente_smn': c.get('fuente_smn',''), 'fuente_ina': c.get('fuente_ina',''), 'fuente_inta': c.get('fuente_inta',''),
            'fuente_pronostico': '', 'observacion_etiqueta': c['impacto_documentado'],
            'estado_validacion': c['estado_calibracion'], 'nota_calibracion': 'Completar sólo con una corrida histórica y una hora de emisión conservadas.',
            'localidad': c.get('localidad',''), 'provincia': c.get('provincia',''),
            'fecha_evidencia': c.get('fecha_evidencia',''), 'tipo_fecha': c.get('tipo_fecha','mes_documentado' if len(str(c.get('inicio',''))) == 7 else 'fecha_evento'),
            'autoridad': c.get('autoridad',''), 'alcance_espacial': c.get('alcance_espacial',''),
            'alcance_temporal': c.get('alcance_temporal',''),
            'grupo_evento': c.get('grupo_evento',c['evento_id']), 'inicializacion_utc': '', 'tipo_emision': '',
            'disponibilidad_confirmada': '',
        })
    for c in controles_documentados():
        filas.append({
            'evento_id': c['evento_id'], 'tipo_registro': 'control_negativo',
            'fecha_evento': c['fecha_evento'], 'emision': '', 'indice': '', 'anegamiento': c['anegamiento'],
            'fuente': c['fuente'], 'fuente_smn': '', 'fuente_ina': '', 'fuente_inta': '',
            'fuente_pronostico': '', 'observacion_etiqueta': c['observacion_etiqueta'],
            'estado_validacion': c['estado_validacion'], 'nota_calibracion': c['nota_calibracion'],
            'localidad': c.get('localidad',''), 'provincia': c.get('provincia',''),
            'fecha_evidencia': c.get('fecha_evidencia',''), 'tipo_fecha': 'parte_municipal_con_hora' if 'T' in str(c.get('fecha_evidencia','')) else 'fecha_parte_provincial',
            'autoridad': c.get('autoridad',''), 'alcance_espacial': c.get('alcance_espacial',''),
            'alcance_temporal': c.get('alcance_temporal',''),
            'grupo_evento': c.get('grupo_evento',c['evento_id']), 'inicializacion_utc': '', 'tipo_emision': '',
            'disponibilidad_confirmada': '',
        })
    return pd.DataFrame(filas)


def planilla_documental_actualizada():
    path = Path(__file__).resolve().parent/'assets'/'planilla_validacion_litoral_completada.csv'
    plantilla = plantilla_validacion_historica().fillna('')
    if not path.exists():
        return plantilla
    conservada = pd.read_csv(path, dtype=str).fillna('')
    nuevos = plantilla[~plantilla.evento_id.isin(conservada.evento_id)]
    return pd.concat([conservada, nuevos], ignore_index=True).fillna('')


def resumen_evidencia_documental(df):
    """Revisión separada de etiquetas observadas y de la cohorte calibrable."""
    if df is None or df.empty:
        return {'positivos': 0, 'controles': 0, 'controles_completos': 0, 'controles_faltantes': 4,
                'positivos_con_fuente': 0, 'cohorte_calibrable': False}
    d=df.copy().fillna('')
    if 'tipo_registro' not in d:
        return {'positivos': 0, 'controles': 0, 'controles_completos': 0,
                'controles_faltantes': 4, 'positivos_con_fuente': 0,
                'cohorte_calibrable': False}
    tipo=d['tipo_registro'].astype(str)
    positivos=d[tipo.eq('episodio_real')]
    controles=d[tipo.eq('control_negativo')]
    def lleno(fila, campo):
        return str(fila.get(campo,'')).strip() != ''
    completos=0
    for _,fila in controles.iterrows():
        if all(lleno(fila,c) for c in ['evento_id','fecha_evento','anegamiento','fuente']) and str(fila.get('anegamiento')) == '0':
            completos += 1
    return {
        'positivos': int(len(positivos)),
        'controles': int(len(controles)),
        'controles_completos': int(completos),
        'controles_faltantes': max(0,4-completos),
        'positivos_con_fuente': int(sum(lleno(f,'fuente') and str(f.get('anegamiento')) == '1' for _,f in positivos.iterrows())),
        'cohorte_calibrable': bool(len(d) >= 10 and completos >= 4 and len(positivos) >= 4 and all(lleno(f,c) for _,f in d.iterrows() for c in ['fecha_evento','emision','indice','anegamiento','fuente'])),
    }


def revisar_completitud_calibracion(df):
    requeridas=['evento_id','tipo_registro','fecha_evento','emision','indice','anegamiento','fuente']
    faltantes=[c for c in requeridas if c not in df.columns]
    if faltantes:
        return [f'Faltan columnas: {", ".join(faltantes)}']
    d=df.copy()
    issues=[]
    if d.evento_id.astype(str).str.strip().eq('').any() or d.evento_id.duplicated().any():
        issues.append('Hay IDs vacíos o repetidos.')
    if not d.tipo_registro.isin(['episodio_real','control_negativo']).all():
        issues.append('tipo_registro sólo admite episodio_real o control_negativo.')
    for c in ['fecha_evento','emision','indice','anegamiento','fuente']:
        if d[c].astype(str).str.strip().eq('').any():
            issues.append(f'Hay valores vacíos en {c}; no se infieren desde el silencio de una fuente.')
    for c in ['grupo_evento','tipo_emision','disponibilidad_confirmada','fuente_pronostico']:
        if c not in d or d[c].fillna('').astype(str).str.strip().eq('').any():
            issues.append(f'Falta trazabilidad en {c}.')
    if 'grupo_evento' in d and d.grupo_evento.duplicated().any():
        issues.append('Varias corridas o localidades del mismo episodio no son muestras independientes.')
    d['anegamiento_num']=pd.to_numeric(d.anegamiento,errors='coerce')
    if d.anegamiento_num.isna().any() or not d.anegamiento_num.isin([0,1]).all():
        issues.append('anegamiento debe ser 0 o 1 en todas las filas.')
    if (d.tipo_registro.eq('episodio_real') & d.anegamiento_num.ne(1)).any():
        issues.append('Cada episodio_real debe tener anegamiento=1 sólo cuando la fuente documente impacto.')
    if (d.tipo_registro.eq('control_negativo') & d.anegamiento_num.ne(0)).any():
        issues.append('Cada control_negativo debe tener anegamiento=0 y evidencia explícita de ausencia.')
    if (d.tipo_registro=='episodio_real').sum()<4:
        issues.append('Se requieren al menos 4 episodios reales etiquetados.')
    if (d.tipo_registro=='control_negativo').sum()<4:
        issues.append('Se requieren al menos 4 controles negativos con ausencia documentada.')
    return issues


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
    st.write('Altura de los ríos y caudal, con estaciones de referencia para el Paraná, Uruguay, Paraguay e Iguazú. El caudal sólo se consulta donde existe una serie configurada.')
    st.caption('La estación representa su escala hidrométrica; no certifica el nivel de todos los campos ni la vigencia operativa de un umbral. Las fechas INA sin offset se interpretan como ART.')
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
        st.rerun()
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
    if not all(provincia_de_coordenadas(lat,lon) for lat,lon in zip(d.lat,d.lon)):
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
                if st.button('Aplicar muestras al semáforo principal', key='ficha_indices_aplicar'):
                    st.rerun()
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
        d = d[d['filtro-provincia'].map(normalizar_nombre).isin([normalizar_nombre(p) for p in PROVINCIAS_LITORAL])]
        d = d.rename(columns={'filtro-provincia':'provincia','filtro-ruta':'ruta','filtro-tramo':'tramo'})
        d['fecha'] = pd.to_datetime(d.actualizado,dayfirst=True,errors='coerce').map(lambda x: x.tz_localize(TZ) if pd.notna(x) else pd.NaT)
        if d.empty:
            raise ValueError('La tabla no contiene tramos del Litoral')
        d['vigencia'] = d.fecha.map(lambda t: 'Reciente · ≤ 24 h' if pd.notna(t) and dato_vigente(t,24) else 'Reconfirmar · reporte antiguo/sin fecha')
        return {'df': d, 'consulta': ahora().isoformat(), 'error': ''}
    except Exception as exc:
        return {'error':str(exc) or type(exc).__name__,'df':pd.DataFrame()}


def copia_vialidad_documentada():
    """Copia oficial conservada; la fecha del parte determina su vigencia."""
    path=Path(__file__).resolve().parent/'assets'/'revision_operativa_norte'/'revision_vialidad.json'
    if not path.exists():
        return None
    registro=json.loads(path.read_text(encoding='utf-8'))
    d=pd.DataFrame(registro.get('registros_litoral',[]))
    if registro.get('error') or d.empty:
        return None
    d['fecha']=pd.to_datetime(d.actualizado,dayfirst=True,errors='coerce').map(lambda x:x.tz_localize(TZ) if pd.notna(x) else pd.NaT)
    d['vigencia']=d.fecha.map(lambda t:'Reciente · ≤ 24 h' if pd.notna(t) and dato_vigente(t,24) else 'Reconfirmar · reporte antiguo/sin fecha')
    return {'df':d,'consulta':registro['consulta'],'error':'','copia_conservada':True}


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
        params={'service':'WFS','version':'2.0.0','request':'GetFeature',
                'typeNames':'ign:salud_020801','outputFormat':'application/json','srsName':'EPSG:4326',
                'bbox':','.join(str(v) for v in limites_litoral())+',EPSG:4326','count':5000}
        features=[];ids=set();completo=False
        for inicio in range(0,25000,5000):
            d=descargar_publico(IGN_SALUD_URL,{**params,'startIndex':inicio}).json()
            pagina=d.get('features',[])
            if not isinstance(pagina,list):
                raise ValueError('Respuesta WFS sin colección válida')
            for f in pagina:
                clave=f.get('id') or json.dumps(f,sort_keys=True)
                if clave in ids:
                    raise ValueError('La consulta WFS repite registros; paginación sin confirmar')
                ids.add(clave);features.append(f)
            total=d.get('numberMatched',d.get('totalFeatures'))
            if str(total).isdigit() and len(features)>=int(total):
                completo=True;break
            if len(pagina)<5000:
                completo=True;break
        if not completo:
            raise ValueError('Catálogo mayor al límite de consulta; no se confirma cobertura completa')
        salida=salud_ign_a_registros({'features':features})
        if salida.empty:
            raise ValueError('Sin puntos de salud válidos')
        return salida, 'Consulta pública IGN/SISA · seis provincias; fecha de relevamiento y operación no confirmadas'
    except Exception:
        salida,origen=copia_salud_litoral()
        return salida,'Consulta actual no completada · '+origen


def salud_ign_a_registros(coleccion):
    filas=[]
    for feat in coleccion.get('features',[]):
        geom=feat.get('geometry') or {};p=feat.get('properties') or {}
        if geom.get('type')!='Point' or len(geom.get('coordinates',[]))<2:
            continue
        lon,lat=geom['coordinates'][:2]
        prov=provincia_de_coordenadas(lat,lon)
        if prov:
            filas.append({'nombre':p.get('fna') or p.get('nam') or 'Efector sin nombre',
                'provincia':prov,'lat':float(lat),'lon':float(lon),'tipo':'salud','fuente':'IGN · catálogo de salud',
                'fuente_original':str(p.get('fdc') or 'No informada'),
                'url_fuente':'https://www.ign.gob.ar/NuestrasActividades/ServiciosWeb',
                'estado':'Ubicación de catálogo · atención/stock sin confirmar'})
    return pd.DataFrame(filas)


def copia_salud_litoral():
    ruta=Path(__file__).resolve().parent/'assets'/'catalogos_sanitarios'/'salud_ign_litoral_6_provincias.geojson'
    if ruta.is_file():
        try:
            salida=salud_ign_a_registros(json.loads(ruta.read_text(encoding='utf-8')))
            if not salida.empty:
                return salida,'Copia del catálogo sanitario IGN descargada 10/10/2026 · seis provincias; procedencia por registro, relevamiento y operación sin confirmar'
        except (OSError,ValueError,KeyError,TypeError):
            pass
    return pd.DataFrame(copia_ficha('salud_ign')), f'Copia parcial IGN/SISA del {FICHA_FECHA_COPIA} · sólo Santa Fe, Corrientes y Entre Ríos; no cubre todo el Litoral'


@st.cache_data(ttl=86400, show_spinner=False)
def consultar_salud_entre_rios():
    """Descarga los catálogos oficiales; conserva el contenido aunque no tenga coordenadas."""
    salida={}
    errores={}
    for nombre,url in SALUD_ER_DESCARGAS.items():
        try:
            r=descargar_publico(url)
            d=leer_catalogo_sanitario(r.content)
            salida[nombre]=d
            if d.attrs.get('filas_rechazadas'):
                errores[nombre]=f"Catálogo parcial: {d.attrs['filas_rechazadas']} fila(s) inconsistente(s) excluida(s); revisar el CSV original"
        except Exception as exc:
            errores[nombre]=str(exc)
    return salida,errores


def leer_catalogo_sanitario(contenido):
    try:
        texto=contenido.decode('utf-8-sig')
    except UnicodeDecodeError:
        texto=contenido.decode('cp1252')
    dialecto=csv.Sniffer().sniff(texto[:10000],delimiters=',;\t')
    filas=[fila for fila in csv.reader(StringIO(texto),dialecto) if any(c.strip() for c in fila)]
    if len(filas)<2:
        raise ValueError('Catálogo sin encabezado o registros')
    columnas=[normalizar_nombre(c).lower().replace(' ','_') for c in filas[0]]
    if len(columnas)!=len(set(columnas)):
        raise ValueError('Catálogo con columnas repetidas')
    validas=[fila for fila in filas[1:] if len(fila)==len(columnas)]
    if not validas:
        raise ValueError('Catálogo sin registros con columnas consistentes')
    resultado=pd.DataFrame(validas,columns=columnas)
    resultado.attrs['filas_rechazadas']=len(filas)-1-len(validas)
    return resultado


def catalogos_salud_conservados():
    carpeta=Path(__file__).resolve().parent/'assets'/'catalogos_sanitarios'
    catalogos={};errores={}
    for nombre,archivo in [('Centros de salud','salud_er_revision_20261010.csv'),
                           ('Hospitales','hospitales_er_revision_20261010.csv')]:
        ruta=carpeta/archivo
        if not ruta.exists():
            continue
        try:
            d=leer_catalogo_sanitario(ruta.read_bytes())
            catalogos[nombre]=d
            if d.attrs.get('filas_rechazadas'):
                errores[nombre]=f"Catálogo parcial: {d.attrs['filas_rechazadas']} fila(s) inconsistente(s) excluida(s); revisar el CSV original"
        except (OSError,ValueError,csv.Error) as exc:
            errores[nombre]=str(exc)
    return catalogos,errores


def validar_recursos(df):
    cols = {'nombre','tipo','lat','lon','fecha_verificacion','valido_hasta','autoridad','fuente','estado','suero_antiofidico'}
    if not cols.issubset(df.columns):
        raise ValueError('Faltan columnas: '+', '.join(sorted(cols-set(df.columns))))
    if df.empty or len(df)>2000:
        raise ValueError('Se requieren de 1 a 2.000 puntos')
    d = df.copy()
    for c in ['lat','lon']:
        d[c] = pd.to_numeric(d[c],errors='coerce')
    if not all(provincia_de_coordenadas(lat,lon) for lat,lon in zip(d.lat,d.lon)):
        raise ValueError('Coordenadas fuera del entorno del Litoral')
    if not d.tipo.isin(['refugio','salud','terreno_alto','limpieza_canal']).all():
        raise ValueError('Tipo de recurso no reconocido')
    if not d.estado.isin(['habilitado','cerrado','sin_confirmar']).all():
        raise ValueError('Estado no reconocido')
    if not d.suero_antiofidico.isin(['confirmado','sin_confirmar','no']).all():
        raise ValueError('Estado de suero no reconocido')
    if not d.fuente.astype(str).str.match(r'^https?://\S+$').all() or not d.autoridad.fillna('').astype(str).str.strip().ne('').all():
        raise ValueError('Cada punto necesita autoridad y URL de evidencia')
    d['fecha_verificacion'] = d.fecha_verificacion.map(fecha_fuente)
    d['valido_hasta'] = d.valido_hasta.map(fecha_fuente)
    if (d.valido_hasta < d.fecha_verificacion).any():
        raise ValueError('La vigencia termina antes de la verificación')
    # Una declaración subida por el usuario conserva su procedencia; no se hace oficial automáticamente.
    for capacidad in ('capacidad_personas','capacidad_animales'):
        if capacidad not in d: d[capacidad]=np.nan
        d[capacidad]=pd.to_numeric(d[capacidad],errors='coerce')
        valores = d[capacidad].dropna()
        if valores.lt(0).any() or not np.isfinite(valores).all() or valores.mod(1).ne(0).any():
            raise ValueError('Capacidad negativa, no finita o no entera')
    if 'acceso_verificado' not in d: d['acceso_verificado']='sin_confirmar'
    if not d.acceso_verificado.isin(['si','no','sin_confirmar']).all(): raise ValueError('Acceso: si/no/sin_confirmar')
    for c in ('telefono','horario_atencion','referente'):
        if c not in d: d[c]=''
        d[c]=d[c].fillna('').astype(str).str.strip()
    if 'atencion_verificada' not in d: d['atencion_verificada']='sin_confirmar'
    if not d.atencion_verificada.isin(['si','no','sin_confirmar']).all():
        raise ValueError('Atención verificada: si/no/sin_confirmar')
    atencion = d.atencion_verificada.eq('si') & d.telefono.astype(str).str.strip().ne('') & d.horario_atencion.astype(str).str.strip().ne('')
    vigente = pd.Series([(e=='habilitado' and dato_vigente(f,24) and fin>=fecha_fuente(ahora()))
                         for e,f,fin in zip(d.estado,d.fecha_verificacion,d.valido_hasta)], index=d.index)
    capacidad = d.capacidad_personas.gt(0) | d.capacidad_animales.gt(0)
    requisitos = (~d.tipo.eq('refugio') | capacidad) & (~d.tipo.eq('salud') | atencion)
    d['utilizable'] = vigente & d.acceso_verificado.eq('si') & requisitos
    d['verificacion_operativa'] = np.select(
        [~vigente, d.acceso_verificado.ne('si'), d.tipo.eq('refugio') & ~capacidad, d.tipo.eq('salud') & ~atencion],
        ['Sin habilitación vigente','Acceso sin confirmar','Capacidad sin confirmar','Atención/horario/contacto sin confirmar'],
        default='Condiciones declaradas vigentes; reconfirmar antes del traslado')
    d['apto_personas_declarado'] = d.utilizable & d.capacidad_personas.gt(0)
    d['apto_hacienda_declarado'] = d.utilizable & d.acceso_verificado.eq('si') & d.capacidad_animales.gt(0)
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
            if len(pt)<2 or not provincia_de_coordenadas(pt[1],pt[0]):
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
D.recursos.forEach(p=>{const destino=p.tipo==='salud'?salud:rec;L.circleMarker([p.lat,p.lon],{radius:p.tipo==='salud'?6:8,color:p.tipo==='salud'?'#7c3aed':p.utilizable?'#15803d':'#6b7280',fillOpacity:.8}).bindPopup('<b>'+esc(p.nombre)+'</b><br>'+esc(p.tipo)+'<br>'+esc(p.estado)+'<br>Capacidad personas: '+esc(p.capacidad_personas ?? 'sin dato')+'; animales: '+esc(p.capacidad_animales ?? 'sin dato')+'<br>Acceso: '+esc(p.acceso_verificado ?? 'sin confirmar')+'<br>Antiofídicos: '+esc(p.suero_antiofidico ?? 'sin confirmar')+'<br>'+esc(p.fuente)).addTo(destino)});salud.addTo(M);rec.addTo(M);
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
    mostrar_registros_operativos()
    st.subheader('Semáforo operativo y planificación territorial')
    nod = st.selectbox('Localidad para planificar',range(len(NODOS)),
                       format_func=lambda i:NODOS[i]['localidad']+' · '+NODOS[i]['provincia'],key='ficha_localidad')
    nodo = NODOS[nod]
    radio = st.select_slider('Entorno de muestreo de relieve (km)',[2,5,10,20],value=5,key='ficha_radio')
    if st.button('Consultar relieve del entorno',key='ficha_dem_cargar'):
        with st.spinner('Consultando cotas del DEM Copernicus…'):
            st.session_state['ficha_relieve'] = (nod,radio,consultar_relieve(nodo['lat'],nodo['lon'],radio))
            st.session_state.setdefault('ficha_dem_por_localidad', {})[normalizar_nombre(nodo['localidad'])] = st.session_state['ficha_relieve'][2]
            st.rerun()
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
    salud,origen_salud=copia_salud_litoral()
    if st.button('Actualizar catálogo de puntos de salud',key='ficha_salud_actualizar'):
        st.session_state['ficha_salud'] = consultar_salud_ign()
    if 'ficha_salud' in st.session_state:
        salud,origen_salud=st.session_state['ficha_salud']
    salud=salud.copy();salud['distancia_km']=distancia_km(nodo['lat'],nodo['lon'],salud.lat,salud.lon)
    salud=salud[salud.distancia_km<=80].sort_values('distancia_km').head(50)
    with st.expander('Catálogos sanitarios oficiales de Entre Ríos'):
        st.write('Estos catálogos agregan hospitales y centros de salud a la trazabilidad. Si no publican coordenadas, se muestran como inventario y no se dibujan automáticamente en el mapa.')
        if st.button('Actualizar catálogos de salud de Entre Ríos',key='ficha_salud_er_actualizar'):
            consultar_salud_entre_rios.clear()
            st.session_state['ficha_salud_er']=consultar_salud_entre_rios()
        er=st.session_state.get('ficha_salud_er')
        if er is None:
            er=catalogos_salud_conservados()
            if er[0]:
                st.caption('Copia descargada el 10/10/2026. Fecha de relevamiento no informada; atención, guardia, capacidad y stock actuales sin confirmar.')
        if er:
            catalogos,errores=er
            for nombre,d in catalogos.items():
                st.write(f'**{nombre} · {len(d)} registros**')
                st.dataframe(d.head(100),use_container_width=True,hide_index=True)
                st.download_button(f'Descargar {nombre} de Entre Ríos',d.to_csv(index=False).encode('utf-8-sig'),f'{normalizar_nombre(nombre).lower().replace(" ","_")}_entre_rios.csv','text/csv',key=f'ficha_salud_er_{normalizar_nombre(nombre)}')
            for nombre,error in errores.items(): st.warning(f'{nombre}: {error}')
        for nombre,url in SALUD_ER_DESCARGAS.items(): st.link_button(f'Fuente oficial · {nombre}',url)
    extras = pd.DataFrame()
    with st.expander('Refugios, terrenos altos, salud y limpieza de canales'):
        st.write('Registro de planificación aportado por municipio, Defensa Civil o comité de cuenca. La habilitación debe tener responsable, evidencia, hora de verificación y fin de vigencia. Los datos cargados siguen siendo declaraciones de su fuente.')
        plantilla='nombre,tipo,lat,lon,fecha_verificacion,valido_hasta,autoridad,fuente,estado,suero_antiofidico,capacidad_personas,capacidad_animales,acceso_verificado,atencion_verificada,horario_atencion,telefono,referente\n'
        st.download_button('Plantilla de recursos territoriales',plantilla,'recursos_territoriales.csv','text/csv',key='ficha_recursos_plantilla')
        st.caption('Vigencia máxima: 24 h. Un refugio necesita acceso confirmado y capacidad declarada >0; salud necesita acceso, atención confirmada, horario y teléfono. Capacidad desconocida queda vacía, nunca 0. Suero se confirma por separado. La declaración cargada no se convierte en certificación institucional.')
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
    conectados=st.session_state.get('ficha_recursos_conectados',pd.DataFrame())
    if not conectados.empty:
        conectados=validar_recursos(conectados)
        conectados=conectados[distancia_km(nodo['lat'],nodo['lon'],conectados.lat,conectados.lon)<=80]
        recursos+=conectados.to_dict('records')
    recursos+=extras.to_dict('records') if not extras.empty else []
    if nodo['localidad']=='Vera':
        recursos+=[p for p in copia_ficha('refugios_vera') if p.get('lat') is not None]
    rutas={'type':'FeatureCollection','features':[]}
    with st.expander('Rutas, cortes y desvíos · Vialidad Nacional'):
        if st.button('Consultar estado de rutas del Litoral',key='ficha_vialidad_cargar'):
            with st.spinner('Consultando tabla pública de Vialidad…'):
                st.session_state['ficha_vialidad']=consultar_vialidad()
        vial=st.session_state.get('ficha_vialidad')
        if not vial or vial.get('error'):
            if vial and vial.get('error'):
                st.warning('No se pudo actualizar Vialidad: '+vial['error'])
            vial=copia_vialidad_documentada() or vial
        if vial:
            if vial['error']:
                st.warning('No se pudo consultar Vialidad: '+vial['error'])
            else:
                vd=vial['df'].copy()
                # Recalcular vigencia en cada render, incluso si la respuesta viene de caché.
                vd['vigencia']=vd.fecha.map(lambda t:'Reciente · ≤ 24 h' if pd.notna(t) and dato_vigente(t,24) else 'Reconfirmar · reporte antiguo/sin fecha')
                st.caption(('Copia oficial conservada: ' if vial.get('copia_conservada') else 'Consulta: ')+vial['consulta']+'. La vigencia se recalcula con la fecha de cada parte. Para actualizar, usá el botón de consulta.')
                provincia=st.selectbox('Provincia del reporte vial',['Todas',*PROVINCIAS_LITORAL],key='ficha_vial_prov')
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
                st.session_state['ficha_rutas_verificadas'] = rutas
            except Exception as exc:
                st.error('Rutas rechazadas: '+str(exc))
    conectadas=st.session_state.get('ficha_rutas_conectadas',{}).get('features',[])
    if conectadas:
        rutas['features']+=validar_rutas_geojson({'type':'FeatureCollection','features':conectadas})['features']
    mostrar_revision_recorrido(nodo, recursos, rutas)
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
                if st.button('Aplicar suelo al semáforo principal', key='ficha_suelo_aplicar'):
                    st.rerun()
                st.dataframe(ds,use_container_width=True)
            except Exception as exc:
                st.session_state.pop('ficha_suelo',None);st.error('Suelo rechazado: '+str(exc))
        else:
            st.session_state.pop('ficha_suelo',None)
    df=st.session_state.get('df_alerta',pd.DataFrame())
    modelo=df[df.localidad==nodo['localidad']].iloc[0].to_dict() if not df.empty and 'localidad' in df else {}
    agua,suelo,baja,rio=componentes_localidad(nodo)
    fusion=fusion_territorial(modelo,agua,suelo,baja,rio)
    st.subheader('Semáforo integrado · '+nodo['localidad'])
    colores={'VERDE':'🟢','AMARILLO':'🟡','ROJO':'🔴','SIN DATOS':'⚪'}
    st.markdown(f'### {colores[fusion["semaforo"]]} {fusion["semaforo"]}')
    st.caption(f'Cobertura territorial: {fusion["cobertura"]}/4 componentes (meteorología, suelo de fuente externa, agua superficial, relieve). La información incompleta mantiene incertidumbre; no certifica ausencia de riesgo.')
    for motivo in fusion['motivos']:
        st.write('• '+motivo)
    orientacion=dict(modelo,localidad=nodo['localidad'],provincia=nodo['provincia'],nivel=fusion['semaforo'],
                     motivos_semaforo=' | '.join(fusion['motivos']))
    mostrar_recomendaciones_alerta(orientacion,key='ficha_plan_acciones_territorio')
    st.caption('Reglas de fusión exploratorias: índice con umbrales amarillo/rojo de referencia 35/55 o evaluados en esta sesión; NDWI > 0 de ≤ 72 h + lluvia ≥ 30 mm/72 h → rojo; agua útil ≥ 90 % de ≤ 10 días + lluvia ≥ 30 → amarillo y, en quintil inferior de relieve, rojo. Un umbral INA vigente puede elevar el estado sólo en el entorno de su estación. ENOS aporta contexto, sin sumar puntos automáticamente.')
    informe={'version':VERSION,'fecha':ahora().isoformat(),'localidad':nodo['localidad'],
             'semaforo':fusion,'modelo':modelo,'agua':agua,'suelo':suelo,'rio':rio,
             'recomendaciones':recomendaciones_para_alerta(orientacion),
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
            'indice':r['indice'],'indice_parcial':r.get('indice_parcial'), 'nivel':r['nivel'],
            'estado':r['estado_datos'], 'lluvia_previa_72_mm':r['lluvia_72'],
            'lluvia_prevista_72_mm':r['lluvia_futura_72'],'humedad_modelo_m3_m3':r['humedad_suelo_promedio'],
            'escorrentia_previa_72_mm':r['runoff_72']})
    return pd.DataFrame(resultados)


HISTORICAL_FORECAST_URL = 'https://historical-forecast-api.open-meteo.com/v1/forecast'
SINGLE_RUNS_URL = 'https://single-runs-api.open-meteo.com/v1/forecast'
HISTORICAL_VARIABLES = [
    'temperature_2m', 'apparent_temperature', 'dew_point_2m',
    'relative_humidity_2m', 'pressure_msl', 'surface_pressure',
    'cloud_cover', 'cloud_cover_low', 'cloud_cover_mid', 'cloud_cover_high',
    'wind_speed_10m', 'wind_gusts_10m', 'wind_direction_10m', 'cape',
    'vapour_pressure_deficit', 'evapotranspiration', 'et0_fao_evapotranspiration',
    'precipitation', 'rain', 'showers', 'precipitation_probability', 'runoff',
    'soil_temperature_0_to_7cm', 'soil_temperature_7_to_28cm',
    'soil_temperature_28_to_100cm', 'soil_temperature_100_to_255cm',
    'soil_moisture_0_to_7cm', 'soil_moisture_7_to_28cm',
    'soil_moisture_28_to_100cm', 'soil_moisture_100_to_255cm', 'weather_code',
]


def registro_vinculacion_indices_historicos():
    filas = []
    for dia, indice in [('24', 25.2), ('25', 27.2), ('26', 44.3)]:
        filas.append({
            'evento_id': '2025-05-27-vera-run-2025-05-'+dia, 'grupo_evento': '2025-05-27-vera',
            'fecha_evento': '2025-05-27', 'inicializacion_utc': '2025-05-'+dia+'T00:00:00+00:00',
            'emision': '2025-05-'+dia+'T03:00:00-03:00', 'indice': '', 'indice_parcial': indice,
            'tipo_emision': 'corte_reconstruido', 'disponibilidad_confirmada': 'no',
            'estado': 'incompleto_sin_escorrentia_antecedente',
            'fuente_pronostico': SINGLE_RUNS_URL,
            'nota': 'Valor parcial de la versión anterior, no índice completo: faltan 72 h de escorrentía antecedente. Corte supuesto +6 h. Un único episodio: Vera 2025.',
        })
    for caso in episodios_documentados() + controles_documentados():
        if caso['evento_id'] == '2025-05-27-vera':
            continue
        fecha = caso.get('fecha_evento') or caso.get('inicio')
        anterior = len(str(fecha)) == 7 or str(fecha) < '2024-03-14'
        descargado=next((r for r in descargas_historicas_documentadas() if r['evento_id']==caso['evento_id']),None)
        filas.append(descargado or {
            'evento_id': caso['evento_id'], 'grupo_evento': caso.get('grupo_evento',caso['evento_id']),
            'fecha_evento': fecha, 'inicializacion_utc': '', 'emision': '', 'indice': '',
            'tipo_emision': '', 'disponibilidad_confirmada': 'no',
            'estado': 'fuera_del_archivo_o_fecha_incompleta' if anterior else 'pendiente_descarga_de_corrida',
            'fuente_pronostico': '',
            'nota': 'Sin índice: el archivo HRES Single Runs comienza el 14/03/2024. Las fechas mensuales necesitan una ventana puntual.' if anterior else 'Falta descargar, calcular y conservar la corrida; no se completa con reanálisis.',
        })
    return pd.DataFrame(filas)


def descargas_historicas_documentadas():
    carpeta=Path(__file__).resolve().parent/'assets'/'corridas_historicas'
    filas=[]
    for nombre in ('registro_descargas.json','registro_descargas_norte.json'):
        path=carpeta/nombre
        if path.exists():
            filas.extend(json.loads(path.read_text(encoding='utf-8')))
    return filas


def punto_evento_historico(evento_id):
    puntos = {
        '2016-04-concordia': (-31.393, -58.020, 'Concordia'),
        '2023-09-01-ituzaingo': (-27.585, -56.687, 'Ituzaingó'),
        '2024-01-11-vera-reconquista': (-29.460, -60.213, 'Vera'),
        '2024-03-03-corrientes': (-27.469, -58.830, 'Corrientes'),
        '2024-03-20-gualeguaychu-santafe': (-33.010, -58.517, 'Gualeguaychú'),
        '2025-05-27-vera': (-29.460, -60.213, 'Vera'),
        'control_negativo_01': (-27.469, -58.830, 'Corrientes'),
    }
    if evento_id in puntos:
        return puntos[evento_id]
    caso=next((c for c in episodios_documentados()+controles_documentados() if c['evento_id']==evento_id),None)
    if caso and caso.get('lat') is not None and caso.get('lon') is not None:
        lat,lon=float(caso['lat']),float(caso['lon'])
        if provincia_de_coordenadas(lat,lon)!=caso['provincia']:
            raise ValueError('La coordenada histórica no pertenece a la provincia del episodio.')
        return (lat,lon,caso.get('localidad_calculo',caso['localidad']))
    return None


def inicializacion_historica(valor):
    run = pd.Timestamp(valor)
    if pd.isna(run) or run.tzinfo is None:
        raise ValueError('La inicialización debe incluir zona horaria UTC.')
    run = run.tz_convert('UTC')
    if run.hour not in (0, 6, 12, 18) or run.minute or run.second or run.microsecond:
        raise ValueError('HRES requiere un ciclo exacto 00, 06, 12 o 18 UTC.')
    if run < pd.Timestamp('2024-03-14', tz='UTC') or run > pd.Timestamp(ahora()).tz_convert('UTC'):
        raise ValueError('La corrida está fuera del archivo HRES disponible desde el 14/03/2024.')
    return run


@st.cache_data(ttl=86400, show_spinner=False)
def consultar_corrida_historica(evento_id, inicializacion_utc):
    punto = punto_evento_historico(evento_id)
    if not punto:
        raise ValueError('Falta una coordenada puntual o la fecha está fuera del archivo.')
    run = inicializacion_historica(inicializacion_utc)
    params = {
        'latitude': punto[0], 'longitude': punto[1],
        'hourly': ','.join(v for v in HISTORICAL_VARIABLES if v in {
            'precipitation','runoff','soil_moisture_0_to_7cm','soil_moisture_7_to_28cm',
            'soil_moisture_28_to_100cm','soil_moisture_100_to_255cm'}),
        'models': 'ecmwf_ifs', 'timezone': 'America/Argentina/Buenos_Aires',
        'temperature_unit': 'celsius', 'wind_speed_unit': 'kmh', 'precipitation_unit': 'mm',
        'cell_selection': 'land',
    }
    corte = (run + pd.Timedelta(hours=6)).tz_convert(TZ)
    pr = {**params, 'run': run.strftime('%Y-%m-%dT%H:%M'), 'forecast_days': 5}
    pa = {**params, 'start_date': (corte-pd.Timedelta(days=7)).strftime('%Y-%m-%d'),
          'end_date': corte.strftime('%Y-%m-%d')}
    corrida = descargar_publico(SINGLE_RUNS_URL, pr).json()
    antecedentes = descargar_publico(HISTORICAL_FORECAST_URL, pa).json()
    return {'corrida': corrida, 'antecedentes': antecedentes,
            'fuente_pronostico': SINGLE_RUNS_URL+'?'+urlencode(pr),
            'fuente_antecedentes': HISTORICAL_FORECAST_URL+'?'+urlencode(pa)}


def ensamblar_corrida_historica(antecedentes, corrida, corte):
    """El futuro procede de una única corrida; nunca de una serie de lead-time fijo."""
    corte = fecha_fuente(corte).tz_localize(None)
    filas = []
    for fuente, futuro in [(antecedentes, False), (corrida, True)]:
        if not isinstance(fuente, dict) or fuente.get('error'):
            raise ValueError('Respuesta histórica ausente o con error.')
        if fuente.get('timezone') != 'America/Argentina/Buenos_Aires':
            raise ValueError('El archivo debe usar horarios de America/Argentina/Buenos_Aires.')
        unidades = fuente.get('hourly_units') or {}
        if unidades.get('precipitation') != 'mm' or (not futuro and unidades.get('runoff') != 'mm'):
            raise ValueError('La lluvia y escorrentía del archivo deben declarar unidades mm.')
        h = fuente.get('hourly') or {}
        tiempos = h.get('time') or []
        if not tiempos:
            raise ValueError('Archivo histórico sin serie horaria.')
        if any(len(v) != len(tiempos) for v in h.values() if isinstance(v, list)):
            raise ValueError('Las variables históricas no tienen la misma cantidad de horas.')
        d = pd.DataFrame(h)
        d['time'] = pd.to_datetime(d['time'], errors='raise')
        if d['time'].isna().any() or d['time'].dt.minute.ne(0).any() or d['time'].dt.second.ne(0).any():
            raise ValueError('La serie debe tener horas exactas, sin fechas vacías.')
        if d['time'].duplicated().any():
            raise ValueError('Archivo histórico con horas repetidas.')
        if d['time'].dt.tz is not None:
            d['time'] = d['time'].dt.tz_convert(TZ).dt.tz_localize(None)
        seleccion = (d.time > corte) & (d.time <= corte+pd.Timedelta(hours=72)) if futuro else (d.time > corte-pd.Timedelta(days=7)) & (d.time <= corte)
        filas.append(d[seleccion])
    d = pd.concat(filas, ignore_index=True).sort_values('time')
    for variable, horas, mascara in [
        ('precipitation', 168, d.time <= corte), ('runoff', 72, (d.time > corte-pd.Timedelta(hours=72)) & (d.time <= corte)),
        ('precipitation', 72, d.time > corte),
    ]:
        valores = pd.to_numeric(d.get(variable, pd.Series(index=d.index, dtype=float)), errors='coerce')
        if len(d.loc[mascara & np.isfinite(valores) & valores.ge(0), 'time'].unique()) < horas:
            raise ValueError(f'Cobertura insuficiente de {variable}: se necesitan {horas} horas válidas.')
    return {'hourly': {c: d[c].dt.strftime('%Y-%m-%dT%H:%M').tolist() if c == 'time' else d[c].tolist() for c in d}}


def evaluar_vinculacion_historica(evento_id, inicializacion_utc, archivos=None):
    caso = next((c for c in episodios_documentados() + controles_documentados() if c['evento_id'] == evento_id), None)
    punto = punto_evento_historico(evento_id)
    if caso is None or punto is None:
        raise ValueError('Evento sin localidad puntual de cálculo.')
    fecha = caso.get('fecha_evento') or caso.get('inicio')
    if len(str(fecha)) != 10:
        raise ValueError('El episodio necesita una fecha puntual; no alcanza un mes.')
    run = inicializacion_historica(inicializacion_utc)
    corte = (run+pd.Timedelta(hours=6)).tz_convert(TZ)
    if (fecha_fuente(fecha)-corte).total_seconds() < 6*3600:
        raise ValueError('Elegí una corrida con corte al menos 6 horas antes del día del evento.')
    datos = archivos or consultar_corrida_historica(evento_id, run.isoformat())
    for archivo in (datos['antecedentes'], datos['corrida']):
        try:
            lat, lon = float(archivo['latitude']), float(archivo['longitude'])
            correcto = np.isfinite(lat) and np.isfinite(lon) and abs(lat-punto[0])<=.25 and abs(lon-punto[1])<=.25
        except (KeyError, TypeError, ValueError):
            correcto = False
        if not correcto:
            raise ValueError('El archivo no identifica una celda próxima a la localidad del evento.')
    serie = ensamblar_corrida_historica(datos['antecedentes'], datos['corrida'], corte)
    salida = procesar_nodo({'localidad': punto[2], 'provincia': caso.get('provincia',''), 'lat': punto[0], 'lon': punto[1]}, serie, corte)
    if not salida.get('cobertura_meteorologica') or not np.isfinite(salida.get('indice', np.nan)):
        raise ValueError('La corrida no tiene lluvia y al menos tres capas de humedad suficientes para calcular el índice.')
    return {
        'evento_id': evento_id, 'grupo_evento': caso.get('grupo_evento',evento_id), 'localidad_calculo': punto[2],
        'fecha_evento': fecha, 'inicializacion_utc': run.isoformat(), 'emision': corte.isoformat(),
        'indice': salida['indice'], 'nivel': salida['nivel'], 'modelo': MODELO,
        'tipo_emision': 'corte_reconstruido', 'disponibilidad_confirmada': 'no',
        'fuente_pronostico': datos.get('fuente_pronostico', SINGLE_RUNS_URL),
        'fuente_antecedentes': datos.get('fuente_antecedentes', HISTORICAL_FORECAST_URL),
        'estado': 'reconstruccion_no_operativa',
        'nota': 'Corte supuesto +6 h. Antecedentes retrospectivos; disponibilidad original no certificada. No usar como emisión operativa ni contar varias corridas como episodios independientes.',
    }


def metricas_clasificacion(y,p):
    y=np.asarray(y,dtype=int);p=np.asarray(p,dtype=int)
    tp=int(np.sum((y==1)&(p==1)));tn=int(np.sum((y==0)&(p==0)))
    fp=int(np.sum((y==0)&(p==1)));fn=int(np.sum((y==1)&(p==0)))
    return {'VP':tp,'VN':tn,'FP':fp,'FN':fn,'sensibilidad':tp/(tp+fn) if tp+fn else None,
            'especificidad':tn/(tn+fp) if tn+fp else None,
            'precision':tp/(tp+fp) if tp+fp else None,
            'F1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0.0}




def evaluar_prueba_cap(resultado, referencia=None):
    referencia = referencia or ahora()
    vigente = dato_vigente(resultado.get('fecha_feed'), 24, referencia)
    completo = not resultado.get('error') and not resultado.get('fallos') and not resultado.get('truncado')
    return {'servicio': 'SMN · alertas CAP', 'ok': bool(vigente and completo),
            'fecha_dato': str(resultado.get('fecha_feed') or ''), 'fuente': SMN_CAP_FEED,
            'detalle': (f"RSS vigente; {resultado.get('leidos',0)} documentos leídos de {resultado.get('total',0)}"
                        if vigente and completo else 'Consulta fallida, parcial o vigencia sin confirmar')}


def probar_servicios_publicos(radar_id):
    """Consultas nuevas desde el servidor, sin enviar mensajes de Telegram."""
    for funcion in (obtener_cap_litoral, descargar_cap_smn, obtener_archivo_observaciones_smn,
                    consultar_radar_sinarame, descargar_frame_sinarame):
        funcion.clear()
    filas = []
    try:
        filas.append(evaluar_prueba_cap(obtener_cap_litoral()))
    except Exception:
        filas.append({'servicio': 'SMN · alertas CAP', 'ok': False, 'detalle': 'Consulta no completada', 'fuente': SMN_CAP_FEED})
    try:
        df, fuente, _ = obtener_archivo_observaciones_smn(ahora().strftime('%Y%m%d'))
        litoral=df[df['Provincia'].isin(PROVINCIAS_LITORAL)] if not df.empty else df
        cobertura=cobertura_observaciones_smn(litoral)
        ultima=litoral['fecha'].max() if not litoral.empty else None
        vigentes=int(cobertura.vigente.sum())
        filas.append({'servicio':'SMN · observaciones','ok':bool(cobertura.vigente.all()),
                      'fecha_dato':str(ultima) if ultima is not None else '', 'fuente':fuente or SMN_OBS_DOWNLOAD,
                      'detalle':f'{len(litoral)} registros; {vigentes}/{len(PROVINCIAS_LITORAL)} provincias con dato de hasta 24 h',
                      'cobertura_por_provincia':json_finito(cobertura.to_dict('records'))})
    except Exception:
        filas.append({'servicio': 'SMN · observaciones', 'ok': False, 'detalle': 'Consulta no completada', 'fuente': SMN_OBS_DOWNLOAD})
    try:
        resultado = consultar_radar_sinarame(radar_id,2)
        frames = resultado['frames']
        ultimo = frames[-1] if frames else None
        reloj = abs((fecha_fuente(resultado['referencia'])-fecha_fuente(ahora())).total_seconds()) <= 600
        vigente = bool(ultimo and dato_vigente(ultimo['fecha'],.5) and reloj)
        filas.append({'servicio': 'Radar · '+radar_id, 'ok': bool(vigente and radar_id in SINARAME_BOUNDS),
                      'fecha_dato': ultimo['fecha'].isoformat() if ultimo else '',
                      'fuente': ultimo['url'] if ultimo else SINARAME_PUBLIC_URL,
                      'detalle': f'{len(frames)} imágenes PNG validadas; vigencia máxima 30 min. Cartografía y movimiento requieren comprobación visual.'})
    except Exception:
        filas.append({'servicio': 'Radar · '+radar_id, 'ok': False, 'detalle': 'Consulta no completada', 'fuente': SINARAME_PUBLIC_URL})
    return {'fecha': ahora().isoformat(), 'version': VERSION, 'resultados': filas}


def mostrar_pruebas_servicios():
    st.subheader('Pruebas de SMN, radar y Telegram')
    entorno = st.selectbox('Entorno de ejecución declarado', ['Sin identificar','Aplicación publicada','Prueba local'], key='ficha_pruebas_entorno')
    url = st.text_input('URL de la aplicación publicada', key='ficha_pruebas_url', placeholder='https://…streamlit.app')
    nombre = st.selectbox('Radar a probar', list(SINARAME_RADARES), key='ficha_pruebas_radar')
    st.caption('Consulta nueva desde este servidor. No verifica por sí sola la página publicada ni la cartografía visible en el navegador.')
    if st.button('Probar SMN y radar ahora', key='ficha_pruebas_publicos'):
        with st.spinner('Consultando servicios públicos...'):
            prueba = probar_servicios_publicos(SINARAME_RADARES[nombre])
        prueba.update(entorno_declarado=entorno, url_declarada=url.strip(), navegador='pendiente')
        st.session_state['ficha_pruebas_publicos_resultado'] = prueba
        st.session_state.pop('ficha_prueba_visual', None)
        st.session_state.pop('ficha_prueba_visual_estado', None)
    prueba = st.session_state.get('ficha_pruebas_publicos_resultado')
    if prueba:
        st.caption(f"Prueba: {prueba['fecha']} · {prueba['version']} · {prueba['entorno_declarado']}")
        st.dataframe(pd.DataFrame(prueba['resultados']), use_container_width=True, hide_index=True)
        visual = st.selectbox('Radar en la pestaña de la app', ['Sin comprobar','Se ven mapa, localidades, radar y cambio de hora','No se ve el mapa o no cambia el radar'], key='ficha_prueba_visual_estado')
        if st.button('Registrar comprobación visual', key='ficha_prueba_visual_guardar', disabled=visual=='Sin comprobar'):
            st.session_state['ficha_prueba_visual'] = {'fecha': ahora().isoformat(), 'resultado': visual, 'tipo': 'observacion_manual_del_usuario'}
    st.divider()
    configurado = all(obtener_secrets_telegram())
    if st.button('Enviar mensaje de prueba a Telegram', key='ficha_pruebas_enviar_telegram', disabled=not configurado):
        st.session_state['ficha_prueba_telegram'] = enviar_telegram_detallado(mensaje_prueba_telegram())
        st.session_state.pop('ficha_recepcion_telegram', None)
    if not configurado:
        st.info('Telegram sin credenciales configuradas en Secrets.')
    telegram = st.session_state.get('ficha_prueba_telegram')
    if telegram:
        st.write(telegram)
        if telegram['ok'] and st.button('Confirmar que recibí el mensaje en el chat', key='ficha_pruebas_confirmar_telegram'):
            st.session_state['ficha_recepcion_telegram'] = {'fecha': ahora().isoformat(), 'message_id': telegram['message_id'], 'tipo': 'confirmacion_manual_del_usuario'}
    registro = {'version': VERSION, 'servicios': prueba, 'telegram': telegram,
                'radar_visual': st.session_state.get('ficha_prueba_visual'),
                'recepcion_telegram': st.session_state.get('ficha_recepcion_telegram'),
                'limitacion': 'El entorno y URL son declarados; la prueba no acredita por sí sola el despliegue. Sin secretos ni ID del chat.'}
    if prueba or telegram:
        st.download_button('Descargar acta de pruebas', json.dumps(json_finito(registro),ensure_ascii=False,indent=2), 'pruebas_servicios_litoral.json', 'application/json', key='ficha_pruebas_exportar')


def mostrar_historia_ficha():
    st.subheader('Caso histórico, evaluación y ficha del proyecto')
    st.write('Problema: anticipar anegamiento en las seis provincias del Litoral argentino para productores, comités de cuenca, municipios y Defensa Civil. Integra acumulados y pronósticos, suelo, agua superficial y relieve para apoyar decisiones agropecuarias y territoriales.')
    st.caption('Ampliación geográfica a Chaco, Formosa y Misiones. Los casos históricos conservan sus localidades y fechas reales: no validan automáticamente el desempeño en las nuevas provincias.')
    caso2016,casovera,calibracion,inventario,pruebas=st.tabs(['Litoral · abril 2016','Vera · mayo 2025','Evaluar umbrales','Datos, vacíos y decisiones','Pruebas de servicios'])
    with caso2016:
        mostrar_caso_2016_integrado()
    with casovera:
        st.write('**Vera · 26–27/05/2025.** El parte provincial del 27/05 reportó más de 400 mm en pocas horas, 117 evacuados y cuatro centros de asistencia. Hay evidencia positiva de impacto; no se atribuye ausencia de anegamiento a los días sin parte.')
        st.link_button('Fuente provincial del impacto observado',CASO_VERA_URL)
        resultados=reconstruir_vera();st.dataframe(resultados,use_container_width=True,hide_index=True)
        st.plotly_chart(px.bar(resultados,x='corte_ART',y='lluvia_prevista_72_mm',labels={'lluvia_prevista_72_mm':'Lluvia estimada a 72 h (mm)'}),use_container_width=True,key='ficha_vera_chart')
        st.caption('Datos: ECMWF IFS HRES vía Open-Meteo Single Runs, inicializaciones 00 UTC del 24, 25 y 26 de mayo. Se fija el corte a 06 UTC (03 ART) como demora de disponibilidad supuesta. Sólo se usa el futuro de esa corrida; los antecedentes provienen del archivo horario y son una reconstrucción retrospectiva. El archivo puede incluir hindcasts del ciclo IFS 49R1: no certifica qué información recibió un usuario en 2025. Malla devuelta: '+str(copia_ficha('vera_2025-05-26')['latitude'])+', '+str(copia_ficha('vera_2025-05-26')['longitude'])+'.')
        st.warning('La lluvia prevista para la celda subestima la lluvia puntual documentada. Falta escorrentía antecedente para calcular el índice completo: el semáforo queda SIN DATOS. Este contraste respalda incorporar observaciones, avisos SMN y vigilancia radar; no permite calibrar el umbral con un índice parcial.')
        st.warning('Los antecedentes conservados no contienen escorrentía válida. Los valores 25.2, 27.2 y 44.3 de la versión anterior quedan como índices parciales, no completos; no se usan para calibrar ni certificar aciertos.')
        st.download_button('Descargar evaluación de Vera',resultados.to_csv(index=False).encode('utf-8-sig'),'Evaluacion_Vera_2025.csv','text/csv',key='ficha_vera_csv')
        st.link_button('Documentación de corridas archivadas','https://open-meteo.com/en/docs/single-runs-api')
    with calibracion:
        mostrar_calibracion_completa()
    with pruebas:
        mostrar_pruebas_servicios()
    with inventario:
        mostrar_cumplimiento_ficha()
        inventario_datos=[
            {'Fuente':'SMN','Dato':'Lluvia, observaciones, alertas CAP y PDF trimestral','Uso':'Alertas, observaciones y mapas trimestrales dentro de la app','Frecuencia / cobertura':'Horaria/diaria y trimestre; cobertura por estación/área','Limitación':'Avisos oficiales y red rural incompleta; tendencia no es lluvia de un día'},
            {'Fuente':'INTA SEPA','Dato':'Recarga del perfil, excedentes, NDVI y archivo de situación hídrica','Uso':'Mapas, GeoTIFF autorizado y muestras; Sentinel-2 para NDWI/NDVI/MNDWI automatizados','Frecuencia / cobertura':'Suelo S-NPP 500 m, decadal; NDVI MODIS 250 m, compuesto 16 días','Limitación':'Nubes y ventana compuesta; JPG no da un valor automático por lote; composición hídrica no es clasificación; NDWI requiere bandas/muestras'},
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


CASO_CONCORDIA_2016 = json.loads(zlib.decompress(base64.b64decode('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')))
# Documentos de apoyo externos para mantener el código liviano.
# El paquete incluye assets/; si faltan, se intenta su fuente pública original.
@st.cache_data(ttl=3600, show_spinner=False)
def documento_de_apoyo(nombre, url):
    archivo_modulo = globals().get('__file__', str(Path.cwd() / 'app.py'))
    carpeta = Path(archivo_modulo).resolve().parent / 'assets'
    documento = carpeta / nombre
    if documento.is_file():
        return documento.read_bytes()
    return descargar_publico(url).content


SMN_TRIMESTRAL_COPIA_URL = 'https://repositorio.smn.gob.ar/bitstream/handle/20.500.12160/3302/0044CL2026.pdf?isAllowed=y&sequence=1'

# ============================================================
# INTEGRACIÓN DE LA FICHA · V4.1
# ============================================================
TRIMESTRAL_REPO = 'https://repositorio.smn.gob.ar/handle/20.500.12160/1179'
STAC_URL = 'https://earth-search.aws.element84.com/v1'
RECOMENDACIONES_NIVELES = {
    'VERDE': {
        'fase': 'Vigilancia y preparación',
        'resumen': 'Seguí los avisos SMN y la evolución de los ríos. Planificá siembra y cosecha con una revisión del lote y de su portancia. Prepará alternativas para hacienda y accesos; el verde no confirma que estén habilitados.',
        'ahora': [
            'Consultá avisos SMN y niveles INA antes de organizar la jornada; revisá también lo que ocurre en el lote.',
            'Comprobá piso y accesos antes de entrar con maquinaria; ajustá las tareas si el suelo no soporta el tránsito.',
            'Identificá un destino alternativo para la hacienda y contactos para confirmar caminos, refugios y atención.',
        ],
        'tareas': [
            {'area': 'Cultivos', 'accion': 'Revisá las ventanas de siembra y cosecha con el pronóstico y tu asesor agronómico; prepará una alternativa si vuelve la lluvia.'},
            {'area': 'Maquinaria', 'accion': 'Verificá la portancia en campo: la capacidad del piso para sostener los equipos. Evitá pasadas innecesarias sobre suelo húmedo.'},
            {'area': 'Hacienda', 'accion': 'Ubicá potreros altos y reservas de alimento y agua de bebida; identificá destino, transporte y contacto veterinario.'},
            {'area': 'Drenajes e insumos', 'accion': 'Revisá obstrucciones desde lugares accesibles y seguros antes del temporal. Protegé semillas, fertilizantes y combustible del agua.'},
            {'area': 'Caminos y accesos', 'accion': 'Consultá estado y fecha del parte por tramo; confirmá accesos rurales y un recorrido alternativo con la autoridad local.'},
            {'area': 'Personas y salud', 'accion': 'Prepará contactos de emergencia, medios de comunicación y elementos básicos; confirmá guardias y puntos de encuentro locales.'},
        ],
    },
    'AMARILLO': {
        'fase': 'Preparar medidas preventivas',
        'resumen': 'Prepará medidas antes de que se complique el acceso. Confirmá destino, forraje y recorrido para la hacienda. Reprogramá tareas en lotes blandos o con agua y coordiná recursos con la autoridad local.',
        'ahora': [
            'Confirmá con la autoridad local los accesos y los avisos vigentes; avisá al equipo qué tareas pueden reprogramarse.',
            'Dejá listo el traslado preventivo de hacienda: destino, alimento, transporte y recorrido confirmados antes de moverla.',
            'Postergá el ingreso a sectores con agua o piso blando; resguardá maquinaria e insumos desde accesos seguros.',
        ],
        'tareas': [
            {'area': 'Cultivos', 'accion': 'Priorizá una evaluación del lote y reprogramá siembra, cosecha o aplicaciones donde haya exceso de agua; acordá la decisión con tu asesor.'},
            {'area': 'Maquinaria', 'accion': 'Prepará un lugar elevado y accesible para los equipos; suspendé entradas a sectores sin piso firme o acceso confirmado.'},
            {'area': 'Hacienda', 'accion': 'Priorizá las categorías sensibles con asesoramiento veterinario. Confirmá potreros receptores, piso, agua y forraje antes de un traslado.'},
            {'area': 'Drenajes e insumos', 'accion': 'Informá obstrucciones o desbordes al municipio o comité de cuenca. Realizá mantenimiento sólo en condiciones seguras y coordiná obras o bombeo.'},
            {'area': 'Caminos y accesos', 'accion': 'Reconfirmá puentes, alcantarillas y cada tramo antes de salir. Si hay agua sobre el recorrido, postergá el movimiento y pedí otra opción verificada.'},
            {'area': 'Personas y salud', 'accion': 'Acordá un contacto y punto de encuentro del equipo; confirmá con la autoridad refugios, cupos, guardia y acceso sanitario.'},
        ],
    },
    'ROJO': {
        'fase': 'Priorizar protección y coordinación',
        'resumen': 'Priorizá la protección de las personas y coordiná con Defensa Civil. Suspendé tareas y circulación en sectores anegados. Evaluá el movimiento de hacienda sólo con destino y recorrido confirmados; ante peligro inmediato pedí asistencia.',
        'ahora': [
            'Contactá a Defensa Civil o emergencias si hay personas en peligro y seguí sus indicaciones sobre resguardo o evacuación.',
            'Suspendé tareas en áreas anegadas; evitá ingresar o cruzar agua, incluso con camioneta, tractor o a pie.',
            'Coordiná la hacienda y los recursos con la autoridad: si el acceso o destino no están confirmados, pedí asistencia antes de moverlos.',
        ],
        'tareas': [
            {'area': 'Cultivos', 'accion': 'Suspendé tareas en sectores inundados o inseguros. Registrá daños desde un lugar seguro y evaluá la recuperación con asistencia técnica.'},
            {'area': 'Maquinaria', 'accion': 'Mantené equipos fuera de sectores anegados; no intentes recuperar máquinas atravesando agua ni trabajar cerca de cables caídos.'},
            {'area': 'Hacienda', 'accion': 'Coordiná traslado o asistencia con destino y recorrido verificados. Pedí apoyo veterinario y organizá alimento y agua de bebida.'},
            {'area': 'Drenajes e insumos', 'accion': 'No intervengas en canales con corrientes, desbordes o riesgo eléctrico. Reportá bloqueos, derrames y necesidades de bombeo a la autoridad.'},
            {'area': 'Caminos y accesos', 'accion': 'Respetá cortes y no cruces zonas inundadas. Un trazado en el mapa no acredita transitabilidad: usá sólo accesos confirmados por la autoridad.'},
            {'area': 'Personas y salud', 'accion': 'Informá si alguien necesita ayuda para desplazarse. La autoridad debe confirmar el destino de resguardo, los cupos y la atención antes del operativo.'},
        ],
    },
    'SIN DATOS': {
        'fase': 'Verificar antes de decidir',
        'resumen': 'La información disponible no permite estimar el riesgo. Consultá SMN, INA y la autoridad local, completá las fuentes y comprobá condiciones en campo sin exponerte. No interpretes la falta de datos como autorización para trabajar o circular.',
        'ahora': [
            'Consultá los avisos SMN, niveles INA y reportes locales; identificá qué fuente falta o quedó antigua.',
            'Antes de tareas o movimientos, pedí confirmación de piso, accesos y destino; no supongas que la falta de datos implica seguridad.',
            'Prepará contactos y alternativas; si hay agua, daños o peligro observado, pedí asistencia sin esperar un índice.',
        ],
        'tareas': [
            {'area': 'Cultivos', 'accion': 'Buscá una evaluación local del lote y un pronóstico disponible; no elijas una ventana de trabajo únicamente con esta consulta incompleta.'},
            {'area': 'Maquinaria', 'accion': 'La portancia no se puede deducir de datos ausentes. Confirmá piso y accesos en campo antes de ingresar equipos.'},
            {'area': 'Hacienda', 'accion': 'Inventariá animales, alimento y agua; confirmá potreros receptores y recorrido con referentes locales antes de planificar movimientos.'},
            {'area': 'Drenajes e insumos', 'accion': 'Solicitá información sobre escurrimiento y desbordes; evitá inspecciones riesgosas y reportá obstrucciones a la autoridad.'},
            {'area': 'Caminos y accesos', 'accion': 'Obtené un parte vigente por tramo, incluidos caminos internos; la ausencia de reportes no demuestra que se pueda transitar.'},
            {'area': 'Personas y salud', 'accion': 'Contactá a referentes locales para confirmar lugares de resguardo, cupos y atención; ante peligro real recurrí a emergencias.'},
        ],
    },
}
ACCIONES_FICHA = {nivel: plan['resumen'] for nivel,plan in RECOMENDACIONES_NIVELES.items()}


def recomendaciones_para_alerta(row):
    """Orientaciones por semáforo integrado; no recalcula umbrales ni habilita recursos."""
    nivel=str(row.get('nivel','SIN DATOS')).strip().upper()
    nivel={'BAJO':'VERDE','MEDIO':'AMARILLO','ALTO':'ROJO','MUY ALTO':'ROJO'}.get(nivel,nivel)
    if nivel not in RECOMENDACIONES_NIVELES:
        nivel='SIN DATOS'
    incompleta=str(row.get('estado_datos','SIN DATOS'))!='OK'
    cobertura=row.get('cobertura_meteorologica',True)
    if cobertura is None or pd.isna(cobertura) or not bool(cobertura):
        incompleta=True
    try:
        incompleta |= not np.isfinite(float(row.get('indice',np.nan)))
    except (ValueError,TypeError):
        incompleta=True
    fecha=row.get('actualizado')
    if isinstance(fecha,str) and fecha.strip():
        try:
            incompleta |= not dato_vigente(datetime.strptime(fecha,'%d/%m/%Y %H:%M'),12)
        except (ValueError,TypeError):
            incompleta=True
    else:
        incompleta=True
    # INA puede elevar la precaución incluso sin un índice meteorológico.
    if nivel=='VERDE' and incompleta:
        nivel='SIN DATOS'
    plan=json.loads(json.dumps(RECOMENDACIONES_NIVELES[nivel],ensure_ascii=False))
    plan['nivel']=nivel
    plan['contexto']='Meteorología incompleta o antigua; el alerta preventivo puede proceder de otras evidencias. Revisá los motivos del semáforo.' if incompleta and nivel in ('AMARILLO','ROJO') else ''
    plan['fuentes']=[
        {'organismo':'INTA','url':'https://www.argentina.gob.ar/noticias/recomendaciones-para-prevenir-riesgos-y-reducir-el-impacto-del-fenomeno-del-nino-0'},
        {'organismo':'SINAGIR','url':'https://www.argentina.gob.ar/sinagir/riesgos-frecuentes/inundacion/prevencion'},
        {'organismo':'SINAGIR','url':'https://www.argentina.gob.ar/sinagir/riesgos-frecuentes/lluvias-intensas/que-hacer'},
    ]
    return plan


def html_recomendaciones_alerta(row):
    plan=recomendaciones_para_alerta(row)
    nivel=plan['nivel']
    color,fondo={'VERDE':('#11633f','#eaf7ef'),'AMARILLO':('#8a5300','#fff6df'),
                 'ROJO':('#ad263d','#fff0f2'),'SIN DATOS':('#475569','#edf2f7')}[nivel]
    def esc(value):
        return html.escape(str(value))
    def numero(campo,sufijo):
        try:
            valor=float(row.get(campo,np.nan))
            return f'{valor:.1f} {sufijo}' if np.isfinite(valor) else 'Sin dato'
        except (ValueError,TypeError):
            return 'Sin dato'
    tareas=''.join(f'<article class="ala-rec-tarea"><h3>{esc(t["area"])}</h3><p>{esc(t["accion"])}</p></article>' for t in plan['tareas'])
    pasos=''.join('<li>'+esc(p)+'</li>' for p in plan['ahora'])
    contexto='<p class="ala-rec-contexto">'+esc(plan['contexto'])+'</p>' if plan['contexto'] else ''
    motivos=esc(row.get('motivos_semaforo','Consultá las fuentes y condiciones locales.'))
    consulta=row.get('actualizado') if isinstance(row.get('actualizado'),str) and row.get('actualizado').strip() else 'Sin hora confirmada'
    return f'''<style>
    .ala-rec{{background:#f8fafc;color:#183448;border:1px solid #d4dee7;border-top:6px solid {color};border-radius:18px;padding:24px;margin:12px 0 22px;font-family:system-ui,sans-serif;box-shadow:0 6px 22px rgba(20,43,60,.07)}}
    .ala-rec *{{box-sizing:border-box}}.ala-rec-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:18px}}.ala-rec-kicker{{font-size:12px;letter-spacing:.06em;font-weight:750;text-transform:uppercase;color:#496579;margin-bottom:5px}}
    .ala-rec h2{{font-size:26px;line-height:1.25;margin:0 0 6px;color:#183448}}.ala-rec-provincia{{font-size:15px;color:#496579}}.ala-rec-badge{{border-radius:12px;padding:12px 16px;background:{fondo};color:{color};font-size:20px;font-weight:800;white-space:nowrap}}
    .ala-rec-meta{{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0 12px}}.ala-rec-meta span{{background:#fff;border:1px solid #dce5ed;border-radius:8px;padding:7px 10px;font-size:13px}}
    .ala-rec h3{{font-size:17px;margin:0 0 8px;color:#183448}}.ala-rec-ahora{{background:{fondo};border-radius:12px;padding:18px 20px;margin:16px 0}}.ala-rec-ahora h3{{font-size:20px;color:{color}}}.ala-rec ol{{margin:10px 0 0;padding-left:24px}}.ala-rec li{{padding-left:6px;margin:8px 0;font-size:16px;line-height:1.5}}
    .ala-rec-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}.ala-rec-tarea{{background:#fff;border:1px solid #dce5ed;border-radius:12px;padding:16px}}.ala-rec p{{font-size:15px;line-height:1.5;margin:0;overflow-wrap:anywhere}}
    .ala-rec-contexto{{background:#fff6df;padding:10px 12px;border-radius:8px;margin:12px 0!important}}.ala-rec-motivos{{margin:10px 0 16px!important;color:#496579;font-size:13px!important}}.ala-rec-footer{{border-top:1px solid #d4dee7;margin-top:18px;padding-top:14px}}.ala-rec-footer p{{font-size:13px;margin:5px 0;color:#496579}}.ala-rec-links{{display:flex;flex-wrap:wrap;gap:10px 18px;margin:12px 0 0}}.ala-rec a{{color:#075d78;font-size:14px;text-decoration:underline;text-underline-offset:3px}}
    @media(max-width:900px){{.ala-rec-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}@media(max-width:600px){{.ala-rec{{padding:17px}}.ala-rec-head{{flex-direction:column;gap:12px}}.ala-rec h2{{font-size:23px}}.ala-rec-grid{{grid-template-columns:1fr}}.ala-rec-badge{{font-size:18px}}.ala-rec-ahora{{padding:15px}}}}
    </style><section class="ala-rec" aria-label="Alerta local y acciones recomendadas"><div class="ala-rec-head"><div><div class="ala-rec-kicker">Alerta local y acciones</div><h2>{esc(row.get('localidad','Localidad sin identificar'))}</h2><div class="ala-rec-provincia">{esc(row.get('provincia',''))} · {esc(plan['fase'])}</div></div><div class="ala-rec-badge">{emoji_nivel(nivel)} {esc(nivel)}</div></div><div class="ala-rec-meta"><span>Índice meteorológico: {esc(numero('indice','/100'))}</span><span>Lluvia prevista 72 h: {esc(numero('lluvia_futura_72','mm'))}</span><span>Consulta: {esc(consulta)}</span></div><p class="ala-rec-motivos"><strong>Motivos del semáforo:</strong> {motivos}</p>{contexto}<div class="ala-rec-ahora"><h3>Qué hacer ahora</h3><ol>{pasos}</ol></div><div class="ala-rec-grid">{tareas}</div><div class="ala-rec-footer"><p>Semáforo experimental. Contrastá la decisión con avisos oficiales y condiciones del lote. El color no confirma portancia, caminos abiertos, cupos ni atención disponible.</p><p>Refugios, caminos y contactos locales: pestaña <strong>Territorio y rutas</strong>. Orientaciones adaptadas de INTA y SINAGIR; no son una orden de evacuación.</p><div class="ala-rec-links"><a href="https://www.smn.gob.ar/" target="_blank" rel="noopener noreferrer">Consultar avisos SMN</a><a href="https://www.argentina.gob.ar/noticias/recomendaciones-para-prevenir-riesgos-y-reducir-el-impacto-del-fenomeno-del-nino-0" target="_blank" rel="noopener noreferrer">Guía rural INTA</a><a href="https://www.argentina.gob.ar/sinagir/riesgos-frecuentes/inundacion/prevencion" target="_blank" rel="noopener noreferrer">Prevención SINAGIR</a></div></div></section>'''


def mostrar_recomendaciones_alerta(row,key):
    st.markdown(html_recomendaciones_alerta(row),unsafe_allow_html=True)
    plan=recomendaciones_para_alerta(row)
    documento={'version':VERSION,'localidad':row.get('localidad'),'provincia':row.get('provincia'),
               'fecha_datos':row.get('actualizado'),'motivos':row.get('motivos_semaforo'),**plan}
    st.download_button('Descargar plan de acciones de esta localidad',json.dumps(documento,ensure_ascii=False,indent=2),
                       'plan_acciones_localidad.json','application/json',key=key)


def acciones_telegram_html(row):
    plan=recomendaciones_para_alerta(row)
    partes=[html.escape(plan['resumen']),'\n<b>Qué hacer ahora:</b>']
    partes.extend(f'{i}. {html.escape(texto)}' for i,texto in enumerate(plan['ahora'],1))
    partes.append('\n<b>Plan por tarea:</b>')
    partes.extend(f'<b>{html.escape(t["area"])}:</b> {html.escape(t["accion"])}' for t in plan['tareas'])
    if plan['contexto']:
        partes.append(html.escape(plan['contexto']))
    return '\n'.join(partes)


def localidad_prioritaria(df):
    if df.empty:
        return None
    prioridad={'ROJO':3,'AMARILLO':2,'VERDE':1,'SIN DATOS':0}
    orden=df.assign(_prioridad=df.nivel.map(prioridad).fillna(0),
                    _indice=pd.to_numeric(df.indice,errors='coerce').fillna(-1))
    return orden.sort_values(['_prioridad','_indice','localidad'],ascending=[False,False,True]).iloc[0]['localidad']


def umbrales_semaforo():
    return float(st.session_state.get('ficha_umbral_amarillo', 35)), float(st.session_state.get('ficha_umbral_local', 55))


def nivel_desde_indice(indice):
    if not np.isfinite(indice):
        return 'SIN DATOS'
    amarillo, rojo = umbrales_semaforo()
    return 'ROJO' if indice >= rojo else 'AMARILLO' if indice >= amarillo else 'VERDE'


def emoji_nivel(nivel):
    return {'VERDE': '🟢', 'AMARILLO': '🟡', 'ROJO': '🔴', 'SIN DATOS': '⚪',
            'BAJO': '🟢', 'MEDIO': '🟡', 'ALTO': '🔴', 'MUY ALTO': '🔴'}.get(nivel, '⚪')


def accion_desde_nivel(nivel):
    return ACCIONES_FICHA.get(nivel, ACCIONES_FICHA['SIN DATOS'])


def fusion_territorial(modelo, agua=None, suelo=None, cota_baja=None, rio=None):
    amarillo, rojo = umbrales_semaforo()
    indice = float(modelo.get('indice', np.nan))
    lluvia = float(modelo.get('lluvia_futura_72', np.nan))
    valido = bool(np.isfinite(indice) and modelo.get('cobertura_meteorologica', True))
    if modelo.get('actualizado'):
        try:
            valido &= dato_vigente(datetime.strptime(modelo['actualizado'], '%d/%m/%Y %H:%M'), 12)
        except (ValueError,TypeError):
            valido = False
    rank = (2 if indice>=rojo else 1 if indice>=amarillo else 0) if valido else None
    motivos = [f'Índice meteorológico {indice:.1f}/100; amarillo ≥ {amarillo:g}, rojo ≥ {rojo:g}'] if valido else ['Meteorología ausente, incompleta o de más de 12 horas']
    cobertura = int(valido)
    if agua and dato_vigente(agua.get('fecha'),72) and np.isfinite(agua.get('NDWI',np.nan)):
        cobertura += 1
        if agua['NDWI']>0 and valido and np.isfinite(lluvia) and lluvia>=30:
            rank=2;motivos.append('NDWI positivo reciente y lluvia ≥ 30 mm/72 h; contrastar agua permanente y detección exploratoria')
    if suelo and dato_vigente(suelo.get('fecha'),240) and np.isfinite(suelo.get('agua_util_pct',np.nan)):
        cobertura += 1
        if suelo['agua_util_pct']>=90 and valido and np.isfinite(lluvia) and lluvia>=30:
            rank=max(rank,1);motivos.append('Agua útil INTA/relevamiento ≥ 90 % y lluvia ≥ 30 mm/72 h')
            if cota_baja:
                rank=2;motivos.append('Cota en el quintil inferior del entorno muestreado')
    if cota_baja is not None:
        cobertura += 1
    if rio and dato_vigente(rio.get('fecha'),48):
        estado=clasificar_rio(float(rio['altura']),rio['estacion'])
        if estado=='EVACUACIÓN':
            rank=2;motivos.append('Referencia fluvial sobre umbral INA de evacuación; verificar área de influencia')
        elif estado=='ALERTA':
            rank=max(rank if rank is not None else 0,1);motivos.append('Referencia fluvial sobre umbral INA de alerta')
    return {'semaforo':['VERDE','AMARILLO','ROJO'][rank] if rank is not None else 'SIN DATOS',
            'motivos':motivos,'cobertura':min(cobertura,4)}


def componentes_localidad(nodo):
    nombre = normalizar_nombre(nodo['localidad'])
    agua = None
    candidatos = []
    indices = st.session_state.get('ficha_indices', pd.DataFrame())
    if not indices.empty:
        d = indices[(indices.localidad.map(normalizar_nombre) == nombre) &
                    (distancia_km(nodo['lat'], nodo['lon'], indices.lat, indices.lon) <= 10)]
        candidatos.extend(d.to_dict('records'))
    sat = st.session_state.get('ficha_satelites', {}).get(nombre)
    if sat and not sat.get('error'):
        candidatos.append(sat['observacion'])
    if candidatos:
        agua = max(candidatos, key=lambda d: fecha_fuente(d['fecha']))
    suelo = None
    fuentes_suelo = [st.session_state.get('ficha_suelo', pd.DataFrame()),
                    st.session_state.get('ficha_suelo_raster', pd.DataFrame())]
    candidatos = []
    for d in fuentes_suelo:
        if not d.empty:
            candidatos.extend(d[d.localidad.map(normalizar_nombre) == nombre].to_dict('records'))
    if candidatos:
        suelo = max(candidatos, key=lambda d: fecha_fuente(d['fecha']))
    rd = st.session_state.get('ficha_dem_por_localidad', {}).get(nombre)
    if rd is None:
        previo = st.session_state.get('ficha_relieve')
        if previo and NODOS[previo[0]]['localidad'] == nodo['localidad']:
            rd = previo[2]
    baja = None
    if rd and not rd.get('error') and not rd['df'].empty:
        dem = rd['df']
        ix = distancia_km(nodo['lat'], nodo['lon'], dem.lat, dem.lon).argmin()
        baja = bool(dem.iloc[ix].cota_m <= dem.cota_m.quantile(.2))
    rio = None
    ri = st.session_state.get('ficha_ina_resultado')
    if ri and ri['estacion'].get('lat') is not None:
        est = ri['estacion']
        if distancia_km(nodo['lat'], nodo['lon'], float(est['lat']), float(est['lon'])) <= 25:
            serie = ri['series'][0]
            if not serie['error'] and not serie['df'].empty and int(serie['meta'].get('unitId', -1)) == 11:
                obs = serie['df'].iloc[-1]
                rio = {'fecha': obs.fecha, 'altura': obs.valor, 'estacion': est}
    return agua, suelo, baja, rio


def integrar_semaforo(df):
    filas = []
    for _, r in df.iterrows():
        row = r.to_dict()
        nodo = next((n for n in NODOS if n['localidad'] == row['localidad']), row)
        fusion = fusion_territorial(row, *componentes_localidad(nodo))
        row['nivel_meteorologico'] = nivel_desde_indice(float(row.get('indice', np.nan)))
        row['nivel'] = fusion['semaforo']
        plan=recomendaciones_para_alerta(row)
        row['accion'] = plan['resumen']
        row['accion_prioritaria'] = plan['ahora'][0]
        row['cobertura_territorial'] = fusion['cobertura']
        row['motivos_semaforo'] = ' | '.join(fusion['motivos'])
        filas.append(row)
    return pd.DataFrame(filas)


def url_publica_evidencia(url, oficial=False):
    from ipaddress import ip_address
    p = urlparse(str(url))
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise ValueError('Usá una URL pública HTTPS sin credenciales')
    host = p.hostname.lower()
    if host == 'localhost' or host.endswith(('.local', '.internal')):
        raise ValueError('No se admiten direcciones locales')
    try:
        if not ip_address(host).is_global:
            raise ValueError('No se admiten direcciones privadas')
    except ValueError as exc:
        if 'direcciones privadas' in str(exc):
            raise
    if oficial and not host.endswith(('.gob.ar', '.gov.ar', '.inta.gob.ar')):
        raise ValueError('Esta conexión necesita un dominio oficial argentino .gob.ar o .gov.ar')
    return str(url)


@st.cache_data(ttl=1800, show_spinner=False)
def descargar_registro(url, formato):
    # Registros públicos; nunca se pide un token ni se envía una credencial del usuario.
    url = url_publica_evidencia(url, oficial=True)
    r = requests.get(url, timeout=(4, 15), allow_redirects=False)
    r.raise_for_status()
    if len(r.content) > 5_000_000:
        raise ValueError('Registro mayor a 5 MB')
    if formato == 'CSV':
        from io import StringIO
        return pd.read_csv(StringIO(r.content.decode('utf-8-sig'))).fillna('')
    return r.json()


def extraer_trimestre(texto):
    meses = ['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre']
    inicial = normalizar_nombre(texto[:450]).lower()
    hallados = re.findall(r'\b('+'|'.join(meses)+r')\b', inicial)[:3]
    ys = re.findall(r'\b20\d{2}\b', inicial)
    if len(hallados) != 3 or not ys:
        raise ValueError('No se pudo determinar el período del pronóstico')
    year = int(ys[0]); mes = meses.index(hallados[0])+1
    inicio = datetime(year, mes, 1, tzinfo=TZ)
    siguiente = inicio
    for _ in range(3):
        siguiente = (siguiente.replace(day=28)+timedelta(days=4)).replace(day=1)
    return inicio, siguiente-timedelta(seconds=1), '–'.join(hallados)+' '+str(year)


def leer_pdf_trimestral(contenido, url):
    import fitz
    if len(contenido)>12_000_000 or not contenido.startswith(b'%PDF'):
        raise ValueError('No se recibió un PDF SMN válido de hasta 12 MB')
    with fitz.open(stream=contenido, filetype='pdf') as doc:
        if len(doc)>35:
            raise ValueError('Documento demasiado extenso')
        texto = '\n'.join(p.get_text() for p in doc)
        inicio, fin, periodo = extraer_trimestre(texto)
        paginas = [p.get_pixmap(matrix=fitz.Matrix(1.25,1.25)).tobytes('png') for p in list(doc)[:3]]
        # Se reproducen los mapas con sus leyendas; no se infiere una probabilidad por ciudad.
        return {'url': url, 'inicio': inicio, 'fin': fin, 'periodo': periodo,
                'paginas': paginas, 'pdf': contenido, 'consulta': ahora().isoformat(), 'error': ''}


@st.cache_data(ttl=21600, show_spinner=False)
def consultar_trimestral():
    try:
        pagina = requests.get(TRIMESTRAL_REPO,timeout=(10,20));pagina.raise_for_status();pagina=pagina.text
        candidatos = re.findall(r'href=["\']([^"\']*/handle/20\.500\.12160/\d+)["\'][^>]*>\s*Pron(?:ó|&oacute;)stico clim', pagina, re.I)
        if not candidatos:
            raise ValueError('El repositorio no devolvió un informe trimestral identificable')
        ficha = urljoin(TRIMESTRAL_REPO, candidatos[0])
        detalle = requests.get(ficha,timeout=(10,20));detalle.raise_for_status();detalle=detalle.text
        pdfs = re.findall(r'href=["\']([^"\']*bitstream[^"\']*\.pdf[^"\']*)["\']',detalle,re.I)
        if not pdfs:
            raise ValueError('Informe sin PDF público')
        url = urljoin(ficha, html.unescape(pdfs[0]))
        if urlparse(url).hostname != 'repositorio.smn.gob.ar':
            raise ValueError('El PDF no pertenece al repositorio SMN')
        pdf = requests.get(url,timeout=(10,20));pdf.raise_for_status()
        return leer_pdf_trimestral(pdf.content,url)
    except Exception as exc:
        try:
            copia=leer_pdf_trimestral(documento_de_apoyo('smn_trimestral_agosto_octubre_2026.pdf', SMN_TRIMESTRAL_COPIA_URL),SMN_TRIMESTRAL_COPIA_URL)
            copia['error_consulta']=str(exc) or type(exc).__name__
            copia['origen']='Copia documental consultada el 09/10/2026; no se afirma que sea la última publicación del sitio principal'
            return copia
        except Exception:
            return {'error':str(exc) or type(exc).__name__}


def mostrar_trimestral_integrado():
    st.subheader('Perspectiva SMN a 90 días · mapas dentro de la aplicación')
    st.caption('Se recupera el último informe disponible en el repositorio oficial. Su publicación puede demorar respecto del sitio principal del SMN.')
    if st.button('Consultar último informe trimestral SMN',key='ficha_trimestral_cargar'):
        with st.spinner('Recuperando el documento oficial y sus mapas…'):
            st.session_state['ficha_trimestral'] = consultar_trimestral()
    with st.expander('Cargar un informe oficial más reciente'):
        up = st.file_uploader('PDF trimestral descargado del SMN',type=['pdf'],key='ficha_trimestral_pdf')
        if up:
            try:
                st.session_state['ficha_trimestral'] = leer_pdf_trimestral(up.getvalue(),'https://www.smn.gob.ar/pronostico-trimestral')
            except Exception as exc:
                st.error(str(exc))
    res = st.session_state.get('ficha_trimestral')
    if not res:
        st.info('Consultá el informe para ver mapas, leyendas y período. Una perspectiva trimestral no determina una tormenta ni suma puntos al semáforo.')
    elif res.get('error'):
        st.warning('No se pudo recuperar el informe: '+res['error'])
    else:
        st.write('**Período: '+res['periodo']+'**')
        if res.get('error_consulta'):
            st.warning('Consulta en vivo no disponible. Se muestra una copia documental con su período original; podés cargar un PDF SMN más reciente.')
        st.caption('Consulta: '+str(res['consulta'])+' · Fuente: SMN')
        if res['fin'] < ahora():
            st.warning('Informe vencido: se muestra como antecedente, no como pronóstico vigente.')
        elif res['inicio'].month != ahora().month:
            st.warning('El período comenzó en un mes anterior. Verificá si el SMN publicó un informe más reciente.')
        for i,p in enumerate(res['paginas']):
            st.image(p,caption=f'SMN · {res["periodo"]} · página {i+1}',use_container_width=True)
        st.download_button('Descargar informe SMN utilizado',res['pdf'],'SMN_trimestral.pdf','application/pdf',key='ficha_trimestral_descarga')
        st.link_button('Fuente del documento',res['url'])


def muestrear_raster_suelo(contenido, fecha, fuente, escala=1.0, offset=0.0):
    import rasterio
    from rasterio.io import MemoryFile
    from rasterio.warp import transform
    if len(contenido)>80_000_000:
        raise ValueError('Raster mayor a 80 MB')
    fuente = url_publica_evidencia(fuente)
    if 'inta' not in (urlparse(fuente).hostname or '').lower():
        raise ValueError('Identificá la página INTA de la que obtuviste el raster')
    filas=[]
    with MemoryFile(contenido) as mem:
        with mem.open() as ds:
            if not ds.crs or ds.count!=1:
                raise ValueError('Se requiere un GeoTIFF georreferenciado de una banda: agua útil del perfil en %')
            xs,ys=transform('EPSG:4326',ds.crs,[n['lon'] for n in NODOS],[n['lat'] for n in NODOS])
            for nodo,x,y in zip(NODOS,xs,ys):
                if not ds.bounds.left<=x<ds.bounds.right or not ds.bounds.bottom<y<=ds.bounds.top:
                    continue
                raw=next(ds.sample([(x,y)],masked=True))[0]
                if np.ma.is_masked(raw) or not np.isfinite(raw):
                    continue
                valor=float(raw)*float(escala)+float(offset)
                if not 0<=valor<=100:
                    continue
                filas.append({'localidad':nodo['localidad'],'lat':nodo['lat'],'lon':nodo['lon'],
                    'fecha':fecha_fuente(fecha),'fuente':fuente,'agua_util_pct':valor,
                    'metodo':'Píxel del GeoTIFF INTA; no medición del lote','resolucion_crs':str(ds.res)})
    if not filas:
        raise ValueError('No hay píxeles válidos de 0–100 % para las localidades del Litoral')
    return pd.DataFrame(filas)


def mostrar_raster_inta():
    with st.expander('Integrar agua útil INTA desde un GeoTIFF'):
        st.write('Descargá el raster PJ por el acceso autorizado de SEPA y cargalo aquí. La app extrae el píxel de cada localidad y lo incorpora al mismo semáforo del mapa y Telegram.')
        up=st.file_uploader('GeoTIFF PJ · agua útil del perfil',type=['tif','tiff'],key='ficha_inta_raster')
        fecha=st.date_input('Fin del período de observación del raster',value=ahora().date(),key='ficha_inta_raster_fecha')
        fuente=st.text_input('Página de origen del raster INTA',value=INTA_BASE+'/productos/geosepa/agua_en_suelo/pj/',key='ficha_inta_raster_fuente')
        st.caption('La escala y el offset deben corresponder a la documentación del raster. Valores por defecto: producto ya expresado en porcentaje.')
        a,b=st.columns(2)
        escala=a.number_input('Escala declarada por el producto',value=1.0,format='%.6f',key='ficha_inta_escala')
        offset=b.number_input('Offset declarado por el producto',value=0.0,format='%.6f',key='ficha_inta_offset')
        if st.button('Extraer e integrar suelo INTA',key='ficha_inta_extraer',disabled=up is None):
            try:
                ds=muestrear_raster_suelo(up.getvalue(),fecha,fuente,escala,offset)
                st.session_state['ficha_suelo_raster']=ds
                st.rerun()
            except Exception as exc:
                st.error('Raster rechazado: '+str(exc))
        ds=st.session_state.get('ficha_suelo_raster',pd.DataFrame())
        if not ds.empty:
            st.dataframe(ds,use_container_width=True,hide_index=True)
            st.download_button('Descargar valores INTA por localidad',ds.to_csv(index=False).encode('utf-8-sig'),'INTA_suelo_localidades.csv','text/csv',key='ficha_raster_csv')
            if st.button('Quitar este raster de la fusión',key='ficha_inta_quitar'):
                st.session_state.pop('ficha_suelo_raster',None);st.rerun()
        st.caption('La fecha limita la vigencia. Agua útil, humedad volumétrica y saturación física no son la misma variable; para saturación se requieren características del suelo o mediciones del perfil.')


@st.cache_data(ttl=21600, show_spinner=False)
def buscar_escenas(lat,lon,desde,hasta):
    dlat=.02;dlon=.02/max(.5,np.cos(np.radians(lat)))
    params={'collections':'sentinel-2-c1-l2a','bbox':f'{lon-dlon},{lat-dlat},{lon+dlon},{lat+dlat}',
            'datetime':str(desde)+'T00:00:00Z/'+str(hasta)+'T23:59:59Z','limit':12}
    try:
        d=descargar_publico(STAC_URL+'/search',params).json()
        items=[]
        for item in d.get('features',[]):
            if item.get('collection')!='sentinel-2-c1-l2a':
                continue
            props=item.get('properties',{})
            if fecha_fuente(props['datetime'])>ahora()+timedelta(minutes=5):
                continue
            if float(props.get('eo:cloud_cover',100))>80:
                continue
            if all(k in item.get('assets',{}) for k in ['green','red','nir','swir16','scl']):
                items.append(item)
        return {'items':sorted(items,key=lambda x:x['properties']['datetime'],reverse=True),'error':''}
    except Exception as exc:
        return {'items':[],'error':str(exc) or type(exc).__name__}


def indices_desde_bandas(bandas, scl):
    valid=np.isin(scl,[4,5,6])
    for v in bandas.values():
        valid &= np.isfinite(v)
    out={}
    for nombre,a,b in [('NDWI','green','nir'),('NDVI','nir','red'),('MNDWI','green','swir16')]:
        den=bandas[a]+bandas[b]
        good=valid & (den>1e-6)
        idx=np.full(den.shape,np.nan,dtype=np.float32)
        idx[good]=(bandas[a][good]-bandas[b][good])/den[good]
        idx[~np.isfinite(idx) | (np.abs(idx)>1)] = np.nan
        out[nombre]=idx
    out['valido']=valid & np.isfinite(out['NDWI']) & np.isfinite(out['MNDWI'])
    out['agua']=out['valido'] & (out['NDWI']>0) & (out['MNDWI']>0)
    return out


def abrir_cog_por_rangos(url, mode='rb'):
    # Rasterio usa este archivo virtual para leer sólo bloques COG. Requests conserva
    # verificación TLS y la configuración de certificados del entorno, también con proxy.
    from io import RawIOBase
    class ArchivoHTTP(RawIOBase):
        def __init__(self, url):
            self.url=url;self.pos=0;self.total=None;self.cache={};self.requests_count=0
            self.session=requests.Session()
            self._bloque(0)
        def readable(self): return True
        def seekable(self): return True
        def tell(self): return self.pos
        def seek(self,offset,whence=0):
            target=offset if whence==0 else self.pos+offset if whence==1 else self.total+offset
            if target<0: raise ValueError('Posición negativa')
            self.pos=target;return target
        def _bloque(self,idx):
            if idx in self.cache: return self.cache[idx]
            if self.requests_count>=80: raise OSError('Límite de lecturas remotas por banda')
            start=idx*262144;end=start+262143
            if self.total is not None:
                if start>=self.total:return b''
                end=min(end,self.total-1)
            self.requests_count+=1
            with self.session.get(self.url,headers={'Range':f'bytes={start}-{end}'},timeout=(10,20),stream=True) as r:
                r.raise_for_status()
                m=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',r.headers.get('Content-Range',''))
                if r.status_code!=206 or not m or int(m[1])!=start:
                    raise OSError('El servidor no admite el rango COG solicitado; no se descarga la escena completa')
                self.total=int(m[3]);data=r.content
                if len(data)>262144 or len(data)!=int(m[2])-start+1:
                    raise OSError('Respuesta de rango incompleta o demasiado grande')
            if len(self.cache)>=24:self.cache.pop(next(iter(self.cache)))
            self.cache[idx]=data;return data
        def read(self,size=-1):
            if size<0:size=self.total-self.pos
            if size>8_000_000:raise OSError('Lectura mayor a 8 MB; el COG no permite una ventana acotada')
            result=[];remaining=min(size,max(0,self.total-self.pos))
            while remaining:
                ix=self.pos//262144;offset=self.pos%262144;b=self._bloque(ix)
                chunk=b[offset:offset+remaining]
                if not chunk:break
                result.append(chunk);self.pos+=len(chunk);remaining-=len(chunk)
            return b''.join(result)
        def readinto(self,b):
            data=self.read(len(b));b[:len(data)]=data;return len(data)
        def close(self):
            self.session.close();super().close()
    if mode not in ('r','rb'):raise ValueError('Sólo lectura')
    if not str(url).endswith('.tif'):raise FileNotFoundError(url)
    return ArchivoHTTP(url)


@st.cache_data(ttl=86400, show_spinner=False, max_entries=20)
def procesar_escena(item,lat,lon):
    import os,certifi
    import rasterio
    from rasterio.warp import transform,transform_bounds
    from rasterio.vrt import WarpedVRT
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    from rasterio.io import MemoryFile
    try:
        epsg=item['properties'].get('proj:epsg')
        if epsg is None:
            raise ValueError('La escena no declara su sistema de coordenadas')
        x,y=transform('EPSG:4326',f'EPSG:{epsg}',[lon],[lat]);x,y=x[0],y[0]
        # Un entorno de 2 x 2 km, a 20 m. Se leen ventanas COG, no la escena completa.
        trans=from_origin(x-1000,y+1000,20,20);shape=(100,100)
        bandas={};scl=None
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR',CPL_VSIL_CURL_ALLOWED_EXTENSIONS='.tif',
                          GDAL_HTTP_TIMEOUT='12',GDAL_HTTP_MAX_RETRY='0',VSI_CACHE=False,
                          CURL_CA_BUNDLE=os.environ.get('SSL_CERT_FILE') or certifi.where()):
            for nombre in ['green','red','nir','swir16','scl']:
                asset=item['assets'][nombre];url=asset['href'];parsed=urlparse(url)
                if parsed.scheme!='https' or parsed.hostname!='e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com':
                    raise ValueError('La banda no pertenece al archivo público Sentinel Collection 1')
                meta=(asset.get('raster:bands') or [{}])[0]
                with rasterio.open(url,opener=abrir_cog_por_rangos) as ds:
                    with WarpedVRT(ds,crs=f'EPSG:{epsg}',transform=trans,width=shape[1],height=shape[0],
                                   resampling=Resampling.nearest if nombre=='scl' else Resampling.bilinear) as vrt:
                        a=vrt.read(1,masked=True).astype(np.float32).filled(np.nan)
                if nombre=='scl':
                    scl=a
                else:
                    if 'scale' not in meta or 'offset' not in meta:
                        raise ValueError('Faltan escala/offset de reflectancia en los metadatos STAC')
                    a=a*float(meta['scale'])+float(meta['offset'])
                    bandas[nombre]=a
        idx=indices_desde_bandas(bandas,scl);coverage=float(idx['valido'].mean())
        if coverage<.2:
            raise ValueError(f'Sólo {coverage:.0%} del entorno es válido; nubes/sombras impiden interpretar agua superficial')
        rgba=np.zeros((*shape,4),dtype=np.uint8)
        rgba[idx['valido']]=[241,196,15,55];rgba[idx['agua']]=[14,116,230,210]
        bio=BytesIO();Image.fromarray(rgba).save(bio,format='PNG')
        west,south,east,north=transform_bounds(f'EPSG:{epsg}','EPSG:4326',x-1000,y-1000,x+1000,y+1000)
        with MemoryFile() as mf:
            with mf.open(driver='GTiff',height=100,width=100,count=3,dtype='float32',crs=f'EPSG:{epsg}',
                         transform=trans,nodata=np.nan,compress='deflate') as dst:
                for i,key in enumerate(['NDWI','NDVI','MNDWI'],1):
                    dst.write(idx[key],i);dst.set_band_description(i,key)
            geotiff=mf.read()
        fuente=STAC_URL+'/collections/'+item['collection']+'/items/'+item['id']
        obs={'fecha':fecha_fuente(item['properties']['datetime']),'fuente':fuente,'lat':lat,'lon':lon,
             'NDWI':float(np.nanmedian(idx['NDWI'])),'NDVI':float(np.nanmedian(idx['NDVI'])),
             'MNDWI':float(np.nanmedian(idx['MNDWI'])),'fraccion_agua':float(idx['agua'].sum()/idx['valido'].sum()),
             'cobertura_valida':coverage,'escena':item['id'],'metodo':'Sentinel-2 L2A C1; SCL 4/5/6; índices a 20 m, entorno 2×2 km'}
        return {'observacion':obs,'png':bio.getvalue(),'bounds':[[south,west],[north,east]],
                'geotiff':geotiff,'error':''}
    except Exception as exc:
        return {'error':str(exc) or type(exc).__name__}


def mostrar_satelite_automatico():
    with st.expander('Obtener NDWI / NDVI / MNDWI desde Sentinel-2'):
        nodo=st.selectbox('Localidad del análisis satelital',range(len(NODOS)),
            format_func=lambda i:NODOS[i]['localidad'],key='ficha_sat_nodo')
        n=NODOS[nodo];nombre=normalizar_nombre(n['localidad'])
        st.caption('Se busca una imagen reciente y se leen bandas de reflectancia y la máscara de calidad. Una imagen histórica conserva su fecha y no se interpreta como condición actual.')
        hasta=st.date_input('Último día de búsqueda',value=ahora().date(),max_value=ahora().date(),key='ficha_sat_hasta')
        if st.button('Buscar escenas satelitales',key='ficha_sat_buscar'):
            res=buscar_escenas(n['lat'],n['lon'],hasta-timedelta(days=30),hasta)
            st.session_state['ficha_sat_catalogo']=(nombre,hasta,res)
        cat=st.session_state.get('ficha_sat_catalogo')
        if cat and cat[:2]==(nombre,hasta):
            res=cat[2]
            if res['error']:
                st.warning(res['error'])
            elif not res['items']:
                st.info('No hay una escena recuperable con nubosidad de escena ≤ 80 % en esos 30 días.')
            else:
                i=st.selectbox('Escena y nubosidad general',range(len(res['items'])),
                    format_func=lambda j:res['items'][j]['properties']['datetime'][:10]+' · '+str(round(res['items'][j]['properties'].get('eo:cloud_cover',100)))+' % nubes',key='ficha_sat_escena')
                if st.button('Calcular e integrar índices de esta escena',key='ficha_sat_procesar'):
                    with st.spinner('Leyendo ventanas de bandas y descartando nubes…'):
                        out=procesar_escena(res['items'][i],n['lat'],n['lon'])
                    if out.get('error'):
                        st.warning(out['error'])
                    else:
                        out['observacion']['localidad']=n['localidad']
                        st.session_state.setdefault('ficha_satelites',{})[nombre]=out
                        st.rerun()
        out=st.session_state.get('ficha_satelites',{}).get(nombre)
        if out:
            o=out['observacion'];a,b,c=st.columns(3)
            a.metric('Agua detectada en píxeles válidos',f'{o["fraccion_agua"]:.1%}')
            b.metric('Cobertura válida del entorno',f'{o["cobertura_valida"]:.0%}');c.metric('NDVI mediano',f'{o["NDVI"]:.2f}')
            st.caption(f'Adquisición: {o["fecha"]} · {o["escena"]}. Azul: NDWI y MNDWI > 0; no equivale a inundación dañina ni separa agua permanente.')
            st.download_button('Exportar índices georreferenciados para QGIS',out['geotiff'],'Sentinel_indices.tif','image/tiff',key='ficha_sat_geotiff')
            st.link_button('Metadatos y fuente de bandas',o['fuente'])
            if not dato_vigente(o['fecha'],72):
                st.warning('Adquisición de más de 72 horas: queda en el mapa como antecedente y no eleva el semáforo actual.')
            if st.button('Quitar escena de la fusión',key='ficha_sat_quitar'):
                st.session_state['ficha_satelites'].pop(nombre,None);st.rerun()
        st.caption('La cobertura se calcula después de la máscara local, no sólo con la nubosidad de la escena. La detección necesita contraste y no certifica un acceso transitable.')
        st.link_button('Método y escala/offset del catálogo','https://github.com/Element84/earth-search/blob/main/docs/collections/sentinel-2-l2a.md')


_mostrar_inta_original=mostrar_inta_ficha


def mostrar_inta_ficha():
    _mostrar_inta_original()
    mostrar_raster_inta()
    mostrar_satelite_automatico()


_documento_territorio_original=documento_territorio


def documento_territorio(nodo,relieve,recursos,rutas):
    doc=_documento_territorio_original(nodo,relieve,recursos,rutas)
    sat=st.session_state.get('ficha_satelites',{}).get(normalizar_nombre(nodo['localidad']))
    if sat and not sat.get('error'):
        png='data:image/png;base64,'+base64.b64encode(sat['png']).decode()
        data=json.dumps({'png':png,'bounds':sat['bounds'],'fecha':str(sat['observacion']['fecha'])}).replace('<','\\u003c')
        script="const SAT="+data+";const capaSAT=L.imageOverlay(SAT.png,SAT.bounds,{opacity:.8,interactive:true}).addTo(M);capaSAT.bindPopup('Sentinel-2 · '+SAT.fecha+' · agua detectada, con máscara de nubes');L.control.layers({}, {'Agua superficial · Sentinel-2':capaSAT}).addTo(M);"
        doc=doc.replace('setTimeout(()=>M.invalidateSize(),0);',script+'setTimeout(()=>M.invalidateSize(),0);')
    return doc


def distancia_segmentos_m(a,b,c,d):
    # Proyección equirectangular local para tramos cortos del Litoral; distancia de planificación.
    lat=np.radians((a[1]+b[1]+c[1]+d[1])/4);f=111320
    pts=[np.array([p[0]*f*np.cos(lat),p[1]*f],dtype=float) for p in (a,b,c,d)]
    a,b,c,d=pts
    def cross(u,v):
        return u[0]*v[1]-u[1]*v[0]
    def punto_seg(p,x,y):
        z=y-x;den=float(np.dot(z,z))
        t=float(np.clip(np.dot(p-x,z)/den,0,1)) if den>0 else 0
        return float(np.linalg.norm(p-x-t*z))
    ab=b-a;cd=d-c;den=cross(ab,cd)
    if abs(den)>1e-9:
        t=cross(c-a,cd)/den;u=cross(c-a,ab)/den
        if 0<=t<=1 and 0<=u<=1:
            return 0.0
    return min(punto_seg(a,c,d),punto_seg(b,c,d),punto_seg(c,a,b),punto_seg(d,a,b))


def evaluar_recorrido(geometry,reportes):
    if geometry.get('type')!='LineString':
        raise ValueError('El recorrido debe ser LineString')
    coords=geometry['coordinates'];cortes=[];revisar=[]
    for feature in reportes.get('features',[]):
        p=feature['properties'];g=feature['geometry']
        vigente=dato_vigente(p['fecha_verificacion'],24) and fecha_fuente(p['valido_hasta'])>=ahora()
        if p['estado']=='habilitado' and vigente:
            continue
        ruta=g['coordinates']
        for a,b in zip(coords,coords[1:]):
            if any(distancia_segmentos_m(a,b,c,d)<=50 for c,d in zip(ruta,ruta[1:])):
                (cortes if p['estado']=='cerrado' and vigente else revisar).append(p['nombre'])
                break
    return {'cortes':sorted(set(cortes)),'reconfirmar':sorted(set(revisar)),
            'estado':'INTERSECTA CORTE VIGENTE' if cortes else 'RECONFIRMAR REPORTES' if revisar else 'SIN CORTES GEORREFERENCIADOS CONOCIDOS'}


def recursos_a_geojson(df):
    return {'type':'FeatureCollection','features':[{'type':'Feature','geometry':{'type':'Point','coordinates':[r['lon'],r['lat']]},
        'properties':json_finito({k:v for k,v in r.items() if k not in ('lat','lon')})} for r in df.to_dict('records')]}


def estado_evidencia_operativa(fila,referencia=None):
    """La fecha de consulta o una guardia publicada no son una confirmación local."""
    if fila.get('confirmacion_actual')!='si':
        return 'Publicado sin confirmación actual'
    def texto(campo):
        valor=fila.get(campo,'')
        return '' if valor is None or (not isinstance(valor,(list,dict)) and pd.isna(valor)) else str(valor).strip()
    if not texto('autoridad') or not re.fullmatch(r'https?://\S+',texto('fuente')):
        return 'Reconfirmar'
    try:
        valores=[pd.Timestamp(fila.get(c)) for c in ('fecha_verificacion','valido_hasta')]
        if any(pd.isna(t) or t.tzinfo is None for t in valores):
            return 'Reconfirmar'
        actual=fecha_fuente(referencia or ahora())
        verificado,hasta=[fecha_fuente(t) for t in valores]
        edad=(actual-verificado).total_seconds()/3600
        if not 0<=edad<=24 or hasta<actual or hasta<verificado:
            return 'Reconfirmar'
    except (TypeError,ValueError,OverflowError):
        return 'Reconfirmar'
    if fila.get('estado')=='cerrado':
        return 'Cierre vigente'
    if fila.get('estado')!='habilitado' or fila.get('acceso_verificado')!='si':
        return 'Reconfirmar'
    if fila.get('tipo')=='refugio':
        capacidades=pd.to_numeric(pd.Series([fila.get('capacidad_personas'),fila.get('capacidad_animales')]),errors='coerce')
        if not (np.isfinite(capacidades) & capacidades.gt(0) & capacidades.mod(1).eq(0)).any():
            return 'Reconfirmar'
    if fila.get('tipo')=='salud' and not (fila.get('atencion_verificada')=='si' and texto('horario_atencion') and texto('telefono')):
        return 'Reconfirmar'
    return 'Confirmación vigente'


def mostrar_revision_operativa_norte():
    carpeta=Path(__file__).resolve().parent/'assets'/'revision_operativa_norte'
    path=carpeta/'registros_documentados.json'
    if not path.exists():
        return
    revision=json.loads(path.read_text(encoding='utf-8'))
    registros=revision.get('registros',[])
    st.subheader('Caminos, refugios y salud · revisión del norte')
    st.caption('Revisión de fuentes: '+str(revision.get('fecha_revision',''))+'. El estado publicado conserva su fecha; apertura, cupos y accesos actuales requieren confirmación local.')
    provincias=['Todas','Chaco','Formosa','Misiones']
    provincia=st.selectbox('Provincia de la revisión operativa',provincias,key='ficha_revision_operativa_provincia')
    filas=[r for r in registros if provincia=='Todas' or r.get('provincia')==provincia]
    vista=pd.DataFrame([{
        'Provincia':r.get('provincia'),'Tipo':r.get('tipo'),'Recurso o tramo':r.get('nombre'),
        'Publicado':r.get('estado_publicado'),'Fecha del parte':r.get('fecha_publicacion') or 'Sin fecha publicada',
        'Disponibilidad actual':estado_evidencia_operativa(r),'Contacto':r.get('telefono') or 'No publicado',
        'Horario publicado':r.get('horario_atencion') or 'No publicado',
        'Domicilio o zona':r.get('domicilio') or r.get('localidad',''),
        'Fuente':r.get('fuente'),
    } for r in filas])
    if not vista.empty:
        st.dataframe(vista,use_container_width=True,hide_index=True,
                     column_config={'Fuente':st.column_config.LinkColumn('Fuente oficial',display_text='Abrir')})
    vigentes=sum(estado_evidencia_operativa(r)=='Confirmación vigente' for r in filas)
    a,b=st.columns(2)
    a.metric('Referencias documentadas',len(filas))
    b.metric('Disponibilidad confirmada ahora',vigentes)
    if vigentes==0:
        st.warning('No hay una confirmación operativa vigente en los registros revisados. Los teléfonos, horarios y antecedentes facilitan la consulta; no acreditan camas libres, refugios abiertos ni paso por los caminos.')
    st.download_button('Descargar revisión de caminos, refugios y salud',json.dumps(revision,ensure_ascii=False,indent=2),
                       'revision_operativa_norte.json','application/json',key='ficha_revision_operativa_norte_json')
    acta={
        'fecha_revision_documental':revision.get('fecha_revision'),
        'instruccion':'Completar con autoridad local. No usar la fecha de descarga como fecha de verificación. No completar cupos con ocupantes históricos.',
        'registros':[dict(r,confirmacion_actual='no',fecha_verificacion=None,valido_hasta=None,
                         estado='sin_confirmar',acceso_verificado='sin_confirmar',atencion_verificada='sin_confirmar',
                         capacidad_personas=None,capacidad_animales=None,suero_antiofidico='sin_confirmar',referente=None)
                     for r in filas],
    }
    st.download_button('Descargar acta para confirmación local',json.dumps(acta,ensure_ascii=False,indent=2),
                       'acta_confirmacion_local.json','application/json',key='ficha_acta_confirmacion_local')
    st.caption('Para integrar una confirmación al mapa, cargá el CSV territorial o el GeoJSON vial de esta pestaña: coordenadas/trazado, autoridad, evidencia, hora de verificación, vigencia y acceso; refugios además capacidad y salud además atención, horario y teléfono.')
    for tipo in ('camino','refugio','salud'):
        seleccion=[r for r in filas if r.get('tipo')==tipo]
        if seleccion:
            with st.expander('Detalle y límites de la evidencia · '+tipo):
                st.dataframe(pd.DataFrame([{'Recurso':r['nombre'],'Estado publicado':r.get('estado_publicado'),
                                            'Límite de uso':r.get('limitacion'),'Fuente':r.get('fuente')}
                                           for r in seleccion]),use_container_width=True,hide_index=True,
                             column_config={'Fuente':st.column_config.LinkColumn('Fuente',display_text='Abrir')})


def mostrar_registros_operativos():
    with st.expander('Conectar registros oficiales de refugios y caminos provinciales/rurales'):
        st.write('**Fuentes y registros locales incorporados**')
        st.dataframe(pd.DataFrame(REGISTROS_LOCALES_FUENTES), use_container_width=True, hide_index=True, column_config={'url': st.column_config.LinkColumn('Fuente', display_text='Abrir')})
        st.caption(f'Revisión documental: {FECHA_REVISION_EVIDENCIA}. Ubicación de catálogo y portal disponible no equivalen a habilitación actual, transitabilidad ni stock sanitario.')
        mostrar_revision_operativa_norte()
        carpeta_salud=Path(__file__).resolve().parent/'assets'/'catalogos_sanitarios'
        revision_salud=carpeta_salud/'revision_descargas.json'
        if revision_salud.exists():
            try:
                registros_salud=json.loads(revision_salud.read_text(encoding='utf-8'))
                st.write('**Catálogos sanitarios conservados · descarga del 10/10/2026**')
                st.dataframe(pd.DataFrame([{
                    'Catálogo':r['archivo'],'Descarga':r['fecha_descarga'],
                    'Registros legibles':r['registros_parseados'],'Filas excluidas':r['filas_rechazadas'],
                    'Estado operativo':'Sin confirmar','Fuente':r['fuente'],
                } for r in registros_salud]),use_container_width=True,hide_index=True,
                    column_config={'Fuente':st.column_config.LinkColumn('Fuente',display_text='Abrir')})
                for r in registros_salud:
                    archivo=carpeta_salud/r['archivo']
                    if archivo.is_file():
                        st.download_button('Descargar original · '+r['archivo'],archivo.read_bytes(),r['archivo'],'text/csv',key='ficha_salud_original_'+r['archivo'])
                st.caption('El CSV de centros contiene una fila inconsistente, excluida de la vista y conservada en el original. Los registros legibles se muestran en «Catálogos sanitarios oficiales de Entre Ríos», en esta pestaña. No se informó la fecha del relevamiento original.')
            except (OSError,ValueError,KeyError,TypeError) as exc:
                st.warning('No se pudo leer la revisión conservada de catálogos: '+str(exc))
        st.warning('Pendiente de confirmación local: caminos provinciales/rurales, refugios abiertos, capacidad disponible y atención sanitaria. Las consultas de catálogo comprueban descarga, no operación. No se obtuvo un registro operativo vigente y completo para las seis provincias.')
        st.dataframe(pd.DataFrame([
            {'Autoridad':'Vialidad Santa Fe','Contacto publicado':'0342 4573962 al 3966 · privadadpv@santafe.gob.ar','Fuente':'https://www.santafe.gov.ar/index.php/web/content/view/full/239909','Alcance':'Contacto oficial; no se realizó confirmación telefónica de tramos'},
            {'Autoridad':'Vialidad Entre Ríos','Contacto publicado':'Formulario y datos del portal oficial','Fuente':'https://www.dpver.gov.ar/contacto/','Alcance':'Contacto oficial; no certifica caminos habilitados'},
            {'Autoridad':'Municipalidad de Corrientes','Contacto publicado':'147','Fuente':CONTROLES_NEGATIVOS_DOCUMENTADOS[0]['fuente'],'Alcance':'Avisos vecinales; no reemplaza Vialidad ni atención de emergencias sanitarias'},
            {'Autoridad':'Defensa Civil Chaco','Contacto publicado':'(0362) 444-8000 interno 103','Fuente':'https://mapadelestado.chaco.gob.ar/dependencia/ver/279','Alcance':'Contacto institucional publicado; no se realizó llamada ni confirmación de refugios'},
            {'Autoridad':'Protección Civil Misiones','Contacto publicado':'103 · (0376) 4427544; Defensa Civil (0376) 444-7636','Fuente':'https://gobierno.misiones.gob.ar/guia-de-autoridades/','Alcance':'Contactos publicados; no se comprobó recepción ni disponibilidad de recursos'},
            {'Autoridad':'Defensa Civil Formosa','Contacto publicado':'Consultar organismo provincial','Fuente':'https://m.formosa.gob.ar/defensacivil/historia','Alcance':'No se confirmó un contacto operativo vigente ni refugios activos'},
        ]),use_container_width=True,hide_index=True,column_config={'Fuente':st.column_config.LinkColumn('Fuente',display_text='Abrir')})
        if st.button('🔎 Verificar catálogos sanitarios oficiales ahora', key='ficha_registros_verificar_salud'):
            consultar_salud_entre_rios.clear()
            catalogos, errores = consultar_salud_entre_rios()
            st.session_state['ficha_verificacion_salud'] = {'fecha': ahora().isoformat(), 'catalogos': {k: len(v) for k,v in catalogos.items()}, 'errores': errores}
        verificacion = st.session_state.get('ficha_verificacion_salud')
        if verificacion:
            st.write('**Última verificación de catálogos sanitarios:**', verificacion['fecha'])
            st.dataframe(pd.DataFrame([{'catálogo': k, 'registros': verificacion['catalogos'].get(k), 'estado': verificacion['errores'].get(k,'descarga correcta')} for k in dict.fromkeys([*verificacion['catalogos'],*verificacion['errores']])]), use_container_width=True, hide_index=True)
            st.caption('La descarga confirma que el catálogo responde; no confirma que cada efector esté abierto, tenga camas libres, atienda guardia o disponga de antiofídico en el momento de la emergencia.')
        st.write('Conectá un registro publicado por municipio, Defensa Civil o Vialidad. Los registros conservan autoridad, evidencia, fecha de verificación y vigencia. Sin esos datos no se marca una instalación o camino como habilitado.')
        tipo=st.radio('Registro público', ['Refugios y salud · CSV','Cortes y caminos · GeoJSON'],key='ficha_reg_tipo',horizontal=True)
        url=st.text_input('URL HTTPS del registro oficial',key='ficha_reg_url',placeholder='https://…gob.ar/registro.csv')
        if st.button('Consultar e integrar registro oficial',key='ficha_reg_conectar',disabled=not url):
            try:
                if tipo.startswith('Refugios'):
                    d=validar_recursos(descargar_registro(url,'CSV'))
                    st.session_state['ficha_recursos_conectados']=d
                else:
                    d=validar_rutas_geojson(descargar_registro(url,'GeoJSON'))
                    st.session_state['ficha_rutas_conectadas']=d
                st.session_state['ficha_reg_origen']=url;st.rerun()
            except Exception as exc:
                st.error('Registro no integrado: '+str(exc))
        recursos=st.session_state.get('ficha_recursos_conectados',pd.DataFrame())
        if not recursos.empty:
            recursos=validar_recursos(recursos)
        rutas=st.session_state.get('ficha_rutas_conectadas',{'type':'FeatureCollection','features':[]})
        if rutas['features']:
            rutas=validar_rutas_geojson(rutas)
        a,b=st.columns(2);a.metric('Recursos con registro conectado',len(recursos));b.metric('Tramos con registro conectado',len(rutas['features']))
        if not recursos.empty:
            st.dataframe(recursos,use_container_width=True,hide_index=True)
            st.download_button('Exportar recursos para QGIS',json.dumps(recursos_a_geojson(recursos),ensure_ascii=False,default=str),'recursos.geojson','application/geo+json',key='ficha_rec_geojson')
        if rutas['features']:
            st.download_button('Exportar caminos y cortes para QGIS',json.dumps(json_finito(rutas),ensure_ascii=False,default=str),'caminos_oficiales.geojson','application/geo+json',key='ficha_caminos_geojson')
        for nombre,enlace in [('Vialidad Santa Fe','https://www.santafe.gov.ar/index.php/web/content/view/full/239909'),
            ('Vialidad Corrientes','https://vialidad.corrientes.gob.ar/'),
            ('Vialidad Entre Ríos','https://www.dpver.gov.ar/'),
            ('Vialidad Chaco · SIGVIAL','https://vialidadchaco.com.ar/sigvial'),
            ('Vialidad Formosa','https://www.formosa.gob.ar/vialidad'),
            ('Vialidad Misiones','https://www.dpv.misiones.gov.ar/contacto.php')]:
            st.link_button(nombre,enlace)
        st.caption('Estos portales no proporcionan en esta versión una API uniforme de cortes vigentes. El conector acepta registros oficiales publicados en el formato de las plantillas y permite cargar relevamientos locales en las secciones siguientes.')


def mostrar_revision_recorrido(nodo,recursos,reportes):
    with st.expander('Cruzar un recorrido con los cortes georreferenciados'):
        if not recursos:
            st.info('Cargá un destino con coordenadas para revisar un recorrido.');return
        dest=st.selectbox('Destino a revisar',range(len(recursos)),format_func=lambda i:recursos[i]['nombre'],key='ficha_cruce_destino')
        if st.button('Calcular y revisar recorrido',key='ficha_cruce_calcular'):
            p=recursos[dest]
            rr=recorrido_orientativo(nodo['lat'],nodo['lon'],float(p['lat']),float(p['lon']))
            if rr.get('error'):
                st.warning(rr['error'])
            else:
                ev=evaluar_recorrido(rr['geometry'],reportes)
                st.session_state['ficha_recorrido_revisado']={'origen':nodo['localidad'],'destino':p['nombre'],
                    'geometry':rr['geometry'],'distancia_km':rr['distancia_km'],'revision':ev,'fecha':ahora().isoformat(),
                    'cantidad_reportes':len(reportes.get('features',[])),
                    'limitacion':'Un trazado sin intersecciones conocidas no certifica transitabilidad ni sirve como ruta de evacuación confirmada.'}
                if ev['cortes']:
                    st.error('Recorrido descartado: intersecta cortes vigentes. '+', '.join(ev['cortes']))
                elif ev['reconfirmar']:
                    st.warning('Necesita reconfirmación: '+', '.join(ev['reconfirmar']))
                else:
                    st.info('Sin intersecciones con los reportes cargados. Cobertura '+str(len(reportes.get('features',[])))+' tramos; falta confirmar el recorrido completo en terreno.')
        d=st.session_state.get('ficha_recorrido_revisado')
        if d and d['origen']==nodo['localidad'] and d['destino']==recursos[dest]['nombre']:
            st.write(d['revision']['estado'])
            st.download_button('Descargar revisión del recorrido',json.dumps(json_finito(d),ensure_ascii=False,indent=2),'revision_recorrido.json','application/json',key='ficha_cruce_export')
        st.caption('Cruce geométrico con corredor de 50 m. Sólo los cortes con verificación de ≤ 24 h y vigencia declarada descartan el recorrido. Los reportes atrasados obligan a reconfirmar; no se generan desvíos seguros automáticamente.')


def cobertura_calibracion_provincial(train,test):
    """No extender umbrales locales a provincias sin ambas clases en ajuste y prueba."""
    faltantes=[]
    conjuntos={}
    for nombre,d in [('entrenamiento',train),('prueba',test)]:
        conjuntos[nombre]=sorted(set(d.get('provincia',pd.Series(dtype=str)).dropna().astype(str)) & set(PROVINCIAS_LITORAL))
        for provincia in PROVINCIAS_LITORAL:
            filas=d[d.provincia.eq(provincia)] if 'provincia' in d else d.iloc[:0]
            clases=set(pd.to_numeric(filas.get('anegamiento',pd.Series(dtype=float)),errors='coerce').dropna())
            if clases!={0,1}:
                faltantes.append(f'{provincia} · {nombre}: faltan episodios positivos o controles negativos')
    return {'completa':not faltantes,'provincias_entrenamiento':conjuntos['entrenamiento'],
            'provincias_prueba':conjuntos['prueba'],'faltantes':faltantes}


def evaluar_umbral_historico(df):
    cols={'evento_id','fecha_evento','emision','indice','anegamiento','fuente'}
    if not cols.issubset(df.columns):
        raise ValueError('Faltan columnas: '+', '.join(sorted(cols-set(df.columns))))
    d=df.copy()
    if d.evento_id.astype(str).str.strip().eq('').any() or d.evento_id.duplicated().any():
        raise ValueError('Una fila por episodio independiente, con ID único')
    if not d.fecha_evento.astype(str).str.match(r'^\d{4}-\d{2}-\d{2}(?:$|T)').all():
        raise ValueError('Cada episodio necesita fecha puntual; un mes no se convierte en su primer día')
    if 'tipo_fecha' in d and d.tipo_fecha.eq('fecha_documentacion_evento_en_curso').any():
        raise ValueError('La fecha de documentación de un evento en curso no fija su inicio. Documentá la ventana del impacto antes de calibrar.')
    trazabilidad = {'grupo_evento','tipo_emision','disponibilidad_confirmada','fuente_pronostico'}
    if not trazabilidad.issubset(d.columns):
        raise ValueError('Falta trazabilidad de emisión: '+', '.join(sorted(trazabilidad-set(d.columns))))
    if d.grupo_evento.fillna('').astype(str).str.strip().eq('').any() or d.grupo_evento.duplicated().any():
        raise ValueError('Una fila por grupo de episodio independiente; no contar varias corridas del mismo evento')
    if not d.tipo_emision.isin(['emision_operativa','corte_reconstruido']).all():
        raise ValueError('tipo_emision: emision_operativa o corte_reconstruido')
    if not d.disponibilidad_confirmada.isin(['si','no']).all():
        raise ValueError('disponibilidad_confirmada: si/no con evidencia archivada')
    if not d.fuente_pronostico.astype(str).str.match(r'^https?://\S+$').all():
        raise ValueError('Cada índice necesita una fuente archivada del pronóstico')
    reconstruidos = d.tipo_emision.eq('corte_reconstruido')
    if reconstruidos.any():
        if 'inicializacion_utc' not in d:
            raise ValueError('Los cortes reconstruidos necesitan la inicialización UTC conservada')
        for valor in d.loc[reconstruidos,'inicializacion_utc']:
            inicializacion_historica(valor)
        if d.loc[reconstruidos,'disponibilidad_confirmada'].ne('no').any():
            raise ValueError('Un corte reconstruido no certifica disponibilidad operativa original')
    operativas = bool((d.tipo_emision.eq('emision_operativa') & d.disponibilidad_confirmada.eq('si')).all())
    if len(d)<10:
        raise ValueError('Se requieren al menos 10 registros independientes: episodios positivos y controles con ausencia documentada')
    if 'tipo_registro' in d:
        problemas=revisar_completitud_calibracion(d)
        if problemas: raise ValueError('Datos incompletos: '+' '.join(problemas))
        episodios=int((d.tipo_registro=='episodio_real').sum()); controles=int((d.tipo_registro=='control_negativo').sum())
    else:
        episodios=controles=None
    if any(pd.Timestamp(valor).tzinfo is None for valor in d.emision):
        raise ValueError('Cada emisión necesita fecha, hora y zona horaria explícita')
    d['fecha_evento']=d.fecha_evento.map(fecha_fuente);d['emision']=d.emision.map(fecha_fuente)
    d['indice']=pd.to_numeric(d.indice,errors='coerce');d['anegamiento']=pd.to_numeric(d.anegamiento,errors='coerce')
    if not d.indice.between(0,100).all() or not d.anegamiento.isin([0,1]).all():
        raise ValueError('Índice 0–100 y anegamiento observado 0/1 son obligatorios')
    if not d.fuente.astype(str).str.match(r'^https?://\S+$').all():
        raise ValueError('Cada episodio y control necesita URL de evidencia; no se infiere ausencia a partir de silencio en las noticias')
    if ((d.fecha_evento-d.emision).dt.total_seconds()<6*3600).any():
        raise ValueError('El índice necesita información disponible al menos 6 horas antes del evento')
    fuentes=['fuente_smn','fuente_ina','fuente_inta']
    contrastes = d[fuentes].apply(lambda fila: fila.astype(str).str.match(r'^https?://\S+$').all(), axis=1) if all(c in d for c in fuentes) else pd.Series(False, index=d.index)
    positivos = d.anegamiento.eq(1)
    multif = bool((contrastes & positivos).any())
    d=d.sort_values('fecha_evento').reset_index(drop=True);cut=int(len(d)*.7)
    train,test=d.iloc[:cut],d.iloc[cut:]
    if train.fecha_evento.max()>=test.fecha_evento.min() or train.fecha_evento.max()>=test.emision.min():
        raise ValueError('El ajuste debe finalizar antes de las emisiones del tramo de prueba')
    if train.anegamiento.nunique()<2 or test.anegamiento.nunique()<2:
        raise ValueError('Ajuste y prueba necesitan eventos y controles con ausencia documentada')
    opciones=[]
    for rojo in range(20,86):
        m=metricas_clasificacion(train.anegamiento,train.indice>=rojo)
        opciones.append((m['F1'],m['sensibilidad'] or 0,-abs(rojo-55),rojo))
    umbral_rojo=max(opciones)[-1]
    avisos=[]
    for amarillo in range(5,umbral_rojo):
        m=metricas_clasificacion(train.anegamiento,train.indice>=amarillo)
        if (m['sensibilidad'] or 0)>=.9: avisos.append((m['especificidad'] or 0,-abs(amarillo-35),amarillo))
    umbral_amarillo=max(avisos)[-1] if avisos else max(5,umbral_rojo-15)
    salida={'umbral_amarillo':umbral_amarillo,'umbral_rojo':umbral_rojo,'n_entrenamiento':len(train),'n_prueba':len(test),
            'criterio':'Rojo: F1 de ajuste. Amarillo: sensibilidad de ajuste ≥ 90 %, priorizando especificidad. Prueba posterior sin reajustar.',
            'entrenamiento_hasta':train.fecha_evento.max().isoformat(),'prueba_desde':test.fecha_evento.min().isoformat(),
            'metricas_prueba':metricas_clasificacion(test.anegamiento,test.indice>=umbral_rojo),
            'metricas_vigilancia':metricas_clasificacion(test.anegamiento,test.indice>=umbral_amarillo),
            'contraste_multifuente':multif,'emisiones_operativas_verificadas':operativas,
            'episodios_positivos':episodios,'controles_negativos':controles,
            'cobertura_provincial':cobertura_calibracion_provincial(train,test),
            'modelo':VERSION,'estado':'Ajuste exploratorio local; no validación institucional ni extrapolación a todo el Litoral'}
    test=test.copy();test['semaforo_evaluado']=np.select([test.indice>=umbral_rojo,test.indice>=umbral_amarillo],['ROJO','AMARILLO'],default='VERDE')
    test['prediccion_rojo']=(test.indice>=umbral_rojo).astype(int)
    return salida,test


def mostrar_calibracion_completa():
    st.subheader('Calibración documentada y prueba temporal')
    st.write('El caso de abril de 2016 reúne SMN, INA e INTA. Permite contrastar el episodio y adoptar las referencias fluviales de la estación. Ajustar la precisión del índice meteorológico requiere episodios independientes y controles: las imágenes posteriores al evento no se usan como predictores anticipados.')
    st.subheader('Episodios reales documentados')
    casos_reales = pd.DataFrame(episodios_documentados())
    st.dataframe(casos_reales, use_container_width=True, hide_index=True, column_config={'fuente_principal': st.column_config.LinkColumn('Fuente principal', display_text='Abrir')})
    st.download_button('Descargar catálogo de episodios reales', casos_reales.to_csv(index=False).encode('utf-8-sig'), 'episodios_reales_litoral.csv', 'text/csv', key='ficha_casos_reales_csv')
    controles = pd.DataFrame(controles_documentados())
    st.subheader(f'{len(controles)} controles negativos con ausencia explícita')
    st.info('Conservan etiqueta 0, autoridad, fecha del parte y alcance territorial. Se usan en métricas sólo cuando exista una corrida completa anterior a la ventana observada.')
    st.dataframe(controles[['evento_id','fecha_evento','localidad','anegamiento','autoridad','alcance_espacial','alcance_temporal','observacion_etiqueta','fecha_evidencia','fuente']], use_container_width=True, hide_index=True, column_config={'fuente': st.column_config.LinkColumn('Fuente oficial', display_text='Abrir')})
    st.download_button('Descargar controles negativos documentados', controles.to_csv(index=False).encode('utf-8-sig'), 'controles_negativos_litoral.csv', 'text/csv', key='ficha_controles_negativos_csv')
    st.info('Estos controles no se deducen de la falta de noticias: cada fila contiene una declaración oficial de ausencia de anegamiento/inundación. La etiqueta no afirma ausencia de árboles caídos, cortes u otros daños.')

    st.subheader('Vinculación de índices históricos y emisiones')
    vinculos = registro_vinculacion_indices_historicos()
    st.dataframe(vinculos, use_container_width=True, hide_index=True, column_config={'fuente_pronostico': st.column_config.LinkColumn('Fuente de pronóstico', display_text='Abrir')})
    st.download_button('Descargar registro de índices y emisiones', vinculos.to_csv(index=False).encode('utf-8-sig'), 'vinculacion_indices_historicos_litoral.csv', 'text/csv', key='ficha_vinculos_historicos_csv')
    st.caption('Cada consulta conserva estado, fuente y corrida. Los índices incompletos quedan vacíos: no se completa escorrentía con cero ni con reanálisis. Varios cortes de un episodio no son muestras independientes.')
    norte=Path(__file__).resolve().parent/'assets'/'validacion_norte'/'revision_historica.json'
    if norte.exists():
        revision=json.loads(norte.read_text(encoding='utf-8'))
        st.write('**Contraste histórico · Chaco, Formosa y Misiones**')
        st.dataframe(pd.DataFrame(revision.get('resumen',[])),use_container_width=True,hide_index=True)
        if revision.get('falsos_negativos_reconstruidos'):
            st.warning('Resistencia, diciembre de 2025: índice reconstruido completo 33,9 (verde), con anegamiento urbano documentado. Es un falso negativo de este contraste local. No valida los umbrales ni el desempeño provincial; las otras cinco consultas no producen un índice completo.')
        st.caption('Fecha del impacto, publicación del parte e inicialización meteorológica son campos distintos. Las crecidas fluviales se contrastan a escala de estación; la lluvia de una ciudad no representa toda la cuenca.')
        st.caption('El archivo Single Runs incluye hindcasts IFS49R1 para 14/03/2024–12/05/2026 06 UTC. La inicialización y el corte supuesto +6 h no prueban la emisión original ni su disponibilidad histórica.')
        st.download_button('Descargar revisión histórica del norte',json.dumps(revision,ensure_ascii=False,indent=2),
                           'revision_historica_norte.json','application/json',key='ficha_revision_norte_json')

    with st.expander('Vincular una corrida fija de Open-Meteo Single Runs'):
        st.caption('Inicialización UTC y corte supuesto +6 h se conservan por separado. El futuro proviene de una sola corrida; los antecedentes son una reconstrucción retrospectiva, no observaciones certificadas disponibles en esa emisión.')
        candidatos = episodios_documentados() + controles_documentados()
        ids = [c['evento_id'] for c in candidatos if punto_evento_historico(c['evento_id']) and str(c.get('fecha_evento') or c.get('inicio')) >= '2024-03-14']
        seleccionado = st.selectbox('Evento', ids, key='ficha_historico_evento')
        caso = next(c for c in candidatos if c['evento_id'] == seleccionado)
        sugerida = pd.Timestamp(caso.get('fecha_evento') or caso.get('inicio'))-pd.Timedelta(days=1)
        fecha_run = st.date_input('Fecha de inicialización UTC', value=sugerida.date(), min_value=datetime(2024,3,14).date(), max_value=ahora().date(), key='ficha_historico_fecha_'+seleccionado)
        ciclo = st.selectbox('Ciclo UTC', [0,6,12,18], format_func=lambda h: f'{h:02d}:00 UTC', key='ficha_historico_ciclo')
        metodo = st.radio('Origen del archivo', ['Consulta pública', 'Archivos JSON conservados'], horizontal=True, key='ficha_historico_metodo')
        archivos = None
        disponibles = True
        if metodo == 'Archivos JSON conservados':
            a = st.file_uploader('JSON de antecedentes horarios (ART)', type=['json'], key='ficha_historico_json_antecedentes')
            r = st.file_uploader('JSON de la corrida fija (ART)', type=['json'], key='ficha_historico_json_corrida')
            disponibles = a is not None and r is not None
            if disponibles:
                try:
                    archivos = {'antecedentes': json.load(a), 'corrida': json.load(r)}
                except (ValueError, TypeError):
                    disponibles = False
                    st.error('Uno de los archivos no contiene JSON válido.')
        if st.button('Calcular y vincular corrida', key='ficha_historico_consultar', disabled=not disponibles):
            try:
                inicializacion = pd.Timestamp(fecha_run, tz='UTC')+pd.Timedelta(hours=ciclo)
                originales = archivos or consultar_corrida_historica(seleccionado, inicializacion.isoformat())
                r = evaluar_vinculacion_historica(seleccionado, inicializacion.isoformat(), originales)
                st.session_state.setdefault('ficha_vinculos_sesion', []).append(r)
                st.session_state['ficha_corrida_original'] = {'registro': r, 'archivos': originales}
                st.success(f"Índice reconstruido: {r['indice']} · {r['nivel']} · corte {r['emision']}")
                st.warning('Reconstrucción archivada. No certifica una emisión operativa original ni completa la prueba temporal.')
            except Exception as exc:
                st.warning('No se pudo recuperar la corrida histórica: '+str(exc))
        if st.session_state.get('ficha_vinculos_sesion'):
            sesion = pd.DataFrame(st.session_state['ficha_vinculos_sesion']).drop_duplicates(subset=['evento_id','emision'])
            st.dataframe(sesion, use_container_width=True, hide_index=True)
            st.download_button('Descargar vinculaciones generadas en esta sesión', sesion.to_csv(index=False).encode('utf-8-sig'), 'vinculaciones_historicas_sesion.csv', 'text/csv', key='ficha_vinculos_sesion_csv')
        if st.session_state.get('ficha_corrida_original'):
            st.download_button('Descargar corrida, antecedentes y parámetros', json.dumps(json_finito(st.session_state['ficha_corrida_original']), ensure_ascii=False, allow_nan=False).encode('utf-8'), 'corrida_historica_trazable.json', 'application/json', key='ficha_corrida_original_json')
    est=CASO_CONCORDIA_2016['estacion']
    st.dataframe(pd.DataFrame([
        {'Variable':'Altura río Uruguay · Concordia','Amarillo':f'{est["nivel_de_alerta"]} m','Rojo':f'{est["nivel_de_evacuacion"]} m','Origen':'Umbrales oficiales de estación, catálogo INA; no umbrales ajustados con el modelo'},
        {'Variable':'Índice meteorológico 0–100','Amarillo':'35','Rojo':'55','Origen':'Valores experimentales de referencia; evaluación local pendiente'},
        {'Variable':'Suelo / agua superficial','Amarillo':'Agua útil ≥ 90 % y lluvia ≥ 30 mm/72 h','Rojo':'Agua detectada reciente + lluvia; o suelo húmedo + cota baja','Origen':'Reglas exploratorias de fusión; no calibradas por los dos casos'},
    ]),hide_index=True,use_container_width=True)
    st.caption('Los umbrales fluviales se aplican a la escala de su estación y a su área de referencia. No se convierten en cota segura de un campo ni en umbral de otro río.')
    st.write('**Evaluar ambos colores del índice con episodios independientes**')
    plantilla_df=plantilla_validacion_historica()
    st.download_button('Plantilla multifuente con episodios y controles',plantilla_df.to_csv(index=False).encode('utf-8-sig'),'eventos_validacion_litoral.csv','text/csv',key='ficha_cal_v43_template')
    planilla_completada_path=Path(__file__).resolve().parent/'assets'/'planilla_validacion_litoral_completada.csv'
    if planilla_completada_path.exists():
        planilla_completada=planilla_documental_actualizada()
        st.download_button('Descargar planilla documental completada',planilla_completada.to_csv(index=False).encode('utf-8-sig'),'planilla_validacion_litoral_completada.csv','text/csv',key='ficha_cal_v43_completed')
        st.caption('La planilla integra los episodios nuevos y conserva los valores anteriores. Las etiquetas observadas no sustituyen índices y emisiones archivados; los campos vacíos no significan ausencia ni riesgo bajo.')
    reconstruccion_vera_path=Path(__file__).resolve().parent/'assets'/'reconstruccion_vera_2025.csv'
    if reconstruccion_vera_path.exists():
        reconstruccion_vera=pd.read_csv(reconstruccion_vera_path,dtype=str).fillna('')
        st.download_button('Descargar reconstrucción de Vera 2025',reconstruccion_vera.to_csv(index=False).encode('utf-8-sig'),'reconstruccion_vera_2025.csv','text/csv',key='ficha_cal_v43_vera_reconstruction')
    st.caption('Completá emision, indice, anegamiento y fuentes de contraste. Un control negativo sólo es válido con ausencia documentada por autoridad o registro; no se deduce porque no haya una noticia.')
    archivo=st.file_uploader('CSV de validación histórica completado',type=['csv'],key='ficha_cal_v43_upload')
    if archivo:
        try:
            datos_cal=pd.read_csv(archivo,dtype=str).fillna('')
            problemas=revisar_completitud_calibracion(datos_cal) if 'tipo_registro' in datos_cal else []
            if problemas:
                st.warning('El archivo todavía no está listo para calcular métricas.')
                st.dataframe(pd.DataFrame({'Revisión':problemas}),hide_index=True,use_container_width=True)
            else:
                res,test=evaluar_umbral_historico(datos_cal)
                a,b=st.columns(2);a.metric('Umbral amarillo evaluado',res['umbral_amarillo']);b.metric('Umbral rojo evaluado',res['umbral_rojo'])
                st.dataframe(pd.DataFrame([{'Decisión':'Rojo',**res['metricas_prueba']},{'Decisión':'Vigilancia amarillo/rojo',**res['metricas_vigilancia']}]),hide_index=True)
                st.dataframe(test,hide_index=True,use_container_width=True)
                st.warning(f'Muestra pequeña: {res["n_entrenamiento"]} episodios de ajuste y {res["n_prueba"]} de prueba. Estas métricas describen únicamente esta muestra; revisar representatividad y nuevas temporadas.')
                if not res['contraste_multifuente']:
                    st.warning('Ningún episodio positivo del CSV reúne las tres fuentes de contraste SMN, INA e INTA.')
                if not res['emisiones_operativas_verificadas']:
                    st.warning('Evaluación retrospectiva: la disponibilidad original de las emisiones no está confirmada. Estos umbrales no se pueden activar en el semáforo operativo.')
                if not res['cobertura_provincial']['completa']:
                    st.warning('Cobertura provincial insuficiente: estos umbrales no pueden aplicarse a los 42 puntos. Ajuste y prueba necesitan impactos y controles propios de cada provincia.')
                st.download_button('Exportar evaluación y trazabilidad',json.dumps(json_finito(res),ensure_ascii=False,indent=2),'calibracion_semaforo.json','application/json',key='ficha_cal_v43_export')
                if st.button('Aplicar ambos umbrales al semáforo de esta sesión',key='ficha_cal_v43_apply',disabled=not (res['contraste_multifuente'] and res['emisiones_operativas_verificadas'] and res['cobertura_provincial']['completa'])):
                    st.session_state['ficha_umbral_local']=res['umbral_rojo'];st.session_state['ficha_umbral_amarillo']=res['umbral_amarillo']
                    st.session_state['ficha_cal_protocolo']=res;st.rerun()
        except Exception as exc:
            st.error('Evaluación no disponible: '+str(exc))
    if 'ficha_cal_protocolo' in st.session_state:
        st.info('Umbrales evaluados activos sólo en esta sesión y reflejados en mapa, territorio y Telegram.')
        if st.button('Restablecer referencias 35 / 55',key='ficha_cal_v41_reset'):
            for k in ['ficha_umbral_local','ficha_umbral_amarillo','ficha_cal_protocolo']:
                st.session_state.pop(k,None)
            st.rerun()


def mostrar_caso_2016_integrado():
    st.subheader('Abril 2016 · contraste SMN + INA + INTA en el Litoral')
    st.write('SMN: Concordia registró 605,5 mm mensuales y 21 días con lluvia. Se agrega la escala INA de Concordia, sobre el Uruguay, y la imagen INTA del mismo episodio. Cada evidencia conserva su escala: lluvia puntual, nivel fluvial y composición satelital regional.')
    c=CASO_CONCORDIA_2016;d=pd.DataFrame(c['data'])
    d['fecha']=d.timestart.map(fecha_fuente);d['altura_m']=pd.to_numeric(d.valor,errors='coerce')
    est=c['estacion'];d['estado_referencia']=d.altura_m.map(lambda h:clasificar_rio(h,est))
    fig=px.line(d,x='fecha',y='altura_m',title='INA Concordia · río Uruguay · marzo–abril 2016')
    for key,color,texto in [('nivel_de_alerta','#eab308','Alerta'),('nivel_de_evacuacion','#dc2626','Evacuación')]:
        fig.add_hline(y=float(est[key]),line_color=color,annotation_text=f'{texto}: {est[key]} m')
    st.plotly_chart(fig,use_container_width=True,key='ficha_concordia2016_chart')
    periodo=d[d.fecha.dt.month==4]
    a,b,c1=st.columns(3);a.metric('Altura máxima en abril',f'{periodo.altura_m.max():.2f} m')
    b.metric('Días ≥ umbral de alerta',int(periodo[periodo.altura_m>=float(est['nivel_de_alerta'])].fecha.dt.date.nunique()))
    c1.metric('Días ≥ umbral de evacuación',int(periodo[periodo.altura_m>=float(est['nivel_de_evacuacion'])].fecha.dt.date.nunique()))
    st.caption('Conteos de días de la serie observada: no son aciertos del pronóstico. Los umbrales del catálogo consultado son referencias actuales; su adopción histórica no demuestra cuál era el protocolo de 2016.')
    try:
        st.image(documento_de_apoyo('inta_abril_2016.jpg', INTA_BASE+'/productos/sepaproductos/inundaciones/in_20160427_1102_terra_litoral.jpg'),caption='INTA SEPA · Terra/MODIS, bandas 1–2–7, 250 m · adquisición indicada en la imagen: 27/04/2016 11:07. El nombre del archivo contiene 1102; se conserva la hora rotulada. Composición visual regional, no clasificación ni NDWI.',use_container_width=True)
    except Exception:
        st.warning("No se pudo mostrar la imagen INTA de 2016. Verificá que la carpeta assets esté junto a app.py; también podés abrir el archivo original con el enlace de abajo.")
    st.download_button('Serie observada INA Concordia y referencias',d.to_csv(index=False).encode('utf-8-sig'),'INA_Concordia_2016.csv','text/csv',key='ficha_concordia2016_csv')
    st.link_button('Serie INA de origen',INA_BASE+'datos&seriesId=79&timeStart=2016-03-01&timeEnd=2016-05-01&format=json')
    st.link_button('Archivo original INTA del episodio',INTA_BASE+'/productos/sepaproductos/inundaciones/in_20160427_1102_terra_litoral.jpg')
    st.link_button('Boletín SMN de abril 2016',CASO_2016_URL)
    st.info('Conclusión: el episodio respalda integrar lluvia, agua superficial y nivel fluvial. La adopción de umbrales INA es trazable; el umbral del índice y las reglas de suelo/NDWI todavía necesitan evaluación independiente. Vera 2025 mantiene visible la subestimación del modelo como prueba de una limitación.')


def matriz_cumplimiento_ficha():
    evaluado='ficha_cal_protocolo' in st.session_state
    rows=[
        ('Precipitación y pronóstico 7–14 días','Implementado','ECMWF/Open-Meteo; SMN para contraste; cobertura meteorológica y fechas visibles'),
        ('Suelo INTA y agua superficial','Implementado con carga/consulta','Mapas SEPA, muestreo de GeoTIFF autorizado, Sentinel-2 automático y CSV; no equivale a saturación física in situ'),
        ('SMN: alertas y tendencia a 90 días','Implementado','Alertas oficiales y PDF trimestral con mapas, período y aviso de vencimiento'),
        ('INA: niveles y caudales','Implementado','Observaciones y umbrales por estación, vigencia y serie histórica Concordia'),
        ('Litoral argentino · seis provincias','Cobertura geográfica ampliada','Santa Fe, Corrientes, Entre Ríos, Chaco, Formosa y Misiones; puntos de monitoreo y límites oficiales. No es cobertura continua ni validación en cada localidad'),
        ('Caso histórico + aplicación','Implementado','2016: SMN + INA Concordia + imagen INTA del episodio; Vera 2025: contraste con corridas archivadas'),
        ('Calibrar umbrales meteorológicos','Evaluado localmente' if evaluado else 'Controles documentados; prueba temporal pendiente',f'{len(controles_documentados())} ausencias explícitas documentadas; faltan emisiones e índices completos de una cohorte independiente'),
        ('Semáforo verde/amarillo/rojo','Implementado','Misma fusión en panel, mapa, acciones, exportación y Telegram; gris para datos insuficientes'),
        ('Cotas y refugios','Fuentes y antecedentes incorporados; habilitación pendiente','DEM Copernicus, cuatro centros históricos de Vera y registros con capacidad declarada; capacidad y habilitación actuales no publicadas en forma verificable'),
        ('Vialidad nacional/provincial y caminos','Fuentes identificadas; estado vigente pendiente','Filtro nacional y fuentes provinciales para las seis jurisdicciones; sin feed provincial uniforme de cortes en la app'),
        ('Salud y suero antiofídico','Catálogos descargados el 10/10/2026; operación pendiente','Catálogo IGN: 2.077 puntos en las seis provincias; Entre Ríos: 212 registros legibles de centros, una fila excluida y 65 de hospitales. Guardia, camas, horarios y suero requieren confirmación local'),
        ('Evacuar hacienda, siembra/cosecha, canales y operativos','Implementado como apoyo','Acciones por color; destinos, accesos y capacidad requieren confirmación del responsable'),
        ('Inventario, calidad y representatividad','Implementado','Fechas, procedencia, datos ausentes, nubes y límites rurales; exportaciones para Python/QGIS/Leaflet'),
    ]
    return pd.DataFrame(rows,columns=['Requisito de la ficha','Estado','Evidencia y condición'])


def mostrar_cumplimiento_ficha():
    st.subheader('Correspondencia con la ficha · estado verificable')
    st.caption('Entrega 1: Ficha del problema e inventario de datos. Autor: Federico Damian Ieraci. Camino: análisis histórico y propuesta de aplicación.')
    d=matriz_cumplimiento_ficha();st.dataframe(d,hide_index=True,use_container_width=True)
    st.download_button('Descargar correspondencia con la ficha',d.to_csv(index=False).encode('utf-8-sig'),'Cumplimiento_ficha.csv','text/csv',key='ficha_cumplimiento_csv')
    st.write(f'Los {len(controles_documentados())} controles negativos conservan evidencia de ausencia con fuente y fecha. Para cerrar la prueba estadística faltan corridas completas independientes y evidencia de disponibilidad original. En territorio, caminos provinciales, refugios, cupos y atención sanitaria requieren confirmación local vigente.')


# ============================================================
# CABECERA VISUAL
# ============================================================

# Respaldo incluido para que la cabecera funcione aun si se sube solo app.py.
HERO_TORMENTA_FALLBACK_B64 = "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"

@st.cache_data(ttl=86400, show_spinner=False)
def cargar_banner_tormenta():
    'Carga el fondo local de la cabecera sin incrustarlo en el código.'
    archivo_modulo = globals().get("__file__", str(Path.cwd() / "app.py"))
    archivo = Path(archivo_modulo).resolve().parent / "assets" / "tormenta_litoral_hero.jpg"
    try:
        if archivo.is_file():
            return "data:image/jpeg;base64," + base64.b64encode(archivo.read_bytes()).decode("ascii")
    except OSError:
        pass
    return "data:image/jpeg;base64," + HERO_TORMENTA_FALLBACK_B64


def mostrar_hero_litoral():
    fondo = cargar_banner_tormenta()
    estilo_fondo = f"background-image:url('{fondo}');" if fondo else "background-image:linear-gradient(125deg,#071d2e,#0a5265 55%,#17253b);"
    html_hero = f'''
    <div class="hero-litoral" role="banner" aria-label="Alerta Litoral Agro">
      <div class="hero-fondo" style="{estilo_fondo}"></div>
      <div class="hero-velo"></div>
      <div class="hero-contenido">
        <div class="hero-kicker">MONITOREO METEOROLÓGICO · LITORAL ARGENTINO</div>
        <h1>🌧️ <span>Alerta Litoral Agro</span></h1>
        <p>Sistema de alerta temprana para anticipar el impacto de lluvias intensas, saturación del suelo y crecidas.</p>
        <div class="hero-datos">{''.join('<span>'+p+'</span>' for p in PROVINCIAS_LITORAL)}<span>ECMWF IFS HRES 9 km</span></div>
      </div>
      <div class="hero-version">{VERSION}</div>
    </div>
    <style>
      *{{box-sizing:border-box}}
      body{{margin:0;background:transparent;font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
      .hero-litoral{{position:relative;min-height:320px;overflow:hidden;border-radius:22px;margin:2px 0 20px;background:#081c2c;color:#fff;box-shadow:0 14px 38px rgba(4,22,37,.23)}}
      .hero-fondo{{position:absolute;inset:0;background-position:center;background-size:cover;transform:scale(1.015)}}
      .hero-velo{{position:absolute;inset:0;background:linear-gradient(90deg,rgba(2,13,27,.88) 0%,rgba(4,24,40,.50) 42%,rgba(5,26,42,.08) 80%,rgba(2,12,23,.06) 100%),linear-gradient(0deg,rgba(2,12,22,.65),transparent 58%)}}
      .hero-contenido{{position:relative;z-index:1;padding:56px 58px 48px;max-width:850px}}
      .hero-kicker{{display:inline-flex;align-items:center;gap:8px;color:#a9edf0;font-size:11px;font-weight:800;letter-spacing:.16em;margin-bottom:18px}}
      .hero-kicker:before{{content:"";width:26px;height:2px;background:#49d3d2;border-radius:2px}}
      h1{{font-size:clamp(36px,5vw,66px);line-height:1.02;letter-spacing:-.045em;margin:0 0 15px;font-weight:850;text-shadow:0 3px 18px rgba(0,0,0,.35)}}
      h1 span{{color:#fff}}
      p{{max-width:690px;font-size:clamp(16px,2vw,22px);line-height:1.4;margin:0;color:#effbff;text-shadow:0 2px 12px rgba(0,0,0,.55)}}
      .hero-datos{{display:flex;flex-wrap:wrap;gap:8px;margin-top:28px}}
      .hero-datos span{{font-size:12px;font-weight:700;color:#dffbff;border:1px solid rgba(182,245,246,.4);background:rgba(4,33,49,.52);padding:7px 11px;border-radius:999px;backdrop-filter:blur(4px)}}
      .hero-version{{position:absolute;right:20px;bottom:18px;z-index:2;font-size:11px;font-weight:800;color:#d9fbff;background:rgba(1,18,30,.68);border:1px solid rgba(184,244,244,.35);padding:7px 10px;border-radius:999px}}
      @media(max-width:620px){{.hero-litoral{{min-height:390px;border-radius:16px}}.hero-contenido{{padding:42px 25px 44px}}.hero-kicker{{font-size:9px;letter-spacing:.1em;line-height:1.4}} .hero-datos{{margin-top:22px}}.hero-datos span{{font-size:11px}}.hero-version{{right:15px;bottom:14px}}}}
    </style>
    '''
    components.html(html_hero, height=350, scrolling=False)


# ============================================================
# INTERFAZ
# ============================================================

mostrar_hero_litoral()
st.caption(f"Herramienta experimental de apoyo a decisiones · Versión {VERSION} · Fuentes y límites documentados en Caso histórico y ficha")
firma_actual=firma_cobertura()
if st.session_state.get('firma_cobertura')!=firma_actual:
    for clave in ('df_alerta','raw_ecmwf','df_operativo','firma_historial_operativo','ficha_localidad','localidad_seleccionada'):
        st.session_state.pop(clave,None)
    st.session_state['firma_cobertura']=firma_actual


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
                               consultar_relieve, consultar_salud_ign, recorrido_orientativo, consultar_trimestral, buscar_escenas, descargar_registro):
            consulta_ficha.clear()
        for clave_ficha in ('ficha_ina_resultado', 'ficha_inta_imagen', 'ficha_relieve',
                            'ficha_vialidad', 'ficha_ruta_calculada', 'ficha_salud', 'ficha_dem_por_localidad', 'ficha_trimestral'):
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

🟢 **VERDE**

🟡 **AMARILLO**

🔴 **ROJO**

⚪ **SIN DATOS**
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


    df_alerta = integrar_semaforo(df_alerta)
    st.session_state["df_operativo"] = df_alerta
    st.subheader('Tu localidad · alerta y acciones recomendadas')
    localidades=sorted(df_alerta.localidad.tolist())
    if localidades:
        if st.session_state.get('localidad_detalle') not in localidades:
            st.session_state.pop('localidad_detalle',None)
        destacada=localidad_prioritaria(df_alerta)
        localidad_seleccionada=st.selectbox('Seleccionar localidad',localidades,
            index=localidades.index(destacada) if destacada in localidades else 0,key='localidad_detalle')
        row_acciones=df_alerta[df_alerta.localidad.eq(localidad_seleccionada)].iloc[0]
        mostrar_recomendaciones_alerta(row_acciones,key='plan_acciones_principal')
    else:
        localidad_seleccionada=None
    st.subheader(f'Cobertura regional · {len(NODOS)} puntos en seis provincias')
    cobertura_regional=pd.DataFrame([{'Provincia':p,'Puntos de monitoreo':sum(n['provincia']==p for n in NODOS),
        'Índices meteorológicos completos':int((df_alerta.provincia.eq(p)&df_alerta.estado_datos.eq('OK')).sum()),
        'Sin índice completo':int((df_alerta.provincia.eq(p)&df_alerta.indice.isna()).sum())} for p in PROVINCIAS_LITORAL])
    st.dataframe(cobertura_regional,hide_index=True,use_container_width=True)
    st.caption('La red monitorea localidades de las seis provincias. Una celda del modelo no representa cada lote; la ampliación geográfica no demuestra calibración local ni disponibilidad de recursos.')
    with st.expander('Ver y descargar localidades monitoreadas'):
        st.dataframe(pd.DataFrame(NODOS),hide_index=True,use_container_width=True)
        st.download_button('Descargar puntos de monitoreo',pd.DataFrame(NODOS).to_csv(index=False).encode('utf-8-sig'),
            'nodos_litoral_6_provincias.csv','text/csv',key='cobertura_nodos_csv')
        limites={'type':'FeatureCollection','features':[{'type':'Feature','properties':{'nombre':p},'geometry':g} for p,g in GEOMETRIAS_LITORAL.items()]}
        st.download_button('Descargar límites provinciales',json.dumps(limites,ensure_ascii=False),'litoral_provincias.geojson',
            'application/geo+json',key='cobertura_limites_geojson')

    firma_historial = hashlib.sha256(df_alerta.to_csv(index=False).encode()).hexdigest()
    if st.session_state.get("firma_historial_operativo") != firma_historial:
        guardar_historial(df_alerta)
        st.session_state["firma_historial_operativo"] = firma_historial

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

        st.info('La red completa sigue visible en el mapa. Los puntos sin cobertura suficiente no se interpretan como riesgo bajo.')


    # ============================================================
    # MÉTRICAS
    # ============================================================

    maximo = df_validos[
        "indice"
    ].max()

    promedio = df_validos[
        "indice"
    ].mean()

    cantidad_rojo = int((df_alerta["nivel"] == "ROJO").sum())
    cantidad_amarillo = int((df_alerta["nivel"] == "AMARILLO").sum())
    cantidad_verde = int((df_alerta["nivel"] == "VERDE").sum())
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("🔴 Rojo", cantidad_rojo)
    col2.metric("🟡 Amarillo", cantidad_amarillo)
    col3.metric("🟢 Verde", cantidad_verde)
    col4.metric("📊 Índice meteorológico máximo", f"{maximo:.1f}/100" if np.isfinite(maximo) else 'Sin datos')
    if cantidad_rojo:
        st.error(f"🔴 {cantidad_rojo} nodos en ROJO según las fuentes disponibles.")
    elif cantidad_amarillo:
        st.warning(f"🟡 {cantidad_amarillo} nodos en AMARILLO; preparar medidas y verificar accesos.")
    elif cantidad_verde:
        st.success("🟢 Los nodos con datos están en VERDE según las fuentes disponibles.")
    else:
        st.warning('⚪ No hay índices meteorológicos completos para interpretar el riesgo de esta consulta.')
    st.caption("El color integra meteorología y las capas vigentes cargadas de suelo, agua, relieve y río. El índice 0–100 conserva su formulación meteorológica. Una cobertura parcial no certifica ausencia de riesgo.")
    st.dataframe(df_alerta[["localidad", "nivel", "cobertura_territorial", "motivos_semaforo", "accion"]],
                 hide_index=True, use_container_width=True,
                 column_config={'accion':st.column_config.TextColumn('Acción recomendada',width='large')})

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
        "El encuadre inicial abarca las seis provincias del Litoral argentino. "
        "Los puntos grises indican información insuficiente; no son riesgo bajo."
    )

    fig_mapa = crear_mapa(
        df_alerta
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
        "las seis provincias del Litoral argentino."
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

    # El selector y el plan se muestran al inicio; este detalle usa la misma localidad.


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

    tabla = df_alerta[
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


    row = df_alerta[
        df_alerta[
            "localidad"
        ]
        == localidad_seleccionada
    ].iloc[0]


    indice_texto=f'{row["indice"]:.1f}/100' if np.isfinite(row['indice']) else 'Sin datos completos'
    if row['estado_datos']!='OK':
        st.warning(row.get('error_datos') or 'Cobertura meteorológica insuficiente: el índice no está disponible.')
    st.markdown(
        f"""
    ## {emoji_nivel(row["nivel"])} {row["nivel"]}

    ### {row["localidad"]}, {row["provincia"]}

    ### Índice: {indice_texto}

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


    st.caption('El plan completo de acciones de esta localidad está al inicio de la pestaña, junto al semáforo.')


    # ============================================================
    # HISTORIAL
    # ============================================================

    st.header(
        "📈 Historial"
    )
    st.caption("Desde V4.1 el historial conserva el semáforo integrado y sus motivos. Los registros anteriores corresponden al índice meteorológico de aquella versión. La historia del servidor puede reiniciarse al desplegar; descargá el CSV para conservarla.")

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
        df_alerta
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
    | 0–34.9 | 🟢 VERDE |
    | 35–54.9 | 🟡 AMARILLO |
    | 55–100 | 🔴 ROJO |

    Son valores de referencia del índice meteorológico. Si se aplica una evaluación local,
    ambos umbrales cambian en esta sesión. Las capas territoriales vigentes pueden elevar
    el color; el índice conserva su formulación. Gris indica datos insuficientes.

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
