"""Build the tiles for the Byzantine Catholic Parishes map.

Run on the mini from the repo root with QGIS's Python and its data paths:

    A=/Applications/QGIS-final-<version>.app/Contents
    PROJ_DATA=$A/Resources/qgis/proj GDAL_DATA=$A/Resources/qgis/gdal \
        $A/MacOS/python _writing/maps/byzantine-parishes.py <scratch folder>

Reads the Byzantine Catholic Parishes project in Drive without touching it and
writes vectors.pmtiles into maps/byzantine-parishes/: the parishes with what a
reader sees on clicking one, the state boundaries (the Census Bureau's 2025
cartographic boundary file of the states, 1:500,000), and the USGS coal fields of
the conterminous United States by rank (East 2013, Open-File Report 2012-1205,
kept in the project as Coal Fields.gpkg).
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
    "coal": (os.path.join(SRC, "Coal Fields.gpkg"), "SELECT rank, geom FROM coal_fields"),
}, os.path.join(OUT, "vectors.pmtiles"), scratch, minzoom=2, maxzoom=10)
print("vectors.pmtiles", round(os.path.getsize(os.path.join(OUT, "vectors.pmtiles")) / 1e6, 1), "MB")
