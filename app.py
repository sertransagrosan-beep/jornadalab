import streamlit as st
import pandas as pd
import io
import re
import requests
from functools import lru_cache

st.title("🚛 Jornada Laboral Conductores (Nivel PRO)")

# ==============================
# CONFIGURACIÓN
# ==============================

HORAS_MAX_JORNADA = st.number_input("Horas máximas jornada", value=8.0, step=0.1)
HORAS_DESCANSO_LARGO = st.number_input("Horas descanso largo", value=4.0, step=0.1)
MIN_PAUSA = st.number_input("Pausa mínima (minutos)", value=30, step=1)
MIN_PARADA = st.number_input("Duración mínima parada (minutos)", value=20, step=1)

HORAS_MIN_PAUSA = MIN_PAUSA / 60
UMBRAL_PARADA_MIN = MIN_PARADA / 60

# ==============================
# 🌍 MUNICIPIOS (API + CACHE)
# ==============================

@lru_cache(maxsize=50000)
def obtener_municipio(lat, lon):
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json"
        r = requests.get(url, headers={"User-Agent": "gps-app"})
        data = r.json()

        address = data.get("address", {})
        return (
            address.get("city") or
            address.get("town") or
            address.get("village") or
            address.get("county") or
            ""
        )
    except:
        return ""

def extraer_lat_lon(coord):
    try:
        lat, lon = coord.split(",")
        return float(lat.strip()), float(lon.strip())
    except:
        return None, None

# ==============================
# 📍 UBICACIÓN INTELIGENTE
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

    def peso(row):
        if row["estado"] in ["ralenti", "apagado"]:
            return row["delta_horas"] * 2
        return row["delta_horas"]

    g["peso"] = g.apply(peso, axis=1)

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
# 📂 SUBIR ARCHIVOS
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

    df["ignicion"] = df["ignicion"].astype(str).str.lower().str.strip()
    df["ignicion_on"] = df["ignicion"].isin(["encendido"])

    df["velocidad"] = (
        df["velocidad"].astype(str)
        .str.replace(",", ".", regex=False)
        .str.extract(r"(\d+\.?\d*)")[0]
    )

    df["velocidad"] = pd.to_numeric(df["velocidad"], errors="coerce").fillna(0)

    df = df.sort_values(by=["vehiculo", "fecha_hora"]).reset_index(drop=True)

    df["fecha"] = df["fecha_hora"].dt.date

    # ==============================
    # MUNICIPIOS
    # ==============================

    df["lat"], df["lon"] = zip(*df["coordenadas"].apply(extraer_lat_lon))
    df["municipio"] = df.apply(lambda x: obtener_municipio(x["lat"], x["lon"]) if pd.notna(x["lat"]) else "", axis=1)

    # ==============================
    # ESTADOS
    # ==============================

    def estado(row):
        if row["ignicion_on"] and row["velocidad"] > 0:
            return "conduciendo"
        elif row["ignicion_on"]:
            return "ralenti"
        return "apagado"

    df["estado"] = df.apply(estado, axis=1)

    # ==============================
    # TIEMPOS
    # ==============================

    df["fecha_sig"] = df.groupby("vehiculo")["fecha_hora"].shift(-1)

    df["delta_horas"] = (
        df["fecha_sig"] - df["fecha_hora"]
    ).dt.total_seconds() / 3600

    df["delta_horas"] = df["delta_horas"].fillna(0)

    # ==============================
    # BLOQUES
    # ==============================

    df["grupo"] = (df["estado"] != df["estado"].shift()).cumsum()

    bloques = df.groupby(["vehiculo", "grupo"]).agg({
        "estado": "first",
        "fecha_hora": ["min", "max"],
        "delta_horas": "sum",
        "ubicacion": ["first", "last"],
        "municipio": ["first", "last"]
    })

    bloques.columns = [
        "estado", "inicio", "fin", "duracion_horas",
        "ubic_inicio", "ubic_fin",
        "mun_inicio", "mun_fin"
    ]

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

        ubic_principal = calcular_ubic_principal(grupo)

        ubic_fin = grupo["municipio"].iloc[-1] if len(grupo) > 0 else ""

        # BLOQUES DEL DÍA
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

            ini = max(b["inicio"], inicio_dia)
            fin = min(b["fin"], fin_dia)

            if ini < fin:
                horas = (fin - ini).total_seconds() / 3600

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
            "ubicacion": ubic_fin,
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

    st.subheader("📊 Resumen")
    st.dataframe(kpis)

    # ==============================
    # EXPORTAR EXCEL
    # ==============================

    buffer = io.BytesIO()

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:

        for conductor, df_c in kpis.groupby("conductor"):

            hoja = re.sub(r'[\\/*?:\\[\\]]', "", str(conductor))[:31]

            df_c.to_excel(writer, sheet_name=hoja, index=False)

            ws = writer.sheets[hoja]

            for i, col in enumerate(df_c.columns):
                max_len = max(df_c[col].astype(str).apply(len).max(), len(col))
                ws.column_dimensions[chr(65+i)].width = max_len + 2

            # BLOQUES
            bloques_cond = bloques[bloques["vehiculo"].isin(df_c["vehiculo"])]

            hoja_b = f"Bloques {hoja}"[:31]

            bloques_cond.to_excel(writer, sheet_name=hoja_b, index=False)

    st.download_button(
        "📥 Descargar Excel",
        buffer,
        file_name="reporte_jornada.xlsx"
    )
