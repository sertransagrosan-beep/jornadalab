import streamlit as st
import pandas as pd
import numpy as np
import io
import re
import json
import zipfile
from shapely.geometry import Point, shape

st.title("Jornada Laboral Conductores")

# ==============================
# CONFIG
# ==============================

HORAS_MAX_JORNADA = st.number_input("Horas máximas jornada", value=8.0)
HORAS_DESCANSO_LARGO = st.number_input("Horas descanso largo", value=4.0)
MIN_PAUSA = st.number_input("Pausa mínima (min)", value=30)
MIN_PARADA = st.number_input("Duración mínima parada (min)", value=20)

HORAS_MIN_PAUSA = MIN_PAUSA / 60
UMBRAL_PARADA_MIN = MIN_PARADA / 60

# ==============================
# CARGAR GEOJSON
# ==============================

@st.cache_data
def cargar_municipios():
    try:
        # intenta normal
        with open("data/municipios.geojson", "r", encoding="utf-8") as f:
            data = json.load(f)
    except:
        # intenta zip
        with zipfile.ZipFile("data/municipios.geojson.zip") as z:
            with z.open(z.namelist()[0]) as f:
                data = json.load(f)

    municipios = []
    for feat in data["features"]:
        geom = shape(feat["geometry"])
        props = feat["properties"]

        nombre = str(list(props.values())[0])  # toma primer campo

        municipios.append((geom, nombre))

    return municipios

municipios = cargar_municipios()

# ==============================
# FUNCIONES GEO
# ==============================

def parse_coords(coord):
    try:
        lat, lon = map(float, str(coord).split(","))
        return lat, lon
    except:
        return np.nan, np.nan

def obtener_municipio(coord):
    lat, lon = parse_coords(coord)
    if np.isnan(lat):
        return ""

    punto = Point(lon, lat)

    for geom, nombre in municipios:
        if geom.contains(punto):
            return nombre

    return "Fuera de zona"

# ==============================
# CLUSTER MEJORADO
# ==============================

def distancia_metros(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = np.radians(lat1)
    phi2 = np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)

    a = np.sin(dphi/2)**2 + np.cos(phi1)*np.cos(phi2)*np.sin(dlambda/2)**2
    return 2 * R * np.arctan2(np.sqrt(a), np.sqrt(1 - a))

def obtener_ubic_principal(grupo, radio=300):

    g = grupo.copy()
    g[["lat", "lon"]] = g["Coordenadas"].apply(lambda x: pd.Series(parse_coords(x)))
    g = g.dropna(subset=["lat"])

    clusters = []

    for _, row in g.iterrows():

        lat, lon = row["lat"], row["lon"]

        peso = row["delta_horas"]
        if row["estado"] in ["ralenti", "apagado"]:
            peso *= 2

        asignado = False

        for c in clusters:
            if distancia_metros(lat, lon, c["lat"], c["lon"]) < radio:
                c["peso"] += peso
                asignado = True
                break

        if not asignado:
            clusters.append({"lat": lat, "lon": lon, "peso": peso})

    if not clusters:
        return ""

    mejor = max(clusters, key=lambda x: x["peso"])

    return obtener_municipio(f"{mejor['lat']},{mejor['lon']}")

# ==============================
# SUBIR ARCHIVOS
# ==============================

files = st.file_uploader("Sube archivos", accept_multiple_files=True)

