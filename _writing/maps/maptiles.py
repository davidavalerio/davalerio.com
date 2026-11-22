"""Turn GIS data into the tile files a map page reads.

Run with QGIS's bundled Python (it carries GDAL and numpy); see a map's own
build script for the call. Inputs are read in place and never written; every
intermediate goes to a scratch folder, and only the finished .pmtiles files
land in the site.
"""
import math
import os
import sqlite3
import subprocess

import numpy as np
from osgeo import gdal, ogr

gdal.UseExceptions()
ogr.UseExceptions()

ORIGIN = 20037508.342789244  # half the width of the web-mercator world, in meters


def tile_bounds(z, x, y):
    size = 2 * ORIGIN / 2 ** z
    return (-ORIGIN + x * size, ORIGIN - (y + 1) * size, -ORIGIN + (x + 1) * size, ORIGIN - y * size)


def tile_range(z, west, south, east, north):
    """Tiles at zoom z covering a lon/lat box."""
    def xy(lon, lat):
        n = 2 ** z
        x = int((lon + 180) / 360 * n)
        y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
        return x, y
    x0, y0 = xy(west, north)
    x1, y1 = xy(east, south)
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def vectors(layers, out, scratch, minzoom, maxzoom, layer_zooms=None):
    """layers: {name: (gpkg path, SQL selecting the kept columns and geometry)}."""
    tmp = os.path.join(scratch, "vectors.gpkg")
    if os.path.exists(tmp):
        os.remove(tmp)
    for name, (src, sql) in layers.items():
        gdal.VectorTranslate(tmp, src, format="GPKG", SQLStatement=sql, layerName=name,
                             dstSRS="EPSG:4326", accessMode="append" if os.path.exists(tmp) else None)
    import json
    conf = json.dumps({k: {"minzoom": v[0], "maxzoom": v[1]} for k, v in (layer_zooms or {}).items()})
    mb = os.path.join(scratch, "vectors.mbtiles")
    for f in (mb, out):
        if os.path.exists(f):
            os.remove(f)
    # Written as MBTiles and converted, since not every GDAL build carries a PMTiles writer.
    gdal.VectorTranslate(mb, tmp, format="MBTiles",
                         datasetCreationOptions=["MINZOOM={}".format(minzoom), "MAXZOOM={}".format(maxzoom),
                                                 "CONF=" + conf, "NAME=" + os.path.basename(out)])
    subprocess.run(["pmtiles", "convert", mb, out], check=True, capture_output=True)


def feather(fine, coarse, out, trim=30, cells=80):
    """Blend a detailed DEM into a coarser one around it, so the two meet
    without a step: the detailed DEM loses its outer `trim` cells, then fades
    into the coarse elevation over the next `cells` cells."""
    src = gdal.Open(fine)
    w, h = src.RasterXSize - 2 * trim, src.RasterYSize - 2 * trim
    a = src.GetRasterBand(1).ReadAsArray(trim, trim, w, h).astype(np.float64)
    gt = list(src.GetGeoTransform())
    gt[0] += trim * gt[1]
    gt[3] += trim * gt[5]
    bounds = (gt[0], gt[3] + h * gt[5], gt[0] + w * gt[1], gt[3])
    ds = gdal.Warp("", coarse, format="MEM", dstSRS=src.GetProjection(), outputBounds=bounds, width=w, height=h,
                   resampleAlg="bilinear")
    under = ds.GetRasterBand(1).ReadAsArray().astype(np.float64)
    i = np.arange(h)[:, None]
    j = np.arange(w)[None, :]
    weight = np.clip(np.minimum(np.minimum(i, h - 1 - i), np.minimum(j, w - 1 - j)) / cells, 0, 1)
    weight[a <= 0] = 0
    blended = weight * a + (1 - weight) * under
    dst = gdal.GetDriverByName("GTiff").Create(out, w, h, 1, gdal.GDT_Float32)
    dst.SetGeoTransform(gt)
    dst.SetProjection(src.GetProjection())
    dst.GetRasterBand(1).WriteArray(blended)
    dst = None


def terrarium(h):
    v = np.round(np.clip(h, -32768, 32767) * 4) / 4 + 32768.0  # quarter-meter steps compress far better
    r = np.floor(v / 256)
    g = np.floor(v) - r * 256
    b = np.floor((v - np.floor(v)) * 256)
    return np.stack([r, g, b]).astype(np.uint8)


def terrain(dems, out, scratch, region, core, region_maxzoom, core_maxzoom, minzoom=5, size=256):
    """Terrarium-encoded elevation tiles.

    dems: GeoTIFFs in meters, coarsest first, each drawn over the one before;
    a value of 0 counts as empty, so a finer DEM's blank edges let the coarser
    one show through. region: lon/lat box tiled up to region_maxzoom; core: the
    smaller box tiled further, to core_maxzoom.
    """
    vrt = os.path.join(scratch, "dem.vrt")
    gdal.BuildVRT(vrt, dems, resolution="highest", resampleAlg="bilinear", srcNodata=0, VRTNodata=-32768)
    mb = os.path.join(scratch, "terrain.mbtiles")
    if os.path.exists(mb):
        os.remove(mb)
    db = sqlite3.connect(mb)
    db.execute("create table metadata (name text, value text)")
    db.execute("create table tiles (zoom_level integer, tile_column integer, tile_row integer, tile_data blob)")
    meta = {"name": os.path.basename(out), "format": "png", "type": "baselayer", "minzoom": minzoom,
            "maxzoom": core_maxzoom, "bounds": ",".join(str(v) for v in region),
            "encoding": "terrarium"}
    db.executemany("insert into metadata values (?, ?)", [(k, str(v)) for k, v in meta.items()])
    count = 0
    for z in range(minzoom, core_maxzoom + 1):
        box = region if z <= region_maxzoom else core
        for x, y in tile_range(z, *box):
            b = tile_bounds(z, x, y)
            res = (b[2] - b[0]) / size
            ds = gdal.Warp("", vrt, format="MEM", dstSRS="EPSG:3857", outputBounds=b, width=size, height=size,
                           resampleAlg="average" if res > 30 else "bilinear", dstNodata=-32768)
            h = ds.GetRasterBand(1).ReadAsArray().astype(np.float64)
            h[h <= -32767] = 0
            rgb = terrarium(h)
            mem = gdal.GetDriverByName("MEM").Create("", size, size, 3, gdal.GDT_Byte)
            for i in range(3):
                mem.GetRasterBand(i + 1).WriteArray(rgb[i])
            path = "/vsimem/t.png"
            gdal.GetDriverByName("PNG").CreateCopy(path, mem, options=["ZLEVEL=9"])
            f = gdal.VSIFOpenL(path, "rb")
            gdal.VSIFSeekL(f, 0, 2)
            n = gdal.VSIFTellL(f)
            gdal.VSIFSeekL(f, 0, 0)
            data = gdal.VSIFReadL(1, n, f)
            gdal.VSIFCloseL(f)
            gdal.Unlink(path)
            db.execute("insert into tiles values (?, ?, ?, ?)", (z, x, 2 ** z - 1 - y, data))
            count += 1
    db.commit()
    db.close()
    if os.path.exists(out):
        os.remove(out)
    subprocess.run(["pmtiles", "convert", mb, out], check=True, capture_output=True)
    return count
