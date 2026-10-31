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

    # ---------------------------------------------------------------- crowns (3D)
    to_m = Transformer.from_crs(4326, 25833, always_xy=True)
    to_g = Transformer.from_crs(25833, 4326, always_xy=True)
    with tempfile.TemporaryDirectory() as td:
        seq = Path(td) / "c.geojsonl"
        with open(a.trees, encoding="utf-8") as fi, open(seq, "w", encoding="utf-8") as fo:
            for line in fi:
                f = json.loads(line)
                p = f["properties"]
                x, y = to_m.transform(*f["geometry"]["coordinates"])
                d = p["ce"]
                h = p.get("h") or max(3.0, min(30.0, 1.6 * d))           # height missing → rough estimate from crown
                base = round(max(1.5, min(h - 1.0, h - 0.9 * d)), 1)      # crown starts roughly one crown depth below the top
                ring = shapely.buffer(shapely.Point(x, y), d / 2, quad_segs=4).exterior.coords
                coords = [[round(c, 6) for c in to_g.transform(cx, cy)] for cx, cy in ring]
                q = {"h": round(h, 1), "b": base, "im": p["im"], "gp": p["gp"]}
                if "y" in p: q["y"] = p["y"]
                fo.write(json.dumps({"type": "Feature", "properties": q,
                                     "geometry": {"type": "Polygon", "coordinates": [coords]}}, separators=(",", ":")) + "\n")
        tippecanoe(seq, out / "crowns.pmtiles", "c", 14, 16, ["--no-tiny-polygon-reduction", "--drop-densest-as-needed"])

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
