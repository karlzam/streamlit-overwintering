import streamlit as st
import geopandas as gpd
import matplotlib.pyplot as plt
import ee
import folium
import json
import os
from datetime import datetime, timedelta
from streamlit.components.v1 import html
import pandas as pd

# =========================================================
# APP CONFIG
# =========================================================
st.set_page_config(layout="wide", page_title="Fire Explorer")

shp_dir = r"Z:\overwintering\shp"

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
# LOAD DATA (ALL CRS SAFE)
# =========================================================
@st.cache_data
def load_data():

    nbac = gpd.read_file(r"Z:\shp\nbac\NBAC_1972to2024_20250506.shp")
    candidates = gpd.read_file(os.path.join(shp_dir, "2024_candidates_test.shp"))
    fires = gpd.read_file(os.path.join(shp_dir, "final_master_data.gpkg"))
    lightning = gpd.read_file(os.path.join(shp_dir, "ignitions_with_lightning.gpkg"))

    # CRS SAFETY
    for gdf in [nbac, candidates, fires, lightning]:
        if gdf.crs is None:
            gdf = gdf.set_crs(epsg=4326)
        gdf.to_crs(epsg=4326, inplace=True)

    fires["label"] = (
        fires["candidate"].astype(str) + " - IGID: " + fires["IGID"].astype(str)
    )

    return nbac, candidates, fires, lightning


nbac, candidates, fires, lightning = load_data()

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


def get_lightning(row):
    """
    Filter lightning by BOTH candidate and IGID
    """
    return lightning[
        (lightning["candidate"] == row["candidate"]) &
        (lightning["IGID"] == row["IGID"])
    ]


def add_ee_layer(m, img, vis, name):

    mid = ee.Image(img).getMapId(vis)

    folium.raster_layers.TileLayer(
        tiles=mid["tile_fetcher"].url_format,
        attr="Google Earth Engine",
        name=name,
        overlay=True,
        control=True,
    ).add_to(m)


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

# =========================================================
# SIDEBAR
# =========================================================
st.sidebar.title("🔥 Fire Explorer")

mode = st.sidebar.radio(
    "Mode",
    ["Vector Map", "Satellite", "Lightning"]
)

selected = st.sidebar.selectbox(
    "Select IGID:",
    fires["label"]
)

row = fires[fires["label"] == selected].iloc[0]

auto_start, auto_end = igid_to_dates(row["IGID"])

if "start_date" not in st.session_state or st.session_state.get("igid") != row["IGID"]:
    st.session_state.start_date = auto_start
    st.session_state.end_date = auto_end
    st.session_state.igid = row["IGID"]

start_date = st.sidebar.date_input("Start", st.session_state.start_date)
end_date = st.sidebar.date_input("End", st.session_state.end_date)

st.session_state.start_date = start_date
st.session_state.end_date = end_date

if st.sidebar.button("⬆️ +1 day"):
    st.session_state.start_date += timedelta(days=1)
    st.session_state.end_date += timedelta(days=1)
    st.rerun()

# =========================================================
# COMMON LAYERS
# =========================================================
before, inter, after = get_layers(row)

# =========================================================
# VECTOR MAP
# =========================================================
if mode == "Vector Map":

    st.title("🔥 Fire Perimeters")

    fig, ax = plt.subplots(figsize=(10, 10))

    # =====================================================
    # ORIGINAL LAYERS (YOUR EXISTING LOGIC)
    # =====================================================
    if not before.empty:
        before.plot(ax=ax, edgecolor="lightgrey", facecolor="none")

    if not inter.empty:
        inter.plot(ax=ax, edgecolor="blue", facecolor="none")

    if not after.empty:
        after.plot(ax=ax, edgecolor="black", facecolor="none")

    # ignition outline
    gpd.GeoSeries([row.geometry], crs=nbac.crs).plot(
        ax=ax,
        edgecolor="red",
        facecolor="none",
        linewidth=2
    )

    # =====================================================
    # FULL CANDIDATE CLUSTER (ALL IGIDs)
    # =====================================================
    candidate = row["candidate"]
    selected_igid = row["IGID"]

    cluster = fires[fires["candidate"] == candidate].copy()

    # CRS safety
    if cluster.crs is None:
        cluster = cluster.set_crs(epsg=4326)
    else:
        cluster = cluster.to_crs(epsg=4326)

    igids = cluster["IGID"].unique()

    for igid in igids:

        subset = cluster[cluster["IGID"] == igid]

        # 🔴 SELECTED IGID
        if igid == selected_igid:

            subset.plot(
                ax=ax,
                facecolor="none",
                edgecolor="red",
                linewidth=2.8,
                label=f"Selected IGID {igid}"
            )

        # 🟢 ALL OTHER IGIDs
        else:

            subset.plot(
                ax=ax,
                facecolor="none",
                edgecolor="darkgreen",
                linewidth=1.5,
                alpha=0.9,
                label=f"IGID {igid}"
            )

    # =====================================================
    # CLEAN LEGEND (REMOVE DUPLICATES)
    # =====================================================
    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    ax.legend(by_label.values(), by_label.keys(), fontsize=8)

    ax.set_axis_off()
    st.pyplot(fig)

    # =====================================================
    # SAFE RAW TABLE (FIXED FILTERING)
    # =====================================================
    st.subheader("📋 Raw Fire Record")

    raw_df = fires[
        (fires["IGID"] == row["IGID"]) &
        (fires["candidate"] == row["candidate"])
    ]

    st.dataframe(
        raw_df.drop(columns="geometry"),
        use_container_width=True
    )

