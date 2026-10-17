# uvicorn api.main:app --reload

# Using rio-tiler for clip, without using cache.
# api/main.py

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from rio_tiler.io import Reader
import numpy as np
from rio_tiler.errors import TileOutsideBounds
from rio_tiler.utils import render
from rio_tiler.colormap import cmap
from pathlib import Path
import geopandas as gpd
from shapely.geometry import Point, mapping
from shapely.ops import unary_union
import rasterio
from rasterio.features import geometry_mask
from rasterio.transform import from_bounds
import io
from PIL import Image

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
)

ROOT = Path("data")
COG_DIR = ROOT / "cog"
SCL_DIR = ROOT / "scl"

BOUNDARY_PATH = Path(r"D:\1_WORK\Devanshu_ML_AI\4_AWS_S3_data_download\Mahoba\project_root_v2\data\boundary\UP-Mahoba.shp")

print("Loading Mahoba boundary geometries...")
aoi = gpd.read_file(BOUNDARY_PATH)
MAHOBA_UNION_4326 = unary_union(aoi.to_crs("EPSG:4326").geometry)
MAHOBA_GEOM_3857 = [mapping(unary_union(aoi.to_crs("EPSG:3857").geometry))]
print("Geometries loaded.")

SCL_LABELS = {
    0: "no data", 1: "saturated/defective", 2: "dark area", 3: "cloud shadow",
    4: "vegetation", 5: "not vegetated", 6: "water", 7: "unclassified",
    8: "cloud (medium)", 9: "cloud (high)", 10: "thin cirrus", 11: "snow/ice"
}

EMPTY_PNG = render(np.zeros((1, 256, 256), dtype="uint8"),
                   mask=np.zeros((256, 256), dtype="uint8"), img_format="PNG")

try:
    NDVI_CMAP = cmap.get("rdylgn")
except Exception:
    NDVI_CMAP = cmap.get("viridis")

def get_dates_from_cube():
    cube_path = COG_DIR / "Mahoba_NDVI_Datacube_COG.tif"
    if not cube_path.exists():
        return []
    with rasterio.open(cube_path) as src:
        dates_str = src.tags().get("DATES", "")
    return dates_str.split(",") if dates_str else []


@app.get("/dates")
def list_dates():
    return get_dates_from_cube()


@app.post("/clip/{date_str}")
def clip_date(date_str: str):
    dates = get_dates_from_cube()
    if date_str not in dates:
        raise HTTPException(status_code=404, detail="Date not found in Datacube")
    return {"status": "realtime_clipping_enabled"}


@app.get("/tiles/{date_str}/{z}/{x}/{y}.png")
def get_tile(date_str: str, z: int, x: int, y: int, clip: bool = False):
    
    empty_tile = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0bIDAT\x08\xd7c`\x00\x02\x00\x00\x05\x00\x01^\xf3*:\x00\x00\x00\x00IEND\xaeB`\x82'
    
    cog_path = COG_DIR / "Mahoba_NDVI_Datacube_COG.tif"
    if not cog_path.exists():
        return Response(empty_tile, media_type="image/png")
    
    dates = get_dates_from_cube()
    if date_str not in dates:
        return Response(empty_tile, media_type="image/png")
    
    date_idx = dates.index(date_str)
    band_indexes = (date_idx + 1,)
    
    try:
        with Reader(cog_path) as cog:
            img = cog.tile(x, y, z, indexes=band_indexes)
            
            # Allow rio-tiler to handle the -9999.0 nodata mask automatically.
            # Just rescale the floats from [-0.2, 1.0] to [0, 255] for the colormap.
            img.rescale(in_range=((-0.2, 1.0),)) 
            rendered_bytes = img.render(img_format="PNG", colormap=NDVI_CMAP)
            
            if clip:
                tile_transform = from_bounds(*img.bounds, img.width, img.height)
                geom_mask = geometry_mask(MAHOBA_GEOM_3857, transform=tile_transform, invert=True, out_shape=(img.height, img.width))
                pil_img = Image.open(io.BytesIO(rendered_bytes)).convert("RGBA")
                arr = np.array(pil_img)
                arr[:, :, 3] = np.where(geom_mask, arr[:, :, 3], 0)
                out_pil = Image.fromarray(arr)
                buf = io.BytesIO()
                out_pil.save(buf, format="PNG")
                rendered_bytes = buf.getvalue()

            return Response(rendered_bytes, media_type="image/png")
            
    except Exception as e:
        print(f"Tile Error ({z}/{x}/{y}): {str(e)}")
        return Response(empty_tile, media_type="image/png")


@app.get("/pixel")
def get_pixel(date_str: str, lat: float, lon: float):
    if not MAHOBA_UNION_4326.contains(Point(lon, lat)):
        raise HTTPException(status_code=400, detail="Point falls outside the Mahoba boundary.")

    cube_path = COG_DIR / "Mahoba_NDVI_Datacube_COG.tif"
    if not cube_path.exists():
        raise HTTPException(status_code=404, detail="Datacube not found")
        
    dates = get_dates_from_cube()
    if date_str not in dates:
        raise HTTPException(status_code=404, detail="Date not found in Datacube")
        
    date_idx = dates.index(date_str)
    b_indices = (date_idx + 1,)
    
    try:
        with Reader(cube_path) as cog:
            pt = cog.point(lon, lat, indexes=b_indices)
            raw_data = pt.data if hasattr(pt, "data") else pt
            ndvi_val = float(raw_data[0])
    except Exception:
        raise HTTPException(status_code=400, detail="Point is physically outside the COG bounding box.")

    scl_file = SCL_DIR / "Mahoba_SCL_Datacube_COG.tif"
    scl_val = None
    if scl_file.exists():
        try:
            with Reader(scl_file) as scl_cog:
                scl_pt = scl_cog.point(lon, lat, indexes=(date_idx + 1,))
                raw_scl = scl_pt.data if hasattr(scl_pt, "data") else scl_pt
                scl_val = int(raw_scl[0])
        except Exception:
            pass

    return {
        "ndvi": None if ndvi_val == -9999.0 else round(ndvi_val, 4),
        "scl": scl_val,
        "scl_label": SCL_LABELS.get(scl_val, "Unknown")
    }


@app.get("/timeseries")
def get_timeseries(lat: float, lon: float):
    if not MAHOBA_UNION_4326.contains(Point(lon, lat)):
        raise HTTPException(status_code=400, detail="Point falls outside the Mahoba boundary.")

    cube_path = COG_DIR / "Mahoba_NDVI_Datacube_COG.tif"
    if not cube_path.exists():
        raise HTTPException(status_code=404, detail="Datacube not found.")

    try:
        with rasterio.open(cube_path) as src:
            dates = src.tags().get("DATES", "").split(",")
            total_bands = src.count
            
        b_indices = tuple(range(1, total_bands + 1))
        
        with Reader(cube_path) as cog:
            pt = cog.point(lon, lat, indexes=b_indices)
            raw_data = pt.data if hasattr(pt, "data") else pt
            
        timeseries_data = []
        for i, date_str in enumerate(dates):
            val = float(raw_data[i])
            if val == -9999.0:  
                timeseries_data.append({
                    "date": date_str,
                    "ndvi": None
                })
            else:
                timeseries_data.append({
                    "date": date_str,
                    "ndvi": round(val, 4)
                })

        return timeseries_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading timeseries from Datacube: {str(e)}")