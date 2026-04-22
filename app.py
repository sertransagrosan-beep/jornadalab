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
MIN_PAUSA = st.number_input("Pausa mínima (minutos)", value=30)
MIN_PARADA = st.number_input("Duración mínima parada (minutos)", value=20)

HORAS_MIN_PAUSA = MIN_PAUSA / 60
UMBRAL_PARADA_MIN = MIN_PARADA / 60

# ==============================
# PARSER ROBUSTO
# ==============================

def leer_archivo(file):

    nombre = file.name.lower()

    # EXCEL
    if nombre.endswith(".xlsx") or nombre.endswith(".xls"):
        try:
            df = pd.read_excel(file)
            return df
        except:
            return pd.DataFrame()

    # CSV
    for enc in ["utf-8", "latin1", "ISO-8859-1"]:
        for sep in [";", ",", "\t", "|"]:
            try:
                file.seek(0)
                df = pd.read_csv(file, sep=sep, encoding=enc, engine="python", on_bad_lines="skip")
                if len(df) > 5:
                    return df
            except:
                continue

    return pd.DataFrame()

# ==============================
# DETECCIÓN DE COLUMNAS
# ==============================

def detectar_columnas(df):

    cols = {c.lower(): c for c in df.columns}

    def buscar(palabras):
        for k, v in cols.items():
            if any(p in k for p in palabras):
                return v
        return None

    return {
        "fecha": buscar(["fecha"]),
        "velocidad": buscar(["velocidad"]),
        "ignicion": buscar(["ignicion", "ignición"]),
        "conductor": buscar(["conductor"]),
        "ubicacion": buscar(["local", "ubic"]),
        "coordenadas": buscar(["coord"])
    }

# ==============================
# UBICACIÓN PRO
# ==============================

def limpiar_ubicacion(x):
    if pd.isna(x):
        return ""
    return re.sub(r"\s+", " ", str(x).lower().strip())

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

    if resumen.empty:
        return ""

    resumen["score"] = (
        resumen["peso"] * 0.7 +
        resumen["delta_horas"] * 0.2 +
        resumen["frecuencia"] * 0.1
    )

    return resumen.sort_values("score", ascending=False).index[0]

# ==============================
# CARGA
# ==============================

files = st.file_uploader("Sube archivos", accept_multiple_files=True)

if files:

    lista_df = []

    for file in files:

        df_temp = leer_archivo(file)

        if df_temp.empty:
            st.warning(f"No se pudo leer: {file.name}")
            continue

        # LIMPIAR COLUMNAS
        df_temp.columns = [str(c).strip() for c in df_temp.columns]
        df_temp = df_temp.loc[:, ~pd.Series(df_temp.columns).str.contains("^Unnamed", na=False)]

        cols = detectar_columnas(df_temp)

        if not cols["fecha"]:
            st.warning(f"Archivo sin fecha: {file.name}")
            continue

        df_temp = df_temp.rename(columns={
            cols["fecha"]: "fecha_hora",
            cols["velocidad"]: "velocidad",
            cols["ignicion"]: "ignicion",
            cols["conductor"]: "conductor",
            cols["ubicacion"]: "ubicacion"
        })

        df_temp["vehiculo"] = file.name[:6].upper()

        lista_df.append(df_temp)

    if not lista_df:
        st.error("❌ No se pudieron leer archivos válidos")
        st.stop()

    df = pd.concat(lista_df, ignore_index=True)

    # ==============================
    # LIMPIEZA
    # ==============================

    df["fecha_hora"] = pd.to_datetime(df["fecha_hora"], errors="coerce")

    df["ignicion"] = df["ignicion"].astype(str).str.lower()
    df["ignicion_on"] = df["ignicion"].isin(["encendido"])

    df["velocidad"] = pd.to_numeric(
        df["velocidad"].astype(str).str.replace(",", ".", regex=False)
        .str.extract(r"(\d+\.?\d*)")[0],
        errors="coerce"
    ).fillna(0)

    df = df.sort_values(["vehiculo", "fecha_hora"])
    df["fecha"] = df["fecha_hora"].dt.date

    # ==============================
    # ESTADOS
    # ==============================

    def estado(r):
        if r["ignicion_on"] and r["velocidad"] > 0:
            return "conduciendo"
        elif r["ignicion_on"]:
            return "ralenti"
        return "apagado"

    df["estado"] = df.apply(estado, axis=1)

    # ==============================
    # TIEMPOS
    # ==============================

    df["sig"] = df.groupby("vehiculo")["fecha_hora"].shift(-1)

    df["delta_horas"] = (
        df["sig"] - df["fecha_hora"]
    ).dt.total_seconds() / 3600

    df["delta_horas"] = df["delta_horas"].clip(lower=0).fillna(0)

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

    bloques.columns = ["estado", "inicio", "fin", "duracion_horas", "ubic_inicio", "ubic_fin"]
    bloques = bloques.reset_index()

    # ==============================
    # KPIs
    # ==============================

    kpis = []

    for (vehiculo, fecha), g in df.groupby(["vehiculo", "fecha"]):

        conductor = g["conductor"].dropna().iloc[0] if "conductor" in g else "N/A"

        inicio = g.loc[g["ignicion_on"], "fecha_hora"].min()
        fin = g.loc[g["ignicion_on"], "fecha_hora"].max()

        hc = g.loc[g["estado"] == "conduciendo", "delta_horas"].sum()
        hr = g.loc[g["estado"] == "ralenti", "delta_horas"].sum()
        ht = hc + hr

        ubic_principal = calcular_ubic_principal(g)
        ubic_fin = g.iloc[-1]["ubicacion"] if "ubicacion" in g else ""

        inicio_dia = pd.Timestamp(fecha)
        fin_dia = inicio_dia + pd.Timedelta(days=1)

        bloques_dia = bloques[
            (bloques["vehiculo"] == vehiculo) &
            (bloques["inicio"] < fin_dia) &
            (bloques["fin"] > inicio_dia)
        ]

        paradas = 0
        descanso = 0
        pausa = 0

        for _, b in bloques_dia.iterrows():
            ini = max(b["inicio"], inicio_dia)
            finb = min(b["fin"], fin_dia)

            if ini < finb:
                h = (finb - ini).total_seconds() / 3600

                if b["estado"] in ["ralenti", "apagado"] and h >= UMBRAL_PARADA_MIN:
                    paradas += 1

                if b["estado"] == "apagado":
                    if h >= HORAS_DESCANSO_LARGO:
                        descanso += h
                    elif h >= HORAS_MIN_PAUSA:
                        pausa += h

        kpis.append({
            "conductor": conductor,
            "vehiculo": vehiculo,
            "fecha": fecha,
            "origen": "",
            "destino": "",
            "ubicación": ubic_fin,
            "inicio_jornada": inicio,
            "fin_jornada": fin,
            "numero_paradas": paradas,
            "horas_trabajo": ht,
            "horas_conduccion": hc,
            "horas_descanso": descanso,
            "horas_pausa": pausa,
            "horas_ralenti": hr,
            "ubic_principal": ubic_principal
        })

    kpis = pd.DataFrame(kpis).round(2)

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

    st.download_button("Descargar Excel", buffer, "reporte.xlsx")