# =========================================================
# SATELLITE
# =========================================================
elif mode == "Satellite":

    st.title("🛰️ MODIS Viewer")

    fire_gdf = nbac[nbac["GID"] == row["candidate"]]

    minx, miny, maxx, maxy = fire_gdf.total_bounds

    # FIXED EE SAFE BOUNDS (no numpy array)
    region = ee.Geometry.BBox(
        float(minx),
        float(miny),
        float(maxx),
        float(maxy)
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

# =========================================================
# ⚡ LIGHTNING MODE (NEW)
# =========================================================
elif mode == "Lightning":

    st.title("⚡ Lightning Ignition Viewer")

    fig, ax = plt.subplots(figsize=(10, 10))

    # fire polygons
    if not before.empty:
        before.plot(ax=ax, edgecolor="lightgrey", facecolor="none")

    if not inter.empty:
        inter.plot(ax=ax, edgecolor="blue", facecolor="none")

    if not after.empty:
        after.plot(ax=ax, edgecolor="black", facecolor="none")

    # ignition outline
    gpd.GeoSeries([row.geometry], crs=nbac.crs).plot(
        ax=ax,
        edgecolor="red",
        facecolor="none",
        linewidth=2
    )

    # ⚡ FILTER LIGHTNING
    lightning_hits = get_lightning(row)

    if not lightning_hits.empty:
        lightning_hits = lightning_hits.to_crs(epsg=4326)

        ax.scatter(lightning_hits.geometry.x,lightning_hits.geometry.y,marker="$⚡$",s=120,color="gold", alpha=0.9)

    ax.set_axis_off()
    st.pyplot(fig)

    # =====================================================
    # TABLE
    # =====================================================
    st.subheader("⚡ Lightning Points (filtered)")

    st.dataframe(
        lightning_hits.drop(columns="geometry"),
        use_container_width=True
    )

    # =====================================================
    # HISTOGRAMS
    # =====================================================
    st.subheader("📊 Lightning Distance Distribution (km)")

    fig1, ax1 = plt.subplots()

    if not lightning_hits.empty and "dist_km" in lightning_hits.columns:
        ax1.hist(
            lightning_hits["dist_km"].dropna(),
            bins=30
        )
        ax1.set_xlabel("Distance (km)")
        ax1.set_ylabel("Count")
    else:
        ax1.text(0.5, 0.5, "No distance data", ha="center", va="center")

    st.pyplot(fig1)

    st.subheader("⏱ Lightning Timeline (Strikes vs Ignition)")

    fig3, ax3 = plt.subplots(figsize=(10, 4))

    if not lightning_hits.empty and "lightning_datetime" in lightning_hits.columns:

        # ensure datetime format
        lightning_hits = lightning_hits.copy()
        lightning_hits["lightning_datetime"] = pd.to_datetime(lightning_hits["lightning_datetime"], errors="coerce")

        times = lightning_hits["lightning_datetime"].dropna().sort_values()

        # lightning strikes as vertical ticks
        ax3.vlines(
            x=times,
            ymin=0,
            ymax=1,
            color="blue",
            alpha=0.6,
            linewidth=1
        )

        # ignition date (convert from IGID)
        #ignition_time = igid_to_dates(row["IGID"])[0]

        if "acq_datetime" in lightning_hits.columns and not lightning_hits.empty:
            ignition_time = pd.to_datetime(lightning_hits["acq_datetime"].dropna().iloc[0])
        else:
            ignition_time = None

        ax3.axvline(ignition_time, color="red", linewidth=2, label="Ignition")

        ax3.set_yticks([])
        ax3.set_xlabel("Time")
        ax3.set_title("Lightning strikes over time")

        ax3.legend()

    else:
        ax3.text(0.5, 0.5, "No datetime data", ha="center", va="center")

    st.pyplot(fig3)

    st.subheader("📊 Time Difference vs Distance")
