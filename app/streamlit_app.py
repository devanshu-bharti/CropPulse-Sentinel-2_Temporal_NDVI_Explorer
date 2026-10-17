
# streamlit run app/streamlit_app.py
# app/streamlit_app.py

# import streamlit as st
# import requests
# import folium
# from streamlit_folium import st_folium
# import pandas as pd
# import altair as alt
# import time

# API_URL = "http://127.0.0.1:8000"

# st.set_page_config(page_title="Mahoba Crop Monitor", layout="wide")
# st.title("Mahoba Sentinel-2 Explorer (NDVI)")

# try:
#     dates_req = requests.get(f"{API_URL}/dates")
#     dates_req.raise_for_status()
#     available_dates = dates_req.json()
# except Exception:
#     st.error("Backend API is unreachable. Ensure 'uvicorn api.main:app' is running.")
#     available_dates = []

# with st.sidebar:
#     st.header("⚙️ Data Ingestion")
#     st.write("Convert raw SAFE zips to an NDVI Datacube COG.")
    
#     raw_dir = st.text_input("RAW Data Directory", value=r"D:\1_WORK\Devanshu_ML_AI\4_AWS_S3_data_download\Mahoba\RAW data")
#     shp_path = st.text_input("Boundary Shapefile", value=r"D:\1_WORK\Devanshu_ML_AI\4_AWS_S3_data_download\Mahoba\project_root_v2\data\boundary\UP-Mahoba.shp")
    
#     if st.button("Process New Data", type="primary"):
#         timer_text = st.empty()
#         progress_bar = st.progress(0.0)
#         status_text = st.empty()
        
#         from processor import process_pipeline
        
#         start_time = time.time()
        
#         for update in process_pipeline(raw_dir, shp_path):
#             # Calculate elapsed time dynamically
#             elapsed_seconds = int(time.time() - start_time)
#             mins, secs = divmod(elapsed_seconds, 60)
#             timer_text.markdown(f"**⏱️ Elapsed Time:** {mins:02d}:{secs:02d}")
            
#             if update["status"] == "progress":
#                 progress_bar.progress(update["value"])
#                 status_text.caption(update["message"])
#             elif update["status"] == "error":
#                 st.error(update["message"])
#             else:
#                 status_text.caption(update["message"])
                
#         final_time = int(time.time() - start_time)
#         f_mins, f_secs = divmod(final_time, 60)
#         st.success(f"✅ Processing Complete in {f_mins:02d}:{f_secs:02d}!")
#         time.sleep(2)
#         st.rerun()

# if not available_dates:
#     st.warning("No COGs found. Run the data processor in the sidebar first.")
#     st.stop()

# ctrl1, ctrl2 = st.columns([1, 2])
# with ctrl1:
#     selected_date = st.selectbox("Select Acquisition Date", available_dates)
# with ctrl2:
#     st.write("") 
#     st.write("")
#     clip_on = st.toggle("Clip to Mahoba Boundary", value=False)

# if clip_on:
#     with st.spinner(f"Verifying {selected_date} Datacube cache..."):
#         try:
#             res_clip = requests.post(f"{API_URL}/clip/{selected_date}")
#             if res_clip.status_code not in [200, 201]:
#                 st.error(f"Backend failed to clip: HTTP {res_clip.status_code}")
#         except Exception as e:
#             st.error(f"API Connection Error: Backend crashed or is unreachable. Details: {e}")

# map_col, data_col = st.columns([1.5, 1.2])

# with map_col:
#     m = folium.Map(location=[25.28, 79.87], zoom_start=10)

#     tile_url = f"{API_URL}/tiles/{selected_date}/{{z}}/{{x}}/{{y}}.png"
#     layer_name = "NDVI"
    
#     if clip_on:
#         tile_url += "?clip=true"
#         layer_name += " [Clipped]"

#     folium.TileLayer(
#         tiles=tile_url,
#         attr="Sentinel-2 L2A (NDVI)",
#         name=layer_name,
#         overlay=True,
#         control=True
#     ).add_to(m)

#     st.write("**Click anywhere within the Mahoba boundary to inspect pixel data.**")
#     map_state = st_folium(m, use_container_width=True, height=550, returned_objects=["last_clicked"])

# with data_col:
#     if map_state and map_state.get("last_clicked"):
#         lat = map_state["last_clicked"]["lat"]
#         lon = map_state["last_clicked"]["lng"]
        
#         with st.spinner("Fetching data and temporal profiles..."):
#             res_pixel = requests.get(f"{API_URL}/pixel", params={"date_str": selected_date, "lat": lat, "lon": lon})
            
