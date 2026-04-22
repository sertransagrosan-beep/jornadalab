import streamlit as st
import pandas as pd
import io
import re
import numpy as np

st.title("Jornada Laboral Conductores")

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
# FUNCIONES GEO
# ==============================

def parse_coords(coord):
    try:
        lat, lon = map(float, str(coord).split(","))
        return lat, lon
    except:
        return np.nan, np.nan

def distancia_metros(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = np.radians(lat1)
    phi2 = np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)

    a = np.sin(dphi/2)**2 + np.cos(phi1)*np.cos(phi2)*np.sin(dlambda/2)**2
    return 2 * R * np.arctan2(np.sqrt(a), np.sqrt(1 - a))

def clusterizar_ubicaciones(df, radio=300):

    clusters = []

    for _, row in df.iterrows():

        lat, lon = row["lat"], row["lon"]
        if np.isnan(lat):
            continue

        asignado = False

        for c in clusters:
            d = distancia_metros(lat, lon, c["lat"], c["lon"])
            if d < radio:
                c["peso"] += row["delta_horas"]
                c["count"] += 1
                asignado = True
                break

        if not asignado:
            clusters.append({
                "lat": lat,
                "lon": lon,
                "peso": row["delta_horas"],
                "count": 1
            })

    return clusters

def obtener_ubic_principal(grupo):

    g = grupo.copy()
    g[["lat", "lon"]] = g["Coordenadas"].apply(lambda x: pd.Series(parse_coords(x)))
    g = g.dropna(subset=["lat"])

    clusters = clusterizar_ubicaciones(g)

    if len(clusters) == 0:
        return ""

    mejor = max(clusters, key=lambda x: x["peso"])

    return f"{round(mejor['lat'],5)}, {round(mejor['lon'],5)}"

def obtener_ciudad_simple(coord):
    lat, lon = parse_coords(coord)
    if np.isnan(lat):
        return ""
    return f"{round(lat,3)}, {round(lon,3)}"  # placeholder ciudad

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
            "Localización": "ubicacion"
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

        conductor = grupo["conductor"].dropna().iloc[0] if "conductor" in grupo else "N/A"

        inicio_jornada = grupo.loc[grupo["ignicion_on"], "fecha_hora"].min()
        fin_jornada = grupo.loc[grupo["ignicion_on"], "fecha_hora"].max()

        horas_conduccion = grupo.loc[grupo["estado"] == "conduciendo", "delta_horas"].sum()
        horas_ralenti = grupo.loc[grupo["estado"] == "ralenti", "delta_horas"].sum()
        horas_trabajo = horas_conduccion + horas_ralenti

        # UBICACIONES
        ultima_coord = grupo["Coordenadas"].dropna().iloc[-1] if "Coordenadas" in grupo else ""
        ubicacion = obtener_ciudad_simple(ultima_coord)
        ubic_principal = obtener_ubic_principal(grupo)

        # ==============================
        # BLOQUES DÍA (CORTE REAL)
        # ==============================

        inicio_dia = pd.Timestamp(fecha)
        fin_dia = inicio_dia + pd.Timedelta(days=1)

        bloques_vehiculo = bloques[bloques["vehiculo"] == vehiculo]

        bloques_dia = bloques_vehiculo[
            (bloques_vehiculo["inicio"] < fin_dia) &
            (bloques_vehiculo["fin"] > inicio_dia)
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
            "ubic_principal": ubic_principal
        })

    kpis = pd.DataFrame(kpis_list).round(2)

    kpis["inicio_jornada"] = pd.to_datetime(kpis["inicio_jornada"]).dt.strftime("%I:%M %p").str.lstrip("0")
    kpis["fin_jornada"] = pd.to_datetime(kpis["fin_jornada"]).dt.strftime("%I:%M %p").str.lstrip("0")

    st.dataframe(kpis)

    # ==============================
    # EXPORTAR
    # ==============================

    buffer = io.BytesIO()

    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        kpis.to_excel(writer, sheet_name="Resumen", index=False)

    st.download_button(
        "Descargar Excel",
        data=buffer,
        file_name="reporte_jornada.xlsx"
    )
