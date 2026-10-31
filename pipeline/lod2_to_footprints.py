"""
LoD2 (CityGML) → building footprints with real heights, one compact GeoParquet.

Reads every CityGML file (.gml / .xml, also inside .zip) in a folder, and for every bldg:Building and
bldg:BuildingPart writes its ground surface (footprint) and height:
  h  = bldg:measuredHeight, or (max z − min z) of its surfaces if that is missing
  hb = ground height (min z)
Used here for: 3D-Stadtmodell LoD2, Freistaat Sachsen (GeoSN), Leipzig tiles, EPSG:25833, dl-de/by-2-0.
  python pipeline/lod2_to_footprints.py --lod2 input/lod2 --out input/leipzig_lod2.parquet
"""
import argparse, io, zipfile
from pathlib import Path

import geopandas as gpd
from lxml import etree
from shapely.geometry import Polygon
from shapely.ops import unary_union

GML = "{http://www.opengis.net/gml}"
TAGS = ("Building", "BuildingPart")


def local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def ring(el):
    pl = el.find(f".//{GML}posList")
    if pl is not None and pl.text:
        v = [float(t) for t in pl.text.split()]
        dim = int(pl.get("srsDimension", 3))
        return [tuple(v[i:i + dim]) for i in range(0, len(v), dim)]
    return [tuple(float(t) for t in p.text.split()) for p in el.iter(f"{GML}pos")]


def own(el, name):
    """descendants with local name `name` that belong to this element, not to a nested BuildingPart"""
    out = []
    for d in el.iter():
        if local(d.tag) != name:
            continue
        p = d.getparent()
        while p is not None and p is not el:
            if local(p.tag) in TAGS:
                break
            p = p.getparent()
        if p is el:
            out.append(d)
    return out


def parse(stream, src):
    rows = []
    for _, el in etree.iterparse(stream, events=("end",), huge_tree=True):
        if local(el.tag) != "Building":
            continue
        for b in [el] + [d for d in el.iter() if local(d.tag) == "BuildingPart"]:
            polys, zs = [], []
            for gs in own(b, "GroundSurface"):
                for lr in gs.iter(f"{GML}LinearRing"):
                    pts = ring(lr)
                    if len(pts) >= 4:
                        polys.append(Polygon([(p[0], p[1]) for p in pts]))
            for s in own(b, "posList"):
                v = [float(t) for t in s.text.split()]
                zs += v[2::3]
            if not polys:
                continue
            fp = unary_union([p.buffer(0) for p in polys])
            mh = own(b, "measuredHeight")
            h = float(mh[0].text) if mh and mh[0].text else (max(zs) - min(zs) if zs else None)
            fn = own(b, "function")
            rows.append({"gml_id": b.get(f"{GML}id"), "part": local(b.tag) == "BuildingPart",
                         "function": fn[0].text if fn else None,
                         "h": round(h, 2) if h is not None else None,
                         "hb": round(min(zs), 2) if zs else None, "src": src, "geometry": fp})
        el.clear()
        while el.getprevious() is not None:
            del el.getparent()[0]
    return rows


def parse_cityjson(j, src):
    """CityJSON 1.x: GroundSurface polygons per Building / BuildingPart"""
    tr = j.get("transform", {"scale": [1, 1, 1], "translate": [0, 0, 0]})
    sc, tl = tr["scale"], tr["translate"]
    V = [(v[0] * sc[0] + tl[0], v[1] * sc[1] + tl[1], v[2] * sc[2] + tl[2]) for v in j["vertices"]]
    rows = []
    for oid, o in j.get("CityObjects", {}).items():
        if o.get("type") not in TAGS:
            continue
        polys, zs = [], []
        for g in o.get("geometry", []):
            sem = g.get("semantics") or {}
            types = [s.get("type") for s in sem.get("surfaces", [])]
            vals, bnd = sem.get("values"), g.get("boundaries", [])
            if g.get("type") in ("Solid",):
                pairs = [(srf, (vals[i][k] if vals else None)) for i, shell in enumerate(bnd) for k, srf in enumerate(shell)]
            elif g.get("type") in ("MultiSurface", "CompositeSurface"):
                pairs = [(srf, (vals[k] if vals else None)) for k, srf in enumerate(bnd)]
            else:
                continue
            for srf, v in pairs:
                pts = [V[i] for i in srf[0]]
                zs += [p[2] for p in pts]
                if v is not None and types[v] == "GroundSurface" and len(pts) >= 3:
                    polys.append(Polygon([(p[0], p[1]) for p in pts]))
        if not polys:
            continue
        at = o.get("attributes", {})
        h = at.get("measuredHeight") or (max(zs) - min(zs) if zs else None)
        rows.append({"gml_id": oid, "part": o["type"] == "BuildingPart", "function": at.get("function"),
                     "h": round(float(h), 2) if h is not None else None, "hb": round(min(zs), 2) if zs else None,
                     "src": src, "geometry": unary_union([p.buffer(0) for p in polys])})
    return rows


def read_any(name, data, src):
    """dispatch on file type; supports .gml/.xml, CityJSON .json, .gz and nested .zip"""
    import gzip, json
    n = name.lower()
    if n.endswith(".gz"):
        return read_any(n[:-3], gzip.decompress(data), src)
    if n.endswith(".zip"):
        rows = []
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for m in z.namelist():
                if not m.endswith("/"):
                    rows += read_any(m, z.read(m), m)
        return rows
    if n.endswith((".gml", ".xml")):
        return parse(io.BytesIO(data), src)
    if n.endswith(".json"):
        j = json.loads(data)
        return parse_cityjson(j, src) if j.get("type") == "CityJSON" else []
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lod2", required=True)
    ap.add_argument("--out", default="input/leipzig_lod2.parquet")
    args = ap.parse_args()
    rows, files = [], sorted(Path(args.lod2).rglob("*"))
    files = [f for f in files if f.is_file() and f.name.lower().endswith((".gml", ".xml", ".zip", ".json", ".gz"))]
    for i, f in enumerate(files, 1):
        if f.name.lower().endswith((".gml", ".xml")):
            with open(f, "rb") as s:
                rows += parse(s, f.name)
        else:
            rows += read_any(f.name, f.read_bytes(), f.name)
        if i % 20 == 0 or i == len(files):
            print(f"[{i}/{len(files)}] {f.name}: {len(rows):,} footprints so far", flush=True)
    if not rows:
        raise SystemExit("No building footprints found")
    g = gpd.GeoDataFrame(rows, geometry="geometry", crs=25833)
    g = g[g.geometry.notna() & ~g.geometry.is_empty]
    g = g.drop_duplicates("gml_id")
    g.to_parquet(args.out, compression="zstd")
    print(f"Wrote {len(g):,} footprints → {args.out} ({Path(args.out).stat().st_size/1e6:.1f} MB)")
    print(g.h.describe().round(1))


if __name__ == "__main__":
    main()
