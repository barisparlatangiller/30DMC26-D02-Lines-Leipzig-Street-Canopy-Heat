"""
Vector tiles for the web map (PMTiles, served straight from GitHub Pages).

  trees.pmtiles      layer "t": every tree of the Baumkataster as a point (crown, height, year, species, gap flag)
  crowns.pmtiles     layer "c": crown discs (diameter = kr_durchm, imputed where missing) with height and
                     crown base, drawn as floating 3D cylinders in the 3D view
  buildings.pmtiles  layer "b": LoD2 footprints with measured height (if input/leipzig_lod2.parquet exists)

Run after build_streets.py:  python pipeline/build_tiles.py
Needs tippecanoe on PATH.
"""
import argparse, json, subprocess, tempfile
from pathlib import Path

import numpy as np
import shapely
from pyproj import Transformer


def tippecanoe(src, dst, layer, zmin, zmax, extra=()):
    cmd = ["tippecanoe", "-o", str(dst), "--force", "-l", layer, "-Z", str(zmin), "-z", str(zmax),
           "--simplification=2", "-P", *extra, str(src)]
    print(" ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)
    print(f"  {dst.name}: {dst.stat().st_size/1e6:.1f} MB", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trees", default="input/trees.geojsonl")
    ap.add_argument("--buildings", default="input/leipzig_lod2.parquet")
    ap.add_argument("--out", default="docs/data")
    a = ap.parse_args()
    out = Path(a.out)

    # ---------------------------------------------------------------- trees (points)
    tippecanoe(Path(a.trees), out / "trees.pmtiles", "t", 11, 16,
               ["--drop-densest-as-needed", "--extend-zooms-if-still-dropping", "-r1"])

    # ---------------------------------------------------------------- 3D trees: trunk + stacked crown tiers
    # Each tree becomes a few floating extrusions: a thin trunk and 3–4 discs whose radius follows a crown shape
    #   0 round (most broadleaf trees) · 1 conical (conifers) · 2 columnar (fastigiate / "Säulen-" cultivars)
    TIERS = {0: [(0.00, 0.30, 0.80), (0.30, 0.75, 1.00), (0.75, 1.00, 0.60)],
             1: [(0.00, 0.30, 1.00), (0.30, 0.55, 0.72), (0.55, 0.80, 0.45), (0.80, 1.00, 0.20)],
             2: [(0.00, 0.20, 0.75), (0.20, 0.85, 1.00), (0.85, 1.00, 0.55)]}
    ang12 = np.linspace(0, 2 * np.pi, 13)
    ang8 = np.linspace(0, 2 * np.pi, 9)

    def ring(lon, lat, r, ang):
        dx = r * np.cos(ang) / (111320 * np.cos(np.radians(lat)))
        dy = r * np.sin(ang) / 110540
        return [[round(lon + a, 7), round(lat + b, 7)] for a, b in zip(dx, dy)]

    with tempfile.TemporaryDirectory() as td:
        seq = Path(td) / "c.geojsonl"
        n = 0
        with open(a.trees, encoding="utf-8") as fi, open(seq, "w", encoding="utf-8") as fo:
            for line in fi:
                f = json.loads(line)
                p = f["properties"]
                lon, lat = f["geometry"]["coordinates"]
                d = p["ce"]; R = d / 2; sh = p.get("sh", 0); g = p.get("g", 11)
                h = p.get("h") or max(3.0, min(30.0, 1.6 * d))           # height missing → rough estimate from crown
                if sh == 1:
                    base = max(1.0, 0.15 * h)
                elif sh == 2:
                    base = max(1.5, 0.2 * h)
                else:
                    base = max(1.5, min(h - 1.0, h - 0.9 * d))         # crown starts about one crown depth below the top
                c = max(0.5, h - base)
                common = {"g": g, "im": p["im"]}
                if "de" in p: common["de"] = p["de"]
                tr = max(0.15, min(0.5, 0.04 * d))
                fo.write(json.dumps({"type": "Feature", "properties": {**common, "k": 0, "b": 0, "h": round(base + 0.2 * c, 1)},
                                     "geometry": {"type": "Polygon", "coordinates": [ring(lon, lat, tr, ang8)]}},
                                    ensure_ascii=False, separators=(",", ":")) + "\n")
                for lo, hi, rf in TIERS[sh]:
                    fo.write(json.dumps({"type": "Feature",
                                         "properties": {**common, "k": 1, "b": round(base + lo * c, 1), "h": round(base + hi * c, 1)},
                                         "geometry": {"type": "Polygon", "coordinates": [ring(lon, lat, R * rf, ang12)]}},
                                        ensure_ascii=False, separators=(",", ":")) + "\n")
                n += 1
        print(f"3D trees: {n:,} trees → {seq.stat().st_size/1e6:.0f} MB of pieces", flush=True)
        tippecanoe(seq, out / "crowns.pmtiles", "c", 15, 16, ["--no-tiny-polygon-reduction", "--no-feature-limit",
                                                              "--no-tile-size-limit", "--no-simplification-of-shared-nodes"])

    # ---------------------------------------------------------------- buildings (3D)
    if Path(a.buildings).exists():
        import geopandas as gpd
        b = gpd.read_parquet(a.buildings)
        b = b[b.h.notna() & (b.h > 0)].copy()
        b["h"] = b.h.clip(2, 200).round(1)
        b = b.to_crs(4326)
        with tempfile.TemporaryDirectory() as td:
            seq = Path(td) / "b.geojsonl"
            with open(seq, "w") as fo:
                for g, h in zip(b.geometry.values, b.h.values):
                    fo.write(json.dumps({"type": "Feature", "properties": {"h": float(h)},
                                         "geometry": shapely.geometry.mapping(g)}, separators=(",", ":")) + "\n")
            tippecanoe(seq, out / "buildings.pmtiles", "b", 13, 16,
                       ["--drop-smallest-as-needed", "--coalesce-densest-as-needed", "--extend-zooms-if-still-dropping"])
        st = json.load(open(out / "stats.json", encoding="utf-8"))
        st["buildings"] = {"n": int(len(b)), "h_median": round(float(b.h.median()), 1)}
        json.dump(st, open(out / "stats.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    else:
        print("No LoD2 buildings — 3D view will show tree crowns only")


if __name__ == "__main__":
    main()
