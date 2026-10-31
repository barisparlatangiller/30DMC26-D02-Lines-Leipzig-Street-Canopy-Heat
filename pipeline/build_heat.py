"""
Summer land-surface temperature (LST) for Leipzig from Landsat 8/9 Collection 2 Level-2 (surface temperature band ST_B10).

Why: the city's Stadtklimaanalyse 2022 is published as PDF maps and an online viewer only, not as open data.
Landsat LST is open (USGS, public domain), reproducible and directly shows the surface urban heat island.

Method
  * all Landsat 8/9 scenes, June–August 2022–2025, cloud cover < 20 %, from the Microsoft Planetary Computer STAC
  * clouds, cloud shadow, dilated clouds, snow and fill removed with QA_PIXEL
  * ST_B10 → °C (DN × 0.00341802 + 149.0 − 273.15), warped to a 30 m EPSG:25833 grid over Leipzig
  * a scene is used if ≥ 70 % of the city is cloud-free; each scene is turned into an anomaly
    (pixel − scene median over the city), the per-pixel median anomaly is added back to the median of the scene medians
    → "a typical clear summer day around 10:30 local time", without seams from warmer / cooler days.

Outputs
  input/lst_summer.tif           float32 °C, EPSG:25833, 30 m   (also copied to docs/data for download)
  docs/data/heat.png + heat.json coloured overlay in Web Mercator and its corner coordinates for the web map

Run: python pipeline/build_heat.py [--years 2022-2025]
"""
import argparse, json, shutil
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds, calculate_default_transform, reproject

BBOX_25833 = (305000, 5677000, 331000, 5704000)       # Leipzig + margin
RES = 30
QA_BAD = (1 << 0) | (1 << 1) | (1 << 3) | (1 << 4) | (1 << 5)   # fill, dilated cloud, cloud, shadow, snow


def grid():
    x0, y0, x1, y1 = BBOX_25833
    w, h = int((x1 - x0) / RES), int((y1 - y0) / RES)
    return from_origin(x0, y1, RES, RES), w, h


def read_to_grid(href, transform, w, h, resampling):
    with rasterio.open(href) as src:
        with WarpedVRT(src, crs="EPSG:25833", transform=transform, width=w, height=h, resampling=resampling) as v:
            return v.read(1)


def city_mask(transform, w, h, boundary):
    if not boundary or not Path(boundary).exists():
        return np.ones((h, w), bool)
    import geopandas as gpd
    from rasterio.features import geometry_mask
    g = gpd.read_file(boundary).to_crs(25833)
    return ~geometry_mask(g.geometry.values, (h, w), transform)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2022-2025")
    ap.add_argument("--boundary", default="input/stadtgrenze.gpkg")
    ap.add_argument("--out", default="input/lst_summer.tif")
    ap.add_argument("--web", default="docs/data")
    ap.add_argument("--max-scenes", type=int, default=30)
    a = ap.parse_args()
    import pystac_client, planetary_computer

    y0, y1 = [int(x) for x in a.years.split("-")]
    lon_lat = transform_bounds("EPSG:25833", "EPSG:4326", *BBOX_25833)
    cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1",
                                    modifier=planetary_computer.sign_inplace)
    items = []
    for y in range(y0, y1 + 1):
        s = cat.search(collections=["landsat-c2-l2"], bbox=lon_lat, datetime=f"{y}-06-01/{y}-08-31",
                       query={"eo:cloud_cover": {"lt": 20}, "platform": {"in": ["landsat-8", "landsat-9"]}})
        items += list(s.items())
    items.sort(key=lambda i: i.properties.get("eo:cloud_cover", 100))
    print(f"Landsat scenes found: {len(items)}", flush=True)

    transform, w, h = grid()
    inside = city_mask(transform, w, h, a.boundary)
    stack, meds, used = [], [], []
    for it in items:
        if len(used) >= a.max_scenes:
            break
        try:
            st = read_to_grid(it.assets["lwir11"].href, transform, w, h, Resampling.bilinear).astype("float32")
            qa = read_to_grid(it.assets["qa_pixel"].href, transform, w, h, Resampling.nearest)
        except Exception as e:
            print("  skip", it.id, e, flush=True)
            continue
        ok = (st > 0) & ((qa & QA_BAD) == 0)
        cover = (ok & inside).sum() / inside.sum()
        if cover < 0.7:
            print(f"  {it.id}: only {cover:.0%} of the city clear — skipped", flush=True)
            continue
        c = np.where(ok, st * 0.00341802 + 149.0 - 273.15, np.nan)
        m = float(np.nanmedian(c[inside]))
        stack.append(c - m); meds.append(m); used.append(it.id)
        print(f"  {it.id} ({it.datetime:%Y-%m-%d}): {cover:.0%} clear, city median {m:.1f} °C", flush=True)
    if len(used) < 3:
        raise SystemExit(f"only {len(used)} usable scenes — heat layer not built")

    anom = np.nanmedian(np.stack(stack), axis=0)
    base = float(np.median(meds))
    lst = (anom + base).astype("float32")
    lst[~inside] = np.nan
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    prof = dict(driver="GTiff", width=w, height=h, count=1, dtype="float32", crs="EPSG:25833", transform=transform,
                nodata=np.nan, compress="deflate", predictor=3, tiled=True)
    with rasterio.open(a.out, "w", **prof) as dst:
        dst.write(lst, 1)
        dst.update_tags(units="degC", source="Landsat 8/9 C2 L2 ST_B10 (USGS) via Microsoft Planetary Computer",
                        scenes=",".join(used), method="median of per-scene anomalies + median scene median, JJA")
    web = Path(a.web); web.mkdir(parents=True, exist_ok=True)
    shutil.copy(a.out, web / "lst_summer_leipzig.tif")
    v = lst[inside]
    info = {"scenes": len(used), "scene_ids": used, "years": a.years, "base_median": round(base, 1),
            "p02": round(float(np.nanpercentile(v, 2)), 1), "p50": round(float(np.nanpercentile(v, 50)), 1),
            "p98": round(float(np.nanpercentile(v, 98)), 1)}
    write_png(lst, transform, web, info)
    print(json.dumps(info, indent=1))


