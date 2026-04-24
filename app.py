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
# 🌍 GEO
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

# ==============================
# SUBIR ARCHIVOS
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

    if len(lista_df) == 0:
        st.error("No hay datos válidos")
        st.stop()

    df = pd.concat(lista_df, ignore_index=True)

    # LIMPIEZA
    df["fecha_hora"] = pd.to_datetime(df["fecha_hora"], errors="coerce")
    df = df.sort_values(["vehiculo","fecha_hora"]).reset_index(drop=True)

    df["ignicion_on"] = df["ignicion"].astype(str).str.lower().isin(["encendido"])

    df["velocidad"] = (
        df["velocidad"].astype(str)
        .str.replace(",", ".", regex=False)
        .str.extract(r"(\d+\.?\d*)")[0]
    )

    df["velocidad"] = pd.to_numeric(df["velocidad"], errors="coerce").fillna(0)

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

    # ==============================
    # KPIs (NO TOCAR)
    # ==============================

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

        numero_paradas = 0
        horas_descanso = 0
        horas_pausa = 0

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
            "ubic_principal": ""
        })

    kpis = pd.DataFrame(kpis_list)

    st.dataframe(kpis)

    # ==============================
    # 🔥 LLENAR UBICACIONES CORRECTAS
    # ==============================

    bloques_export = bloques.copy()

    inicio_ubica = []
    fin_ubica = []

    for _, b in bloques_export.iterrows():

        df_block = df[
            (df["vehiculo"] == b["vehiculo"]) &
            (df["fecha_hora"] >= b["inicio"]) &
            (df["fecha_hora"] <= b["fin"])
        ]

        if len(df_block) > 0 and "Localización" in df_block.columns:

            inicio_ubica.append(str(df_block.iloc[0]["Localización"]))
            fin_ubica.append(str(df_block.iloc[-1]["Localización"]))

        else:
            inicio_ubica.append("")
            fin_ubica.append("")

    bloques_export["inicio_ubica"] = inicio_ubica
    bloques_export["fin_ubica"] = fin_ubica

    # ==============================
    # EXPORTAR
    # ==============================

    buffer = io.BytesIO()

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:

        kpis.to_excel(writer, sheet_name="Resumen", index=False)
        bloques_export.to_excel(writer, sheet_name="Bloques", index=False)

    st.download_button(
        "Descargar Excel",
        data=buffer,
        file_name="reporte.xlsx"
    )
