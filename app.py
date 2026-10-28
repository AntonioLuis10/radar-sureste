import os
import time
import requests
from datetime import datetime, timezone
import streamlit as st
import folium
from streamlit_folium import st_folium
from skyfield.api import load, wgs84

# ==========================================
# 1. CONFIGURACIÓN DE PÁGINA Y ESTILO MISSION CONTROL
# ==========================================
st.set_page_config(
    page_title="HOUSTON FDO | SE SECTOR",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Estilo CSS personalizado para consola retro / NASA Mission Control
estilo_mision_control = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');
    
    /* Fondo global y tipografía monoespaciada */
    .stApp {
        background-color: #050509 !important;
        font-family: 'Share Tech Mono', monospace !important;
        color: #00ff41 !important; /* Verde radar */
    }
    
    /* Títulos y cabeceras */
    h1, h2, h3 {
        color: #00ffff !important; /* Cian */
        text-transform: uppercase;
        letter-spacing: 2px;
        border-bottom: 1px solid #00ffff;
        padding-bottom: 5px;
    }
    
    /* Cajas de métricas de telemetría */
    div[data-testid="metric-container"] {
        background-color: #0a0a1a !important;
        border: 1px solid #00ffff !important;
        border-radius: 0px !important;
        padding: 10px !important;
        box-shadow: 0 0 10px rgba(0, 255, 255, 0.2);
    }
    div[data-testid="metric-container"] label {
        color: #00ffff !important;
    }
    div[data-testid="metric-container"] div {
        color: #ffffff !important;
    }

    /* Inversión de mapa OpenStreetMap a modo oscuro sintético */
    iframe[title="streamlit_folium.st_folium"] {
        filter: invert(100%) hue-rotate(180deg) brightness(85%) contrast(120%) grayscale(20%);
        border: 2px solid #00ffff;
        border-radius: 5px;
    }

    /* Tablas de datos */
    .stDataFrame {
        border: 1px solid #00ff41;
    }
</style>
"""
st.markdown(estilo_mision_control, unsafe_allow_html=True)

# ==========================================
# 2. DELIMITACIÓN GEOGRÁFICA Y GEOMETRÍA
# ==========================================
POLIGONO_ZONA = [
    (-2.075, 37.648), # Vélez-Rubio
    (-1.695, 37.673), # Lorca
    (-1.582, 37.411), # Águilas 
    (-1.891, 36.995), # Carboneras
    (-2.392, 37.050), # Tabernas
    (-2.297, 37.353)  # Olula del Río
]

LOMIN, LOMAX = min(p[0] for p in POLIGONO_ZONA), max(p[0] for p in POLIGONO_ZONA)
LAMIN, LAMAX = min(p[1] for p in POLIGONO_ZONA), max(p[1] for p in POLIGONO_ZONA)

def dentro_del_poligono(lon, lat, poligono):
    num_vertices = len(poligono)
    adentro = False
    p1x, p1y = poligono[0]
    for i in range(num_vertices + 1):
        p2x, p2y = poligono[i % num_vertices]
        if lat > min(p1y, p2y):
            if lat <= max(p1y, p2y):
                if lon <= max(p1x, p2x):
                    if p1y != p2y:
                        xints = (lat - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or lon <= xints:
                        adentro = not adentro
        p1x, p1y = p2x, p2y
    return adentro

# ==========================================
# 3. TELEMETRÍA ORBITAL (SKYFIELD / CELESTRAK)
# ==========================================
@st.cache_resource(ttl=43200)
def cargar_satelites():
    url = 'https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle'
    archivo_tle = 'satelites_activos.txt'
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=20)
        if res.status_code == 200:
            with open(archivo_tle, 'w', encoding='utf-8') as f:
                f.write(res.text)
    except Exception:
        pass 

    ts = load.timescale()
    try:
        if os.path.exists(archivo_tle):
            satelites = load.tle_file(archivo_tle)
        else:
            satelites = load.tle_file(url, filename=archivo_tle)
    except Exception:
        satelites = []

    return satelites, ts

with st.spinner('ESTABLECIENDO ENLACE DE DATOS CON CELESTRAK...'):
    satelites_global, ts_global = cargar_satelites()

def obtener_satelites(lista_sats, escala_tiempo):
    if not lista_sats:
        return []
    t = escala_tiempo.now()
    satelites_detectados = []
    for sat in lista_sats:
        subpunto = wgs84.subpoint(sat.at(t))
        lat, lon, alt_km = subpunto.latitude.degrees, subpunto.longitude.degrees, subpunto.elevation.km
        if lat and lon and alt_km > 100 and dentro_del_poligono(lon, lat, POLIGONO_ZONA):
            satelites_detectados.append({
                "DESIGNATOR": sat.name,
                "LAT": round(lat, 3),
                "LON": round(lon, 3),
                "ALT (km)": round(alt_km, 1)
            })
    return satelites_detectados

# ==========================================
# 4. TELEMETRÍA ATMOSFÉRICA (OPENSKY OAUTH2)
# ==========================================
@st.cache_data(ttl=1500)
def obtener_access_token_opensky(client_id, client_secret):
    auth_url = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    try:
        res = requests.post(auth_url, data=payload, headers=headers, timeout=10)
        if res.status_code == 200:
            return res.json().get("access_token")
    except Exception:
        pass
    return None

@st.cache_data(ttl=20)
def obtener_vuelos():
    url = "https://opensky-network.org/api/states/all"
    params = {"lamin": LAMIN, "lamax": LAMAX, "lomin": LOMIN, "lomax": LOMAX}
    headers = {}
    vuelos_detectados = []
    
    if "OPENSKY_CLIENT_ID" in st.secrets and "OPENSKY_CLIENT_SECRET" in st.secrets:
        token = obtener_access_token_opensky(
            st.secrets["OPENSKY_CLIENT_ID"], 
            st.secrets["OPENSKY_CLIENT_SECRET"]
        )
        if token:
            headers["Authorization"] = f"Bearer {token}"
            
    try:
        res = requests.get(url, params=params, headers=headers, timeout=5)
        if res.status_code == 200:
            states = res.json().get("states", [])
            if states:
                for s in states:
                    callsign = s[1].strip() if s[1] else "UNKNOWN"
                    lon, lat, alt_m, vel_ms = s[5], s[6], s[7] or 0, s[9] or 0
                    if lon and lat and dentro_del_poligono(lon, lat, POLIGONO_ZONA):
                        vuelos_detectados.append({
                            "CALLSIGN": callsign,
                            "LAT": round(lat, 3),
                            "LON": round(lon, 3),
                            "ALT (ft)": int(alt_m * 3.28084),
                            "VEL (km/h)": int(vel_ms * 3.6)
                        })
        elif res.status_code == 429:
            st.toast("⚠️ Rate limit alcanzado en OpenSky (HTTP 429).", icon="⏳")
        elif res.status_code == 401:
            st.toast("🔑 Token de OpenSky inválido o expirado (HTTP 401).", icon="❌")
    except Exception:
        pass
        
    return vuelos_detectados

# ==========================================
# 5. RENDERIZADO DE INTERFAZ Y REFRESCO
# ==========================================
vuelos = obtener_vuelos()
sats = obtener_satelites(satelites_global, ts_global)

st.title("M.C.C. FLIGHT DYNAMICS // SE SECTOR")

m1, m2, m3, m4 = st.columns(4)
m1.metric("SYS STATUS", "NOMINAL")
m2.metric("AERO CONTACTS", len(vuelos))
m3.metric("ORBITAL OBJECTS", len(sats))
m4.metric("TIME (UTC)", datetime.now(timezone.utc).strftime("%H:%M:%S"))

col_map, col_data = st.columns([2, 1])

with col_map:
    st.markdown("### 🌐 MAIN TACTICAL DISPLAY")
    centro_lat = sum(p[1] for p in POLIGONO_ZONA) / len(POLIGONO_ZONA)
    centro_lon = sum(p[0] for p in POLIGONO_ZONA) / len(POLIGONO_ZONA)
    
    m = folium.Map(location=[centro_lat, centro_lon], zoom_start=9, tiles="OpenStreetMap")
    
    # Delimitación del polígono objetivo
    folium.Polygon(
        locations=[[p[1], p[0]] for p in POLIGONO_ZONA],
        color="#00ffff", fill=True, fill_color="#00ffff", fill_opacity=0.1, weight=2
    ).add_to(m)
    
    # Marcadores de aviación (Puntos amarillos)
    for v in vuelos:
        folium.CircleMarker(
            location=[v["LAT"], v["LON"]], radius=6, color="yellow", fill=True,
            popup=f"AERO: {v['CALLSIGN']} | Alt: {v['ALT (ft)']} ft"
        ).add_to(m)
        
    # Marcadores de satélites (Puntos rojos)
    for s in sats:
        folium.CircleMarker(
            location=[s["LAT"], s["LON"]], radius=6, color="red", fill=True,
            popup=f"ORBIT: {s['DESIGNATOR']} | Alt: {s['ALT (km)']} km"
        ).add_to(m)
        
    st_folium(m, width="100%", height=550, returned_objects=[])

with col_data:
    st.markdown("### ✈️ ATMOSPHERIC TELEMETRY")
    if vuelos:
        st.dataframe(vuelos, use_container_width=True, hide_index=True)
    else:
        st.info("NO AIR TRAFFIC DETECTED")
        
    st.markdown("### 🛰️ ORBITAL TELEMETRY")
    if sats:
        st.dataframe(sats, use_container_width=True, hide_index=True)
    else:
        st.info("NO ORBITAL OBJECTS IN ZENITH")

# Bucle de actualización automática cada 15 segundos
time.sleep(15)
st.rerun()
