"""Relief computed from an elevation raster: shading from several lights and a tint by elevation, as image tiles."""
import hashlib
import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import reproject, Resampling
from PIL import Image

TILE, LAP = 8192, 2      # tiles overlap by two pixels so no seam shows between them


def hex_rgb(h):
    h = h.lstrip('#')
    return [int(h[i:i + 2], 16) for i in (0, 2, 4)]


def shade(z, cell, exaggeration, azimuth, altitude):
    """Shading from four lights spread about `azimuth`, each weighted by how obliquely it strikes the slope, so
    ridges of every bearing show. 1.0 is flat ground; brighter faces the light."""
    gy, gx = np.gradient(z * exaggeration, cell)
    gy = -gy                                                     # row 0 is north
    norm = np.sqrt(1 + gx * gx + gy * gy)
    g2 = gx * gx + gy * gy
    alt = math.radians(altitude)
    total = np.zeros_like(z)
    for off in (-67.5, -22.5, 22.5, 67.5):
        az = math.radians(azimuth + off)
        lx, ly = math.sin(az), math.cos(az)
        lit = (math.sin(alt) - math.cos(alt) * (gx * lx + gy * ly)) / norm
        along = gx * lx + gy * ly
        w = np.where(g2 > 0, 1 - along * along / np.where(g2 > 0, g2, 1), 0.5)
        total += w * lit
    return (total / 2) / math.sin(alt)


def render(frame, dem, dpi, stops, low, high, empty=None, exaggeration=1.0, azimuth=292.5, altitude=45.0, strength=0.5):
    """The relief for a frame as an RGB array at `dpi` on the page."""
    k = frame.page.k
    W = max(2, round(frame.w * k / 72 * dpi))
    H = max(2, round(frame.h * k / 72 * dpi))
    gx0, gy0, gx1, gy1 = frame.ground
    cell = (gx1 - gx0) / W
    dst = np.full((H, W), np.nan, 'float32')
    with rasterio.open(dem) as src:
        z = src.read(1).astype('float32')
        nodata = src.nodata
        if nodata is not None:
            z[z == nodata] = np.nan
        if empty is not None:
            z[z <= empty] = np.nan
        reproject(z, dst, src_transform=src.transform, src_crs=src.crs, src_nodata=np.nan,
                  dst_transform=from_origin(gx0, gy1, cell, (gy1 - gy0) / H), dst_crs=frame.crs.to_wkt(), dst_nodata=np.nan,
                  resampling=Resampling.cubic)
    hole = np.isnan(dst)
    zf = np.where(hole, np.nanmin(dst) if not hole.all() else 0, dst)
    s = shade(zf, cell, exaggeration, azimuth, altitude)
    factor = np.clip(1 + strength * (s - 1), 0, 1.12)
    t = np.clip((zf - low) / (high - low), 0, 1)
    xs = np.linspace(0, 1, len(stops))
    cols = np.array([hex_rgb(c) for c in stops], 'float32')
    rgb = np.stack([np.interp(t, xs, cols[:, i]) for i in range(3)], -1) * factor[..., None]
    rgb[hole] = 255
    return np.clip(rgb + 0.5, 0, 255).astype('uint8')


def tiles(frame, dem, out, name, dpi, **opts):
    """Write the relief as JPEG tiles into `out`; returns [(file, x, y, w, h)] in frame units. The file names
    carry a mark of the pixels, so a changed relief is a new file and an unchanged one is not rewritten."""
    rgb = render(frame, dem, dpi, **opts)
    H, W = rgb.shape[:2]
    mark = hashlib.sha1(rgb.tobytes()).hexdigest()[:10]
    ux, uy = frame.w / W, frame.h / H
    res = []
    for ty in range(0, H, TILE):
        for tx in range(0, W, TILE):
            part = rgb[ty:ty + TILE + LAP, tx:tx + TILE + LAP]
            f = f'{name}-{mark}-{ty // TILE}-{tx // TILE}.jpg'
            if not (Path(out) / f).exists():
                Image.fromarray(part).save(Path(out) / f, quality=93, subsampling=0)
            res.append((f, tx * ux, ty * uy, part.shape[1] * ux, part.shape[0] * uy))
    return res
