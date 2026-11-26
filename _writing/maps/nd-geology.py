"""Build the tiles for the Geologic Map of North Dakota.

Run on the mini from the repo root with QGIS's Python and its data paths:

    A=/Applications/QGIS-final-<version>.app/Contents
    PROJ_DATA=$A/Resources/qgis/proj GDAL_DATA=$A/Resources/qgis/gdal \
        $A/MacOS/python _writing/maps/nd-geology.py <scratch folder>

Reads the North Dakota Geology project in Drive without touching it and
writes vectors.pmtiles (the units, each with its legend wording, the glacial
features, counties and towns) and terrain.pmtiles (its 100 m
elevation, faded into a coarser download around it) into maps/nd-geology/. UNITS is the print map's legend, so
the web map and the print say the same thing.
"""
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import maptiles  # noqa: E402

SRC = os.path.expanduser("~/My Drive (david@davalerio.com)/1-Practice/GIS/North Dakota Geology")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "maps", "nd-geology")
scratch = sys.argv[1]
os.makedirs(scratch, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

UNITS = {
    "Qor": "River sediment: overbank clay and sand over channel sand", "Qod": "Windblown sand in dunes",
    "Qol": "Windblown silt", "Qop": "Pond sediment in ephemeral ponds", "ls": "Landslide deposits",
    "Qccg": "Collapsed glacial sediment, gently undulating", "Qccu": "Collapsed glacial sediment, undulating",
    "Qccr": "Collapsed glacial sediment, rolling", "Qcch": "Collapsed glacial sediment, hilly",
    "Qcdc": "Collapsed sediment draped over older topography", "Qcdg": "Draped over older glacial topography",
    "Qcdn": "Draped over nonglacial topography", "Qcm": "On subglacially molded ridges and grooves",
    "Qct": "On glacial thrust masses", "Qcer": "Eroded by meltwater rivers", "Qces": "Eroded by slopewash on valley sides",
    "Qcew": "Eroded by waves", "Qcs": "Shoreline sediment: beach-ridge complexes", "Qcoh": "Ice-walled-lake and collapsed lake sediment", "Qcof": "Proglacial-lake sediment",
    "Qcoe": "Eroded lake sediment on valley sides", "Qcrf": "River sediment of outwash plains and terraces",
    "Qcrh": "Collapsed river sediment", "QTu": "Terrace, fan and pediment sediment, undivided",
    "QTou": "Windblown and older sand, undivided", "Tm": "Sandstone and limestone, Oligocene to Pliocene",
    "Tw": "White River Group, Oligocene", "Tg": "Golden Valley Formation, Paleocene–Eocene",
    "Ts": "Sentinel Butte Formation, Paleocene", "Tb": "Bullion Creek Formation, Paleocene",
    "Tp": "Slope Formation, Paleocene", "Tc": "Cannonball Formation, Paleocene, marine",
    "Tl": "Ludlow Formation, Paleocene", "Kh": "Hell Creek Formation", "Kf": "Fox Hills Formation, marine",
    "Kp": "Pierre Formation, marine shale", "Kn": "Niobrara and Carlile Formations, marine shale",
    "SP": "Not described in the survey's data", "water": "Water",
}
label = "CASE Abbreviation " + " ".join("WHEN '{}' THEN '{}'".format(k, v.replace("'", "''")) for k, v in UNITS.items()) + " END"

gpkg = os.path.join(SRC, "North Dakota Geology.gpkg")
maptiles.vectors({
    "units": (gpkg, "SELECT Abbreviation AS abbr, {} AS label, geom FROM surface_geology".format(label)),
    "glacial": (gpkg, "SELECT FEATURE1 AS kind, FEATURE2 AS certainty, geom FROM linear_features"),
    "counties": (gpkg, "SELECT geom FROM counties"),
    "towns": (gpkg, "SELECT name, pop2020, geom FROM towns"),
}, os.path.join(OUT, "vectors.pmtiles"), scratch, minzoom=5, maxzoom=11)

# 3DEP at about 600 m over the map's bounds, so the relief runs on past the project's own
# elevation instead of ending at a cliff, and the project's 100 m faded into it.
coarse = os.path.join(scratch, "region-dem-nd-geology.tif")
if not os.path.exists(coarse):
    time.sleep(2)
    urllib.request.urlretrieve(
        "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"
        "?bbox=-106.5,44.6,-94.1,50.3&bboxSR=4326&imageSR=32614&size=1550,1060"
        "&format=tiff&pixelType=F32&interpolation=RSP_BilinearInterpolation&f=image", coarse)
fine = os.path.join(scratch, "project-dem.tif")
maptiles.feather(os.path.join(SRC, "Elevation.tif"), coarse, fine, trim=5, cells=40)
n = maptiles.terrain([coarse, fine], os.path.join(OUT, "terrain.pmtiles"), scratch,
                     region=(-106.5, 44.6, -94.1, 50.3), core=(-104.7, 45.6, -96.2, 49.3),
                     region_maxzoom=8, core_maxzoom=10)
print("terrain tiles:", n)
for f in ("vectors.pmtiles", "terrain.pmtiles"):
    print(f, round(os.path.getsize(os.path.join(OUT, f)) / 1e6, 1), "MB")
