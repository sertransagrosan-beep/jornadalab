import streamlit as st
import pandas as pd
import numpy as np
import io
import requests
import time
from pathlib import Path
import hashlib
import pickle

st.set_page_config(page_title="Jornada Conductores PRO", layout="wide")
st.title("🚛 Jornada Laboral Conductores - PRO")

# ==============================
# CONFIG
# ==============================
with st.sidebar:
    HORAS_MAX_JORNADA = st.number_input("Horas máximas jornada", value=8.0)
    HORAS_DESCANSO_LARGO = st.number_input("Horas descanso largo", value=4.0)
    MIN_PAUSA = st.number_input("Pausa mínima (min)", value=34)
    MIN_PARADA = st.number_input("Parada mínima (min)", value=17)
    RADIO_CLUSTER = st.slider("Radio cluster (m)", 50, 1000, 300)

HORAS_MIN_PAUSA = MIN_PAUSA / 60
UMBRAL_PARADA_MIN = MIN_PARADA / 60

# ==============================
# CACHE GEO
# ==============================
CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)

def get_cache_key(lat, lon):
    return hashlib.md5(f"{round(lat,4)}_{round(lon,4)}".encode()).hexdigest()

def load_cache(key):
    f = CACHE_DIR / f"{key}.pkl"
    if f.exists():
        return pickle.load(open(f, "rb"))
    return None

def save_cache(key, value):
    pickle.dump(value, open(CACHE_DIR / f"{key}.pkl", "wb"))

@st.cache_data(ttl=3600)
def coord_a_municipio(lat, lon):
    if pd.isna(lat):
        return ""

    key = get_cache_key(lat, lon)
    cached = load_cache(key)
    if cached:
        return cached

    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json"},
            headers={"User-Agent": "app"},
            timeout=3
        )
        if r.status_code == 200:
            addr = r.json().get("address", {})
            ciudad = (
                addr.get("city")
                or addr.get("town")
                or addr.get("village")
                or addr.get("municipality")
                or ""
            )
            save_cache(key, ciudad)
            return ciudad
    except:
        pass

    fallback = f"{round(lat,3)},{round(lon,3)}"
    save_cache(key, fallback)
    return fallback

def normalizar_ciudad(c):
    if not c:
        return c
    return c.replace("Municipio de ", "").strip()

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
# GEO
# ==============================
def parse_coords(coord):
    try:
        lat, lon = map(float, str(coord).split(","))
        return lat, lon
    except:
        return np.nan, np.nan

# ==============================
# CLUSTER
# ==============================
def distancia(lat1, lon1, lat2, lon2):
    R = 6371000
    a = np.sin(np.radians(lat2-lat1)/2)**2 + \
        np.cos(np.radians(lat1))*np.cos(np.radians(lat2))* \
        np.sin(np.radians(lon2-lon1)/2)**2
    return 2*R*np.arctan2(np.sqrt(a), np.sqrt(1-a))

def clusterizar(df, radio):
    clusters = []
    for _, r in df.iterrows():
        if np.isnan(r["lat"]):
            continue
        asignado = False
        for c in clusters:
            if distancia(r["lat"], r["lon"], c["lat"], c["lon"]) < radio:
                total = c["peso"] + r["peso"]
                c["lat"] = (c["lat"]*c["peso"] + r["lat"]*r["peso"]) / total
                c["lon"] = (c["lon"]*c["peso"] + r["lon"]*r["peso"]) / total
                c["peso"] = total
                asignado = True
                break
        if not asignado:
            clusters.append({"lat": r["lat"], "lon": r["lon"], "peso": r["peso"]})
    return clusters

def ubic_principal(grupo):

    g = grupo.copy()
    g[["lat","lon"]] = g["Coordenadas"].apply(lambda x: pd.Series(parse_coords(x)))

    g["peso"] = np.where(
        g["estado"].isin(["ralenti","apagado"]),
        g["delta_horas"] * 2,
        g["delta_horas"] * 0.3
    )

    g = g.dropna(subset=["lat"])

    if g.empty:
        coord_raw = grupo["Coordenadas"].dropna()
        if not coord_raw.empty:
            lat, lon = parse_coords(coord_raw.iloc[0])
            return coord_a_municipio(lat, lon)
        return "Sin ubicación"

    clusters = clusterizar(g, RADIO_CLUSTER)

    if not clusters:
        return "Sin ubicación"

    best = max(clusters, key=lambda x: x["peso"])
    return coord_a_municipio(best["lat"], best["lon"])

