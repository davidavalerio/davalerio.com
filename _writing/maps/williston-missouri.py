"""Build the tiles for the Williston and the Missouri map.

Run on the mini from the repo root with QGIS's Python and its data paths:

    A=/Applications/QGIS-final-<version>.app/Contents
    PROJ_DATA=$A/Resources/qgis/proj GDAL_DATA=$A/Resources/qgis/gdal \
        $A/MacOS/python _writing/maps/williston-missouri.py <scratch folder>

Reads the Williston Area project in Drive without touching it, fetches a
coarser 3DEP elevation for the land around it (so tilted views have real
terrain past the project's edge), and writes vectors.pmtiles and
terrain.pmtiles into maps/williston-missouri/.
"""
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maptiles  # noqa: E402

SRC = os.path.expanduser("~/My Drive (david@davalerio.com)/1-Practice/GIS/Williston Area")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "maps", "williston-missouri")
scratch = sys.argv[1]
os.makedirs(scratch, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

gpkg = os.path.join(SRC, "Williston Area.gpkg")
maptiles.vectors({
    "contours": (gpkg, "SELECT elev_ft, geom FROM contours"),
    "streams": (gpkg, "SELECT gnis_name AS name, geom FROM streams WHERE ftype <> 558"),
    "rivers": (gpkg, "SELECT geom FROM rivers"),
    "waterbodies": (gpkg, "SELECT FTYPE AS ftype, geom FROM waterbodies"),
    "city": (gpkg, "SELECT BASENAME AS name, geom FROM city_limits"),
}, os.path.join(OUT, "vectors.pmtiles"), scratch, minzoom=8, maxzoom=14, layer_zooms={"contours": (10, 14)})

# 3DEP around the project at 100 m: 120 km square on Williston, UTM 13N.
coarse = os.path.join(scratch, "region-dem.tif")
if not os.path.exists(coarse):
    url = ("https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
           "?bbox=542000,5272000,662000,5392000&bboxSR=32613&imageSR=32613&size=1200,1200"
           "&format=tiff&pixelType=F32&interpolation=RSP_BilinearInterpolation&f=image")
    urllib.request.urlretrieve(url, coarse)

# Fade the project DEM into the coarse one so they meet without a step.
fine = os.path.join(scratch, "project-dem.tif")
maptiles.feather(os.path.join(SRC, "Elevation.tif"), coarse, fine)

n = maptiles.terrain([coarse, fine], os.path.join(OUT, "terrain.pmtiles"), scratch,
                     region=(-104.42, 47.62, -102.82, 48.68), core=(-103.86, 48.0, -103.38, 48.29),
                     region_maxzoom=11, core_maxzoom=13)
print("terrain tiles:", n)
for f in ("vectors.pmtiles", "terrain.pmtiles"):
    print(f, round(os.path.getsize(os.path.join(OUT, f)) / 1e6, 1), "MB")
