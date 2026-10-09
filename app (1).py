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
# NO UTILIZA DATOS DE VIALIDAD.
#
# Versión: V3.10.2
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
VERSION = "V3.10.2"
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


def procesar_nodo(nodo, datos):

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

    referencia = hora_actual_local_redondeada()

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

tab_agro, tab_radar, tab_smn = st.tabs([
    "🌧️ Alerta Litoral Agro", "📡 Radar SINARAME", "🇦🇷 Datos oficiales SMN",
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

**No se utilizan datos de Vialidad.**
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
        "Sistema experimental · Sin datos de Vialidad"
    )