# ==============================
# PROCESAMIENTO
# ==============================
def procesar(df):

    df["fecha_hora"] = pd.to_datetime(df["fecha_hora"], errors="coerce")
    df = df.dropna(subset=["fecha_hora"])

    df["ignicion_on"] = df["ignicion"].astype(str).str.lower().isin(["encendido","true","1"])

    df["velocidad"] = (
        df["velocidad"].astype(str)
        .str.replace(",", ".", regex=False)
        .str.extract(r"(\d+\.?\d*)")[0]
    )
    df["velocidad"] = pd.to_numeric(df["velocidad"], errors="coerce").fillna(0)

    df = df.sort_values(["vehiculo","fecha_hora"]).reset_index(drop=True)

    df["fecha"] = df["fecha_hora"].dt.date

    # ESTADOS
    UMBRAL_MOV = 3
    df["estado"] = "apagado"

    df.loc[df["ignicion_on"] & (df["velocidad"] >= UMBRAL_MOV), "estado"] = "conduciendo"
    df.loc[df["ignicion_on"] & (df["velocidad"] < UMBRAL_MOV), "estado"] = "ralenti"

    # TIEMPOS
    df["fecha_sig"] = df.groupby("vehiculo")["fecha_hora"].shift(-1)
    df["delta_horas"] = (df["fecha_sig"] - df["fecha_hora"]).dt.total_seconds()/3600
    df.loc[df["delta_horas"] > 0.5, "delta_horas"] = 0
    df["delta_horas"] = df["delta_horas"].fillna(0)

    # BLOQUES (FIX)
    df["grupo"] = df.groupby("vehiculo")["estado"].transform(
        lambda x: (x != x.shift()).cumsum()
    )

    bloques = df.groupby(["vehiculo","grupo"]).agg(
        estado=("estado","first"),
        inicio=("fecha_hora","min"),
        fin=("fecha_hora","max"),
        duracion=("delta_horas","sum")
    ).reset_index()

    # ==============================
    # KPIs
    # ==============================
    kpis_list = []

    for (vehiculo, fecha), g in df.groupby(["vehiculo","fecha"]):

        if g[g["ignicion_on"]].empty:
            continue

        conductor = g["conductor"].dropna().iloc[0] if "conductor" in g else "NA"

        inicio = g.loc[g["ignicion_on"],"fecha_hora"].min()
        fin = g.loc[g["ignicion_on"],"fecha_hora"].max()

        horas_conduccion = g.loc[g["estado"]=="conduciendo","delta_horas"].sum()
        horas_ralenti = g.loc[g["estado"]=="ralenti","delta_horas"].sum()
        horas_trabajo = horas_conduccion + horas_ralenti

        # 🔥 UBICACIÓN ROBUSTA (CLAVE)
        coord_raw = g["Coordenadas"].dropna()

        if not coord_raw.empty:
            lat, lon = parse_coords(coord_raw.iloc[-1])
            ubic = coord_a_municipio(lat, lon)
        else:
            ubic = "Sin ubicación"

        ubic_p = ubic_principal(g)

        ubic = normalizar_ciudad(ubic)
        ubic_p = normalizar_ciudad(ubic_p)

        bloques_v = bloques[bloques["vehiculo"]==vehiculo]

        n_paradas = 0
        h_descanso = 0
        h_pausa = 0

        inicio_d = pd.Timestamp(fecha)
        fin_d = inicio_d + pd.Timedelta(days=1)

        for _, b in bloques_v.iterrows():

            ini = max(b["inicio"], inicio_d)
            finb = min(b["fin"], fin_d)

            if ini < finb:
                h = (finb - ini).total_seconds()/3600

                if b["estado"]=="apagado" and h >= UMBRAL_PARADA_MIN:
                    n_paradas += 1

                if b["estado"]=="apagado":
                    if h >= HORAS_DESCANSO_LARGO:
                        h_descanso += h
                    elif h >= HORAS_MIN_PAUSA:
                        h_pausa += h

        kpis_list.append({
            "conductor": conductor,
            "vehiculo": vehiculo,
            "fecha": fecha,
            "origen": "",
            "destino": "",
            "ubicacion": ubic,
            "inicio_jornada": inicio,
            "fin_jornada": fin,
            "numero_paradas": n_paradas,
            "horas_trabajo": round(horas_trabajo,2),
            "horas_conduccion": round(horas_conduccion,2),
            "horas_descanso": round(h_descanso,2),
            "horas_pausa": round(h_pausa,2),
            "horas_ralenti": round(horas_ralenti,2),
            "ubic_principal": ubic_p
        })

    kpis = pd.DataFrame(kpis_list)

    # ORDEN FINAL
    column_order = [
        "conductor","vehiculo","fecha","origen","destino","ubicacion",
        "inicio_jornada","fin_jornada","numero_paradas",
        "horas_trabajo","horas_conduccion","horas_descanso",
        "horas_pausa","horas_ralenti","ubic_principal"
    ]

    for col in column_order:
        if col not in kpis.columns:
            kpis[col] = ""

    kpis = kpis[column_order]

    return kpis

# ==============================
# APP
# ==============================
files = st.file_uploader("Sube archivos", accept_multiple_files=True)

if files:

    dfs = []

    for f in files:
        d = leer_archivo(f)
        if d is None or d.empty:
            continue

        d = d.rename(columns={
            "Fecha y Hora":"fecha_hora",
            "Velocidad":"velocidad",
            "Ignicion*":"ignicion",
            "Conductor":"conductor"
        })

        d["vehiculo"] = f.name[:6].upper()
        dfs.append(d)

    df = pd.concat(dfs, ignore_index=True)

    kpis = procesar(df)

    st.success(f"{len(kpis)} jornadas procesadas")
    st.dataframe(kpis, use_container_width=True)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        kpis.to_excel(writer, index=False)

    st.download_button("📥 Descargar Excel", buffer, "reporte.xlsx")

else:
    st.info("Sube archivos para iniciar")
