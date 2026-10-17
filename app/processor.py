# # V2.0

# # processor.py

# import json
# import os
# import re
# import time
# import warnings
# import xml.etree.ElementTree as ET
# import zipfile
# from collections import defaultdict
# from concurrent.futures import ThreadPoolExecutor, as_completed
# from pathlib import Path

# import geopandas as gpd
# import numpy as np
# import pandas as pd
# import rasterio
# from rasterio.crs import CRS
# from rasterio.enums import Resampling
# from rasterio.features import geometry_mask
# from rasterio.merge import merge
# from rasterio.shutil import copy as rio_copy
# from rasterio.transform import from_origin
# from shapely.geometry import box, mapping
# from shapely.ops import unary_union

# warnings.filterwarnings("ignore", category=rasterio.errors.NotGeoreferencedWarning)

# # --- CONFIGURATION ---
# CRS_EPSG = 32644
# PAD_M = 5000
# RES = 10
# BLOCK = 512
# NAME_RE = re.compile(r"^(S2[ABC])_MSIL2A_(\d{8})T\d{6}_N(\d{4})_R\d{3}_(T\d{2}[A-Z]{3})_")

# _CPU = os.cpu_count() or 4
# N_WORKERS = max(1, min(4, _CPU - 1))  # each worker holds ~0.7 GB RAM
# N_IO_THREADS = 8                      # pass-1 probing threads
# GDAL_THREADS = "ALL_CPUS"             # first attempt; "1" forces single-thread
# GDAL_CACHE_MB = 2048

# # --- GLOBAL CONTEXT ---
# _CTX = {}

# def _make_grid(boundary_path):
#     aoi = gpd.read_file(boundary_path)
#     if aoi.crs is None:
#         raise ValueError("Boundary file has no CRS defined (.prj missing?). Aborting.")
#     aoi = aoi.to_crs(CRS_EPSG)
#     minx, miny, maxx, maxy = aoi.total_bounds
#     bounds = (
#         float(np.floor((minx - PAD_M) / RES) * RES),
#         float(np.floor((miny - PAD_M) / RES) * RES),
#         float(np.ceil((maxx + PAD_M) / RES) * RES),
#         float(np.ceil((maxy + PAD_M) / RES) * RES),
#     )
#     w = int(round((bounds[2] - bounds[0]) / RES))
#     h = int(round((bounds[3] - bounds[1]) / RES))
#     transform = from_origin(bounds[0], bounds[3], RES, RES)
#     return aoi, bounds, w, h, transform

# def _init_worker(boundary_path):
#     """Populate per-process grid + AOI mask"""
#     _CTX.clear()
#     aoi, bounds, w, h, transform = _make_grid(boundary_path)
#     geoms = [mapping(g) for g in aoi.geometry]
#     aoi_mask = geometry_mask(geoms, out_shape=(h, w), transform=transform, invert=True)
#     _CTX.update(aoi=aoi, bounds=bounds, w=w, h=h, transform=transform, aoi_mask=aoi_mask)

# # --- HELPERS ---
# def index_products(zip_dir):
#     by_date, skipped = defaultdict(list), []
#     for z in sorted(Path(zip_dir).glob("*.zip")):
#         m = NAME_RE.match(z.name)
#         if m:
#             _, date, baseline, tile = m.groups()
#             by_date[date].append({"zip": z, "baseline": int(baseline), "tile": tile})
#         else:
#             skipped.append(z.name)
#     return by_date, skipped

# def zip_paths(z: Path) -> dict:
#     with zipfile.ZipFile(z) as zf:
#         names = zf.namelist()

#     def find(rx):
#         hits = [n for n in names if re.search(rx, n)]
#         if len(hits) != 1:
#             raise RuntimeError(f"{z.name}: pattern {rx} matched {len(hits)} files (expected 1).")
#         return f"/vsizip/{z.as_posix()}/{hits[0]}"

#     return {
#         "B04": find(r"/R10m/[^/]*_B04_10m\.jp2$"),
#         "SCL": find(r"/R20m/[^/]*_SCL_20m\.jp2$"),
#     }