def write_png(lst, transform, web, info):
    """colour the raster and warp it to Web Mercator so MapLibre can place it with 4 corners"""
    from PIL import Image
    h, w = lst.shape
    left, top = transform.c, transform.f
    right, bottom = left + w * transform.a, top + h * transform.e
    dt, dw, dh = calculate_default_transform("EPSG:25833", "EPSG:3857", w, h, left, bottom, right, top, resolution=20)
    merc = np.full((dh, dw), np.nan, "float32")
    reproject(lst, merc, src_transform=transform, src_crs="EPSG:25833", dst_transform=dt, dst_crs="EPSG:3857",
              resampling=Resampling.bilinear, src_nodata=np.nan, dst_nodata=np.nan)
    lo, hi = info["p02"], info["p98"]
    stops = [(0.0, (49, 54, 149)), (0.2, (69, 117, 180)), (0.4, (171, 217, 233)), (0.5, (255, 255, 191)),
             (0.65, (253, 174, 97)), (0.82, (215, 48, 39)), (1.0, (120, 0, 30))]
    t = np.nan_to_num(np.clip((merc - lo) / (hi - lo), 0, 1))
    rgb = np.zeros((dh, dw, 4), "uint8")
    xs = np.array([s[0] for s in stops])
    for ch in range(3):
        rgb[..., ch] = np.interp(t, xs, [s[1][ch] for s in stops]).astype("uint8")
    rgb[..., 3] = np.where(np.isnan(merc), 0, 255)
    Image.fromarray(rgb, "RGBA").save(web / "heat.png", optimize=True)
    x0, y1 = dt.c, dt.f
    x1, y0 = x0 + dw * dt.a, y1 + dh * dt.e
    from pyproj import Transformer
    tr = Transformer.from_crs(3857, 4326, always_xy=True)
    corners = [tr.transform(x0, y1), tr.transform(x1, y1), tr.transform(x1, y0), tr.transform(x0, y0)]
    info["corners"] = [[round(x, 6), round(y, 6)] for x, y in corners]
    info["scale"] = {"lo": lo, "hi": hi, "stops": [[s[0], "#%02x%02x%02x" % s[1]] for s in stops]}
    json.dump(info, open(web / "heat.json", "w"), indent=1)


if __name__ == "__main__":
    main()
