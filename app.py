import streamlit as st
import pandas as pd
import numpy as np
import io
import re

# GEO opcional
try:
    import geopandas as gpd
    from shapely.geometry import Point
    GEO_OK = True
except:
    GEO_OK = False

st.title("Jornada Laboral Conductores")

# ==============================
# CONFIGURACIÓN
# ==============================

HORAS_MAX_JORNADA = st.number_input("Horas máximas jornada", value=8.0)
HORAS_DESCANSO_LARGO = st.number_input("Horas descanso largo", value=4.0)

MIN_PAUSA = st.number_input("Pausa mínima (minutos)", value=34)
MIN_PARADA = st.number_input("Duración mínima parada (minutos)", value=17)

HORAS_MIN_PAUSA = MIN_PAUSA / 60
UMBRAL_PARADA_MIN = MIN_PARADA / 60

# ==============================
# 📍 CARGA MUNICIPIOS (OPCIONAL)
# ==============================

@st.cache_data
def cargar_municipios():
    if not GEO_OK:
        return None
    try:
        return gpd.read_file("data/municipios.geojson")
    except:
        return None

municipios_gdf = cargar_municipios()

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
# 🧠 CACHE GEOGRÁFICO PRO
# ==============================

@st.cache_data
def construir_cache_municipios(coords_unicas):

    cache = {}

    for coord in coords_unicas:

        try:
            lat, lon = map(float, str(coord).split(","))

            # 🔥 REDONDEO ANTI RUIDO GPS
            lat_r = round(lat, 4)
            lon_r = round(lon, 4)

            key = f"{lat_r},{lon_r}"

            if key not in cache:
                cache[key] = coord_a_municipio(lat_r, lon_r)

        except:
            continue

    return cache

def obtener_municipio_cache(coord, cache):

    try:
        lat, lon = map(float, str(coord).split(","))

        lat_r = round(lat, 4)
        lon_r = round(lon, 4)

        key = f"{lat_r},{lon_r}"

        return cache.get(key, "")

    except:
        return ""

# ==============================
# LECTOR INTELIGENTE
# ==============================

def leer_archivo(file):

    nombre = file.name.lower()

    try:
        if nombre.endswith(".xlsx") or nombre.endswith(".xls"):
            df = pd.read_excel(file)
        else:
            try:
                df = pd.read_csv(file, sep=";", encoding="utf-8")
            except:
                file.seek(0)
                df = pd.read_csv(file, sep=None, engine="python")

        df.columns = df.columns.astype(str)
        df.columns = [c.strip() for c in df.columns]

        df = df.loc[:, ~df.columns.str.contains("^Unnamed", na=False)]

        return df

    except:
        return None

# ==============================
# GEO FUNCIONES
# ==============================

def parse_coords(coord):
    try:
        lat, lon = map(float, str(coord).split(","))
        return lat, lon
    except:
        return np.nan, np.nan

def distancia_metros(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = np.radians(lat1)
    phi2 = np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)

    a = np.sin(dphi/2)**2 + np.cos(phi1)*np.cos(phi2)*np.sin(dlambda/2)**2
    return 2 * R * np.arctan2(np.sqrt(a), np.sqrt(1 - a))

# ==============================
# CLUSTERING
# ==============================

def clusterizar_ubicaciones(df, radio=300):

    clusters = []

    for _, row in df.iterrows():

        lat, lon = row["lat"], row["lon"]
        if np.isnan(lat):
            continue

        asignado = False

        for c in clusters:

            d = distancia_metros(lat, lon, c["lat"], c["lon"])

            if d < radio:
                total = c["peso"] + row["peso"]

                c["lat"] = (c["lat"] * c["peso"] + lat * row["peso"]) / total
                c["lon"] = (c["lon"] * c["peso"] + lon * row["peso"]) / total
                c["peso"] = total
                c["count"] += 1

                asignado = True
                break

        if not asignado:
            clusters.append({
                "lat": lat,
                "lon": lon,
                "peso": row["peso"],
                "count": 1
            })

    return clusters

def obtener_ubic_principal(grupo, cache):

    g = grupo.copy()
    g[["lat","lon"]] = g["Coordenadas"].apply(lambda x: pd.Series(parse_coords(x)))

    g["peso"] = g.apply(
        lambda r: r["delta_horas"] * 2 if r["estado"] in ["ralenti","apagado"]
        else r["delta_horas"] * 0.3,
        axis=1
    )

    g = g.dropna(subset=["lat"])

    clusters = clusterizar_ubicaciones(g)

    if len(clusters) == 0:
        return ""

    mejor = max(clusters, key=lambda x: x["peso"])

    return obtener_municipio_cache(
        f"{mejor['lat']},{mejor['lon']}",
        cache
    )

