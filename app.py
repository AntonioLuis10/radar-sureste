import os
import math
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

estilo_mision_control = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');

    .stApp {
        background-color: #050509 !important;
        font-family: 'Share Tech Mono', monospace !important;
        color: #00ff41 !important;
    }
    h1, h2, h3 {
        color: #00ffff !important;
        text-transform: uppercase;
        letter-spacing: 2px;
        border-bottom: 1px solid #00ffff;
        padding-bottom: 5px;
    }
    div[data-testid="metric-container"] {
        background-color: #0a0a1a !important;
        border: 1px solid #00ffff !important;
        border-radius: 0px !important;
        padding: 10px !important;
        box-shadow: 0 0 10px rgba(0, 255, 255, 0.2);
    }
    div[data-testid="metric-container"] label { color: #00ffff !important; }
    div[data-testid="metric-container"] div { color: #ffffff !important; }

    iframe[title="streamlit_folium.st_folium"] {
        filter: invert(100%) hue-rotate(180deg) brightness(85%) contrast(120%) grayscale(20%);
        border: 2px solid #00ffff;
        border-radius: 5px;
    }
    .stDataFrame { border: 1px solid #00ff41; }
</style>
"""
st.markdown(estilo_mision_control, unsafe_allow_html=True)

# ==========================================
# 2. DELIMITACIÓN GEOGRÁFICA Y GEOMETRÍA
# ==========================================
POLIGONO_ZONA = [
    (-2.075, 37.648),  # Vélez-Rubio
    (-1.695, 37.673),  # Lorca
    (-1.582, 37.411),  # Águilas
    (-1.891, 36.995),  # Carboneras
    (-2.392, 37.050),  # Tabernas
    (-2.297, 37.353),  # Olula del Río
]

LOMIN, LOMAX = min(p[0] for p in POLIGONO_ZONA), max(p[0] for p in POLIGONO_ZONA)
LAMIN, LAMAX = min(p[1] for p in POLIGONO_ZONA), max(p[1] for p in POLIGONO_ZONA)

INTERVALO_REFRESCO_S = 30  # segundos entre actualizaciones


def dentro_del_poligono(lon, lat, poligono):
    """Ray casting: devuelve True si (lon, lat) está dentro del polígono."""
    n = len(poligono)
    adentro = False
    p1x, p1y = poligono[0]
    for i in range(n + 1):
        p2x, p2y = poligono[i % n]
        if min(p1y, p2y) < lat <= max(p1y, p2y) and lon <= max(p1x, p2x):
            if p1y != p2y:
                xints = (lat - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                if p1x == p2x or lon <= xints:
                    adentro = not adentro
            elif p1x == p2x:
                adentro = not adentro
        p1x, p1y = p2x, p2y
    return adentro


# ==========================================
# 3. TELEMETRÍA ORBITAL (SKYFIELD / CELESTRAK)
# ==========================================
URL_TLE = "https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle"
ARCHIVO_TLE = "satelites_activos.txt"
EDAD_MAX_TLE_S = 2 * 3600  # Celestrak pide no descargar más de 1 vez cada 2 h


@st.cache_resource(ttl=43200)
def cargar_satelites():
    """Descarga (si hace falta) y carga los TLE. Devuelve (satelites, timescale, estado)."""
    estado = "OK"
    ts = load.timescale()

    necesita_descarga = (
        not os.path.exists(ARCHIVO_TLE)
        or (time.time() - os.path.getmtime(ARCHIVO_TLE)) > EDAD_MAX_TLE_S
    )
    if necesita_descarga:
        try:
            res = requests.get(URL_TLE, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            if res.status_code == 200 and "1 " in res.text and len(res.text) > 10000:
                with open(ARCHIVO_TLE, "w", encoding="utf-8") as f:
                    f.write(res.text)
            else:
                estado = f"Celestrak HTTP {res.status_code} / respuesta inválida"
        except Exception as e:
            estado = f"Celestrak ERROR: {e}"

    satelites = []
    if os.path.exists(ARCHIVO_TLE):
        try:
            satelites = load.tle_file(ARCHIVO_TLE)
        except Exception as e:
            estado = f"Error leyendo TLE: {e}"
    elif estado == "OK":
        estado = "Sin archivo TLE"

    # Prefiltro: un satélite con inclinación < latitud mínima de la zona nunca la sobrevuela.
    lat_min = LAMIN
    filtrados = []
    for sat in satelites:
        inc = math.degrees(sat.model.inclo)
        inc_efectiva = min(inc, 180 - inc)
        if inc_efectiva >= lat_min - 1:
            filtrados.append(sat)

    return filtrados, ts, estado, len(satelites)


with st.spinner("ESTABLECIENDO ENLACE DE DATOS CON CELESTRAK..."):
    satelites_global, ts_global, estado_tle, total_tle = cargar_satelites()


@st.cache_data(ttl=10)
def obtener_satelites(_lista_sats, _escala_tiempo):
    """Satélites actualmente sobre el polígono. (Argumentos con _ no se hashean.)"""
    if not _lista_sats:
        return []
    t = _escala_tiempo.now()
    detectados = []
    for sat in _lista_sats:
        try:
            sp = wgs84.subpoint(sat.at(t))
        except Exception:
            continue  # TLE decaído o con error de propagación
        lat, lon, alt_km = sp.latitude.degrees, sp.longitude.degrees, sp.elevation.km
        if math.isnan(lat) or math.isnan(lon):
            continue
        # Prefiltro rápido por caja envolvente antes del test de polígono
        if not (LAMIN <= lat <= LAMAX and LOMIN <= lon <= LOMAX):
            continue
        if alt_km > 100 and dentro_del_poligono(lon, lat, POLIGONO_ZONA):
            detectados.append({
                "DESIGNATOR": sat.name,
                "LAT": round(lat, 3),
                "LON": round(lon, 3),
                "ALT (km)": round(alt_km, 1),
            })
    return detectados


# ==========================================
# 4. TELEMETRÍA ATMOSFÉRICA (OPENSKY OAUTH2)
# ==========================================
@st.cache_data(ttl=1500)
def _token_opensky(client_id, client_secret):
    """Si falla lanza excepción, y así Streamlit NO cachea el fallo."""
    auth_url = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
    res = requests.post(
        auth_url,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    res.raise_for_status()
    return res.json()["access_token"]


@st.cache_data(ttl=20)
def obtener_vuelos():
    """Devuelve (lista_de_vuelos, estado_texto)."""
    url = "https://opensky-network.org/api/states/all"
    params = {"lamin": LAMIN, "lamax": LAMAX, "lomin": LOMIN, "lomax": LOMAX}
    headers = {}
    vuelos = []
    estado = "OK (anónimo)"

    try:
        if "OPENSKY_CLIENT_ID" in st.secrets and "OPENSKY_CLIENT_SECRET" in st.secrets:
            try:
                token = _token_opensky(
                    st.secrets["OPENSKY_CLIENT_ID"], st.secrets["OPENSKY_CLIENT_SECRET"]
                )
                headers["Authorization"] = f"Bearer {token}"
                estado = "OK (OAuth2)"
            except Exception as e:
                estado = f"Token falló ({e}); usando acceso anónimo"
        else:
            estado = "Sin credenciales en secrets (anónimo)"
    except Exception:
        pass  # st.secrets no disponible

    try:
        res = requests.get(url, params=params, headers=headers, timeout=10)
        if res.status_code == 200:
            states = res.json().get("states") or []
            for s in states:
                callsign = s[1].strip() if s[1] else "UNKNOWN"
                lon, lat = s[5], s[6]
                alt_m = s[7] if s[7] is not None else (s[13] or 0)
                vel_ms = s[9] or 0
                if lon is None or lat is None:
                    continue
                if dentro_del_poligono(lon, lat, POLIGONO_ZONA):
                    vuelos.append({
                        "CALLSIGN": callsign,
                        "LAT": round(lat, 3),
                        "LON": round(lon, 3),
                        "ALT (ft)": int(alt_m * 3.28084),
                        "VEL (km/h)": int(vel_ms * 3.6),
                    })
            estado += f" | HTTP 200 | {len(states)} en caja, {len(vuelos)} en polígono"
        else:
            estado += f" | HTTP {res.status_code}"
    except Exception as e:
        estado += f" | ERROR: {e}"

    return vuelos, estado


# ==========================================
# 5. RENDERIZADO DE INTERFAZ Y REFRESCO
# ==========================================
vuelos, estado_vuelos = obtener_vuelos()
sats = obtener_satelites(satelites_global, ts_global)

st.title("M.C.C. FLIGHT DYNAMICS // SE SECTOR")

m1, m2, m3, m4 = st.columns(4)
m1.metric("SYS STATUS", "NOMINAL")
m2.metric("AERO CONTACTS", len(vuelos))
m3.metric("ORBITAL OBJECTS", len(sats))
m4.metric("TIME (UTC)", datetime.now(timezone.utc).strftime("%H:%M:%S"))

with st.expander("🔧 DIAGNÓSTICO DE ENLACES", expanded=(len(vuelos) == 0 and len(satelites_global) == 0)):
    st.write(f"**OpenSky:** {estado_vuelos}")
    st.write(f"**Celestrak:** {estado_tle} | TLE totales: {total_tle} | tras prefiltro por inclinación: {len(satelites_global)}")
    st.caption("Es normal ver 0 satélites casi todo el tiempo: la zona es pequeña frente a la órbita terrestre.")

col_map, col_data = st.columns([2, 1])

with col_map:
    st.markdown("### 🌐 MAIN TACTICAL DISPLAY")
    centro_lat = sum(p[1] for p in POLIGONO_ZONA) / len(POLIGONO_ZONA)
    centro_lon = sum(p[0] for p in POLIGONO_ZONA) / len(POLIGONO_ZONA)

    m = folium.Map(location=[centro_lat, centro_lon], zoom_start=9, tiles="OpenStreetMap")

    folium.Polygon(
        locations=[[p[1], p[0]] for p in POLIGONO_ZONA],
        color="#00ffff", fill=True, fill_color="#00ffff", fill_opacity=0.1, weight=2,
    ).add_to(m)

    for v in vuelos:
        folium.CircleMarker(
            location=[v["LAT"], v["LON"]], radius=6, color="yellow", fill=True,
            popup=f"AERO: {v['CALLSIGN']} | Alt: {v['ALT (ft)']} ft",
        ).add_to(m)

    for s in sats:
        folium.CircleMarker(
            location=[s["LAT"], s["LON"]], radius=6, color="red", fill=True,
            popup=f"ORBIT: {s['DESIGNATOR']} | Alt: {s['ALT (km)']} km",
        ).add_to(m)

    st_folium(m, width=None, height=550, returned_objects=[])

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

# Refresco automático
try:
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=INTERVALO_REFRESCO_S * 1000, key="refresco_mcc")
except ImportError:
    time.sleep(INTERVALO_REFRESCO_S)
    st.rerun()
