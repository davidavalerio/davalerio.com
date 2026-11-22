"""Build the tiles for the Byzantine Catholic Parishes map.

Run on the mini from the repo root with QGIS's Python and its data paths:

    A=/Applications/QGIS-final-<version>.app/Contents
    PROJ_DATA=$A/Resources/qgis/proj GDAL_DATA=$A/Resources/qgis/gdal \
        $A/MacOS/python _writing/maps/byzantine-parishes.py <scratch folder>

Reads the Byzantine Catholic Parishes project in Drive without touching it and
writes vectors.pmtiles (the parishes with what a reader sees on clicking one,
and the state boundaries) into maps/byzantine-parishes/.
"""
import os
import sys

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