# def read_on_grid(path, dtype):
#     with rasterio.open(path) as src:
#         arr, tr = merge([src], bounds=_CTX["bounds"], res=RES, nodata=0,
#                         dtype=dtype, resampling=Resampling.nearest)
#     if tr != _CTX["transform"] or arr.shape[1:] != (_CTX["h"], _CTX["w"]):
#         raise RuntimeError("Mosaic grid differs from the fixed grid; cannot build a consistent stack.")
#     return arr[0]

# # --- PASS 1: PROBE ---
# def _probe_date(date, products, union):
#     hits = []
#     for p in products:
#         try:
#             paths = zip_paths(p["zip"])
#             with rasterio.open(paths["B04"]) as s:
#                 if s.crs is None or s.crs.to_epsg() != CRS_EPSG:
#                     continue
#                 if box(*s.bounds).intersects(union):
#                     hits.append({"zip": str(p["zip"]), "baseline": p["baseline"], "tile": p["tile"],
#                                  "b04": paths["B04"], "scl": paths["SCL"]})
#         except Exception:
#             return date, None
#     return date, hits

# # --- PASS 2: PER-DATE MOSAIC ---
# def _checkpoint_paths(chk_b04, chk_scl, chk_meta, date):
#     return chk_b04 / f"{date}.tif", chk_scl / f"{date}.tif", chk_meta / f"{date}.json"

# def _process_date(task, chk_b04, chk_scl, chk_meta):
#     date, hits = task
#     t0 = time.time()
#     b04_file, scl_file, meta_file = _checkpoint_paths(chk_b04, chk_scl, chk_meta, date)
#     try:
#         h, w, aoi_mask = _CTX["h"], _CTX["w"], _CTX["aoi_mask"]
#         b04 = np.zeros((h, w), np.uint16)
#         scl = np.zeros((h, w), np.uint8)
#         offsets = []

#         for p in hits:
#             offset = 1000 if p["baseline"] >= 400 else 0
#             offsets.append(offset)

#             a = read_on_grid(p["b04"], "uint16")
#             valid = a > 0
#             np.maximum(a, offset + 1, out=a)
#             a -= offset
#             a[~valid] = 0
#             np.copyto(b04, a, where=(b04 == 0))
#             del a, valid

#             s = read_on_grid(p["scl"], "uint8")
#             np.copyto(scl, s, where=(scl == 0))
#             del s

#         base_profile = dict(
#             driver="GTiff", count=1, nodata=0, height=h, width=w,
#             crs=f"EPSG:{CRS_EPSG}", transform=_CTX["transform"], compress="deflate",
#             predictor=2, tiled=True, blockxsize=BLOCK, blockysize=BLOCK,
#         )
#         with rasterio.open(b04_file, "w", dtype="uint16", **base_profile) as dst:
#             dst.write(b04, 1)
#         with rasterio.open(scl_file, "w", dtype="uint8", **base_profile) as dst:
#             dst.write(scl, 1)

#         scl_in = scl[aoi_mask]
#         pct = lambda codes: round(100 * float(np.isin(scl_in, codes).mean()), 2)
#         meta = dict(
#             date=date,
#             tiles=",".join(p["tile"] for p in hits),
#             baseline=",".join(f"N{p['baseline']:04d}" for p in hits),
#             dn_offset=",".join(str(o) for o in offsets),
#             aoi_px=int(aoi_mask.sum()),
#             nodata_pct=round(100 * float((scl_in == 0).mean()), 2),
#             clear_pct=pct([4, 5, 6]),
#             cloud_pct=pct([8, 9, 10]),
#             shadow_pct=pct([3]),
#         )
#         with open(meta_file, "w") as f:
#             json.dump(meta, f)
#         return date, True, f"{int(time.time() - t0)}s"

#     except Exception as e:
#         for f in (b04_file, scl_file, meta_file):
#             if f.exists():
#                 f.unlink()
#         return date, False, str(e)