if files:

    lista_df = []

    for file in files:
        try:
            df_temp = pd.read_excel(file)
        except:
            try:
                df_temp = pd.read_csv(file, sep=";", encoding="latin1")
            except:
                continue

        df_temp.columns = [str(c).strip() for c in df_temp.columns]

        df_temp = df_temp.rename(columns={
            "Fecha y Hora": "fecha_hora",
            "Velocidad": "velocidad",
            "Ignicion*": "ignicion",
            "Conductor": "conductor",
            "Localización": "ubicacion"
        })

        df_temp["vehiculo"] = file.name[:6].upper()

        lista_df.append(df_temp)

    if not lista_df:
        st.error("No se pudieron leer archivos")
        st.stop()

    df = pd.concat(lista_df, ignore_index=True)

    # ==============================
    # LIMPIEZA
    # ==============================

    df["fecha_hora"] = pd.to_datetime(df["fecha_hora"], errors="coerce")

    df["ignicion"] = df["ignicion"].astype(str).str.lower()
    df["ignicion_on"] = df["ignicion"].isin(["encendido"])

    df["velocidad"] = pd.to_numeric(
        df["velocidad"].astype(str).str.extract(r"(\d+\.?\d*)")[0],
        errors="coerce"
    ).fillna(0)

    df = df.sort_values(["vehiculo", "fecha_hora"]).reset_index(drop=True)

    df["fecha"] = df["fecha_hora"].dt.date

    # ==============================
    # ESTADOS
    # ==============================

    df["estado"] = np.select(
        [
            (df["ignicion_on"]) & (df["velocidad"] > 0),
            (df["ignicion_on"]) & (df["velocidad"] == 0)
        ],
        ["conduciendo", "ralenti"],
        default="apagado"
    )

    # ==============================
    # TIEMPO
    # ==============================

    df["fecha_siguiente"] = df.groupby("vehiculo")["fecha_hora"].shift(-1)

    df["delta_horas"] = (
        (df["fecha_siguiente"] - df["fecha_hora"])
        .dt.total_seconds() / 3600
    ).fillna(0)

    # ==============================
    # BLOQUES
    # ==============================

    df["grupo"] = (df["estado"] != df["estado"].shift()).cumsum()

    bloques = df.groupby(["vehiculo", "grupo"]).agg({
        "estado": "first",
        "fecha_hora": ["min", "max"],
        "delta_horas": "sum"
    })

    bloques.columns = ["estado", "inicio", "fin", "duracion_horas"]
    bloques = bloques.reset_index()

    # ==============================
    # KPIs
    # ==============================

    kpis_list = []

    for (vehiculo, fecha), grupo in df.groupby(["vehiculo", "fecha"]):

        conductor = grupo["conductor"].dropna().iloc[0] if "conductor" in grupo else "N/A"

        inicio_jornada = grupo.loc[grupo["ignicion_on"], "fecha_hora"].min()
        fin_jornada = grupo.loc[grupo["ignicion_on"], "fecha_hora"].max()

        horas_conduccion = grupo.loc[grupo["estado"] == "conduciendo", "delta_horas"].sum()
        horas_ralenti = grupo.loc[grupo["estado"] == "ralenti", "delta_horas"].sum()
        horas_trabajo = horas_conduccion + horas_ralenti

        ultima_coord = grupo["Coordenadas"].dropna().iloc[-1] if "Coordenadas" in grupo else ""
        ubicacion = obtener_municipio(ultima_coord)

        ubic_principal = obtener_ubic_principal(grupo)

        # bloques día con corte correcto
        inicio_dia = pd.Timestamp(fecha)
        fin_dia = inicio_dia + pd.Timedelta(days=1)

        bloques_dia = bloques[
            (bloques["vehiculo"] == vehiculo) &
            (bloques["inicio"] < fin_dia) &
            (bloques["fin"] > inicio_dia)
        ]

        numero_paradas = 0
        horas_descanso = 0
        horas_pausa = 0

        for _, b in bloques_dia.iterrows():

            inicio_real = max(b["inicio"], inicio_dia)
            fin_real = min(b["fin"], fin_dia)

            horas = (fin_real - inicio_real).total_seconds() / 3600

            if b["estado"] in ["ralenti", "apagado"] and horas >= UMBRAL_PARADA_MIN:
                numero_paradas += 1

            if b["estado"] == "apagado":
                if horas >= HORAS_DESCANSO_LARGO:
                    horas_descanso += horas
                elif horas >= HORAS_MIN_PAUSA:
                    horas_pausa += horas

        kpis_list.append({
            "conductor": conductor,
            "vehiculo": vehiculo,
            "fecha": fecha,
            "origen": "",
            "destino": "",
            "ubicación": ubicacion,
            "inicio_jornada": inicio_jornada,
            "fin_jornada": fin_jornada,
            "numero_paradas": numero_paradas,
            "horas_trabajo": horas_trabajo,
            "horas_conduccion": horas_conduccion,
            "horas_descanso": horas_descanso,
            "horas_pausa": horas_pausa,
            "horas_ralenti": horas_ralenti,
            "ubic_principal": ubic_principal
        })

    kpis = pd.DataFrame(kpis_list).round(2)

    kpis["inicio_jornada"] = pd.to_datetime(kpis["inicio_jornada"]).dt.strftime("%I:%M %p").str.lstrip("0")
    kpis["fin_jornada"] = pd.to_datetime(kpis["fin_jornada"]).dt.strftime("%I:%M %p").str.lstrip("0")

    st.dataframe(kpis)

    # ==============================
    # EXPORTAR
    # ==============================

    buffer = io.BytesIO()

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        kpis.to_excel(writer, sheet_name="Resumen", index=False)
        bloques.to_excel(writer, sheet_name="Bloques", index=False)

    st.download_button(
        "Descargar Excel",
        data=buffer,
        file_name="reporte_jornada.xlsx"
    )
