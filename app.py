import streamlit as st
import pandas as pd
import io
import re
import requests
import time

st.title("Jornada Laboral Conductores")

# ==============================
# CONFIGURACIÓN
# ==============================

HORAS_MAX_JORNADA = st.number_input("Horas máximas jornada", value=8.0, step=0.1)
HORAS_DESCANSO_LARGO = st.number_input("Horas descanso largo", value=4.0, step=0.1)
MIN_PAUSA = st.number_input("Pausa mínima (minutos)", value=34, step=1)
MIN_PARADA = st.number_input("Duración mínima parada (minutos)", value=17, step=1)

HORAS_MIN_PAUSA = MIN_PAUSA / 60
UMBRAL_PARADA_MIN = MIN_PARADA / 60

# ==============================
# CACHE MUNICIPIOS
# ==============================

@st.cache_data(show_spinner=False)
def obtener_municipio(lat, lon):
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}"
        headers = {"User-Agent": "streamlit-app"}

        r = requests.get(url, headers=headers, timeout=5)
        data = r.json()

        address = data.get("address", {})

        return (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("municipality")
            or ""
        )
    except:
        return ""

def convertir_coordenadas(coord):
    try:
        lat, lon = coord.split(",")
        return float(lat.strip()), float(lon.strip())
    except:
        return None, None

# ==============================
# FUNCIONES UBICACIÓN PRO
# ==============================

def limpiar_ubicacion(texto):
    if pd.isna(texto):
        return ""
    texto = str(texto).lower().strip()
    texto = re.sub(r'\s+', ' ', texto)
    return texto

def calcular_ubic_principal(grupo):

    g = grupo.copy()
    g["ubic_limpia"] = g["ubicacion"].apply(limpiar_ubicacion)

    def peso_estado(row):
        if row["estado"] in ["ralenti", "apagado"]:
            return row["delta_horas"] * 2
        else:
            return row["delta_horas"]

    g["peso"] = g.apply(peso_estado, axis=1)

    resumen = g.groupby("ubic_limpia").agg({
        "delta_horas": "sum",
        "peso": "sum",
        "estado": "count"
    }).rename(columns={"estado": "frecuencia"})

    if len(resumen) == 0:
        return ""

    resumen["score"] = (
        resumen["peso"] * 0.7 +
        resumen["delta_horas"] * 0.2 +
        resumen["frecuencia"] * 0.1
    )

    return resumen.sort_values("score", ascending=False).index[0]

# ==============================
# SUBIR ARCHIVOS
# ==============================

files = st.file_uploader("Sube archivos CSV", accept_multiple_files=True)