# # --- PASS 3: VIRTUAL STACK ---
# def write_stack_vrt(vrt_path, chk_dir, dates, dtype_name, suffix, tags):
#     root = ET.Element("VRTDataset", rasterXSize=str(_CTX["w"]), rasterYSize=str(_CTX["h"]))
#     ET.SubElement(root, "SRS").text = CRS.from_epsg(CRS_EPSG).to_wkt()
#     ET.SubElement(root, "GeoTransform").text = ", ".join(repr(v) for v in _CTX["transform"].to_gdal())
#     md = ET.SubElement(root, "Metadata")
#     for k, v in tags.items():
#         ET.SubElement(md, "MDI", key=k).text = v
#     for i, d in enumerate(dates, start=1):
#         band = ET.SubElement(root, "VRTRasterBand", dataType=dtype_name, band=str(i))
#         ET.SubElement(band, "Description").text = f"{d}_{suffix}"
#         ET.SubElement(band, "NoDataValue").text = "0"
#         src = ET.SubElement(band, "SimpleSource")
#         fn = ET.SubElement(src, "SourceFilename", relativeToVRT="1")
#         fn.text = f"{chk_dir.name}/{d}.tif"
#         ET.SubElement(src, "SourceBand").text = "1"
#     ET.ElementTree(root).write(vrt_path, encoding="utf-8", xml_declaration=True)

# # --- PASS 4: COG EXPORT ---
# def _export_cog(src_vrt, dst, resampling, thread_modes, tmp_dir):
#     last = None
#     for mode in thread_modes:
#         env = {
#             "GDAL_CACHEMAX": GDAL_CACHE_MB,
#             "GDAL_NUM_THREADS": mode,
#             "GDAL_MAX_DATASET_POOL_SIZE": 256,
#             "CPL_TMPDIR": str(tmp_dir),
#         }
#         try:
#             with rasterio.Env(**env):
#                 rio_copy(str(src_vrt), str(dst), driver="COG", compress="deflate",
#                          predictor=2, blocksize=BLOCK, bigtiff="YES", interleave="band",
#                          num_threads=mode, resampling=resampling)
#             return mode
#         except Exception as e:
#             last = e
#             try:
#                 Path(dst).unlink(missing_ok=True)
#             except OSError:
#                 pass
#     raise last

# # --- MAIN GENERATOR FUNCTION FOR STREAMLIT ---
# def process_pipeline(raw_data_dir: str, boundary_path: str):
#     """
#     Generator function that yields status updates to Streamlit.
#     Yields dicts with format: {"status": "progress"|"info"|"error", "value": float, "message": str}
#     """
#     try:
#         OUT_ROOT = Path("data").resolve()
#         OUT_COG = OUT_ROOT / "cog"
#         OUT_SCL = OUT_ROOT / "scl"
#         OUT_MASK = OUT_ROOT / "mask"
#         TMP = OUT_ROOT / "tmp"
#         CHK_B04 = TMP / "b04_checkpoints"
#         CHK_SCL = TMP / "scl_checkpoints"
#         CHK_META = TMP / "meta_checkpoints"

#         for d in (OUT_COG, OUT_SCL, OUT_MASK, TMP, CHK_B04, CHK_SCL, CHK_META):
#             d.mkdir(parents=True, exist_ok=True)

#         yield {"status": "progress", "value": 0.05, "message": "[1/6] Loading boundary shapefile and computing grid..."}
#         _init_worker(boundary_path)
#         union = unary_union(_CTX["aoi"].geometry)
#         gpd.GeoDataFrame(geometry=[unary_union(_CTX["aoi"].to_crs(4326).geometry)], crs=4326).to_file(
#             OUT_ROOT / "mahoba_boundary_4326.geojson", driver="GeoJSON")

#         yield {"status": "progress", "value": 0.10, "message": "[2/6] Indexing SAFE products..."}
#         products_by_date, skipped = index_products(raw_data_dir)
#         if skipped:
#             yield {"status": "info", "message": f"{len(skipped)} zip(s) did not match the SAFE pattern and were ignored."}
#         if not products_by_date:
#             raise RuntimeError("No SAFE zips found in directory.")

