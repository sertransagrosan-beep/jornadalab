import streamlit as st
import pandas as pd
import numpy as np
import io
import requests
import time

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
# GEO (SE MANTIENE)
# ==============================

cache_municipios = {}

def coord_a_municipio(lat, lon):

    if np.isnan(lat):
        return ""

    key = f"{round(lat,4)}_{round(lon,4)}"

    if key in cache_municipios:
        return cache_municipios[key]

    try:
        url = "https://nominatim.openstreetmap.org/reverse"
        params = {"lat": lat, "lon": lon, "format": "json"}
        headers = {"User-Agent": "streamlit-app"}

        r = requests.get(url, params=params, headers=headers, timeout=5)

        if r.status_code == 200:
            data = r.json()
            address = data.get("address", {})

            ciudad = (
                address.get("city")
                or address.get("town")
                or address.get("village")
                or address.get("county")
                or ""
            )

            cache_municipios[key] = ciudad
            time.sleep(1)

            return ciudad

    except:
        pass

    return f"{round(lat,3)}, {round(lon,3)}"

# ==============================
# LECTOR
# ==============================

def leer_archivo(file):

    try:
        if file.name.endswith(".xlsx"):
            df = pd.read_excel(file)
        else:
            try:
                df = pd.read_csv(file, sep=";", encoding="utf-8")
            except:
                file.seek(0)
                df = pd.read_csv(file, sep=None, engine="python")

        df.columns = df.columns.astype(str).str.strip()
        df = df.loc[:, ~df.columns.str.contains("^Unnamed", na=False)]

        return df

    except:
        return None

# ==============================
# GEO AUX
# ==============================

def parse_coords(coord):
    try:
        lat, lon = map(float, str(coord).split(","))
        return lat, lon
    except:
        return np.nan, np.nan

def distancia_metros(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
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

def obtener_ubic_principal(grupo):

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

    return coord_a_municipio(mejor["lat"], mejor["lon"])

# ==============================
# AUTOAJUSTE EXCEL
# ==============================

def auto_ajustar_excel(ws, df):
    for i, col in enumerate(df.columns):
        try:
            max_len = max(df[col].astype(str).map(len).max(), len(col))
        except:
            max_len = len(col)
        ws.column_dimensions[chr(65 + i)].width = max_len + 2

def obtener_mes_nombre(df):
    try:
        fecha_max = pd.to_datetime(df["fecha"]).max()
        return fecha_max.strftime("%Y-%m")
    except:
        return "sin_fecha"

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
            "Conductor": "conductor"
        })

        df_temp["vehiculo"] = file.name[:6].upper()

        lista_df.append(df_temp)

    df = pd.concat(lista_df, ignore_index=True)

    df["fecha_hora"] = pd.to_datetime(df["fecha_hora"], errors="coerce")

    df["ignicion_on"] = df["ignicion"].astype(str).str.lower().isin(["encendido"])

    df["velocidad"] = (
        df["velocidad"].astype(str)
        .str.replace(",", ".", regex=False)
        .str.extract(r"(\d+\.?\d*)")[0]
    )

    df["velocidad"] = pd.to_numeric(df["velocidad"], errors="coerce").fillna(0)

    df = df.sort_values(["vehiculo","fecha_hora"]).reset_index(drop=True)

    df["fecha"] = df["fecha_hora"].dt.date

    df["estado"] = df.apply(
        lambda r: "conduciendo" if r["ignicion_on"] and r["velocidad"]>0
        else "ralenti" if r["ignicion_on"]
        else "apagado",
        axis=1
    )

    df["fecha_siguiente"] = df.groupby("vehiculo")["fecha_hora"].shift(-1)

    df["delta_horas"] = (
        df["fecha_siguiente"] - df["fecha_hora"]
    ).dt.total_seconds()/3600

    df["delta_horas"] = df["delta_horas"].fillna(0)

    df["grupo"] = (df["estado"] != df["estado"].shift()).cumsum()

    bloques = df.groupby(["vehiculo","grupo"]).agg({
        "estado":"first",
        "fecha_hora":["min","max"],
        "delta_horas":"sum"
    })

    bloques.columns = ["estado","inicio","fin","duracion_horas"]
    bloques = bloques.reset_index()

    # KPIs (NO TOCAR)
    kpis_list = []

    for (vehiculo, fecha), grupo in df.groupby(["vehiculo","fecha"]):

        conductor = grupo["conductor"].dropna().iloc[0]

        inicio_jornada = grupo.loc[grupo["ignicion_on"],"fecha_hora"].min()
        fin_jornada = grupo.loc[grupo["ignicion_on"],"fecha_hora"].max()

        horas_conduccion = grupo.loc[grupo["estado"]=="conduciendo","delta_horas"].sum()
        horas_ralenti = grupo.loc[grupo["estado"]=="ralenti","delta_horas"].sum()
        horas_trabajo = horas_conduccion + horas_ralenti

        lat, lon = parse_coords(grupo["Coordenadas"].dropna().iloc[-1])
        ubicacion = coord_a_municipio(lat, lon)

        ubic_principal = obtener_ubic_principal(grupo)

        kpis_list.append({
            "conductor": conductor,
            "vehiculo": vehiculo,
            "fecha": fecha,
            "ubicación": ubicacion,
            "inicio_jornada": inicio_jornada,
            "fin_jornada": fin_jornada,
            "horas_trabajo": horas_trabajo,
            "horas_conduccion": horas_conduccion,
            "horas_ralenti": horas_ralenti,
            "ubic_principal": ubic_principal
        })

    kpis = pd.DataFrame(kpis_list)

    st.dataframe(kpis)

    # EXPORTAR
    buffer = io.BytesIO()

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:

        for conductor, df_conductor in kpis.groupby("conductor"):

            nombre_conductor = str(conductor)[:20]

            # HOJA 1
            df_conductor.to_excel(writer, sheet_name=nombre_conductor, index=False)
            auto_ajustar_excel(writer.sheets[nombre_conductor], df_conductor)

            # HOJA 2
            vehiculos = df_conductor["vehiculo"].unique()
            bloques_cond = bloques[bloques["vehiculo"].isin(vehiculos)].copy()

            ubic_inicio = []
            ubic_fin = []

            for _, b in bloques_cond.iterrows():

                df_block = df[
                    (df["vehiculo"] == b["vehiculo"]) &
                    (df["fecha_hora"] >= b["inicio"]) &
                    (df["fecha_hora"] <= b["fin"])
                ]

                if len(df_block) > 0:
                    ubic_inicio.append(str(df_block.iloc[0].get("Localización", "")))
                    ubic_fin.append(str(df_block.iloc[-1].get("Localización", "")))
                else:
                    ubic_inicio.append("")
                    ubic_fin.append("")

            bloques_cond["ubic_inicio"] = ubic_inicio
            bloques_cond["ubic_fin"] = ubic_fin

            nombre_bloques = f"{vehiculos[0]}_{nombre_conductor}"[:31]
            bloques_cond.to_excel(writer, sheet_name=nombre_bloques, index=False)
            auto_ajustar_excel(writer.sheets[nombre_bloques], bloques_cond)

    mes = obtener_mes_nombre(kpis)
    nombre_archivo = f"reporte-{nombre_conductor}-{mes}.xlsx"

    st.download_button(
        "Descargar Excel",
        data=buffer,
        file_name=nombre_archivo
    )