#             if res_pixel.status_code == 200:
#                 d = res_pixel.json()
#                 st.success(f"📍 Selected Coordinates: {lat:.6f}, {lon:.6f}")
                
#                 c1, c2 = st.columns(2)
#                 ndvi_val = d.get('ndvi')
#                 c1.metric("NDVI", "No data" if ndvi_val is None else f"{ndvi_val:.4f}")
#                 c2.caption(f"**Scene class:** {d.get('scl_label', 'Unknown')}")
                
#                 st.divider()
                
#                 st.markdown("#### NDVI Temporal Profile")
#                 res_ts = requests.get(f"{API_URL}/timeseries", params={"lat": lat, "lon": lon})
                
#                 if res_ts.status_code == 200:
#                     ts_data = res_ts.json()
                    
#                     if ts_data:
#                         df = pd.DataFrame(ts_data)
#                         df['date'] = pd.to_datetime(df['date'])
                        
#                         valid_dates = df['date'].tolist()
                        
#                         chart = alt.Chart(df).mark_line(point=True).encode(
#                             x=alt.X(
#                                 'date:T', 
#                                 title='Acquisition Date',
#                                 axis=alt.Axis(
#                                     values=valid_dates, 
#                                     format='%Y-%m-%d',
#                                     labelAngle=-45
#                                 )
#                             ),
#                             y=alt.Y('ndvi:Q', title='NDVI', scale=alt.Scale(domain=[-0.2, 1.0])),
#                             tooltip=['date:T', 'ndvi:Q']
#                         ).properties(
#                             height=350
#                         )
                        
#                         st.altair_chart(chart, use_container_width=True)
#                     else:
#                         st.info("No valid temporal data available for this point.")
#                 else:
#                     st.error("Failed to fetch time-series data from backend.")
                    
#             else:
#                 try:
#                     error_detail = res_pixel.json().get("detail", "Unknown API error")
#                 except Exception:
#                     error_detail = f"Backend returned HTTP {res_pixel.status_code}"
                
#                 st.error(f"API Error: {error_detail}")
#     else:
#         st.info("👆 Click a location on the map to view spectral metrics and temporal graphs.")

# app/streamlit_app.py

import streamlit as st
import requests
import folium
from streamlit_folium import st_folium
import pandas as pd
import altair as alt
import time

API_URL = "http://127.0.0.1:8000"

st.set_page_config(page_title="Mahoba Crop Monitor", layout="wide")
st.title("Mahoba Sentinel-2 Explorer (NDVI)")

try:
    dates_req = requests.get(f"{API_URL}/dates")
    dates_req.raise_for_status()
    available_dates = dates_req.json()
except Exception:
    st.error("Backend API is unreachable. Ensure 'uvicorn api.main:app' is running.")
    available_dates = []

with st.sidebar:
    st.header("⚙️ Data Ingestion")
    st.write("Convert raw SAFE zips to an NDVI Datacube COG.")
    
    raw_dir = st.text_input("RAW Data Directory", value=r"D:\1_WORK\Devanshu_ML_AI\4_AWS_S3_data_download\Mahoba\RAW data")
    shp_path = st.text_input("Boundary Shapefile", value=r"D:\1_WORK\Devanshu_ML_AI\4_AWS_S3_data_download\Mahoba\project_root_v2\data\boundary\UP-Mahoba.shp")
    
    if st.button("Process New Data", type="primary"):
        timer_text = st.empty()
        progress_bar = st.progress(0.0)
        status_text = st.empty()
        
        from processor import process_pipeline
        
        start_time = time.time()
        
        for update in process_pipeline(raw_dir, shp_path):
            # Calculate elapsed time dynamically
            elapsed_seconds = int(time.time() - start_time)
            mins, secs = divmod(elapsed_seconds, 60)
            timer_text.markdown(f"**⏱️ Elapsed Time:** {mins:02d}:{secs:02d}")
            
            if update["status"] == "progress":
                progress_bar.progress(update["value"])
                status_text.caption(update["message"])
            elif update["status"] == "error":
                st.error(update["message"])
            else:
                status_text.caption(update["message"])
                
        final_time = int(time.time() - start_time)
        f_mins, f_secs = divmod(final_time, 60)
        st.success(f"✅ Processing Complete in {f_mins:02d}:{f_secs:02d}!")
        time.sleep(2)
        st.rerun()

if not available_dates:
    st.warning("No COGs found. Run the data processor in the sidebar first.")
    st.stop()

ctrl1, ctrl2 = st.columns([1, 2])
with ctrl1:
    selected_date = st.selectbox("Select Acquisition Date", available_dates)
with ctrl2:
    st.write("") 
    st.write("")
    clip_on = st.toggle("Clip to Mahoba Boundary", value=False)