#         yield {"status": "progress", "value": 0.15, "message": f"[3/6] Pass 1: Probing tile/AOI intersection ({N_IO_THREADS} threads)..."}
#         plan = {}
#         with ThreadPoolExecutor(max_workers=N_IO_THREADS) as ex:
#             futs = [ex.submit(_probe_date, d, ps, union) for d, ps in sorted(products_by_date.items())]
#             for f in as_completed(futs):
#                 date, hits = f.result()
#                 if hits:
#                     plan[date] = hits

#         dates = sorted(plan)
#         if not dates:
#             raise RuntimeError("No date has a tile intersecting the Mahoba boundary.")

#         mask_path = OUT_MASK / "Mahoba_boundary_mask_COG.tif"
#         if not mask_path.exists():
#             with rasterio.open(
#                 mask_path, "w", driver="GTiff", count=1, dtype="uint8", nodata=0,
#                 height=_CTX["h"], width=_CTX["w"], crs=f"EPSG:{CRS_EPSG}",
#                 transform=_CTX["transform"], compress="deflate",
#             ) as dst:
#                 dst.write(_CTX["aoi_mask"].astype("uint8"), 1)

#         yield {"status": "progress", "value": 0.20, "message": f"[4/6] Pass 2: Processing {len(dates)} dates ({N_WORKERS} threads)..."}
#         success = set()
#         todo = []
#         for date in dates:
#             b, s, m = _checkpoint_paths(CHK_B04, CHK_SCL, CHK_META, date)
#             if b.exists() and s.exists() and m.exists():
#                 success.add(date)
#             else:
#                 todo.append((date, plan[date]))

#         if todo:
#             total_todo = len(todo)
#             with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
#                 futs = [ex.submit(_process_date, t, CHK_B04, CHK_SCL, CHK_META) for t in todo]
#                 for i, f in enumerate(as_completed(futs)):
#                     date, ok, msg = f.result()
#                     if ok:
#                         success.add(date)
                    
#                     progress_val = 0.20 + (0.50 * ((i + 1) / total_todo))
#                     yield {"status": "progress", "value": progress_val, "message": f"[4/6] Mosaiced {date} ({i+1}/{total_todo})"}

#         success_dates = sorted(success)
#         if not success_dates:
#             raise RuntimeError("No dates were processed successfully.")

#         yield {"status": "progress", "value": 0.75, "message": "[5/6] Pass 3: Building virtual stacks and catalog..."}
#         for legacy in ("datacube_stacked.tif", "scl_datacube_stacked.tif"):
#             (TMP / legacy).unlink(missing_ok=True)
#         for tmp_file in TMP.glob("*.tmp"):
#             tmp_file.unlink()

#         vrt_b04 = TMP / "b04_stack.vrt"
#         vrt_scl = TMP / "scl_stack.vrt"
#         write_stack_vrt(vrt_b04, CHK_B04, success_dates, "UInt16", "B04",
#                         {"DATES": ",".join(success_dates), "BAND_ORDER": "B04"})
#         write_stack_vrt(vrt_scl, CHK_SCL, success_dates, "Byte", "SCL",
#                         {"DATES": ",".join(success_dates)})

#         rows = []
#         for date in success_dates:
#             with open(CHK_META / f"{date}.json", "r") as f:
#                 rows.append(json.load(f))
#         pd.DataFrame(rows).to_csv(OUT_ROOT / "catalog.csv", index=False)

#         yield {"status": "progress", "value": 0.80, "message": "[6/6] Pass 4: Writing Cloud-Optimized GeoTIFFs..."}
#         out_cube_cog = OUT_COG / "Mahoba_Datacube_COG.tif"
#         out_scl_cog = OUT_SCL / "Mahoba_SCL_Datacube_COG.tif"

#         modes = [GDAL_THREADS] if GDAL_THREADS == "1" else [GDAL_THREADS, "1"]
        
#         yield {"status": "progress", "value": 0.85, "message": "Exporting B04 COG (multithreaded)..."}
#         _export_cog(vrt_b04, out_cube_cog, "average", modes, TMP)

