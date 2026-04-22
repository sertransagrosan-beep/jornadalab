import streamlit as st
import pandas as pd
import io
import re

st.title("Jornada Laboral Conductores")

# ==============================
# CONFIGURACIÓN
# ==============================

HORAS_MAX_JORNADA = st.number_input("Horas máximas jornada", value=8.0)
MIN_PAUSA = st.number_input("Pausa mínima (minutos)", value=30)
MIN_PARADA = st.number_input("Duración mínima parada (minutos)", value=20)

HORAS_MIN_PAUSA = MIN_PAUSA / 60
UMBRAL_PARADA = MIN_PARADA / 60

# ==============================
# LECTOR UNIVERSAL (CSV + EXCEL)
# ==============================

def leer_archivo(file):
    try:
        if file.name.endswith(".csv"):
            for enc in ["utf-8", "latin1", "cp1252"]:
                try:
                    return pd.read_csv(file, sep=";", encoding=enc)
                except:
                    continue

        elif file.name.endswith(".xlsx"):
            return pd.read_excel(file)

    except:
        return None

# ==============================
# LIMPIEZA SEGURA
# ==============================

def limpiar_columnas(df):
    df.columns = [str(c).strip() for c in df.columns]

    # eliminar columnas basura
    mask = ~pd.Series(df.columns).astype(str).str.contains("^Unnamed", na=False)
    df = df.loc[:, mask.values]

    return df

# ==============================
# NORMALIZAR COLUMNAS
# ==============================

def normalizar(df):
    rename = {
        "Fecha y Hora": "fecha_hora",
        "Velocidad": "velocidad",
        "Ignicion*": "ignicion",
        "Conductor": "conductor",
        "Localización": "ubicacion",
        "Coordenadas": "coordenadas"
    }

    df = df.rename(columns=rename)

    # asegurar columnas
    for col in ["fecha_hora", "velocidad", "ignicion"]:
        if col not in df.columns:
            return None

    return df

# ==============================
# UBICACIÓN INTELIGENTE
# ==============================

def limpiar_ubic(texto):
    if pd.isna(texto):
        return ""
    return re.sub(r"\s+", " ", str(texto).lower().strip())

def calcular_ubic_principal(grupo):

    g = grupo.copy()
    g["ubic_limpia"] = g["ubicacion"].apply(limpiar_ubic)

    g["peso"] = g.apply(
        lambda x: x["delta_horas"] * 2 if x["estado"] != "conduciendo" else x["delta_horas"],
        axis=1
    )

    resumen = g.groupby("ubic_limpia").agg({
        "delta_horas": "sum",
        "peso": "sum"
    })

    if len(resumen) == 0:
        return ""

    return resumen.sort_values("peso", ascending=False).index[0]

# ==============================
# APP
# ==============================

files = st.file_uploader("Sube archivos", accept_multiple_files=True)

