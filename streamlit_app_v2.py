import streamlit as st
import geopandas as gpd
import matplotlib.pyplot as plt
import ee
import folium
import json
from datetime import datetime, timedelta
from streamlit.components.v1 import html

# =========================================================
# APP CONFIG
# =========================================================
st.set_page_config(layout="wide", page_title="Fire Explorer")

# =========================================================
# EARTH ENGINE INIT
# =========================================================
if "ee_init" not in st.session_state:
    try:
        ee.Initialize(project="karlzam")
    except Exception:
        ee.Authenticate()
        ee.Initialize(project="karlzam")

    st.session_state["ee_init"] = True

# =========================================================
# LOAD DATA SAFE CRS HANDLING
# =========================================================
@st.cache_data
def load_data():

    nbac = gpd.read_file(r"Z:\shp\nbac\NBAC_1972to2024_20250506.shp")
    candidates = gpd.read_file(r"Z:\overwintering\shp\2024_candidates_test.shp")
    fires = gpd.read_file(r"Z:\overwintering\shp\final_master_data.gpkg")

    # IMPORTANT: set CRS BEFORE transforming
    if nbac.crs is None:
        nbac = nbac.set_crs(epsg=4326)
    if candidates.crs is None:
        candidates = candidates.set_crs(epsg=4326)
    if fires.crs is None:
        fires = fires.set_crs(epsg=4326)

    nbac = nbac.to_crs(epsg=4326)
    candidates = candidates.to_crs(epsg=4326)
    fires = fires.to_crs(epsg=4326)

    fires["label"] = (
        fires["candidate"].astype(str)
        + " - IGID: "
        + fires["IGID"].astype(str)
    )

    return nbac, candidates, fires


nbac, candidates, fires = load_data()

# =========================================================
# HELPERS
# =========================================================
def igid_to_dates(igid):
    julian = int(str(igid).split("_")[0])
    year = 2024

    start = datetime(year, 1, 1) + timedelta(days=julian - 1)
    end = start + timedelta(days=1)

    return start, end


def get_layers(row):
    after = nbac[nbac["GID"] == row["candidate"]]

    pattern = rf"(?:^|_){row['candidate']}(?:_|$)"
    inter = candidates[candidates["pair_id"].str.contains(pattern, na=False)]

    before = nbac[nbac["GID"].isin(inter["GID_2"])]

    return before, inter, after


def add_gdf(m, gdf, color, name):

    if gdf.empty:
        return

    gdf = gdf.copy()

    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=4326)

    if gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    for c in gdf.columns:
        if c != "geometry":
            gdf[c] = gdf[c].astype(str)

    geojson = json.loads(gdf.to_json())

    folium.GeoJson(
        geojson,
        name=name,
        style_function=lambda x: {
            "color": color,
            "weight": 2,
            "fill": False,
        },
    ).add_to(m)


def add_ee_layer(m, img, vis, name):

    mid = ee.Image(img).getMapId(vis)

    folium.raster_layers.TileLayer(
        tiles=mid["tile_fetcher"].url_format,
        attr="Google Earth Engine",
        name=name,
        overlay=True,
        control=True,
    ).add_to(m)

# =========================================================
# SIDEBAR
# =========================================================
st.sidebar.title("🔥 Fire Explorer")

selected = st.sidebar.selectbox(
    "Select IGID:",
    fires["label"]
)

row = fires[fires["label"] == selected].iloc[0]

# =========================================================
# AUTO DATE FROM IGID (DEFAULTS BUT EDITABLE)
# =========================================================
auto_start, auto_end = igid_to_dates(row["IGID"])

st.sidebar.subheader("Date window (auto + editable)")

start_date = st.sidebar.date_input("Start date", value=auto_start)
end_date = st.sidebar.date_input("End date", value=auto_end)

# =========================================================
# LAYERS
# =========================================================
before, inter, after = get_layers(row)

# =========================================================
# MODE SELECT
# =========================================================
mode = st.sidebar.radio("Mode", ["Vector Map", "Satellite"])

# =========================================================
# VECTOR MAP
# =========================================================
if mode == "Vector Map":

    st.title("🔥 Fire Perimeters")

    fig, ax = plt.subplots(figsize=(10, 10))

    if not before.empty:
        before.plot(ax=ax, edgecolor="lightgrey", facecolor="none")

    if not inter.empty:
        inter.plot(ax=ax, edgecolor="blue", facecolor="none")

    if not after.empty:
        after.plot(ax=ax, edgecolor="black", facecolor="none")

    # SAFE ignition polygon (NO transform errors)
    gpd.GeoSeries([row.geometry], crs=nbac.crs).plot(
        ax=ax,
        edgecolor="red",
        facecolor="none",
        linewidth=2
    )

    ax.set_axis_off()
    st.pyplot(fig)

    st.write("IGID:", row["IGID"])
    st.write("Candidate:", row["candidate"])

# =========================================================
# SATELLITE MAP (FIXED EE BOUNDS)
# =========================================================
else:

    st.title("🛰️ Imagery Viewer")

    fire_gdf = nbac[nbac["GID"] == row["candidate"]]

    minx, miny, maxx, maxy = fire_gdf.total_bounds

    # ---------------------------------------------------------
    # 10 km buffer (approx in degrees)
    # 1 degree ≈ 111 km → 10 km ≈ 0.09 degrees
    # ---------------------------------------------------------
    buffer_deg = 10 / 111  # ≈ 0.09009

    region = ee.Geometry.BBox(
        float(minx - buffer_deg),
        float(miny - buffer_deg),
        float(maxx + buffer_deg),
        float(maxy + buffer_deg)
    )

    center = [(miny + maxy) / 2, (minx + maxx) / 2]

    Map = folium.Map(location=center, zoom_start=7)
    folium.TileLayer("CartoDB positron").add_to(Map)

    # MODIS collection
    collection = (
        ee.ImageCollection("MODIS/061/MOD09GA")
        .filterDate(
            start_date.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d")
        )
        .filterBounds(region)
        .select(["sur_refl_b07", "sur_refl_b02", "sur_refl_b01"])
    )

    images = collection.toList(5)

    for i in range(images.size().getInfo()):

        img = ee.Image(images.get(i))

        date = ee.Date(img.get("system:time_start")).format("YYYY-MM-DD").getInfo()

        vis = {
            "bands": ["sur_refl_b07", "sur_refl_b02", "sur_refl_b01"],
            "min": 0,
            "max": 4000,
            "gamma": 1.1
        }

        add_ee_layer(Map, img.clip(region), vis, f"MODIS {date}")

    # overlays
    add_gdf(Map, after, "black", "Current Fire")
    add_gdf(Map, inter, "blue", "Intersection")
    add_gdf(Map, before, "lightgrey", "Previous Fires")

    # ignition polygon (SAFE)
    ignition = gpd.GeoSeries([row.geometry], crs=nbac.crs).to_frame("geometry")

    folium.GeoJson(
        ignition.to_json(),
        name="Ignition",
        style_function=lambda x: {
            "color": "red",
            "weight": 3,
            "fill": False,
        },
    ).add_to(Map)

    folium.LayerControl().add_to(Map)

    html(Map._repr_html_(), height=750)