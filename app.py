import streamlit as st
import pandas as pd
import numpy as np
import io
import requests
import zipfile
import tempfile

# GEO
import geopandas as gpd
from shapely.geometry import Point

st.title("Jornada Laboral Conductores")

# ==============================
# CONFIG
# ==============================

HORAS_MAX_JORNADA = st.number_input("Horas máximas jornada", value=8.0)
HORAS_DESCANSO_LARGO = st.number_input("Horas descanso largo", value=4.0)

MIN_PAUSA = st.number_input("Pausa mínima (minutos)", value=34)
MIN_PARADA = st.number_input("Duración mínima parada (minutos)", value=17)

HORAS_MIN_PAUSA = MIN_PAUSA / 60
UMBRAL_PARADA_MIN = MIN_PARADA / 60

# ==============================
# 📍 CARGA SHAPEFILE DESDE DRIVE
# ==============================

@st.cache_data
def cargar_municipios():

    file_id = "1POxehTrIfY2ZxnLreboTGKAxqFJM0tFU"
    url = f"https://drive.google.com/uc?export=download&id={file_id}"

    response = requests.get(url)

    if response.status_code != 200:
        st.error("Error descargando shapefile desde Drive")
        return None

    import tempfile, zipfile, os

    # guardar temporal
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.write(response.content)
    tmp.close()

    # extraer zip
    extract_path = tempfile.mkdtemp()

    try:
        with zipfile.ZipFile(tmp.name, 'r') as zip_ref:
            zip_ref.extractall(extract_path)
    except:
        st.error("El archivo no es un ZIP válido")
        return None

    # buscar .shp
    shp_file = None
    for file in os.listdir(extract_path):
        if file.endswith(".shp"):
            shp_file = os.path.join(extract_path, file)
            break

    if shp_file is None:
        st.error("No se encontró archivo .shp dentro del ZIP")
        return None

    gdf = gpd.read_file(shp_file)

    return gdf

municipios_gdf = cargar_municipios()

# ==============================
# GEO FUNCIONES
# ==============================

def parse_coords(coord):
    try:
        lat, lon = map(float, str(coord).split(","))
        return lat, lon
    except:
        return np.nan, np.nan

def coord_a_municipio(lat, lon):

    if municipios_gdf is None or np.isnan(lat):
        return f"{round(lat,3)}, {round(lon,3)}"

    punto = Point(lon, lat)

    try:
        match = municipios_gdf[municipios_gdf.contains(punto)]
        if len(match) > 0:
            return match.iloc[0].get("NOMBRE_MPIO", "Municipio")
    except:
        pass

    return f"{round(lat,3)}, {round(lon,3)}"

# ==============================
# LECTOR
# ==============================

def leer_archivo(file):

    try:
        if file.name.endswith(".xlsx"):
            return pd.read_excel(file)
        else:
            return pd.read_csv(file, sep=";", encoding="utf-8")
    except:
        return None

# ==============================
# SUBIR ARCHIVOS
# ==============================

files = st.file_uploader("Sube archivos", accept_multiple_files=True)

if files:

    lista_df = []

    for file in files:

        df_temp = leer_archivo(file)

        if df_temp is None:
            continue

        df_temp = df_temp.rename(columns={
            "Fecha y Hora": "fecha_hora",
            "Velocidad": "velocidad",
            "Ignicion*": "ignicion",
            "Conductor": "conductor"
        })

        df_temp["vehiculo"] = file.name[:6].upper()

        lista_df.append(df_temp)

    df = pd.concat(lista_df)

    # LIMPIEZA
    df["fecha_hora"] = pd.to_datetime(df["fecha_hora"], errors="coerce")

    df["ignicion_on"] = df["ignicion"].astype(str).str.lower().isin(["encendido"])

    df["velocidad"] = pd.to_numeric(df["velocidad"], errors="coerce").fillna(0)

    df = df.sort_values(["vehiculo","fecha_hora"])

    df["fecha"] = df["fecha_hora"].dt.date

    # ESTADO
    df["estado"] = df.apply(
        lambda r: "conduciendo" if r["ignicion_on"] and r["velocidad"]>0
        else "ralenti" if r["ignicion_on"]
        else "apagado",
        axis=1
    )

    # TIEMPO
    df["fecha_siguiente"] = df.groupby("vehiculo")["fecha_hora"].shift(-1)

    df["delta_horas"] = (
        df["fecha_siguiente"] - df["fecha_hora"]
    ).dt.total_seconds()/3600

    df["delta_horas"] = df["delta_horas"].fillna(0)

    # KPIs
    kpis_list = []

    for (vehiculo, fecha), grupo in df.groupby(["vehiculo","fecha"]):

        conductor = grupo["conductor"].dropna().iloc[0]

        inicio_jornada = grupo.loc[grupo["ignicion_on"],"fecha_hora"].min()
        fin_jornada = grupo.loc[grupo["ignicion_on"],"fecha_hora"].max()

        horas_conduccion = grupo.loc[grupo["estado"]=="conduciendo","delta_horas"].sum()
        horas_ralenti = grupo.loc[grupo["estado"]=="ralenti","delta_horas"].sum()

        lat, lon = parse_coords(grupo["Coordenadas"].dropna().iloc[-1])
        ubicacion = coord_a_municipio(lat, lon)

        kpis_list.append({
            "conductor": conductor,
            "vehiculo": vehiculo,
            "fecha": fecha,
            "ubicación": ubicacion,
            "inicio_jornada": inicio_jornada,
            "fin_jornada": fin_jornada,
            "horas_conduccion": horas_conduccion,
            "horas_ralenti": horas_ralenti
        })

    kpis = pd.DataFrame(kpis_list)

    st.dataframe(kpis)