if files:

    lista_df = []

    for file in files:
        df_temp = pd.read_csv(file, sep=";", encoding="utf-8")

        df_temp.columns = df_temp.columns.str.strip()

        df_temp = df_temp.rename(columns={
            "Fecha y Hora": "fecha_hora",
            "Velocidad": "velocidad",
            "Ignicion*": "ignicion",
            "Conductor": "conductor",
            "Localización": "ubicacion",
            "Coordenadas": "coordenadas"
        })

        df_temp["vehiculo"] = file.name[:6].upper()

        lista_df.append(df_temp)

    df = pd.concat(lista_df, ignore_index=True)

    # ==============================
    # LIMPIEZA
    # ==============================

    df["fecha_hora"] = pd.to_datetime(df["fecha_hora"], errors="coerce")

    df["ignicion"] = df["ignicion"].astype(str).str.strip().str.lower()
    df["ignicion_on"] = df["ignicion"].isin(["encendido"])

    df["velocidad"] = (
        df["velocidad"]
        .astype(str)
        .str.replace(",", ".", regex=False)
        .str.extract(r"(\d+\.?\d*)")[0]
    )

    df["velocidad"] = pd.to_numeric(df["velocidad"], errors="coerce").fillna(0)

    df = df.sort_values(by=["vehiculo", "fecha_hora"]).reset_index(drop=True)

    df["fecha"] = df["fecha_hora"].dt.date

    # ==============================
    # MUNICIPIOS (NUEVO)
    # ==============================

    if "coordenadas" in df.columns:

        coords_unicas = df["coordenadas"].dropna().unique()
        mapa_municipios = {}

        with st.spinner("Obteniendo municipios..."):
            for coord in coords_unicas[:200]:  # límite seguridad
                lat, lon = convertir_coordenadas(coord)

                if lat is not None:
                    municipio = obtener_municipio(lat, lon)
                    mapa_municipios[coord] = municipio
                    time.sleep(0.2)

        df["municipio"] = df["coordenadas"].map(mapa_municipios)
        df["ubicacion"] = df["municipio"].fillna(df["ubicacion"])

    # ==============================
    # ESTADOS
    # ==============================

    def clasificar_estado(row):
        if row["ignicion_on"] and row["velocidad"] > 0:
            return "conduciendo"
        elif row["ignicion_on"] and row["velocidad"] == 0:
            return "ralenti"
        else:
            return "apagado"

    df["estado"] = df.apply(clasificar_estado, axis=1)

    # ==============================
    # TIEMPOS
    # ==============================

    df["fecha_siguiente"] = df.groupby("vehiculo")["fecha_hora"].shift(-1)

    df["delta_horas"] = (
        df["fecha_siguiente"] - df["fecha_hora"]
    ).dt.total_seconds() / 3600

    df["delta_horas"] = df["delta_horas"].fillna(0)

    # ==============================
    # BLOQUES
    # ==============================

    df["grupo"] = (df["estado"] != df["estado"].shift()).cumsum()

    bloques_list = []

    for (vehiculo, grupo), g in df.groupby(["vehiculo", "grupo"]):

        g = g.sort_values("fecha_hora")

        bloques_list.append({
            "vehiculo": vehiculo,
            "grupo": grupo,
            "estado": g["estado"].iloc[0],
            "inicio": g["fecha_hora"].iloc[0],
            "fin": g["fecha_hora"].iloc[-1],
            "duracion_horas": g["delta_horas"].sum(),
            "ubic_inicio": g["ubicacion"].iloc[0],
            "ubic_fin": g["ubicacion"].iloc[-1]
        })

    bloques = pd.DataFrame(bloques_list)

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

        ubic_inicio = grupo.loc[grupo["fecha_hora"] == inicio_jornada, "ubicacion"].iloc[0] if pd.notna(inicio_jornada) else ""
        ubic_fin = grupo.loc[grupo["fecha_hora"] == fin_jornada, "ubicacion"].iloc[0] if pd.notna(fin_jornada) else ""
        ubic_principal = calcular_ubic_principal(grupo)

        numero_paradas = 0
        horas_descanso = 0
        horas_pausa = 0

        for _, b in bloques.iterrows():

            if b["estado"] in ["ralenti", "apagado"] and b["duracion_horas"] >= UMBRAL_PARADA_MIN:
                numero_paradas += 1

            if b["estado"] == "apagado":
                if b["duracion_horas"] >= HORAS_DESCANSO_LARGO:
                    horas_descanso += b["duracion_horas"]
                elif b["duracion_horas"] >= HORAS_MIN_PAUSA:
                    horas_pausa += b["duracion_horas"]

        kpis_list.append({
            "conductor": conductor,
            "vehiculo": vehiculo,
            "fecha": fecha,
            "inicio_jornada": inicio_jornada,
            "fin_jornada": fin_jornada,
            "ubic_inicio": ubic_inicio,
            "ubic_fin": ubic_fin,
            "ubic_principal": ubic_principal,
            "numero_paradas": numero_paradas,
            "horas_trabajo": horas_trabajo,
            "horas_conduccion": horas_conduccion,
            "horas_ralenti": horas_ralenti,
            "horas_descanso": horas_descanso,
            "horas_pausa": horas_pausa
        })

    kpis = pd.DataFrame(kpis_list).round(2)

    kpis["inicio_jornada"] = pd.to_datetime(kpis["inicio_jornada"]).dt.strftime("%I:%M %p").str.lstrip("0")
    kpis["fin_jornada"] = pd.to_datetime(kpis["fin_jornada"]).dt.strftime("%I:%M %p").str.lstrip("0")

    st.subheader("Resumen por conductor")
    st.dataframe(kpis)

    buffer = io.BytesIO()

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        kpis.to_excel(writer, sheet_name="KPIs", index=False)
        bloques.to_excel(writer, sheet_name="Bloques", index=False)

    st.download_button("Descargar Excel", buffer, "reporte.xlsx")
