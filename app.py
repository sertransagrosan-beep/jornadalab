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
# GEO (SE DEJA IGUAL)
# ==============================

cache_municipios = {}

def coord_a_municipio(lat, lon):
    if np.isnan(lat):
        return ""
    return f"{round(lat,3)}, {round(lon,3)}"  # ya no relevante para bloques

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
# AUX
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
            "Conductor": "conductor",
            "Localización": "ubicacion"   # 🔥 IMPORTANTE
        })

        df_temp["vehiculo"] = file.name[:6].upper()

        lista_df.append(df_temp)

    if len(lista_df) == 0:
        st.error("No hay datos válidos")
        st.stop()

    df = pd.concat(lista_df, ignore_index=True)

    # ==============================
    # LIMPIEZA
    # ==============================

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
    # TIEMPOS
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
        "delta_horas":"sum"
    })

    bloques.columns = ["estado","inicio","fin","duracion_horas"]
    bloques = bloques.reset_index()

    # ==============================
    # KPIs (SE DEJA IGUAL)
    # ==============================

    kpis_list = []

    for (vehiculo, fecha), grupo in df.groupby(["vehiculo","fecha"]):

        conductor = grupo["conductor"].dropna().iloc[0]

        inicio_jornada = grupo.loc[grupo["ignicion_on"],"fecha_hora"].min()
        fin_jornada = grupo.loc[grupo["ignicion_on"],"fecha_hora"].max()

        horas_conduccion = grupo.loc[grupo["estado"]=="conduciendo","delta_horas"].sum()
        horas_ralenti = grupo.loc[grupo["estado"]=="ralenti","delta_horas"].sum()
        horas_trabajo = horas_conduccion + horas_ralenti

        ubic_principal = ""  # dejamos vacío para no afectar

        kpis_list.append({
            "conductor": conductor,
            "vehiculo": vehiculo,
            "fecha": fecha,
            "ubicación": "",
            "inicio_jornada": inicio_jornada,
            "fin_jornada": fin_jornada,
            "numero_paradas": 0,
            "horas_trabajo": horas_trabajo,
            "horas_conduccion": horas_conduccion,
            "horas_descanso": 0,
            "horas_pausa": 0,
            "horas_ralenti": horas_ralenti,
            "ubic_principal": ubic_principal
        })

    kpis = pd.DataFrame(kpis_list)

    kpis["inicio_jornada"] = pd.to_datetime(kpis["inicio_jornada"]).dt.strftime("%I:%M %p").str.lstrip("0")
    kpis["fin_jornada"] = pd.to_datetime(kpis["fin_jornada"]).dt.strftime("%I:%M %p").str.lstrip("0")

    st.dataframe(kpis)

    # ==============================
    # EXPORTAR
    # ==============================

    buffer = io.BytesIO()

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:

        for conductor, df_conductor in kpis.groupby("conductor"):

            nombre_conductor = str(conductor)[:20]

            df_conductor.to_excel(writer, sheet_name=nombre_conductor, index=False)

            # ==============================
            # 🔥 BLOQUES OPTIMIZADO (SIN GEO)
            # ==============================

            vehiculos = df_conductor["vehiculo"].unique()

            bloques_cond = bloques[bloques["vehiculo"].isin(vehiculos)].copy()

            # 🔥 mapa rápido de ubicación por timestamp
            df_map = df[["vehiculo","fecha_hora","ubicacion"]].dropna()

            # merge para inicio
            bloques_cond = bloques_cond.merge(
                df_map,
                left_on=["vehiculo","inicio"],
                right_on=["vehiculo","fecha_hora"],
                how="left"
            ).rename(columns={"ubicacion":"ubic_inicio"}).drop(columns=["fecha_hora"])

            # merge para fin
            bloques_cond = bloques_cond.merge(
                df_map,
                left_on=["vehiculo","fin"],
                right_on=["vehiculo","fecha_hora"],
                how="left"
            ).rename(columns={"ubicacion":"ubic_fin"}).drop(columns=["fecha_hora"])

            nombre_bloques = f"{vehiculos[0]}_{nombre_conductor}"[:31]

            bloques_cond.to_excel(writer, sheet_name=nombre_bloques, index=False)

    st.download_button(
        "Descargar Excel",
        data=buffer,
        file_name="reporte.xlsx"
    )