# ==============================
# APP
# ==============================

files = st.file_uploader("Sube archivos", accept_multiple_files=True)

if files:

    lista_df = []

    for file in files:

        df_temp = leer_archivo(file)

        if df_temp is None or df_temp.empty:
            continue

        df_temp = df_temp.rename(columns={
            "Fecha y Hora": "fecha_hora",
            "Velocidad": "velocidad",
            "Ignicion*": "ignicion",
            "Conductor": "conductor",
            "Localización": "ubicacion"
        })

        df_temp["vehiculo"] = file.name[:6].upper()

        lista_df.append(df_temp)

    if len(lista_df) == 0:
        st.error("No hay datos válidos")
        st.stop()

    df = pd.concat(lista_df, ignore_index=True)

    # ==============================
    # 🔥 CACHE GEO (AQUÍ ESTÁ LA MAGIA)
    # ==============================

    coords_unicas = df["Coordenadas"].dropna().unique()
    cache_geo = construir_cache_municipios(coords_unicas)

    # ==============================
    # LIMPIEZA
    # ==============================

    df["fecha_hora"] = pd.to_datetime(df["fecha_hora"], errors="coerce")

    df["ignicion"] = df["ignicion"].astype(str).str.lower()
    df["ignicion_on"] = df["ignicion"].isin(["encendido"])

    df["velocidad"] = (
        df["velocidad"].astype(str)
        .str.replace(",", ".", regex=False)
        .str.extract(r"(\d+\.?\d*)")[0]
    )

    df["velocidad"] = pd.to_numeric(df["velocidad"], errors="coerce").fillna(0)

    df = df.sort_values(by=["vehiculo","fecha_hora"]).reset_index(drop=True)

    df["fecha"] = df["fecha_hora"].dt.date

    # ==============================
    # ESTADOS
    # ==============================

    df["estado"] = df.apply(
        lambda r: "conduciendo" if r["ignicion_on"] and r["velocidad"]>0
        else "ralenti" if r["ignicion_on"]
        else "apagado",
        axis=1
    )

    # ==============================
    # TIEMPO
    # ==============================

    df["fecha_siguiente"] = df.groupby("vehiculo")["fecha_hora"].shift(-1)

    df["delta_horas"] = (
        df["fecha_siguiente"] - df["fecha_hora"]
    ).dt.total_seconds()/3600

    df["delta_horas"] = df["delta_horas"].fillna(0)

    # ==============================
    # BLOQUES
    # ==============================

    df["grupo"] = (df["estado"] != df["estado"].shift()).cumsum()

    bloques = df.groupby(["vehiculo","grupo"]).agg({
        "estado":"first",
        "fecha_hora":["min","max"],
        "delta_horas":"sum",
        "ubicacion":["first","last"]
    })

    bloques.columns = [
        "estado","inicio","fin","duracion_horas",
        "ubic_inicio_txt","ubic_fin_txt"
    ]

    bloques = bloques.reset_index()

    # ==============================
    # KPIs
    # ==============================

    kpis_list = []

    for (vehiculo, fecha), grupo in df.groupby(["vehiculo","fecha"]):

        conductor = grupo["conductor"].dropna().iloc[0]

        inicio_jornada = grupo.loc[grupo["ignicion_on"],"fecha_hora"].min()
        fin_jornada = grupo.loc[grupo["ignicion_on"],"fecha_hora"].max()

        horas_conduccion = grupo.loc[grupo["estado"]=="conduciendo","delta_horas"].sum()
        horas_ralenti = grupo.loc[grupo["estado"]=="ralenti","delta_horas"].sum()
        horas_trabajo = horas_conduccion + horas_ralenti

        coord_final = grupo["Coordenadas"].dropna().iloc[-1]
        ubicacion = obtener_municipio_cache(coord_final, cache_geo)

        ubic_principal = obtener_ubic_principal(grupo, cache_geo)

        bloques_v = bloques[bloques["vehiculo"]==vehiculo]

        numero_paradas = 0
        horas_descanso = 0
        horas_pausa = 0

        for _, b in bloques_v.iterrows():

            inicio_dia = pd.Timestamp(fecha)
            fin_dia = inicio_dia + pd.Timedelta(days=1)

            inicio_real = max(b["inicio"], inicio_dia)
            fin_real = min(b["fin"], fin_dia)

            if inicio_real < fin_real:

                horas = (fin_real - inicio_real).total_seconds()/3600

                if b["estado"] in ["ralenti","apagado"] and horas >= UMBRAL_PARADA_MIN:
                    numero_paradas += 1

                if b["estado"]=="apagado":
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

    st.download_button("Descargar Excel", data=buffer, file_name="reporte.xlsx")
