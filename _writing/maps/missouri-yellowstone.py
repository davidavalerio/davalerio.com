"""Build the tiles for The Missouri and the Yellowstone.

Run on the mini from the repo root with QGIS's Python and its data paths:

    A=/Applications/QGIS-final-<version>.app/Contents
    PROJ_DATA=$A/Resources/qgis/proj GDAL_DATA=$A/Resources/qgis/gdal \
        $A/MacOS/python _writing/maps/missouri-yellowstone.py <scratch folder>

Reads the Williston Confluence project in Drive without touching it (its
Scripts/build.py makes the line drawing), fetches a coarser 3DEP elevation for
the land around it, and writes vectors.pmtiles and terrain.pmtiles into
maps/missouri-yellowstone/.
"""
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maptiles  # noqa: E402

SRC = os.path.expanduser("~/My Drive (david@davalerio.com)/1-Practice/GIS/Williston Confluence")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "maps", "missouri-yellowstone")
scratch = sys.argv[1]
os.makedirs(scratch, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

gpkg = os.path.join(SRC, "Williston Confluence.gpkg")
maptiles.vectors({name: (gpkg, "SELECT * FROM " + name) for name in
                  ("contours", "shoreline", "waterlines", "streams", "streets")},
                 os.path.join(OUT, "vectors.pmtiles"), scratch, minzoom=8, maxzoom=14,
                 layer_zooms={"contours": (8, 14), "waterlines": (8, 14), "streets": (8, 14)})

# 3DEP at 100 m over 160 km around the drawing, so tilted views have land past its edge.
coarse = os.path.join(scratch, "region-dem-missouri.tif")
if not os.path.exists(coarse):
    time.sleep(2)
    urllib.request.urlretrieve(
        "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
        "?bbox=508000,5244000,668000,5404000&bboxSR=32613&imageSR=32613&size=1600,1600"
        "&format=tiff&pixelType=F32&interpolation=RSP_BilinearInterpolation&f=image", coarse)
fine = os.path.join(scratch, "fine-dem.tif")
maptiles.feather(os.path.join(SRC, "Elevation 10m.tif"), coarse, fine, trim=5, cells=150)
n = maptiles.terrain([coarse, fine], os.path.join(OUT, "terrain.pmtiles"), scratch,
                     region=(-104.65, 47.33, -102.75, 48.78), core=(-104.45, 47.78, -103.33, 48.52),
                     region_maxzoom=10, core_maxzoom=12)
print("terrain tiles:", n)
for f in ("vectors.pmtiles", "terrain.pmtiles"):
    print(f, round(os.path.getsize(os.path.join(OUT, f)) / 1e6, 1), "MB")