#         yield {"status": "progress", "value": 0.95, "message": "Exporting SCL COG (multithreaded)..."}
#         _export_cog(vrt_scl, out_scl_cog, "nearest", modes, TMP)

#         yield {"status": "progress", "value": 1.0, "message": f"Pipeline complete! Processed {len(success_dates)} dates."}

#     except Exception as e:
#         yield {"status": "error", "message": f"Pipeline Failed: {str(e)}"}


# processor.py

import json
import os
import re
import time
import warnings
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.merge import merge
from rasterio.shutil import copy as rio_copy
from rasterio.transform import from_origin
from shapely.geometry import box, mapping
from shapely.ops import unary_union

warnings.filterwarnings("ignore", category=rasterio.errors.NotGeoreferencedWarning)

# --- CONFIGURATION ---
CRS_EPSG = 32644
PAD_M = 5000
RES = 10
BLOCK = 512
NAME_RE = re.compile(r"^(S2[ABC])_MSIL2A_(\d{8})T\d{6}_N(\d{4})_R\d{3}_(T\d{2}[A-Z]{3})_")

_CPU = os.cpu_count() or 4
# Hard-capped to 2 workers to prevent RAM exhaustion when calculating Float32 NDVI + SCL
N_WORKERS = max(1, min(2, _CPU - 1))  
N_IO_THREADS = 8                      
GDAL_THREADS = "4" # Avoid ALL_CPUS to prevent GDAL infinite hang bugs
GDAL_CACHE_MB = 1024

# --- GLOBAL CONTEXT ---
_CTX = {}

def _make_grid(boundary_path):
    aoi = gpd.read_file(boundary_path)
    if aoi.crs is None:
        raise ValueError("Boundary file has no CRS defined (.prj missing?). Aborting.")
    aoi = aoi.to_crs(CRS_EPSG)
    minx, miny, maxx, maxy = aoi.total_bounds
    bounds = (
        float(np.floor((minx - PAD_M) / RES) * RES),
        float(np.floor((miny - PAD_M) / RES) * RES),
        float(np.ceil((maxx + PAD_M) / RES) * RES),
        float(np.ceil((maxy + PAD_M) / RES) * RES),
    )
    w = int(round((bounds[2] - bounds[0]) / RES))
    h = int(round((bounds[3] - bounds[1]) / RES))
    transform = from_origin(bounds[0], bounds[3], RES, RES)
    return aoi, bounds, w, h, transform

def _init_worker(boundary_path):
    """Populate per-process grid + AOI mask"""
    _CTX.clear()
    aoi, bounds, w, h, transform = _make_grid(boundary_path)
    geoms = [mapping(g) for g in aoi.geometry]
    aoi_mask = geometry_mask(geoms, out_shape=(h, w), transform=transform, invert=True)
    _CTX.update(aoi=aoi, bounds=bounds, w=w, h=h, transform=transform, aoi_mask=aoi_mask)

# --- HELPERS ---
def index_products(zip_dir):
    by_date, skipped = defaultdict(list), []
    for z in sorted(Path(zip_dir).glob("*.zip")):
        m = NAME_RE.match(z.name)
        if m:
            _, date, baseline, tile = m.groups()
            by_date[date].append({"zip": z, "baseline": int(baseline), "tile": tile})
        else:
            skipped.append(z.name)
    return by_date, skipped

def zip_paths(z: Path) -> dict:
    with zipfile.ZipFile(z) as zf:
        names = zf.namelist()

    def find(rx):
        hits = [n for n in names if re.search(rx, n)]
        if len(hits) != 1:
            raise RuntimeError(f"{z.name}: pattern {rx} matched {len(hits)} files (expected 1).")
        return f"/vsizip/{z.as_posix()}/{hits[0]}"

    return {
        "B04": find(r"/R10m/[^/]*_B04_10m\.jp2$"),
        "B08": find(r"/R10m/[^/]*_B08_10m\.jp2$"),
        "SCL": find(r"/R20m/[^/]*_SCL_20m\.jp2$")
    }