if files:

    lista = []

    for file in files:

        df_temp = leer_archivo(file)

        if df_temp is None or df_temp.empty:
            st.warning(f"No se pudo leer: {file.name}")
            continue

        df_temp = limpiar_columnas(df_temp)
        df_temp = normalizar(df_temp)

        if df_temp is None:
            st.warning(f"Formato inválido: {file.name}")
            continue

        df_temp["vehiculo"] = file.name[:6].upper()
        lista.append(df_temp)

    if not lista:
        st.error("No se pudieron leer archivos válidos")
        st.stop()

    df = pd.concat(lista, ignore_index=True)

    # ==============================
    # LIMPIEZA DATOS
    # ==============================

    df["fecha_hora"] = pd.to_datetime(df["fecha_hora"], errors="coerce")

    df["velocidad"] = (
        df["velocidad"]
        .astype(str)
        .str.replace(",", ".", regex=False)
        .str.extract(r"(\d+\.?\d*)")[0]
    )
    df["velocidad"] = pd.to_numeric(df["velocidad"], errors="coerce").fillna(0)

    df["ignicion"] = df["ignicion"].astype(str).str.lower()
    df["ignicion_on"] = df["ignicion"].str.contains("encendido")

    df = df.sort_values(["vehiculo", "fecha_hora"])
    df["fecha"] = df["fecha_hora"].dt.date

    # ==============================
    # ESTADOS
    # ==============================

    df["estado"] = df.apply(
        lambda x: "conduciendo" if x["ignicion_on"] and x["velocidad"] > 0
        else "ralenti" if x["ignicion_on"]
        else "apagado",
        axis=1
    )

    # ==============================
    # TIEMPOS
    # ==============================

    df["fecha_sig"] = df.groupby("vehiculo")["fecha_hora"].shift(-1)

    df["delta_horas"] = (
        (df["fecha_sig"] - df["fecha_hora"])
        .dt.total_seconds() / 3600
    ).fillna(0)

    # ==============================
    # BLOQUES
    # ==============================

    df["grupo"] = (df["estado"] != df["estado"].shift()).cumsum()

    bloques = df.groupby(["vehiculo", "grupo"]).agg({
        "estado": "first",
        "fecha_hora": ["min", "max"],
        "delta_horas": "sum",
        "ubicacion": ["first", "last"]
    })

    bloques.columns = [
        "estado", "inicio", "fin",
        "duracion_horas",
        "ubic_inicio", "ubic_fin"
    ]

    bloques = bloques.reset_index()

    # ==============================
    # KPIs
    # ==============================

    kpis = []

    for (vehiculo, fecha), g in df.groupby(["vehiculo", "fecha"]):

        if g.empty:
            continue

        inicio = g["fecha_hora"].min()
        fin = g["fecha_hora"].max()

        horas_conduccion = g.loc[g["estado"]=="conduciendo","delta_horas"].sum()
        horas_ralenti = g.loc[g["estado"]=="ralenti","delta_horas"].sum()
        horas_trabajo = horas_conduccion + horas_ralenti

        ubic_principal = calcular_ubic_principal(g)

        # BLOQUES DEL DÍA
        b = bloques[bloques["vehiculo"] == vehiculo]

        numero_paradas = 0
        horas_descanso = 0
        horas_pausa = 0

        for _, row in b.iterrows():

            if row["duracion_horas"] <= 0:
                continue

            if row["estado"] in ["ralenti","apagado"] and row["duracion_horas"] >= UMBRAL_PARADA:
                numero_paradas += 1

            if row["estado"] == "apagado":
                if row["duracion_horas"] >= 4:
                    horas_descanso += row["duracion_horas"]
                elif row["duracion_horas"] >= HORAS_MIN_PAUSA:
                    horas_pausa += row["duracion_horas"]

        kpis.append({
            "conductor": g["conductor"].dropna().iloc[0] if "conductor" in g else "N/A",
            "vehiculo": vehiculo,
            "fecha": fecha,
            "origen": "",
            "destino": "",
            "ubicacion": g["ubicacion"].iloc[-1] if "ubicacion" in g else "",
            "inicio_jornada": inicio,
            "fin_jornada": fin,
            "numero_paradas": numero_paradas,
            "horas_trabajo": horas_trabajo,
            "horas_conduccion": horas_conduccion,
            "horas_descanso": horas_descanso,
            "horas_pausa": horas_pausa,
            "horas_ralenti": horas_ralenti,
            "ubic_principal": ubic_principal
        })

    kpis = pd.DataFrame(kpis).round(2)

    if kpis.empty:
        st.error("No se generaron KPIs")
        st.stop()

    # FORMATO
    kpis["inicio_jornada"] = pd.to_datetime(kpis["inicio_jornada"]).dt.strftime("%I:%M %p").str.lstrip("0")
    kpis["fin_jornada"] = pd.to_datetime(kpis["fin_jornada"]).dt.strftime("%I:%M %p").str.lstrip("0")

    st.dataframe(kpis)

    # ==============================
    # EXPORTAR
    # ==============================

    buffer = io.BytesIO()

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:

        kpis.to_excel(writer, sheet_name="KPIs", index=False)
        bloques.to_excel(writer, sheet_name="Bloques", index=False)

    st.download_button(
        "Descargar Excel",
        buffer,
        "reporte.xlsx"
    )