if clip_on:
    with st.spinner(f"Verifying {selected_date} Datacube cache..."):
        try:
            res_clip = requests.post(f"{API_URL}/clip/{selected_date}")
            if res_clip.status_code not in [200, 201]:
                st.error(f"Backend failed to clip: HTTP {res_clip.status_code}")
        except Exception as e:
            st.error(f"API Connection Error: Backend crashed or is unreachable. Details: {e}")

# ==========================================
# FRAGMENT: Isolates map clicks from the rest of the page
# ==========================================
@st.fragment
def render_interactive_section(current_date, is_clipped):
    map_col, data_col = st.columns([1.5, 1.2])

    # 1. Safely extract click from session state BEFORE rendering the map
    click_lat, click_lon = None, None
    if "ndvi_map" in st.session_state and st.session_state["ndvi_map"].get("last_clicked"):
        click_lat = st.session_state["ndvi_map"]["last_clicked"]["lat"]
        click_lon = st.session_state["ndvi_map"]["last_clicked"]["lng"]

    with map_col:
        m = folium.Map(location=[25.28, 79.87], zoom_start=10)

        tile_url = f"{API_URL}/tiles/{current_date}/{{z}}/{{x}}/{{y}}.png"
        layer_name = "NDVI"
        
        if is_clipped:
            tile_url += "?clip=true"
            layer_name += " [Clipped]"

        folium.TileLayer(
            tiles=tile_url,
            attr="Sentinel-2 L2A (NDVI)",
            name=layer_name,
            overlay=True,
            control=True
        ).add_to(m)

        # Drop a red pin if we have clicked coordinates
        if click_lat is not None and click_lon is not None:
            folium.Marker(
                location=[click_lat, click_lon],
                icon=folium.Icon(color="red", icon="pushpin"),
                tooltip="Selected Pixel"
            ).add_to(m)

        st.write("**Click anywhere within the Mahoba boundary to inspect pixel data.**")
        
        # We don't need to assign this to a variable anymore, session_state handles it
        st_folium(m, width='stretch', height=550, returned_objects=["last_clicked"], key="ndvi_map")

    with data_col:
        # 2. Use the securely extracted coordinates to render the charts
        if click_lat is not None and click_lon is not None:
            lat = click_lat
            lon = click_lon
            
            with st.spinner("Fetching data and temporal profiles..."):
                res_pixel = requests.get(f"{API_URL}/pixel", params={"date_str": current_date, "lat": lat, "lon": lon})
                
                if res_pixel.status_code == 200:
                    d = res_pixel.json()
                    st.success(f"📍 Selected Coordinates: {lat:.6f}, {lon:.6f}")
                    
                    c1, c2 = st.columns(2)
                    ndvi_val = d.get('ndvi')
                    c1.metric("NDVI", "No data" if ndvi_val is None else f"{ndvi_val:.4f}")
                    c2.caption(f"**Scene class:** {d.get('scl_label', 'Unknown')}")
                    
                    st.divider()
                    
                    st.markdown("#### NDVI Temporal Profile")
                    res_ts = requests.get(f"{API_URL}/timeseries", params={"lat": lat, "lon": lon})
                    
                    if res_ts.status_code == 200:
                        ts_data = res_ts.json()
                        
                        if ts_data:
                            df = pd.DataFrame(ts_data)
                            df['date'] = pd.to_datetime(df['date'])
                            
                            valid_dates = df['date'].tolist()
                            
                            chart = alt.Chart(df).mark_line(point=True).encode(
                                x=alt.X(
                                    'date:T', 
                                    title='Acquisition Date',
                                    axis=alt.Axis(
                                        values=valid_dates, 
                                        format='%Y-%m-%d',
                                        labelAngle=-45
                                    )
                                ),
                                y=alt.Y('ndvi:Q', title='NDVI', scale=alt.Scale(domain=[-0.2, 1.0])),
                                tooltip=['date:T', 'ndvi:Q']
                            ).properties(
                                height=350
                            )
                            
                            st.altair_chart(chart, width='stretch')#use_container_width=True)
                        else:
                            st.info("No valid temporal data available for this point.")
                    else:
                        st.error("Failed to fetch time-series data from backend.")
                        
                else:
                    try:
                        error_detail = res_pixel.json().get("detail", "Unknown API error")
                    except Exception:
                        error_detail = f"Backend returned HTTP {res_pixel.status_code}"
                    
                    st.error(f"API Error: {error_detail}")
        else:
            st.info("👆 Click a location on the map to view spectral metrics and temporal graphs.")

# Execute the fragment
render_interactive_section(selected_date, clip_on)