def read_on_grid(path, dtype):
    with rasterio.open(path) as src:
        arr, tr = merge([src], bounds=_CTX["bounds"], res=RES, nodata=0,
                        dtype=dtype, resampling=Resampling.nearest)
    if tr != _CTX["transform"] or arr.shape[1:] != (_CTX["h"], _CTX["w"]):
        raise RuntimeError("Mosaic grid differs from the fixed grid; cannot build a consistent stack.")
    return arr[0]

# --- PASS 1: PROBE ---
def _probe_date(date, products, union):
    hits = []
    for p in products:
        try:
            paths = zip_paths(p["zip"])
            with rasterio.open(paths["B04"]) as s:
                if s.crs is None or s.crs.to_epsg() != CRS_EPSG:
                    continue
                if box(*s.bounds).intersects(union):
                    hits.append({
                        "zip": str(p["zip"]), "baseline": p["baseline"], "tile": p["tile"],
                        "b04": paths["B04"], "b08": paths["B08"], "scl": paths["SCL"]
                    })
        except Exception:
            return date, None
    return date, hits

# --- PASS 2: PER-DATE MOSAIC ---
def _checkpoint_paths(chk_ndvi, chk_scl, chk_meta, date):
    return (
        chk_ndvi / f"{date}.tif", 
        chk_scl / f"{date}.tif", 
        chk_meta / f"{date}.json"
    )

def _process_date(task, chk_ndvi, chk_scl, chk_meta):
    date, hits = task
    t0 = time.time()
    ndvi_file, scl_file, meta_file = _checkpoint_paths(chk_ndvi, chk_scl, chk_meta, date)
    
    try:
        h, w, aoi_mask = _CTX["h"], _CTX["w"], _CTX["aoi_mask"]
        b04 = np.zeros((h, w), np.uint16)
        b08 = np.zeros((h, w), np.uint16)
        scl = np.zeros((h, w), np.uint8)
        offsets = []

        for p in hits:
            offset = 1000 if p["baseline"] >= 400 else 0
            offsets.append(offset)

            # Read B04 temporarily
            a4 = read_on_grid(p["b04"], "uint16")
            valid4 = a4 > 0
            np.maximum(a4, offset + 1, out=a4)
            a4 -= offset
            a4[~valid4] = 0
            np.copyto(b04, a4, where=(b04 == 0))
            del a4, valid4

            # Read B08 temporarily
            a8 = read_on_grid(p["b08"], "uint16")
            valid8 = a8 > 0
            np.maximum(a8, offset + 1, out=a8)
            a8 -= offset
            a8[~valid8] = 0
            np.copyto(b08, a8, where=(b08 == 0))
            del a8, valid8

            # Read SCL
            s = read_on_grid(p["scl"], "uint8")
            np.copyto(scl, s, where=(scl == 0))
            del s

        # --- CALCULATE NDVI (Memory Optimized) ---
        ndvi = np.full((h, w), -9999.0, dtype=np.float32)
        valid_mask = (b08 > 0) | (b04 > 0)
        
        # Convert to float for division
        b04_f = b04.astype(np.float32)
        b08_f = b08.astype(np.float32)
        
        # AGGRESSIVELY CLEAR RAM BEFORE MATH
        del b04
        del b08
        
        denom = b08_f + b04_f
        calc_mask = valid_mask & (denom != 0)
        
        # Compute and assign NDVI
        ndvi[calc_mask] = (b08_f[calc_mask] - b04_f[calc_mask]) / denom[calc_mask]
        
        # Clear float arrays from RAM
        del b04_f
        del b08_f
        del denom
        del calc_mask
        
        # --- WRITE CHECKPOINTS ---
        base_profile = dict(
            driver="GTiff", count=1, height=h, width=w,
            crs=f"EPSG:{CRS_EPSG}", transform=_CTX["transform"], compress="deflate",
            tiled=True, blockxsize=BLOCK, blockysize=BLOCK,
        )
            
        with rasterio.open(ndvi_file, "w", dtype="float32", nodata=-9999.0, predictor=3, **base_profile) as dst:
            dst.write(ndvi, 1)

        with rasterio.open(scl_file, "w", dtype="uint8", nodata=0, predictor=2, **base_profile) as dst:
            dst.write(scl, 1)

        scl_in = scl[aoi_mask]
        pct = lambda codes: round(100 * float(np.isin(scl_in, codes).mean()), 2)
        meta = dict(
            date=date,
            tiles=",".join(p["tile"] for p in hits),
            baseline=",".join(f"N{p['baseline']:04d}" for p in hits),
            dn_offset=",".join(str(o) for o in offsets),
            aoi_px=int(aoi_mask.sum()),
            nodata_pct=round(100 * float((scl_in == 0).mean()), 2),
            clear_pct=pct([4, 5, 6]),
            cloud_pct=pct([8, 9, 10]),
            shadow_pct=pct([3]),
        )
        with open(meta_file, "w") as f:
            json.dump(meta, f)
            
        return date, True, f"{int(time.time() - t0)}s"

    except Exception as e:
        for f in (ndvi_file, scl_file, meta_file):
            if f.exists():
                f.unlink()
        return date, False, str(e)

