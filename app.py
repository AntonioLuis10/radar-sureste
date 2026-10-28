import os
import time
import requests
from datetime import datetime, timezone
import streamlit as st
import folium
from streamlit_folium import st_folium
from skyfield.api import load, wgs84

# Configuración de la página Web
st.set_page_config(
    page_title="Houston SE Sector Tracking",
    page_icon="🛰️",
    layout="wide"
)

# 1. COORDENADAS DEL POLÍGONO (lon, lat)
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

# Función robusta con 3 planes de contingencia
@st.cache_resource(ttl=43200)
def cargar_satelites():
    url = 'https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle'
    archivo_tle = 'satelites_activos.txt'
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
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

# EJECUCIÓN OBLIGATORIA: Aquí se crean "satelites_global" y "ts_global"
with st.spinner('🛰️ Conectando con CelesTrak y calculando órbitas. Esto puede tardar unos segundos...'):
    satelites_global, ts_global = cargar_satelites()

def obtener_vuelos():
    url = "https://opensky-network.org/api/states/all"
    params = {"lamin": LAMIN, "lamax": LAMAX, "lomin": LOMIN, "lomax": LOMAX}
    vuelos_detectados = []
    try:
        res = requests.get(url, params=params, timeout=5)
        if res.status_code == 200:
            states = res.json().get("states", [])
            if states:
                for s in states:
                    callsign = s[1].strip() if s[1] else "DESCONOCIDO"
                    lon, lat = s[5], s[6]
                    alt_m = s[7] if s[7] else 0
                    vel_ms = s[9] if s[9] else 0
                    
                    if lon and lat and dentro_del_poligono(lon, lat, POLIGONO_ZONA):
                        vuelos_detectados.append({
                            "callsign": callsign,
                            "lat": lat,
                            "lon": lon,
                            "alt_ft": int(alt_m * 3.28084),
                            "vel_kmh": int(vel_ms * 3.6)
                        })
    except Exception:
        pass
    return vuelos_detectados

# Se añaden los parámetros para evitar el NameError
def obtener_satelites(lista_sats, escala_tiempo):
    if not lista_sats:
        return []
        
    t = escala_tiempo.now() # Ahora sí reconoce la variable 'escala_tiempo'
    satelites_detectados = []
    for sat in lista_sats:
        geocentrico = sat.at(t)
        subpunto = wgs84.subpoint(geocentrico)
        lat, lon = subpunto.latitude.degrees, subpunto.longitude.degrees
        alt_km = subpunto.elevation.km
        
        if lat and lon and alt_km > 100:
            if dentro_del_poligono(lon, lat, POLIGONO_ZONA):
                satelites_detectados.append({
                    "nombre": sat.name,
                    "lat": lat,
                    "lon": lon,
                    "alt_km": round(alt_km, 1)
                })
    return satelites_detectados

# Interface principal
st.title("🛰️ Houston SE Sector Tracking System")
st.caption("Monitorización orbital y atmosférica en tiempo real sobre el polígono del Sureste de España.")

col_head1, col_head2 = st.columns([3, 1])
with col_head2:
    if st.button("🔄 Actualizar Telemetría", use_container_width=True):
        st.rerun()

# Llamadas finales pasando las variables de forma segura
vuelos = obtener_vuelos()
sats = obtener_satelites(satelites_global, ts_global)

# Métricas superiores
m1, m2, m3 = st.columns(3)
m1.metric("Aeronaves en Zona", len(vuelos))
m2.metric("Satélites en Cénit (LEO)", len(sats))
m3.metric("Última Actualización UTC", datetime.now(timezone.utc).strftime("%H:%M:%S"))

col_map, col_data = st.columns([2, 1])

with col_map:
    centro_lat = sum(p[1] for p in POLIGONO_ZONA) / len(POLIGONO_ZONA)
    centro_lon = sum(p[0] for p in POLIGONO_ZONA) / len(POLIGONO_ZONA)
    
    m = folium.Map(location=[centro_lat, centro_lon], zoom_start=9, tiles="CartoDB dark_matter")
    
    puntos_folium = [[p[1], p[0]] for p in POLIGONO_ZONA]
    folium.Polygon(
        locations=puntos_folium,
        color="#3186cc",
        fill=True,
        fill_color="#3186cc",
        fill_opacity=0.15,
        weight=2,
        popup="Zona Cobertura Sureste"
    ).add_to(m)
    
    for v in vuelos:
        folium.Marker(
            location=[v["lat"], v["lon"]],
            popup=f"✈️ {v['callsign']}<br>Alt: {v['alt_ft']:,} ft<br>Vel: {v['vel_kmh']} km/h",
            icon=folium.Icon(color="orange", icon="plane", prefix="fa")
        ).add_to(m)
        
    for s in sats:
        folium.Marker(
            location=[s["lat"], s["lon"]],
            popup=f"🛰️ {s['nombre']}<br>Alt: {s['alt_km']} km",
            icon=folium.Icon(color="green", icon="rocket", prefix="fa")
        ).add_to(m)
        
    st_folium(m, width="100%", height=500, returned_objects=[])

with col_data:
    st.subheader("✈️ Tráfico Aéreo Detectado")
    if vuelos:
        st.dataframe(vuelos, use_container_width=True)
    else:
        st.info("No hay aeronaves reportadas por OpenSky en este momento.")
        
    st.subheader("🛰️ Objetos Orbitales")
    if sats:
        st.dataframe(sats, use_container_width=True)
    else:
        st.info("No hay satélites sobrevolando el cénit actualmente.")
