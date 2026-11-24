"""Build the tiles for the Byzantine Catholic Parishes map.

Run on the mini from the repo root with QGIS's Python and its data paths:

    A=/Applications/QGIS-final-<version>.app/Contents
    PROJ_DATA=$A/Resources/qgis/proj GDAL_DATA=$A/Resources/qgis/gdal \
        $A/MacOS/python _writing/maps/byzantine-parishes.py <scratch folder>

Reads the Byzantine Catholic Parishes project in Drive without touching it and
writes vectors.pmtiles (the parishes with what a reader sees on clicking one,
and the state boundaries) and terrain.pmtiles (USGS 3DEP elevation for the
shaded relief beneath them: about 5 km over the whole map, 1.2 km over the
lower 48) into maps/byzantine-parishes/.
"""
import os
import sys
import time
import urllib.request

import numpy as np
from osgeo import gdal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maptiles  # noqa: E402

SRC = os.path.expanduser("~/My Drive (david@davalerio.com)/1-Practice/GIS/Byzantine Catholic Parishes")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "maps", "byzantine-parishes")
scratch = sys.argv[1]
os.makedirs(scratch, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

gpkg = os.path.join(SRC, "Byzantine Catholic Parishes.gpkg")
maptiles.vectors({
    "parishes": (gpkg, 'SELECT name, city, state, "group" AS grp, eparchy, status, sunday_liturgy, geom FROM parishes'),
    "states": (gpkg, "SELECT geom FROM states"),
}, os.path.join(OUT, "vectors.pmtiles"), scratch, minzoom=2, maxzoom=10)
print("vectors.pmtiles", round(os.path.getsize(os.path.join(OUT, "vectors.pmtiles")) / 1e6, 1), "MB")

# 3DEP elevation in Web Mercator, below sea level set to 0 so the sea stays flat paper.
EXPORT = ("https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
          "?bbox={},{},{},{}&bboxSR=3857&imageSR=3857&size={},{}&format=tiff&pixelType=F32"
          "&interpolation=RSP_BilinearInterpolation&f=image")
gdal.UseExceptions()
dems = []
# The lower 48 comes in eight pieces, since the server refuses larger requests.
pieces = [("region", (-19926000, 1345000, -5566000, 12216000), (2935, 2223))]
for i in range(4):
    for j in range(2):
        x0, y0 = -14472000 + i * 1948250, 2511000 + j * 2144500
        pieces.append(("core-{}{}".format(i, j), (x0, y0, x0 + 1948250, y0 + 2144500), (1595, 1755)))
for name, box, size in pieces:
    raw = os.path.join(scratch, "dem-{}-raw.tif".format(name))
    if not os.path.exists(raw):
        time.sleep(5)
        urllib.request.urlretrieve(EXPORT.format(*box, *size), raw)
    clean = os.path.join(scratch, "dem-{}.tif".format(name))
    src = gdal.Open(raw)
    a = src.GetRasterBand(1).ReadAsArray()
    a[~np.isfinite(a) | (a < 0) | (a > 9000)] = 0
    dst = gdal.GetDriverByName("GTiff").CreateCopy(clean, src)
    dst.GetRasterBand(1).WriteArray(a)
    dst.GetRasterBand(1).DeleteNoDataValue()
    dst = None
    dems.append(clean)
n = maptiles.terrain(dems, os.path.join(OUT, "terrain.pmtiles"), scratch,
                     region=(-179, 12, -50, 73), core=(-125, 24, -66, 50),
                     region_maxzoom=6, core_maxzoom=7, minzoom=2)
print("terrain tiles:", n, round(os.path.getsize(os.path.join(OUT, "terrain.pmtiles")) / 1e6, 1), "MB")
