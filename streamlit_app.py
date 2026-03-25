import streamlit as st
import geopandas as gpd
import pandas as pd
import matplotlib.pyplot as plt
import os
import ee
import folium
from streamlit.components.v1 import html

# --- APP CONFIG ---
st.set_page_config(layout="wide", page_title="Overwintering Fire Explorer")

# --- INITIALIZE EARTH ENGINE ---
if 'ee_initialized' not in st.session_state:
    try:
        ee.Initialize(project='karlzam')
    except Exception:
        ee.Authenticate()
        ee.Initialize(project='karlzam')

    st.session_state['ee_initialized'] = True

# --- DATA LOADING ---
@st.cache_data
def load_data():
    nbac = gpd.read_file(r'Z:\shp\nbac\NBAC_1972to2024_20250506.shp')
    all_candidates = gpd.read_file(r'Z:\overwintering\shp\2024_candidates_test.shp')
    final_multi_ignitions = gpd.read_file(r'Z:\overwintering\shp\final_master_data.gpkg')

    final_multi_ignitions['display_name'] = (
        final_multi_ignitions['candidate'].astype(str) + " - IGID: " +
        final_multi_ignitions['IGID'].astype(str)
    )
    return nbac, all_candidates, final_multi_ignitions

nbac, all_candidates, final_multi_ignitions = load_data()

# --- SIDEBAR ---
st.sidebar.title("🔥 Fire Explorer")
page = st.sidebar.radio("View Mode:", ["Static Perimeter Map", "Satellite Imagery (GEE)"])

st.sidebar.markdown("---")
st.sidebar.header("Global Filters")
selected_label = st.sidebar.selectbox(
    "Select a Candidate/IGID:",
    options=final_multi_ignitions['display_name'].unique()
)

row = final_multi_ignitions[final_multi_ignitions['display_name'] == selected_label].iloc[0]

# --- PAGE 1: STATIC MAP ---
if page == "Static Perimeter Map":
    st.title("📍 Overwintering Fire Candidate Explorer")

    nbac_after = nbac[nbac['GID'] == row['candidate']]
    pattern = rf"(?:^|_){row['candidate']}(?:_|$)"
    result = all_candidates[all_candidates['pair_id'].str.contains(pattern, regex=True, na=False)]
    nbac_before = nbac[nbac['GID'].isin(result['GID_2'])]

    col1, col2 = st.columns([3, 1])

    with col1:
        st.subheader(f"Analysis for {selected_label}")
        fig, ax = plt.subplots(figsize=(10, 10))

        if not nbac_before.empty:
            nbac_before.plot(ax=ax, edgecolor='lightgrey', facecolor='none', linewidth=1)
        if not result.empty:
            result.plot(ax=ax, edgecolor='blue', facecolor='none', linewidth=1)
        if not nbac_after.empty:
            nbac_after.plot(ax=ax, edgecolor='black', facecolor='none', linewidth=2)

        gpd.GeoSeries([row['geometry']]).plot(ax=ax, color='red', markersize=100, marker='X')

        ax.set_axis_off()
        st.pyplot(fig)

    with col2:
        st.subheader("Attributes")
        st.write("**Candidate GID:**", row['candidate'])
        st.write("**Ignition ID:**", row['IGID'])
        if st.checkbox("View Detailed Overlap Table"):
            st.dataframe(result.drop(columns='geometry'))

# --- PAGE 2: GEE MAP ---
else:
    st.title("🛰️ MODIS Satellite Viewer")

    nbac_temp = nbac[nbac['GID'] == row['candidate']].to_crs(epsg=4326)

    if nbac_temp.empty:
        st.error(f"Could not find geometry for GID: {row['candidate']}")
    else:
        b = nbac_temp.total_bounds
        region = ee.Geometry.Rectangle([b[0], b[1], b[2], b[3]]).buffer(2000).bounds()

        # Sidebar controls
        st.sidebar.subheader("Satellite Controls")
        d_start = st.sidebar.date_input("Start Date", value=pd.to_datetime('2024-05-09'))
        d_end = st.sidebar.date_input("End Date", value=pd.to_datetime('2024-05-12'))

        # --- CREATE FOLIUM MAP ---
        center_lat = (b[1] + b[3]) / 2
        center_lon = (b[0] + b[2]) / 2

        Map = folium.Map(location=[center_lat, center_lon], zoom_start=7)
        folium.TileLayer('CartoDB positron').add_to(Map)

        # --- FUNCTION TO ADD EE LAYERS ---
        def add_ee_layer(folium_map, ee_image, vis_params, name):
            map_id_dict = ee.Image(ee_image).getMapId(vis_params)
            folium.raster_layers.TileLayer(
                tiles=map_id_dict['tile_fetcher'].url_format,
                attr='Google Earth Engine',
                name=name,
                overlay=True,
                control=True
            ).add_to(folium_map)

        # --- IMAGE COLLECTION ---
        daily_collection = (ee.ImageCollection('MODIS/061/MOD09GA')
                            .filterDate(d_start.strftime('%Y-%m-%d'), d_end.strftime('%Y-%m-%d'))
                            .filterBounds(region)
                            .select(['sur_refl_b07', 'sur_refl_b02', 'sur_refl_b01']))

        img_list = daily_collection.toList(daily_collection.size())

        with st.spinner("Fetching imagery from Earth Engine..."):
            try:
                num_images = img_list.size().getInfo()

                if num_images == 0:
                    st.warning("No images found for these dates.")
                else:
                    num_images = min(num_images, 5)

                    for i in range(num_images):
                        image = ee.Image(img_list.get(i))

                        date_str = ee.Date(
                            image.get('system:time_start')
                        ).format('YYYY-MM-DD').getInfo()

                        vis_params = {
                            'bands': ['sur_refl_b07', 'sur_refl_b02', 'sur_refl_b01'],
                            'min': 0,
                            'max': 4000,
                            'gamma': 1.1
                        }

                        add_ee_layer(Map, image.clip(region), vis_params, f"MODIS | {date_str}")

                folium.LayerControl().add_to(Map)

                # --- RENDER IN STREAMLIT ---
                html(Map._repr_html_(), height=700)

            except Exception as e:
                st.error(f"Error communicating with GEE: {e}")