# --- PASS 3: VIRTUAL STACK ---
def write_stack_vrt(vrt_path, chk_dir, dates, dtype_name, suffix, tags, nodata="0"):
    root = ET.Element("VRTDataset", rasterXSize=str(_CTX["w"]), rasterYSize=str(_CTX["h"]))
    ET.SubElement(root, "SRS").text = CRS.from_epsg(CRS_EPSG).to_wkt()
    ET.SubElement(root, "GeoTransform").text = ", ".join(repr(v) for v in _CTX["transform"].to_gdal())
    md = ET.SubElement(root, "Metadata")
    for k, v in tags.items():
        ET.SubElement(md, "MDI", key=k).text = v
    for i, d in enumerate(dates, start=1):
        band = ET.SubElement(root, "VRTRasterBand", dataType=dtype_name, band=str(i))
        ET.SubElement(band, "Description").text = f"{d}_{suffix}"
        ET.SubElement(band, "NoDataValue").text = nodata
        src = ET.SubElement(band, "SimpleSource")
        fn = ET.SubElement(src, "SourceFilename", relativeToVRT="1")
        fn.text = f"{chk_dir.name}/{d}.tif"
        ET.SubElement(src, "SourceBand").text = "1"
    ET.ElementTree(root).write(vrt_path, encoding="utf-8", xml_declaration=True)

# --- PASS 4: COG EXPORT ---
def _export_cog(src_vrt, dst, resampling, thread_modes, tmp_dir, predictor=2):
    last = None
    for mode in thread_modes:
        env = {
            "GDAL_CACHEMAX": GDAL_CACHE_MB,
            "GDAL_NUM_THREADS": mode,
            "GDAL_MAX_DATASET_POOL_SIZE": 256,
            "CPL_TMPDIR": str(tmp_dir),
        }
        try:
            with rasterio.Env(**env):
                rio_copy(str(src_vrt), str(dst), driver="COG", compress="deflate",
                         predictor=predictor, blocksize=BLOCK, bigtiff="YES", interleave="band",
                         num_threads=mode, resampling=resampling)
            return mode
        except Exception as e:
            last = e
            try:
                Path(dst).unlink(missing_ok=True)
            except OSError:
                pass
    raise last

