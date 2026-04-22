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

MIN_PAUSA = st.number_input("Pausa mínima (minutos)", value=34)
MIN_PARADA = st.number_input("Duración mínima parada (minutos)", value=17)

HORAS_MIN_PAUSA = MIN_PAUSA / 60
UMBRAL_PARADA_MIN = MIN_PARADA / 60

# ==============================
# 🔥 LECTOR INTELIGENTE PRO
# ==============================

def leer_archivo(file):

    nombre = file.name.lower()

    try:
        # ======================
        # EXCEL
        # ======================
        if nombre.endswith(".xlsx") or nombre.endswith(".xls"):
            df = pd.read_excel(file)

        # ======================
        # CSV (varios formatos)
        # ======================
        else:
            try:
                df = pd.read_csv(file, sep=";", encoding="utf-8")
            except:
                file.seek(0)
                try:
                    df = pd.read_csv(file, sep=",", encoding="utf-8")
                except:
                    file.seek(0)
                    df = pd.read_csv(file, sep=None, engine="python")

        # ======================
        # LIMPIEZA SEGURA
        # ======================

        df.columns = df.columns.astype(str).str.strip()

        # eliminar columnas vacías tipo Unnamed
        df = df.loc[:, ~df.columns.str.contains("^Unnamed", na=False)]

        return df

    except Exception as e:
        st.warning(f"⚠️ No se pudo leer {file.name}")
        return None

# ==============================
# FUNCIONES UBICACIÓN
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

    g["peso"] = g.apply(
        lambda row: row["delta_horas"] * 2 if row["estado"] in ["ralenti", "apagado"] else row["delta_horas"],
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
# SUBIR ARCHIVOS
# ==============================

files = st.file_uploader("Sube archivos (CSV o Excel)", accept_multiple_files=True)

if files:

    lista_df = []

    for file in files:

        df_temp = leer_archivo(file)

        if df_temp is None or df_temp.empty:
            continue

        # ======================
        # NORMALIZAR COLUMNAS
        # ======================

        df_temp = df_temp.rename(columns={
            "Fecha y Hora": "fecha_hora",
            "Velocidad": "velocidad",
            "Ignicion*": "ignicion",
            "Conductor": "conductor",
            "Localización": "ubicacion"
        })

        # validar columnas mínimas
        if "fecha_hora" not in df_temp.columns:
            continue

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

    df["ignicion"] = df.get("ignicion", "").astype(str).str.lower()
    df["ignicion_on"] = df["ignicion"].isin(["encendido"])

    df["velocidad"] = (
        df.get("velocidad", 0)
        .astype(str)
        .str.replace(",", ".", regex=False)
        .str.extract(r"(\d+\.?\d*)")[0]
    )

    df["velocidad"] = pd.to_numeric(df["velocidad"], errors="coerce").fillna(0)

    df = df.sort_values(by=["vehiculo", "fecha_hora"]).reset_index(drop=True)

    df["fecha"] = df["fecha_hora"].dt.date

    # ==============================
    # ESTADOS
    # ==============================

    df["estado"] = df.apply(
        lambda row: "conduciendo" if row["ignicion_on"] and row["velocidad"] > 0
        else "ralenti" if row["ignicion_on"]
        else "apagado",
        axis=1
    )

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

    bloques = df.groupby(["vehiculo", "grupo"]).agg({
        "estado": "first",
        "fecha_hora": ["min", "max"],
        "delta_horas": "sum"
    })

    bloques.columns = ["estado", "inicio", "fin", "duracion_horas"]
    bloques = bloques.reset_index()

    # ==============================
    # KPIs
    # ==============================

    kpis_list = []

    for (vehiculo, fecha), grupo in df.groupby(["vehiculo", "fecha"]):

        inicio_jornada = grupo.loc[grupo["ignicion_on"], "fecha_hora"].min()
        fin_jornada = grupo.loc[grupo["ignicion_on"], "fecha_hora"].max()

        horas_conduccion = grupo.loc[grupo["estado"] == "conduciendo", "delta_horas"].sum()
        horas_ralenti = grupo.loc[grupo["estado"] == "ralenti", "delta_horas"].sum()
        horas_trabajo = horas_conduccion + horas_ralenti

        numero_paradas = bloques[
            (bloques["vehiculo"] == vehiculo) &
            (bloques["estado"].isin(["ralenti", "apagado"])) &
            (bloques["duracion_horas"] >= UMBRAL_PARADA_MIN)
        ].shape[0]

        horas_descanso = grupo.loc[grupo["estado"] == "apagado", "delta_horas"].sum()
        horas_pausa = grupo.loc[grupo["estado"] == "ralenti", "delta_horas"].sum()

        ubic_principal = calcular_ubic_principal(grupo) if "ubicacion" in grupo else ""

        kpis_list.append({
            "conductor": grupo.get("conductor", ["N/A"]).iloc[0],
            "vehiculo": vehiculo,
            "fecha": fecha,
            "inicio_jornada": inicio_jornada,
            "fin_jornada": fin_jornada,
            "numero_paradas": numero_paradas,
            "horas_trabajo": horas_trabajo,
            "horas_conduccion": horas_conduccion,
            "horas_ralenti": horas_ralenti,
            "horas_descanso": horas_descanso,
            "horas_pausa": horas_pausa,
            "ubic_principal": ubic_principal
        })

    kpis = pd.DataFrame(kpis_list)

    if kpis.empty:
        st.error("❌ No se generaron KPIs")
        st.stop()

    # formato hora
    kpis["inicio_jornada"] = pd.to_datetime(kpis["inicio_jornada"]).dt.strftime("%I:%M %p").str.lstrip("0")
    kpis["fin_jornada"] = pd.to_datetime(kpis["fin_jornada"]).dt.strftime("%I:%M %p").str.lstrip("0")

    st.dataframe(kpis)

    # ==============================
    # EXPORTAR EXCEL
    # ==============================

    buffer = io.BytesIO()

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:

        kpis.to_excel(writer, sheet_name="KPIs", index=False)
        bloques.to_excel(writer, sheet_name="Bloques", index=False)

    st.download_button(
        "📥 Descargar Excel",
        data=buffer,
        file_name="reporte_jornada.xlsx"
    )
