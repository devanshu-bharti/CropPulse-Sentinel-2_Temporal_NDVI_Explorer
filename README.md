# CropPulse: Sentinel-2 Temporal NDVI Explorer
CropPulse is an end-to-end geospatial data pipeline and interactive web application designed to monitor crop health over time. It processes raw Sentinel-2 L2A satellite imagery into highly compressed Cloud-Optimized GeoTIFF (COG) datacubes, serving them through a lightning-fast API for interactive mapping and time-series analysis.


## ✨ Features

* **High-Performance Data Pipeline:** Ingests raw Sentinel-2 SAFE archives, extracts Red (B04) and NIR (B08) bands, calculates NDVI on the fly, and compresses the output into temporal COG datacubes.
* **Memory-Optimized Processing:** Features aggressive garbage collection, GDAL threading safeguards, and BigTIFF intermediate generation to prevent memory deadlocks on large datasets.
* **Dynamic XYZ Tile Server:** A FastAPI backend leveraging `rio-tiler` to dynamically render COGs into web tiles with custom colormaps and on-the-fly shapefile clipping (transparent masking).
* **Interactive Map Explorer:** A Streamlit frontend allowing users to browse acquisition dates, toggle boundary clipping, and click anywhere on the map to extract precise pixel-level spectral metrics.
* **Temporal Profiling:** Automatically extracts and plots historical NDVI trends for any clicked location using Altair.

---

## 🏗️ Architecture

1. **Ingestion (`processor.py`):** Multi-threaded workers scan raw Sentinel-2 `.zip` files, probe for intersections with the Region of Interest (ROI), compute NDVI dynamically, and build a multi-date virtual stack (`.vrt`).
2. **Storage (`/data`):** The VRT is converted into a master `Float32` Cloud-Optimized GeoTIFF alongside a `UInt8` Scene Classification Layer (SCL) cloud mask.
3. **Backend API (`api/main.py`):** FastAPI reads the COGs using `rio-tiler`. It exposes endpoints for available dates, tile rendering (`/tiles/{z}/{x}/{y}.png`), single-pixel querying, and time-series extraction.
4. **Frontend UI (`app/streamlit_app.py`):** Streamlit consumes the API, rendering the tiles on a Folium map and plotting historical NDVI data.

---

## 💻 Tech Stack

**Backend & Data Pipeline:**
* [FastAPI](https://fastapi.tiangolo.com/) & Uvicorn (REST API)
* [Rasterio](https://rasterio.readthedocs.io/) & [GDAL](https://gdal.org/) (Geospatial I/O and COG generation)
* [rio-tiler](https://cogeotiff.github.io/rio-tiler/) (Dynamic map tile generation)
* [GeoPandas](https://geopandas.org/) & [Shapely](https://shapely.readthedocs.io/) (Vector geometry operations)
* [NumPy](https://numpy.org/) (High-speed array mathematics)

**Frontend:**
* [Streamlit](https://streamlit.io/) (UI Framework)
* [Folium](https://python-visualization.github.io/folium/) & `streamlit-folium` (Interactive web mapping)
* [Altair](https://altair-viz.github.io/) & [Pandas](https://pandas.pydata.org/) (Time-series data visualization)

---



## 📂 Project Structure

```text
├── api/
│   └── main.py                 # FastAPI backend for tile serving and data querying
├── app/
│   └── streamlit_app.py        # Streamlit frontend UI
├── data/                       # Generated automatically by processor
│   ├── cog/                    # Final NDVI Cloud-Optimized GeoTIFFs
│   ├── scl/                    # Final SCL (Cloud Mask) GeoTIFFs
│   └── tmp/                    # Checkpoints and intermediate BigTIFFs
├── RAW data/                   # Place your downloaded Sentinel-2 SAFE .zip files here
├── processor.py                # Heavy-lifting geospatial data processor
├── requirements.txt            # Project dependencies
└── README.md

```

---



## Note: 

  **Why are the `data/cog/` and `data/scl/` folders empty?**
> A single multi-date Sentinel-2 datacube routinely exceeds 10+ GB in size. To comply with GitHub's file size limits, the generated Cloud-Optimized GeoTIFFs (`.tif` files) are explicitly ignored via `.gitignore`. 
> 
> The folders are left empty by design. You must supply your own raw Sentinel-2 `.zip` archives and use the built-in UI data processor to generate the datacubes locally on your machine.