# --- MAIN GENERATOR FUNCTION FOR STREAMLIT ---
def process_pipeline(raw_data_dir: str, boundary_path: str):
    try:
        OUT_ROOT = Path("data").resolve()
        OUT_COG = OUT_ROOT / "cog"
        OUT_SCL = OUT_ROOT / "scl"
        TMP = OUT_ROOT / "tmp"
        
        CHK_NDVI = TMP / "ndvi_checkpoints"
        CHK_SCL = TMP / "scl_checkpoints"
        CHK_META = TMP / "meta_checkpoints"

        for d in (OUT_COG, OUT_SCL, TMP, CHK_NDVI, CHK_SCL, CHK_META):
            d.mkdir(parents=True, exist_ok=True)

        yield {"status": "progress", "value": 0.05, "message": "[1/5] Loading boundary shapefile and computing grid..."}
        _init_worker(boundary_path)
        union = unary_union(_CTX["aoi"].geometry)

        yield {"status": "progress", "value": 0.10, "message": "[2/5] Indexing SAFE products..."}
        products_by_date, skipped = index_products(raw_data_dir)
        if skipped:
            yield {"status": "info", "message": f"{len(skipped)} zip(s) did not match the SAFE pattern and were ignored."}
        if not products_by_date:
            raise RuntimeError("No SAFE zips found in directory.")

        yield {"status": "progress", "value": 0.15, "message": f"[3/5] Probing AOI intersection ({N_IO_THREADS} threads)..."}
        plan = {}
        with ThreadPoolExecutor(max_workers=N_IO_THREADS) as ex:
            futs = [ex.submit(_probe_date, d, ps, union) for d, ps in sorted(products_by_date.items())]
            for f in as_completed(futs):
                date, hits = f.result()
                if hits:
                    plan[date] = hits

        dates = sorted(plan)
        if not dates:
            raise RuntimeError("No date has a tile intersecting the Mahoba boundary.")

        yield {"status": "progress", "value": 0.20, "message": f"[4/5] Computing NDVI & SCL for {len(dates)} dates ({N_WORKERS} workers)..."}
        success = set()
        todo = []
        for date in dates:
            ndvi, scl, m = _checkpoint_paths(CHK_NDVI, CHK_SCL, CHK_META, date)
            if ndvi.exists() and scl.exists() and m.exists():
                success.add(date)
            else:
                todo.append((date, plan[date]))

        if todo:
            total_todo = len(todo)
            with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
                futs = [ex.submit(_process_date, t, CHK_NDVI, CHK_SCL, CHK_META) for t in todo]
                for i, f in enumerate(as_completed(futs)):
                    date, ok, msg = f.result()
                    if ok:
                        success.add(date)
                    
                    progress_val = 0.20 + (0.50 * ((i + 1) / total_todo))
                    yield {"status": "progress", "value": progress_val, "message": f"Processed {date} ({i+1}/{total_todo}) - {msg}"}

        success_dates = sorted(success)
        if not success_dates:
            raise RuntimeError("No dates were processed successfully.")

        yield {"status": "progress", "value": 0.75, "message": "[5/5] Building virtual stacks..."}
        for tmp_file in TMP.glob("*.tmp"):
            tmp_file.unlink()

        vrt_ndvi = TMP / "ndvi_stack.vrt"
        vrt_scl = TMP / "scl_stack.vrt"
        
        write_stack_vrt(vrt_ndvi, CHK_NDVI, success_dates, "Float32", "NDVI", {"DATES": ",".join(success_dates), "BAND_ORDER": "NDVI"}, nodata="-9999.0")
        write_stack_vrt(vrt_scl, CHK_SCL, success_dates, "Byte", "SCL", {"DATES": ",".join(success_dates)}, nodata="0")

        yield {"status": "progress", "value": 0.80, "message": "Exporting Final NDVI COG (multithreaded)..."}
        out_ndvi_cog = OUT_COG / "Mahoba_NDVI_Datacube_COG.tif"
        out_scl_cog = OUT_SCL / "Mahoba_SCL_Datacube_COG.tif"
        
        modes = [GDAL_THREADS] if GDAL_THREADS == "1" else [GDAL_THREADS, "1"]
        _export_cog(vrt_ndvi, out_ndvi_cog, "average", modes, TMP, predictor=3)

        yield {"status": "progress", "value": 0.90, "message": "Exporting Final SCL COG (multithreaded)..."}
        _export_cog(vrt_scl, out_scl_cog, "nearest", modes, TMP, predictor=2)

        yield {"status": "progress", "value": 1.0, "message": f"Pipeline complete! NDVI and SCL Datacubes created."}

    except Exception as e:
        yield {"status": "error", "message": f"Pipeline Failed: {str(e)}"}