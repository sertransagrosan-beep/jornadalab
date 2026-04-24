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
# 🌍 GEO (SE MANTIENE IGUAL)
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
# UTILIDADES
# ==============================

def parse_coords(coord):
    try:
        lat, lon = map(float, str(coord).split(","))
        return lat, lon
    except:
        return np.nan, np.nan

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
# CLUSTER UBICACIÓN PRINCIPAL
# ==============================

def obtener_ubic_principal(grupo):

    g = grupo.copy()
    g[["lat","lon"]] = g["Coordenadas"].apply(lambda x: pd.Series(parse_coords(x)))

    g["peso"] = g.apply(
        lambda r: r["delta_horas"] * 2 if r["estado"] in ["ralenti","apagado"]
        else r["delta_horas"] * 0.3,
        axis=1
    )

    g = g.dropna(subset=["lat"])

    if g.empty:
        return ""

    mejor = g.loc[g["peso"].idxmax()]

    return coord_a_municipio(mejor["lat"], mejor["lon"])

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

    if len(lista_df) == 0:
        st.error("No hay datos válidos")
        st.stop()

    df = pd.concat(lista_df, ignore_index=True)

    # ==============================
    # LIMPIEZA
    # ==============================

    df["fecha_hora"] = pd.to_datetime(df["fecha_hora"], errors="coerce")
    df = df.dropna(subset=["fecha_hora"])

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

    df["estado"] = np.select(
        [
            (df["ignicion_on"]) & (df["velocidad"] > 0),
            (df["ignicion_on"]) & (df["velocidad"] == 0)
        ],
        ["conduciendo", "ralenti"],
        default="apagado"
    )

    # ==============================
    # TIEMPOS
    # ==============================

    df["fecha_siguiente"] = df.groupby("vehiculo")["fecha_hora"].shift(-1)
    df["delta_horas"] = (
        df["fecha_siguiente"] - df["fecha_hora"]
    ).dt.total_seconds()/3600

    df["delta_horas"] = df["delta_horas"].clip(lower=0).fillna(0)

    # ==============================
    # BLOQUES
    # ==============================

    df["grupo"] = (df["estado"] != df.groupby("vehiculo")["estado"].shift()).cumsum()

    bloques = df.groupby(["vehiculo","grupo"]).agg({
        "estado":"first",
        "fecha_hora":["min","max"],
        "delta_horas":"sum"
    })

    bloques.columns = ["estado","inicio","fin","duracion_horas"]
    bloques = bloques.reset_index()

    # ==============================
    # KPIs
    # ==============================

    kpis_list = []

    for (vehiculo, fecha), grupo in df.groupby(["vehiculo","fecha"]):

        grupo = grupo.sort_values("fecha_hora")

        conductor = grupo["conductor"].dropna()
        conductor = conductor.iloc[0] if not conductor.empty else "NA"

        # Jornada real
        ign_on = grupo[grupo["ignicion_on"]]

        if ign_on.empty:
            continue

        inicio_jornada = ign_on["fecha_hora"].min()
        fin_jornada = ign_on["fecha_hora"].max()

        # Horas
        horas_conduccion = grupo.loc[grupo["estado"]=="conduciendo","delta_horas"].sum()
        horas_ralenti = grupo.loc[grupo["estado"]=="ralenti","delta_horas"].sum()
        horas_trabajo = horas_conduccion + horas_ralenti

        # ==============================
        # 🔥 ORIGEN Y DESTINO (NUEVO)
        # ==============================

        try:
            origen_row = ign_on.iloc[0]
            lat_o, lon_o = parse_coords(origen_row["Coordenadas"])
            origen = coord_a_municipio(lat_o, lon_o)
        except:
            origen = ""

        try:
            # destino = último apagado largo
            bloques_dia = bloques[bloques["vehiculo"]==vehiculo]

            candidatos = bloques_dia[
                (bloques_dia["estado"]=="apagado") &
                (bloques_dia["duracion_horas"] >= HORAS_DESCANSO_LARGO)
            ]

            if not candidatos.empty:
                fin_apagado = candidatos.iloc[-1]["fin"]
                fila_destino = grupo[grupo["fecha_hora"]<=fin_apagado].tail(1)

                lat_d, lon_d = parse_coords(fila_destino["Coordenadas"].iloc[0])
                destino = coord_a_municipio(lat_d, lon_d)
            else:
                destino = ""
        except:
            destino = ""

        # ==============================
        # UBICACIÓN (SE MANTIENE ORIGINAL)
        # ==============================

        try:
            lat, lon = parse_coords(grupo["Coordenadas"].dropna().iloc[-1])
            ubicacion = coord_a_municipio(lat, lon)
        except:
            ubicacion = ""

        ubic_principal = obtener_ubic_principal(grupo)

        # ==============================
        # PARADAS / DESCANSO
        # ==============================

        numero_paradas = 0
        horas_descanso = 0
        horas_pausa = 0

        fecha_ts = pd.Timestamp(fecha)
        next_day = fecha_ts + pd.Timedelta(days=1)

        bloques_v = bloques[bloques["vehiculo"]==vehiculo]

        for _, b in bloques_v.iterrows():

            inicio = max(b["inicio"], fecha_ts)
            fin = min(b["fin"], next_day)

            if inicio < fin:

                horas = (fin - inicio).total_seconds()/3600

                if b["estado"] in ["ralenti","apagado"] and horas >= UMBRAL_PARADA_MIN:
                    numero_paradas += 1

                if b["estado"]=="apagado":
                    if horas >= HORAS_DESCANSO_LARGO:
                        horas_descanso += horas
                    elif horas >= HORAS_MIN_PAUSA:
                        horas_pausa += horas

        kpis_list.append({
            "conductor": conductor,
            "vehiculo": vehiculo,
            "Fecha": fecha,
            "origen": origen,
            "destino": destino,
            "ubicacion": ubicacion,
            "inicio jornada": inicio_jornada,
            "fin jornada": fin_jornada,
            "numero_paradas": numero_paradas,
            "horas_trabajo": round(horas_trabajo,2),
            "horas_conduccion": round(horas_conduccion,2),
            "horas_descanso": round(horas_descanso,2),
            "horas_pausa": round(horas_pausa,2),
            "horas_ralenti": round(horas_ralenti,2),
            "ubic_principal": ubic_principal
        })

    kpis = pd.DataFrame(kpis_list)

    # formato hora
    kpis["inicio jornada"] = pd.to_datetime(kpis["inicio jornada"]).dt.strftime("%I:%M %p").str.lstrip("0")
    kpis["fin jornada"] = pd.to_datetime(kpis["fin jornada"]).dt.strftime("%I:%M %p").str.lstrip("0")

    # ORDEN FINAL
    columnas = [
        "conductor","vehiculo","Fecha","origen","destino","ubicacion",
        "inicio jornada","fin jornada","numero_paradas",
        "horas_trabajo","horas_conduccion","horas_descanso",
        "horas_pausa","horas_ralenti","ubic_principal"
    ]

    kpis = kpis[columnas]

    st.dataframe(kpis)

    # EXPORTAR
    buffer = io.BytesIO()

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        kpis.to_excel(writer, sheet_name="Resumen", index=False)
        bloques.to_excel(writer, sheet_name="Bloques", index=False)

    st.download_button("Descargar Excel", data=buffer, file_name="reporte.xlsx")
