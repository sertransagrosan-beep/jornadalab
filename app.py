import streamlit as st
import pandas as pd
import io
import re

st.title("Jornada Laboral Conductores")

# ==============================
# CONFIGURACIÓN
# ==============================

HORAS_MAX_JORNADA = st.number_input("Horas máximas jornada", value=8.0)
HORAS_DESCANSO_LARGO = st.number_input("Horas descanso largo", value=4.0)
MIN_PAUSA = st.number_input("Pausa mínima (minutos)", value=30, step=1)
MIN_PARADA = st.number_input("Duración mínima parada (minutos)", value=20, step=1)

HORAS_MIN_PAUSA = MIN_PAUSA / 60
UMBRAL_PARADA_MIN = MIN_PARADA / 60

# ==============================
# PARSER INTELIGENTE
# ==============================

def leer_archivo_inteligente(file):

    nombre = file.name.lower()

    # EXCEL
    if nombre.endswith(".xlsx") or nombre.endswith(".xls"):
        try:
            df = pd.read_excel(file)
            df = df.dropna(how="all")
            return df
        except:
            return pd.DataFrame()

    # CSV
    separadores = [";", ",", "\t", "|"]
    encodings = ["utf-8", "latin1", "ISO-8859-1"]

    for enc in encodings:
        for sep in separadores:
            try:
                file.seek(0)
                df = pd.read_csv(file, sep=sep, encoding=enc, engine="python", on_bad_lines="skip")

                if len(df) > 10:
                    return df
            except:
                continue

    return pd.DataFrame()

# ==============================
# LIMPIEZA UBICACIÓN
# ==============================

def limpiar_ubicacion(x):
    if pd.isna(x):
        return ""
    x = str(x).lower().strip()
    x = re.sub(r"\s+", " ", x)
    return x

# ==============================
# UBICACIÓN PRINCIPAL PRO
# ==============================

def calcular_ubic_principal(grupo):

    g = grupo.copy()
    g["ubic_limpia"] = g["ubicacion"].apply(limpiar_ubicacion)

    g["peso"] = g.apply(
        lambda r: r["delta_horas"] * 2 if r["estado"] in ["ralenti", "apagado"] else r["delta_horas"],
        axis=1
    )

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
# CARGA DE ARCHIVOS
# ==============================

files = st.file_uploader("Sube archivos (CSV o Excel)", accept_multiple_files=True)

if files:

    lista_df = []

    for file in files:

        df_temp = leer_archivo_inteligente(file)

        if df_temp.empty:
            st.warning(f"No se pudo leer: {file.name}")
            continue

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

    if len(lista_df) == 0:
        st.error("❌ No se pudieron leer archivos válidos")
        st.stop()

    df = pd.concat(lista_df, ignore_index=True)

    # ==============================
    # LIMPIEZA
    # ==============================

    df["fecha_hora"] = pd.to_datetime(df["fecha_hora"], errors="coerce")

    df["ignicion"] = df["ignicion"].astype(str).str.lower()
    df["ignicion_on"] = df["ignicion"].isin(["encendido"])

    df["velocidad"] = (
        df["velocidad"]
        .astype(str)
        .str.replace(",", ".", regex=False)
        .str.extract(r"(\d+\.?\d*)")[0]
    )

    df["velocidad"] = pd.to_numeric(df["velocidad"], errors="coerce").fillna(0)

    df = df.sort_values(["vehiculo", "fecha_hora"])
    df["fecha"] = df["fecha_hora"].dt.date

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
        "ubicacion": ["first", "last"]
    })

    bloques.columns = [
        "estado", "inicio", "fin", "duracion_horas",
        "ubic_inicio", "ubic_fin"
    ]

    bloques = bloques.reset_index()

    # ==============================
    # KPIs
    # ==============================

    kpis_list = []

    for (vehiculo, fecha), grupo in df.groupby(["vehiculo", "fecha"]):

        conductor = grupo["conductor"].dropna().iloc[0] if "conductor" in grupo else "N/A"

        inicio = grupo.loc[grupo["ignicion_on"], "fecha_hora"].min()
        fin = grupo.loc[grupo["ignicion_on"], "fecha_hora"].max()

        horas_conduccion = grupo.loc[grupo["estado"] == "conduciendo", "delta_horas"].sum()
        horas_ralenti = grupo.loc[grupo["estado"] == "ralenti", "delta_horas"].sum()
        horas_trabajo = horas_conduccion + horas_ralenti

        ubic_inicio = grupo.iloc[0]["ubicacion"] if "ubicacion" in grupo else ""
        ubic_fin = grupo.iloc[-1]["ubicacion"] if "ubicacion" in grupo else ""
        ubic_principal = calcular_ubic_principal(grupo)

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
            finb = min(b["fin"], fin_dia)

            if ini < finb:
                horas = (finb - ini).total_seconds() / 3600

                if b["estado"] in ["ralenti", "apagado"] and horas >= UMBRAL_PARADA_MIN:
                    numero_paradas += 1

                if b["estado"] == "apagado":
                    if horas >= HORAS_DESCANSO_LARGO:
                        horas_descanso += horas
                    elif horas >= HORAS_MIN_PAUSA:
                        horas_pausa += horas

        horas_extra = max(0, horas_trabajo - HORAS_MAX_JORNADA)

        kpis_list.append({
            "conductor": conductor,
            "vehiculo": vehiculo,
            "fecha": fecha,
            "origen": "",
            "destino": "",
            "ubicación": ubic_fin,
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

    kpis = pd.DataFrame(kpis_list).round(2)

    # FORMATO HORA
    kpis["inicio_jornada"] = pd.to_datetime(kpis["inicio_jornada"]).dt.strftime("%I:%M %p").str.lstrip("0")
    kpis["fin_jornada"] = pd.to_datetime(kpis["fin_jornada"]).dt.strftime("%I:%M %p").str.lstrip("0")

    st.dataframe(kpis)

    # ==============================
    # EXPORTAR EXCEL
    # ==============================

    buffer = io.BytesIO()

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:

        kpis.to_excel(writer, sheet_name="Resumen", index=False)
        bloques.to_excel(writer, sheet_name="Bloques", index=False)

        for sheet in writer.sheets:
            ws = writer.sheets[sheet]
            df_sheet = kpis if sheet == "Resumen" else bloques

            for i, col in enumerate(df_sheet.columns):
                try:
                    max_len = max(df_sheet[col].astype(str).map(len).max(), len(col))
                except:
                    max_len = len(col)
                ws.column_dimensions[chr(65+i)].width = max_len + 2

    st.download_button(
        "📥 Descargar Excel",
        buffer,
        "reporte_jornada.xlsx"
    )
