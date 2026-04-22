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
# PARSER INTELIGENTE PRO
# ==============================

def leer_csv_inteligente(file):

    separadores = [";", ",", "\t", "|"]
    encodings = ["utf-8", "latin1", "ISO-8859-1"]

    for enc in encodings:
        for sep in separadores:
            try:
                file.seek(0)

                df = pd.read_csv(
                    file,
                    sep=sep,
                    encoding=enc,
                    engine="python",
                    on_bad_lines="skip"
                )

                columnas = [c.lower() for c in df.columns]

                if (
                    len(df) > 10 and
                    any("fecha" in c for c in columnas)
                ):
                    return df

            except:
                continue

    # fallback
    try:
        file.seek(0)
        df = pd.read_csv(file, engine="python", on_bad_lines="skip")
        if len(df) > 10:
            return df
    except:
        pass

    return pd.DataFrame()

# ==============================
# LIMPIEZA UBICACIÓN
# ==============================

def limpiar_ubicacion(texto):
    if pd.isna(texto):
        return ""
    texto = str(texto).lower().strip()
    texto = re.sub(r'\s+', ' ', texto)
    return texto

# ==============================
# UBICACIÓN PRINCIPAL PRO
# ==============================

def calcular_ubic_principal(grupo):

    if "ubicacion" not in grupo:
        return ""

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

    if resumen.empty:
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

        df_temp = leer_csv_inteligente(file)

        st.write("📄 Archivo:", file.name)

        if df_temp.empty:
            st.warning("No se pudo leer este archivo")
            continue

        df_temp.columns = df_temp.columns.str.strip().str.lower()

        st.write("Columnas detectadas:", df_temp.columns.tolist())
        st.write("Filas:", len(df_temp))

        df_temp = df_temp.rename(columns={
            "fecha y hora": "fecha_hora",
            "fecha": "fecha_hora",
            "velocidad": "velocidad",
            "ignicion*": "ignicion",
            "ignicion": "ignicion",
            "conductor": "conductor",
            "localización": "ubicacion",
            "localizacion": "ubicacion"
        })

        df_temp["vehiculo"] = file.name[:6].upper()

        lista_df.append(df_temp)

    if not lista_df:
        st.error("❌ No se pudieron leer archivos válidos")
        st.stop()

    df = pd.concat(lista_df, ignore_index=True)

    # ==============================
    # VALIDACIÓN
    # ==============================

    if "fecha_hora" not in df.columns:
        st.error("❌ No se encontró columna de fecha")
        st.stop()

    # ==============================
    # LIMPIEZA
    # ==============================

    df["fecha_hora"] = pd.to_datetime(df["fecha_hora"], errors="coerce")

    df["ignicion"] = df.get("ignicion", "").astype(str).str.lower().str.strip()
    df["ignicion_on"] = df["ignicion"].isin(["encendido"])

    df["velocidad"] = (
        df.get("velocidad", 0)
        .astype(str)
        .str.replace(",", ".", regex=False)
        .str.extract(r"(\d+\.?\d*)")[0]
    )

    df["velocidad"] = pd.to_numeric(df["velocidad"], errors="coerce").fillna(0)

    df = df.sort_values(["vehiculo", "fecha_hora"]).reset_index(drop=True)
    df["fecha"] = df["fecha_hora"].dt.date

    # ==============================
    # ESTADOS
    # ==============================

    def estado(row):
        if row["ignicion_on"] and row["velocidad"] > 0:
            return "conduciendo"
        elif row["ignicion_on"]:
            return "ralenti"
        else:
            return "apagado"

    df["estado"] = df.apply(estado, axis=1)

    # ==============================
    # DELTA TIEMPO
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

    kpis_list = []

    for (vehiculo, fecha), grupo in df.groupby(["vehiculo", "fecha"]):

        conductor = grupo["conductor"].dropna().iloc[0] if "conductor" in grupo else "N/A"

        inicio_jornada = grupo.loc[grupo["ignicion_on"], "fecha_hora"].min()
        fin_jornada = grupo.loc[grupo["ignicion_on"], "fecha_hora"].max()

        horas_conduccion = grupo.loc[grupo["estado"] == "conduciendo", "delta_horas"].sum()
        horas_ralenti = grupo.loc[grupo["estado"] == "ralenti", "delta_horas"].sum()
        horas_trabajo = horas_conduccion + horas_ralenti

        # UBICACIONES
        try:
            ubic_inicio = grupo.loc[grupo["fecha_hora"] == inicio_jornada, "ubicacion"].iloc[0]
        except:
            ubic_inicio = ""

        try:
            ubic_fin = grupo.loc[grupo["fecha_hora"] == fin_jornada, "ubicacion"].iloc[0]
        except:
            ubic_fin = ""

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

            inicio_real = max(b["inicio"], inicio_dia)
            fin_real = min(b["fin"], fin_dia)

            if inicio_real < fin_real:

                horas = (fin_real - inicio_real).total_seconds() / 3600

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

    if not kpis_list:
        st.error("❌ No se generaron KPIs")
        st.stop()

    kpis = pd.DataFrame(kpis_list).round(2)

    if "inicio_jornada" in kpis:
        kpis["inicio_jornada"] = pd.to_datetime(kpis["inicio_jornada"], errors="coerce").dt.strftime("%I:%M %p").str.lstrip("0")

    if "fin_jornada" in kpis:
        kpis["fin_jornada"] = pd.to_datetime(kpis["fin_jornada"], errors="coerce").dt.strftime("%I:%M %p").str.lstrip("0")

    st.subheader("Resumen por conductor")
    st.dataframe(kpis)

    # ==============================
    # EXPORTAR EXCEL
    # ==============================

    buffer = io.BytesIO()

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:

        for conductor, df_conductor in kpis.groupby("conductor"):

            df_conductor.to_excel(writer, sheet_name=conductor[:31], index=False)

            bloques_cond = bloques[
                bloques["vehiculo"].isin(df_conductor["vehiculo"])
            ]

            bloques_cond.to_excel(writer, sheet_name=f"Bloques {conductor[:20]}", index=False)

    st.download_button(
        label="📥 Descargar Excel",
        data=buffer,
        file_name="reporte_jornada.xlsx"
    )
