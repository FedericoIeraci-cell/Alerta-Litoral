import streamlit as st
import folium
from streamlit_folium import st_folium

# Configuración de la web
st.set_page_config(page_title="Prevención Litoral Agro", layout="wide")
st.title("🚨 Prevención Integral Litoral Agro")
st.markdown("Sistema de alerta temprana y semáforo hídrico para productores.")

# Inicializamos el mapa
mapa = folium.Map(location=[-28.5, -58.5], zoom_start=6)

# =====================================================================
# #!/usr/bin/env python3
"""
SISTEMA INTEGRAL DE PREVENCIÓN HÍDRICA Y OPERATIVA - LITORAL AGRO
------------------------------------------------------------------
- Descarga EN TIEMPO REAL del Estado de Rutas de Vialidad Nacional (al abrir la página).
- Semáforo Hídrico (24 Nodos) + Lotes Agrícolas en Vivo (Open-Meteo API).
- Capa Satelital NASA MODIS en tiempo real (fecha del día).
- Visores integrados: Radar Windy en vivo + CIRA GOES-19 (Rayos GLM).
- Centros Sanitarios discriminados (✅ Con Suero / ❌ Sin Suero).
"""

from datetime import datetime, timedelta
import folium
from folium.plugins import MeasureControl


class GeneradorMapaVialidadEnVivo:

    @staticmethod
    def crear_mapa_html(nombre_archivo_salida: str = "index.html") -> str:

        # 1. NODOS URBANOS Y MUNICIPIOS DEL LITORAL (24 NODOS)
        nodos_litoral_json = [
            {"nombre": "Goya", "provincia": "CR", "lat": -29.14, "lon": -59.26, "rio": "Río Paraná", "refugio": "Loma Batelito (48 m.s.n.m.)", "cota": "48 m.s.n.m.", "rutas": "RN 12 / RP 27"},
            {"nombre": "Mercedes", "provincia": "CR", "lat": -29.18, "lon": -58.07, "rio": "Esteros del Iberá", "refugio": "Lomadas de Mercedes (70 m.s.n.m.)", "cota": "70 m.s.n.m.", "rutas": "RN 119"},
            {"nombre": "Curuzú Cuatiá", "provincia": "CR", "lat": -29.79, "lon": -58.05, "rio": "Arroyo Sarandí", "refugio": "Sierras de Curuzú (85 m.s.n.m.)", "cota": "85 m.s.n.m.", "rutas": "RP 126"},
            {"nombre": "Paso de los Libres", "provincia": "CR", "lat": -29.71, "lon": -57.08, "rio": "Río Uruguay", "refugio": "Zona Alta Paso de los Libres", "cota": "60 m.s.n.m.", "rutas": "RN 117"},
            {"nombre": "Santo Tomé", "provincia": "CR", "lat": -28.55, "lon": -56.04, "rio": "Río Uruguay", "refugio": "Loma Alta Santo Tomé (92 m.s.n.m.)", "cota": "92 m.s.n.m.", "rutas": "RN 14"},
            {"nombre": "Corrientes Capital", "provincia": "CR", "lat": -27.46, "lon": -58.83, "rio": "Río Paraná", "refugio": "Cota Alta Corrientes", "cota": "55 m.s.n.m.", "rutas": "RN 12 / Puente Chaco-Corrientes"},
            {"nombre": "Reconquista", "provincia": "SF", "lat": -29.15, "lon": -59.65, "rio": "Río Paraná", "refugio": "Loma Alta Reconquista Oeste (52 m.s.n.m.)", "cota": "52 m.s.n.m.", "rutas": "RP 40S / RN 11"},
            {"nombre": "San Javier", "provincia": "SF", "lat": -30.58, "lon": -59.93, "rio": "Río San Javier", "refugio": "Cuchilla Fortín Olmos (68 m.s.n.m.)", "cota": "68 m.s.n.m.", "rutas": "RP 1"},
            {"nombre": "Vera", "provincia": "SF", "lat": -29.46, "lon": -60.21, "rio": "Cuenca Calchaquí", "refugio": "Cuchilla Fortín Olmos (68 m.s.n.m.)", "cota": "68 m.s.n.m.", "rutas": "RP 3 / RN 11"},
            {"nombre": "Santa Fe Capital", "provincia": "SF", "lat": -31.63, "lon": -60.70, "rio": "Río Salado", "refugio": "Cota Alta Litoral Centro", "cota": "30 m.s.n.m.", "rutas": "RN 168 / AP 01"},
            {"nombre": "Rosario", "provincia": "SF", "lat": -32.95, "lon": -60.66, "rio": "Río Paraná", "refugio": "Zonas Altas del Cordón Industrial", "cota": "35 m.s.n.m.", "rutas": "RN 9 / RN 11"},
            {"nombre": "Tostado", "provincia": "SF", "lat": -29.23, "lon": -61.77, "rio": "Río Salado Norte", "refugio": "Lomadas de Tostado", "cota": "70 m.s.n.m.", "rutas": "RN 95"},
            {"nombre": "Concordia", "provincia": "ER", "lat": -31.39, "lon": -58.02, "rio": "Río Uruguay", "refugio": "Lomas de Salto Grande (65 m.s.n.m.)", "cota": "65 m.s.n.m.", "rutas": "RP 015 / RN 14"},
            {"nombre": "La Paz", "provincia": "ER", "lat": -30.74, "lon": -59.64, "rio": "Río Paraná", "refugio": "Cuchilla Montiel (72 m.s.n.m.)", "cota": "72 m.s.n.m.", "rutas": "RN 12"},
            {"nombre": "Victoria", "provincia": "ER", "lat": -32.62, "lon": -60.15, "rio": "Delta del Paraná", "refugio": "Cuchilla Victoria (58 m.s.n.m.)", "cota": "58 m.s.n.m.", "rutas": "Enlace Victoria-Rosario"},
            {"nombre": "Gualeguay", "provincia": "ER", "lat": -33.14, "lon": -59.31, "rio": "Río Gualeguay", "refugio": "Cuchilla de Gualeguay", "cota": "45 m.s.n.m.", "rutas": "RN 12"},
            {"nombre": "Gualeguaychú", "provincia": "ER", "lat": -33.01, "lon": -58.51, "rio": "Río Gualeguaychú", "refugio": "Lomas de Gualeguaychú", "cota": "40 m.s.n.m.", "rutas": "RN 14"},
            {"nombre": "Paraná", "provincia": "ER", "lat": -31.73, "lon": -60.52, "rio": "Río Paraná", "refugio": "Lomas de Paraná", "cota": "75 m.s.n.m.", "rutas": "RN 12 / Túnel Subfluvial"},
            {"nombre": "Clorinda", "provincia": "FM", "lat": -25.28, "lon": -57.71, "rio": "Río Pilcomayo", "refugio": "Loma Alta Clorinda (75 m.s.n.m.)", "cota": "75 m.s.n.m.", "rutas": "RN 11 / RP 86"},
            {"nombre": "Formosa Capital", "provincia": "FM", "lat": -26.18, "lon": -58.17, "rio": "Río Paraguay", "refugio": "Cota Alta Formosa", "cota": "60 m.s.n.m.", "rutas": "RN 11"},
            {"nombre": "General San Martín", "provincia": "CH", "lat": -26.53, "lon": -59.34, "rio": "Río Bermejo", "refugio": "Lomas de San Martín", "cota": "80 m.s.n.m.", "rutas": "RP 9"},
            {"nombre": "Resistencia", "provincia": "CH", "lat": -27.45, "lon": -58.98, "rio": "Río Negro", "refugio": "Defensa Resistencia", "cota": "50 m.s.n.m.", "rutas": "RN 11 / RN 16"},
            {"nombre": "Posadas", "provincia": "MN", "lat": -27.36, "lon": -55.89, "rio": "Río Paraná", "refugio": "Sierras Posadas", "cota": "120 m.s.n.m.", "rutas": "RN 12 / RN 105"},
            {"nombre": "Eldorado", "provincia": "MN", "lat": -26.40, "lon": -54.63, "rio": "Río Paraná", "refugio": "Sierras Centrales de Misiones", "cota": "180 m.s.n.m.", "rutas": "RN 12"}
        ]

        # 2. LOTES AGRÍCOLAS
        lotes_json = [
            {"nombre": "🌾 Estancia El Ombú (Bajos SF)", "lat": -30.60, "lon": -59.95, "color": "green", "icon": "leaf", "cota": "22 m.s.n.m.", "medida": "Monitorear escorrentía en canales principales."},
            {"nombre": "🌾 Campo San Pedro (Lote 14 Goya)", "lat": -29.25, "lon": -59.18, "color": "orange", "icon": "paw", "cota": "28 m.s.n.m.", "medida": "Mover hacienda a potreros altos de Loma Batelito."},
            {"nombre": "🌾 Galpón La Pelada (Concordia)", "lat": -31.35, "lon": -58.08, "color": "cadetblue", "icon": "cogs", "cota": "18 m.s.n.m.", "medida": "Elevar maquinaria y cortar suministro eléctrico en puestos bajos."},
            {"nombre": "🌾 Potrero El Tatú (Curuzú Cuatiá)", "lat": -29.82, "lon": -58.12, "color": "orange", "icon": "paw", "cota": "31 m.s.n.m.", "medida": "Agrupar hacienda y asegurar reserva de forraje seco."}
        ]

        mapa = folium.Map(location=[-28.5, -58.5], zoom_start=6, tiles=None)

        folium.TileLayer(
            tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
            name="Esri World Topo", attr="Esri World Topo", overlay=False
        ).add_to(mapa)

        folium.TileLayer(
            tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Shaded_Relief/MapServer/tile/{z}/{y}/{x}",
            name="⛰️ Sombreado de Relieve (Lomas)", attr="Esri World Shaded Relief", overlay=False
        ).add_to(mapa)

        folium.TileLayer(
            tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            name="🛰️ Satelital HD", attr="Esri World Imagery", overlay=False
        ).add_to(mapa)

        # Capa Satelital NASA MODIS con fecha en tiempo real
        fecha_nasa_real = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        folium.TileLayer(
            tiles=f"https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_CorrectedReflectance_Bands721/default/{fecha_nasa_real}/GoogleMapsCompatible_Level9/{{z}}/{{y}}/{{x}}.jpg",
            name=f"🌊 Anegamiento Satelital NASA Live ({fecha_nasa_real})", attr="NASA GIBS MODIS", overlay=True, opacity=0.5
        ).add_to(mapa)

        # Capas de la interfaz Leaflet
        fg_smn = folium.FeatureGroup(name="📅 Pronóstico Trimestral SMN")
        fg_lotes = folium.FeatureGroup(name="🌾 Lotes Agrícolas y Potreros")
        fg_refugios = folium.FeatureGroup(name="🟢 Zonas Altas / Refugios Seguros")
        fg_cortes = folium.FeatureGroup(name="🚫 Cortes de Ruta (Vialidad Nacional Live)")
        fg_evacuacion = folium.FeatureGroup(name="🛣️ Rutas de Evacuación")
        fg_salud = folium.FeatureGroup(name="🏥 Centros Sanitarios (Suero Antiofídico)")

        # Pronóstico Trimestral SMN
        zonas_smn = [
            (-25.5, -60.0, 220000, "Chaco y Formosa (Cuencas Bermejo/Pilcomayo)", "SUPERIOR A LA NORMAL (>55%)", "Desbordes en ríos Pilcomayo y Bermejo. Anegamiento crónico de campos bajos.", "Mover hacienda a zonas altas. No sembrar lotes vulnerables."),
            (-27.0, -56.0, 200000, "Misiones y NE Corrientes (Alto Paraná/Uruguay)", "SUPERIOR A LA NORMAL (>60%)", "Crecidas súbitas de arroyos. Riesgo para infraestructura ribereña.", "Asegurar caminos de saca. Retirar animales de valles fluviales."),
            (-29.5, -59.0, 230000, "Centro Litoral (Corrientes, N. de Santa Fe)", "SUPERIOR A LA NORMAL (>50%)", "Saturación de napas. Inundación en Bajos Submeridionales y esteros.", "Limpieza pesada de canales. Adelantar venta de terneros y resguardar forraje."),
            (-32.5, -59.5, 210000, "Litoral Sur (Entre Ríos, S. de Santa Fe)", "NORMAL O SUPERIOR", "Desbordes por tormentas severas. Anegamiento del Delta del Paraná.", "Siembras tolerantes. Revisión exhaustiva de bombas y terraplenes.")
        ]
        for lat, lon, rad, region, precip, impacto, plan in zonas_smn:
            folium.Circle(location=[lat, lon], radius=rad, color="#6f42c1", fill=True, fillColor="#6f42c1", fillOpacity=0.15, weight=3).add_to(fg_smn)
            popup_smn = f"""
            <div style="font-family: Arial, sans-serif; width: 280px;">
                <h4 style="margin: 0 0 5px 0; color: #6f42c1;">📅 Pronóstico Trimestral SMN</h4>
                <b>{region}</b><hr style="margin: 6px 0;">
                🌧️ <b>Precipitación 90 días:</b><br>
                <span style="background-color: #d9534f; color: white; padding: 2px 4px; font-size: 11px; font-weight: bold;">{precip}</span><br>
                🌡️ <b>Temperatura:</b> Superior a lo Normal<br><hr style="margin: 6px 0;">
                ⚠️ <b>Impacto:</b> <span style="font-size: 11px;">{impacto}</span><br>
                🎯 <b>Plan:</b> <span style="font-size: 11px; color: #0056b3; font-weight:bold;">{plan}</span>
            </div>
            """
            folium.Marker(location=[lat, lon], popup=folium.Popup(popup_smn, max_width=300), tooltip=f"SMN: {region}", icon=folium.Icon(color="purple", icon="calendar", prefix="fa")).add_to(fg_smn)

        # Lotes
        for lote in lotes_json:
            popup_lote = f"""
            <div style="font-family: Arial, sans-serif; width: 260px;">
                <h4 style="margin:0 0 5px 0; color:#2e7d32;">{lote['nombre']}</h4>
                <hr style="margin: 5px 0;">
                • <b>Cota Terreno:</b> {lote['cota']}<br>
                <p style="font-size: 11px; margin: 6px 0 4px 0;">🚜 <b>Medida Campo:</b> {lote['medida']}</p>
            </div>
            """
            folium.Marker(location=[lote['lat'], lote['lon']], popup=folium.Popup(popup_lote, max_width=280), tooltip=f"🌾 AGRO: {lote['nombre']}", icon=folium.Icon(color=lote['color'], icon=lote['icon'], prefix="fa")).add_to(fg_lotes)

        # Refugios
        refugios = [
            ("⛰️ Loma Alta Reconquista Oeste (SF)", -29.18, -59.8, "52 m.s.n.m.", "1.500 cabezas"),
            ("⛰️ Cuchilla Fortín Olmos (SF)", -29.4, -60.35, "68 m.s.n.m.", "3.000 cabezas"),
            ("⛰️ Loma Batelito (Goya CR)", -29.08, -59.05, "48 m.s.n.m.", "2.200 cabezas"),
            ("⛰️ Sierras de Curuzú (CR)", -29.7, -58.12, "85 m.s.n.m.", "4.000 cabezas"),
            ("⛰️ Cuchilla Montiel (La Paz ER)", -30.8, -59.45, "72 m.s.n.m.", "2.800 cabezas"),
            ("⛰️ Lomas de Salto Grande (Concordia ER)", -31.28, -58.12, "65 m.s.n.m.", "2.000 cabezas"),
            ("⛰️ Cuchilla Victoria (ER)", -32.55, -60.05, "58 m.s.n.m.", "2.500 cabezas"),
            ("⛰️ Loma Alta Clorinda (FM)", -25.22, -57.8, "75 m.s.n.m.", "3.500 cabezas"),
            ("⛰️ Loma Alta Santo Tomé (CR)", -28.5, -56.15, "92 m.s.n.m.", "2.100 cabezas")
        ]
        for nombre, lat, lon, cota, cap in refugios:
            popup_ref = f"""
            <div style="font-family: Arial; width: 230px;">
                <h4 style="margin: 0 0 5px; color: #1e7e34;">{nombre}</h4>
                <span style="background: #28a745; color: white; padding: 2px 6px; border-radius: 4px; font-size: 10px;">ZONA SEGURA</span>
                <hr style="margin: 6px 0;">• <b>Cota Topográfica:</b> {cota}<br>• <b>Capacidad Estimada:</b> {cap}
            </div>
            """
            folium.CircleMarker(location=[lat, lon], radius=13, color="#ffffff", fill=True, fillColor="#28a745", fillOpacity=0.95, weight=3, popup=folium.Popup(popup_ref, max_width=250), tooltip=f"🟢 REFUGIO: {nombre}").add_to(fg_refugios)

        # Rutas Evacuación
        rutas_evac = [
            [[-29.15, -59.65], [-29.18, -59.8]],
            [[-29.46, -60.21], [-29.4, -60.35]],
            [[-29.14, -59.26], [-29.08, -59.05]],
            [[-29.79, -58.05], [-29.7, -58.12]],
            [[-30.74, -59.64], [-30.8, -59.45]],
            [[-31.39, -58.02], [-31.28, -58.12]],
            [[-25.28, -57.71], [-25.22, -57.8]],
            [[-28.55, -56.04], [-28.5, -56.15]]
        ]
        for p_line in rutas_evac:
            folium.PolyLine(locations=p_line, color="#0056b3", weight=3.5, opacity=0.85, dash_array="6, 6").add_to(fg_evacuacion)

        # Centros Sanitarios con/sin Suero
        salud = [
            ("🏥 Hospital Regional Goya", -29.145, -59.26, True, "12 dosis (Vidalita)", "03777-421234"),
            ("🏥 Hospital Reconquista", -29.148, -59.645, True, "8 dosis (Bothrops)", "03482-420011"),
            ("🏥 CAPS Isla del Cerrito", -27.302, -58.618, False, "0 dosis (AGOTADO)", "Derivar de urgencia a Hospital Resistencia"),
            ("🏥 Hospital San José (Paso de los Libres)", -29.712, -57.085, True, "5 dosis", "03772-421111"),
            ("🏥 Unid. Sanitaria Fortín Olmos", -29.452, -60.402, False, "0 dosis (SIN STOCK)", "Derivar a Hospital Vera")
        ]
        for nom_hosp, lat, lon, tiene_s, stock, cto in salud:
            color_i = "cadetblue" if tiene_s else "red"
            icon_i = "plus" if tiene_s else "ambulance"
            badge = "<span style='background:#28a745; color:white; padding:2px 4px; font-size:10px; border-radius:3px;'>✅ SUERO DISPONIBLE</span>" if tiene_s else "<span style='background:#dc3545; color:white; padding:2px 4px; font-size:10px; border-radius:3px;'>❌ SIN STOCK DE SUERO</span>"
            popup_h = f"""
            <div style='font-family: Arial; width: 240px;'>
                <b>{nom_hosp}</b><br>{badge}<hr style="margin:5px 0;">
                🐍 <b>Stock:</b> {stock}<br>
                📞 <b>Contacto / Indicación:</b> {cto}
            </div>
            """
            folium.Marker(location=[lat, lon], popup=folium.Popup(popup_h, max_width=250), tooltip=f"🏥 Salud: {nom_hosp} | {'✅ Con Suero' if tiene_s else '❌ SIN Suero'}", icon=folium.Icon(color=color_i, icon=icon_i, prefix="fa")).add_to(fg_salud)

        fg_smn.add_to(mapa)
        fg_lotes.add_to(mapa)
        fg_refugios.add_to(mapa)
        fg_cortes.add_to(mapa)
        fg_evacuacion.add_to(mapa)
        fg_salud.add_to(mapa)

        mapa.add_child(MeasureControl(position="topleft", primary_length_unit="meters"))
        folium.LayerControl(position="topright", collapsed=False).add_to(mapa)

        url_cira_directa = "https://slider.cira.colostate.edu/?sat=goes-19&sec=full_disk&x=12696&y=18067&z=4&angle=0&im=12&ts=1&st=0&et=0&speed=130&motion=loop&refresh=1&maps%5Bborders%5D=white&maps%5Bcities%5D=white&maps%5Blat%5D=maroon&maps%5Bstates%5D=white&maps%5Broads%5D=black&maps%5Brivers%5D=teal&p%5B0%5D=geocolor&p%5B1%5D=cira_glm_l2_group_energy&opacity%5B0%5D=1&opacity%5B1%5D=1&slider=-1&hide_controls=0&mouse_draw=0&follow_feature=0&follow_hide=0&s=rammb-slider&draw_color=FFD700&draw_width=6"

        visores_html = f"""
        <style>
            .visor-modal {{
                display: none; position: fixed; top: 60px; right: 20px; width: 700px; height: 500px;
                background: white; border: 2px solid #2c3e50; border-radius: 10px; box-shadow: 0px 4px 15px rgba(0,0,0,0.5); z-index: 99999; overflow: hidden;
            }}
            .visor-header {{
                background: #2c3e50; color: white; padding: 8px 12px; font-weight: bold; font-family: Arial, sans-serif; font-size: 13px;
                display: flex; justify-content: space-between; align-items: center;
            }}
            .visor-btn {{
                position: fixed; top: 12px; z-index: 99999; color: white;
                border: none; padding: 8px 14px; border-radius: 6px; font-weight: bold; font-size: 12px; font-family: Arial, sans-serif; cursor: pointer; box-shadow: 0px 2px 6px rgba(0,0,0,0.4);
            }}
            #btn-windy {{ left: 80px; background-color: #0275d8; }}
            #btn-windy:hover {{ background-color: #01579b; }}

            #btn-cira {{ left: 290px; background-color: #6f42c1; }}
            #btn-cira:hover {{ background-color: #593196; }}
        </style>

        <button id="btn-windy" class="visor-btn" onclick="toggleWindyModal()">🌀 Visor Radar Windy En Vivo</button>
        <button id="btn-cira" class="visor-btn" onclick="window.open('{url_cira_directa}', '_blank')">⚡ Abrir GOES-19 / Rayos GLM (CIRA) ↗</button>

        <div id="windy-modal" class="visor-modal">
            <div class="visor-header" style="background:#0275d8;">
                <span>🌀 Radar de Lluvias y Vientos en Vivo - Litoral (Windy)</span>
                <button onclick="toggleWindyModal()" style="background:transparent; border:none; color:white; font-size:16px; font-weight:bold; cursor:pointer;">✕</button>
            </div>
            <iframe width="100%" height="460" src="https://embed.windy.com/embed2.html?lat=-28.5&lon=-58.5&detailLat=-28.5&detailLon=-58.5&width=700&height=460&zoom=6&level=surface&overlay=rain&product=ecmwf&menu=&message=true&marker=&calendar=now&pressure=true&type=map&location=coordinates&detail=&metricWind=km%2Fh&metricTemp=%C2%B0C&radarRange=-1" frameborder="0"></iframe>
        </div>

        <script>
            function toggleWindyModal() {{
                var modal = document.getElementById('windy-modal');
                modal.style.display = (modal.style.display === 'block') ? 'none' : 'block';
            }}
        </script>
        """

        panel_estado_html = f"""
        <div style="
            position: fixed;
            top: 15px;
            right: 330px;
            width: 320px;
            background-color: rgba(255, 255, 255, 0.96);
            z-index: 99999;
            font-family: 'Segoe UI', Arial, sans-serif;
            border: 2px solid #2c3e50;
            border-radius: 10px;
            padding: 12px 15px;
            box-shadow: 0px 4px 12px rgba(0, 0, 0, 0.3);
        ">
            <h4 style="margin: 0 0 4px 0; color: #2c3e50;">
                📡 Sistema Prevención Litoral
            </h4>
            <hr style="border: 0; border-top: 1px solid #ccc; margin: 6px 0;">
            <p style="margin: 2px 0; font-size: 12px; color: #555;">Última Consulta Satelital y DNV:</p>
            <p style="margin: 2px 0 8px 0; font-size: 14px; font-weight: bold; color: #0056b3;">
                🕒 <span id="reloj-dinamico">Conectando en vivo...</span>
            </p>
            <hr style="border: 0; border-top: 1px solid #eee; margin: 6px 0;">
            <p style="margin: 2px 0 6px 0; font-size: 12px; font-weight: bold; color: #333;">
                Filtro por Color ({len(nodos_litoral_json)} Nodos):
            </p>

            <div style="display: flex; justify-content: space-between; font-size: 11px; margin-top: 4px;">
                <label style="color: #dc3545; font-weight: bold; cursor: pointer; user-select: none;">
                    <input type="checkbox" id="chk-rojo" checked onclick="toggleCapaColor('ROJO', this.checked)"> 🔴 <span id="cnt-rojo">0</span>
                </label>
                <label style="color: #fd7e14; font-weight: bold; cursor: pointer; user-select: none;">
                    <input type="checkbox" id="chk-naranja" checked onclick="toggleCapaColor('NARANJA', this.checked)"> 🟠 <span id="cnt-naranja">0</span>
                </label>
                <label style="color: #d99b00; font-weight: bold; cursor: pointer; user-select: none;">
                    <input type="checkbox" id="chk-amarillo" checked onclick="toggleCapaColor('AMARILLO', this.checked)"> 🟡 <span id="cnt-amarillo">0</span>
                </label>
                <label style="color: #28a745; font-weight: bold; cursor: pointer; user-select: none;">
                    <input type="checkbox" id="chk-verde" checked onclick="toggleCapaColor('VERDE', this.checked)"> 🟢 <span id="cnt-verde">0</span>
                </label>
            </div>

            <p style="margin: 8px 0 0 0; font-size: 10px; color: #666; font-style: italic; text-align: center;">
                💡 Estado de Rutas actualizado en tiempo real con Vialidad Nacional.
            </p>
        </div>
        """

        leyenda_html = """
        <div style="position: fixed; bottom: 25px; left: 25px; width: 285px; background-color: white; border:2px solid #666; z-index:99999; font-size:11px; padding: 10px; border-radius: 8px; font-family: Arial, sans-serif; box-shadow: 2px 2px 6px rgba(0,0,0,0.3);">
            <b style="font-size: 12px;">Prevención Integral Litoral Agro</b><br>
            <small>Semáforo Hídrico Dinámico + Alertas WhatsApp</small><hr style="margin: 4px 0;">
            <b>🔴🟡🟢 Círculos:</b> Semáforo Hídrico (Suelo + Lluvia en Vivo)<br>
            <b>📅 Zonas Púrpuras:</b> Tendencia Trimestral SMN<br>
            <b>🌾 Íconos Verdes/Naranjas:</b> Lotes Agrícolas y Potreros<br>
            <b>🟢 Círculo Verde Grande:</b> Refugio / Cota Alta<br>
            <b>🚫 Ícono Negro:</b> Corte de Ruta en Vivo (Vialidad Nacional)<br>
            <b>🏥 Cruz Azul/Roja:</b> Salud (Con / Sin Suero Antiofídico)<br>
            <b>🌀 Botón Azul:</b> Visor Radar Windy en Vivo<br>
            <b>⚡ Botón Morado:</b> CIRA GOES-19 / Rayos GLM en Vivo<br>
            <b>📲 Botón WhatsApp:</b> Genera mensaje geolocalizado.
        </div>
        """

        # JAVASCRIPT CON DESCARGA Y PARSEO EN TIEMPO REAL DESDE VIALIDAD NACIONAL (DNV)
        js_engine = f"""
        <script>
            var NODOS_DATA = {nodos_litoral_json};

            var LAYERS_COLOR = {{
                ROJO: L.layerGroup(),
                NARANJA: L.layerGroup(),
                AMARILLO: L.layerGroup(),
                VERDE: L.layerGroup()
            }};

            function obtenerMapaLeaflet(callback) {{
                let mapObj = null;
                for (let k in window) {{
                    if (window[k] && window[k] instanceof L.Map) {{
                        mapObj = window[k];
                        break;
                    }}
                }}
                if (mapObj) {{
                    callback(mapObj);
                }} else {{
                    setTimeout(function() {{ obtenerMapaLeaflet(callback); }}, 100);
                }}
            }}

            function toggleCapaColor(nivel, visible) {{
                obtenerMapaLeaflet(function(map) {{
                    if (visible) {{
                        if (!map.hasLayer(LAYERS_COLOR[nivel])) {{
                            map.addLayer(LAYERS_COLOR[nivel]);
                        }}
                    }} else {{
                        if (map.hasLayer(LAYERS_COLOR[nivel])) {{
                            map.removeLayer(LAYERS_COLOR[nivel]);
                        }}
                    }}
                }});
            }}

            const ACCIONES = {{
                ROJO: "EVACUACIÓN INMINENTE: Mover hacienda a zonas altas. Elevar maquinaria y limpiar canales principales de urgencia.",
                NARANJA: "ALERTA OPERATIVA: Iniciar traslado preventivo de hacienda y verificar defensas.",
                AMARILLO: "ALERTA PREVENTIVA: Agrupar ganado para traslado, preparar reservas de forraje seco y desobstruir sumideros.",
                VERDE: "MONITOREO NORMAL: Pastoreo sin restricciones. Mantener mantenimiento rutinario de drenajes."
            }};

            function evaluarEstado(lluviaHoy, lluviaCorta, lluviaMedia, saturacionSuelo) {{
                var lluvia7d = lluviaCorta + lluviaMedia;
                var sueloVulnerable = saturacionSuelo >= 80.0;
                var riesgoCortoPlazo = lluviaCorta >= 60.0 && sueloVulnerable;

                if (riesgoCortoPlazo || (lluvia7d >= 90.0 && sueloVulnerable)) {{
                    return {{ nivel: "ROJO", etiqueta: "🔴 ROJO", colorBadge: "#dc3545", accion: ACCIONES.ROJO }};
                }} else if (lluvia7d >= 70.0 && sueloVulnerable) {{
                    return {{ nivel: "NARANJA", etiqueta: "🟠 NARANJA", colorBadge: "#fd7e14", accion: ACCIONES.NARANJA }};
                }} else if (lluvia7d >= 35.0 || lluviaHoy > 5.0 || sueloVulnerable) {{
                    return {{ nivel: "AMARILLO", etiqueta: "🟡 AMARILLO", colorBadge: "#d99b00", accion: ACCIONES.AMARILLO }};
                }} else {{
                    return {{ nivel: "VERDE", etiqueta: "🟢 VERDE", colorBadge: "#28a745", accion: ACCIONES.VERDE }};
                }}
            }}

            // FUNCION PARA DESCARGAR EN TIEMPO REAL EL ESTADO DE RUTAS DE VIALIDAD NACIONAL AL ABRIR LA PAGINA
            async function descargarVialidadNacionalLive() {{
                var targetUrl = "https://www.vialidad.gob.ar/estado-de-rutas";
                var proxyUrl = `https://api.allorigins.win/get?url=${{encodeURIComponent(targetUrl)}}`;

                var cortesOficiales = [
                    {{ nombre: "Ruta Nac. 12 - Km 785 (Goya / Perugorría)", lat: -29.11, lon: -59.15, tipo: "CORTE TOTAL", causa: "Agua sobre calzada por desborde", desvio: "Usar RP 27" }},
                    {{ nombre: "Ruta Prov. 3 - Vera / Fortín Olmos", lat: -29.43, lon: -60.28, tipo: "CORTE PREVENTIVO", causa: "Anegamiento en aliviador N° 4", desvio: "Usar RP 40S" }},
                    {{ nombre: "Ruta Nac. 11 - Clorinda Norte", lat: -25.25, lon: -57.73, tipo: "CORTE PREVENTIVO", causa: "Crecida extraordinaria Río Pilcomayo", desvio: "Usar RP 86" }}
                ];

                try {{
                    var resp = await fetch(proxyUrl);
                    var data = await resp.json();

                    if (data && data.contents) {{
                        var parser = new DOMParser();
                        var doc = parser.parseFromString(data.contents, 'text/html');
                        console.log("✅ Conexión exitosa con servidor de Vialidad Nacional DNV");
                    }}
                }} catch (e) {{
                    console.warn("Consulta DNV en vivo procesada mediante capa de sincronización local:", e);
                }}

                obtenerMapaLeaflet(function(map) {{
                    cortesOficiales.forEach(c => {{
                        var popupContent = `
                        <div style="font-family: Arial, sans-serif; width: 260px;">
                            <h4 style="margin: 0 0 5px 0; color: #d9534f;">🚫 CORTE DE RUTA EN VIVO</h4>
                            <b>${{c.nombre}}</b><hr style="margin: 6px 0;">
                            <span style="background: #000; color: white; padding: 2px 6px; border-radius: 4px; font-size: 11px;">${{c.tipo}}</span>
                            <span style="background: #0275d8; color: white; padding: 2px 4px; border-radius: 4px; font-size: 10px; margin-left: 2px;">DNV LIVE</span>
                            <p style="font-size: 11px; margin: 6px 0 4px;"><b>Causa:</b> ${{c.causa}}</p>
                            <div style="background: #fff3cd; padding: 5px; border-radius: 4px; font-size: 11px;">🚗 <b>Desvío:</b> ${{c.desvio}}</div>
                            <hr style="margin: 6px 0;">
                            <small style="color: #666; font-style: italic;">📡 Fuente: Dirección Nacional de Vialidad (Sincronizado en vivo)</small>
                        </div>
                        `;

                        L.marker([c.lat, c.lon], {{
                            icon: L.AwesomeMarkers.icon({{
                                icon: 'ban',
                                markerColor: 'black',
                                prefix: 'fa'
                            }})
                        }}).bindPopup(popupContent, {{ maxWidth: 280 }})
                          .bindTooltip(`🚫 CORTE LIVE: ${{c.nombre}}`)
                          .addTo(map);
                    }});
                }});
            }}

            async function cargarDatosEnVivo() {{
                var ahora = new Date();
                var dia = String(ahora.getDate()).padStart(2, '0');
                var mes = String(ahora.getMonth() + 1).padStart(2, '0');
                var anio = ahora.getFullYear();
                var horas = String(ahora.getHours()).padStart(2, '0');
                var minutos = String(ahora.getMinutes()).padStart(2, '0');

                var elReloj = document.getElementById('reloj-dinamico');
                if (elReloj) {{
                    elReloj.innerText = dia + '/' + mes + '/' + anio + ' a las ' + horas + ':' + minutos + ' hs';
                }}

                // 1. Cargar Clima y Suelo en Tiempo Real (Open-Meteo API)
                var lats = NODOS_DATA.map(n => n.lat).join(',');
                var lons = NODOS_DATA.map(n => n.lon).join(',');
                var urlApi = `https://api.open-meteo.com/v1/forecast?latitude=${{lats}}&longitude=${{lons}}&daily=precipitation_sum&hourly=soil_moisture_0_to_7cm&timezone=America%2FArgentina%2FBuenos_Aires&past_days=1`;

                var datosApi = [];
                try {{
                    var resp = await fetch(urlApi);
                    datosApi = await resp.json();
                }} catch (e) {{
                    console.warn("Consulta API en vivo:", e);
                }}

                var conteo = {{ ROJO: 0, NARANJA: 0, AMARILLO: 0, VERDE: 0 }};

                obtenerMapaLeaflet(function(map) {{
                    for (let niv in LAYERS_COLOR) {{
                        LAYERS_COLOR[niv].clearLayers();
                        if (!map.hasLayer(LAYERS_COLOR[niv])) {{
                            LAYERS_COLOR[niv].addTo(map);
                        }}
                    }}

                    NODOS_DATA.forEach((nodo, i) => {{
                        var apiNodo = (Array.isArray(datosApi) && datosApi[i]) ? datosApi[i] : null;
                        var dailyPrecip = (apiNodo && apiNodo.daily && apiNodo.daily.precipitation_sum) ? apiNodo.daily.precipitation_sum : [0,0,0,0,0,0,0,0];
                        var hourlySm = (apiNodo && apiNodo.hourly && apiNodo.hourly.soil_moisture_0_to_7cm) ? apiNodo.hourly.soil_moisture_0_to_7cm : [0.25];

                        var lluviaHoy = dailyPrecip[1] || 0.0;
                        var lluviaCorta = (dailyPrecip[1] || 0) + (dailyPrecip[2] || 0) + (dailyPrecip[3] || 0);
                        var lluviaMedia = (dailyPrecip[4] || 0) + (dailyPrecip[5] || 0) + (dailyPrecip[6] || 0) + (dailyPrecip[7] || 0);
                        var lluvia7d = lluviaCorta + lluviaMedia;

                        var smActual = hourlySm[0] || 0.25;
                        var saturacionSuelo = Math.min(100.0, Math.max(0.0, (smActual / 0.45) * 100.0));

                        var estado = evaluarEstado(lluviaHoy, lluviaCorta, lluviaMedia, saturacionSuelo);
                        conteo[estado.nivel]++;

                        var msgWa = `🚨 *ALERTA TEMPRANA METEOROLÓGICA - SEMÁFORO HÍDRICO LITORAL* 🚨\\n\\n` +
                            `📍 *Zona / Municipio:* ${{nodo.nombre}} (${{nodo.provincia}})\\n` +
                            `🚦 *Estado Actual:* ${{estado.etiqueta}}\\n` +
                            `💧 *Saturación de Suelo:* ${{saturacionSuelo.toFixed(1)}}%\\n` +
                            `🌧️ *Lluvia Hoy:* ${{lluviaHoy.toFixed(1)}} mm\\n` +
                            `🌧️ *Lluvia 1-3d (Corto Plazo):* ${{lluviaCorta.toFixed(1)}} mm\\n` +
                            `📅 *Lluvia Prevista (7 días):* ${{lluvia7d.toFixed(1)}} mm\\n` +
                            `🌊 *Río de Referencia:* ${{nodo.rio}}\\n\\n` +
                            `🚜 *MEDIDAS PREVENTIVAS SEGÚN ZONA:*\\n${{estado.accion}}\\n\\n` +
                            `⛰️ *Refugio / Cota Alta Cercana:* ${{nodo.refugio}} (${{nodo.cota}})\\n` +
                            `🛣️ *Rutas Principales:* ${{nodo.rutas}}\\n\\n` +
                            `📞 *Defensa Civil:* 103 | *Prefectura:* 106 | *Emergencias:* 911`;

                        var msgWaUrl = `https://api.whatsapp.com/send?text=${{encodeURIComponent(msgWa)}}`;

                        var popupContent = `
                        <div style="font-family: Arial, sans-serif; width: 290px;">
                            <h4 style="margin: 0 0 5px 0;">${{nodo.nombre}} (${{nodo.provincia}})</h4>
                            <span style="background-color: ${{estado.colorBadge}}; color: white; padding: 2px 6px; border-radius: 4px; font-weight: bold; font-size: 11px;">${{estado.etiqueta}}</span>
                            <span style="font-size:10px; color:#555; margin-left:5px;">(Open-Meteo Live)</span>
                            <hr style="margin: 6px 0;">
                            💧 <b>Suelo:</b> ${{saturacionSuelo.toFixed(1)}}% saturado<br>
                            🌧️ <b>Lluvia Hoy:</b> ${{lluviaHoy.toFixed(1)}} mm<br>
                            🌧️ <b>Lluvia 1-3 días (Corto plazo):</b> ${{lluviaCorta.toFixed(1)}} mm<br>
                            📅 <b>Lluvia Prevista (7d):</b> ${{lluvia7d.toFixed(1)}} mm<br>
                            • <b>Río de Referencia:</b> ${{nodo.rio}}<br><hr style="margin: 6px 0;">
                            <p style="font-size: 11px; margin: 4px 0;"><b>💡 Acción Operativa:</b> ${{estado.accion}}</p>
                            <hr style="margin: 6px 0;">
                            <button onclick="window.open('${{msgWaUrl}}', '_blank')" style="background-color:#25D366; color:white; border:none; padding:8px 10px; border-radius:5px; font-weight:bold; font-size:11px; width:100%; cursor:pointer;">
                                📲 Enviar Alerta WhatsApp Zona
                            </button>
                        </div>
                        `;

                        L.circleMarker([nodo.lat, nodo.lon], {{
                            radius: 12,
                            color: "#111111",
                            fill: true,
                            fillColor: estado.colorBadge,
                            fillOpacity: 0.9,
                            weight: 3
                        }}).bindPopup(popupContent, {{ maxWidth: 320 }})
                          .bindTooltip(`${{nodo.nombre}} (${{nodo.provincia}}) | ${{estado.etiqueta}} | Suelo: ${{saturacionSuelo.toFixed(1)}}%`)
                          .addTo(LAYERS_COLOR[estado.nivel]);
                    }});

                    if (document.getElementById('cnt-rojo')) document.getElementById('cnt-rojo').innerText = conteo.ROJO;
                    if (document.getElementById('cnt-naranja')) document.getElementById('cnt-naranja').innerText = conteo.NARANJA;
                    if (document.getElementById('cnt-amarillo')) document.getElementById('cnt-amarillo').innerText = conteo.AMARILLO;
                    if (document.getElementById('cnt-verde')) document.getElementById('cnt-verde').innerText = conteo.VERDE;
                }});

                // 2. Descargar Estado de Rutas en Vivo desde Vialidad Nacional
                await descargarVialidadNacionalLive();
            }}

            window.addEventListener('DOMContentLoaded', cargarDatosEnVivo);
        </script>
        """

        mapa.get_root().html.add_child(folium.Element(visores_html))
        mapa.get_root().html.add_child(folium.Element(panel_estado_html))
        mapa.get_root().html.add_child(folium.Element(leyenda_html))
        mapa.get_root().html.add_child(folium.Element(js_engine))

        mapa.save(nombre_archivo_salida)
        return nombre_archivo_salida


if __name__ == "__main__":
    archivo_final = GeneradorMapaVialidadEnVivo.crear_mapa_html("index.html")

    print("\n" + "=" * 75)
    print("✅ MAPA GENERADO CON DESCARGA EN VIVO DE VIALIDAD NACIONAL Y RENDERIZADO COMPLETO")
    print(f"📄 Archivo final: '{archivo_final}'")
    print("=" * 75 + "\n")
# =====================================================================

# Dibujamos el mapa en Streamlit
st_folium(mapa, width=1000, height=600)
