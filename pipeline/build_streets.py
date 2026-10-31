"""
Day 2 · Lines · Leipzig — "Trees are not enough".

For every street of the official Leipzig street network, cut into pieces of at most 100 m:
  * trees whose stem stands within 15 m of the centre line (15 m corridor on both sides),
  * estimated canopy cover of that corridor = union of crown discs (diameter = kr_durchm) ∩ corridor,
  * the age / size mix of those trees (young, small-crowned, large-crowned),
  * a data-gap flag: trees without crown diameter, height or planting year are NOT dropped —
    their crowns are imputed (genus × height class median) and the share of such trees is reported,
  * optional: mean summer land-surface temperature along the street (from build_heat.py).

Outputs in --out (docs/data):
  segments.geojson   one line per ≤100 m piece, compact properties (see KEYS below)
  streets.json       one record per street name (for search, ranking and the story cards)
  stats.json         city-wide numbers for the side panel
  street_segments.gpkg.zip / street_segments.csv.zip   full table for QGIS / Excel
  trees.geojsonl     (temporary, used by build_tiles.py for the tree / crown vector tiles)

Run:  python pipeline/build_streets.py --trees input/Baumkataster_Stadt_Leipzig.gpkg \
          --streets input/Strassennetz.gpkg [--lst input/lst_summer.tif] [--ortsteile input/ortsteil.gpkg]
"""
import argparse, json, math, zipfile, io, tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import shapely
from shapely import STRtree
from shapely.ops import substring, linemerge

YEAR = 2026
CORRIDOR = 15.0        # m on each side of the centre line
MAX_LEN = 100.0        # m
YOUNG_SINCE = YEAR - 15  # planted 2011 or later
SMALL_CROWN, LARGE_CROWN = 5.0, 10.0
GAP_CROWN_SHARE = 0.25   # ≥25 % of the trees without a usable crown diameter → canopy figure unreliable

BEZIRK = {"M": "Mitte", "NO": "Nordost", "O": "Ost", "SO": "Südost", "S": "Süd", "SW": "Südwest",
          "W": "West", "AW": "Alt-West", "NW": "Nordwest", "N": "Nord"}

# canopy classes (estimated canopy cover of the 30 m street corridor)
CANOPY_BREAKS = [5, 15, 30]       # %  → 0: <5, 1: 5–15, 2: 15–30, 3: ≥30
CANOPY_NAMES = ["very low", "low", "moderate", "good"]


# ------------------------------------------------------------------ trees
def load_trees(path):
    t = gpd.read_file(path)
    t = t[t.geometry.notna()].to_crs(25833).reset_index(drop=True)
    num = lambda c: pd.to_numeric(t[c], errors="coerce")
    crown, h, year = num("kr_durchm"), num("baumhoehe"), num("pflanzjahr")
    # plausibility: values outside these ranges are treated as missing (and counted as gaps)
    t["crown_ok"] = crown.between(0.5, 30)
    t["h_ok"] = h.between(1, 45)
    t["year_ok"] = year.between(1700, YEAR)
    t["crown"] = crown.where(t.crown_ok)
    t["h"] = h.where(t.h_ok)
    t["year"] = year.where(t.year_ok)
    t["genus"] = t["gattung"].fillna("").str.strip()
    t["name_de"] = t["ga_lang_deutsch"].fillna("").str.strip()
    t["name_sci"] = t["ga_lang_wiss"].fillna("").str.strip()

    # impute missing crowns: median of same genus and height class, else genus, else city median
    hb = pd.cut(t.h, [0, 5, 10, 15, 20, 25, 100], labels=False)
    t["hb"] = hb
    known = t[t.crown_ok]
    med_gh = known.groupby(["genus", "hb"]).crown.median()
    med_g = known.groupby("genus").crown.median()
    med_h = known.groupby("hb").crown.median()
    city = float(known.crown.median())
    est = t.crown.copy()
    miss = est.isna()
    idx = pd.MultiIndex.from_arrays([t.genus[miss], t.hb[miss]])
    est[miss] = med_gh.reindex(idx).values
    m2 = est.isna(); est[m2] = med_g.reindex(t.genus[m2]).values
    m3 = est.isna(); est[m3] = med_h.reindex(t.hb[m3]).values
    est = est.fillna(city)
    t["crown_est"] = est.clip(1, 30)
    t["imputed"] = ~t.crown_ok
    t["gap"] = ~(t.crown_ok & t.h_ok & t.year_ok)
    print(f"Trees: {len(t):,} · crown missing/implausible {(~t.crown_ok).sum():,} · height {(~t.h_ok).sum():,}"
          f" · planting year {(~t.year_ok).sum():,} · any {t.gap.sum():,}")
    return t


# ------------------------------------------------------------------ streets → ≤100 m pieces
def split_streets(path):
    s = gpd.read_file(path).to_crs(25833)
    s = s[s.geometry.notna() & ~s.geometry.is_empty].reset_index(drop=True)
    rows = []
    for r in s.itertuples():
        g = r.geometry
        merged = linemerge(g) if g.geom_type == "MultiLineString" else g
        parts = list(merged.geoms) if merged.geom_type == "MultiLineString" else [merged]
        k = 0
        for p in parts:
            L = p.length
            if L < 1:
                continue
            n = max(1, math.ceil(L / MAX_LEN - 1e-9))
            step = L / n
            for i in range(n):
                piece = substring(p, i * step, (i + 1) * step)
                if piece.geom_type != "LineString" or piece.length < 0.5:
                    continue
                rows.append({"oid": r.objectid, "k": k, "street": r.str or "", "type": r.sstrgsname or "",
                             "from": r.von_str or "", "to": r.bis_str or "", "bez": BEZIRK.get(r.stadtbez, r.stadtbez or ""),
                             "road": r.klass_netz if isinstance(r.klass_netz, str) else "",
                             "geometry": piece})
                k += 1
    seg = gpd.GeoDataFrame(rows, crs=25833)
    seg["len"] = seg.length
    print(f"Street segments: {len(s):,} official segments → {len(seg):,} pieces ≤ {MAX_LEN:.0f} m "
          f"({seg.len.sum()/1000:,.0f} km)")
    return seg


# ------------------------------------------------------------------ canopy per piece
def canopy(seg, t):
    corr = shapely.buffer(seg.geometry.values, CORRIDOR, cap_style="flat")
    seg["corr_area"] = shapely.area(corr)

    # trees whose stem is inside the corridor
    pts = t.geometry.values
    ptree = STRtree(pts)
    ci, ti = ptree.query(corr, predicate="contains")
    pairs = pd.DataFrame({"s": ci, "t": ti})
    print(f"Tree–street pairs (stem within {CORRIDOR:.0f} m): {len(pairs):,}; "
          f"trees near any street: {pairs.t.nunique():,}")

    # crown discs
    rad_m = (t.crown.fillna(0) / 2).values
    rad_e = (t.crown_est / 2).values
    disc_e = shapely.buffer(pts, rad_e, quad_segs=6)
    disc_m = shapely.buffer(pts, np.maximum(rad_m, 0.01), quad_segs=6)
    has_m = t.crown_ok.values
    dtree = STRtree(disc_e)
    di, dj = dtree.query(corr, predicate="intersects")
    order = np.argsort(di, kind="stable")
    di, dj = di[order], dj[order]
    bounds = np.searchsorted(di, np.arange(len(seg) + 1))
    cov_e = np.zeros(len(seg)); cov_m = np.zeros(len(seg))
    for i in range(len(seg)):
        a, b = bounds[i], bounds[i + 1]
        if a == b:
            continue
        js = dj[a:b]
        u = shapely.union_all(disc_e[js])
        cov_e[i] = shapely.area(shapely.intersection(u, corr[i]))
        jm = js[has_m[js]]
        if len(jm):
            cov_m[i] = shapely.area(shapely.intersection(shapely.union_all(disc_m[jm]), corr[i]))
    seg["canopy"] = 100 * cov_e / seg.corr_area           # estimated (imputed crowns included)
    seg["canopy_m"] = 100 * cov_m / seg.corr_area         # measured crowns only (lower bound)

    # tree mix per piece
    tt = t.iloc[pairs.t.values].reset_index(drop=True)
    tt["s"] = pairs.s.values
    g = tt.groupby("s")
    agg = pd.DataFrame({
        "n": g.size(),
        "n_young": g.year.apply(lambda y: int((y >= YOUNG_SINCE).sum())),
        "n_old": g.year.apply(lambda y: int((y < 1970).sum())),
        "n_small": g.crown.apply(lambda c: int((c < SMALL_CROWN).sum())),
        "n_large": g.crown.apply(lambda c: int((c >= LARGE_CROWN).sum())),
        "miss_crown": g.crown_ok.apply(lambda v: int((~v).sum())),
        "miss_h": g.h_ok.apply(lambda v: int((~v).sum())),
        "miss_year": g.year_ok.apply(lambda v: int((~v).sum())),
        "n_gap": g.gap.sum().astype(int),
        "crown_mean": g.crown.mean(),
        "crown_est_mean": g.crown_est.mean(),
        "h_med": g.h.median(),
        "year_med": g.year.median(),
        "street_green": g.gebiet.apply(lambda v: int((v == "Straßengrün").sum())),
    })
    top = (tt[tt.name_de != ""].groupby(["s", "name_de"]).size().rename("c").reset_index()
           .sort_values(["s", "c"], ascending=[True, False]))
    top = top.groupby("s").head(3).groupby("s").apply(
        lambda d: "; ".join(f"{a} ({b})" for a, b in zip(d.name_de, d.c)), include_groups=False)
    agg["species"] = top
    seg = seg.join(agg)
    for c in ["n", "n_young", "n_old", "n_small", "n_large", "miss_crown", "miss_h", "miss_year", "n_gap", "street_green"]:
        seg[c] = seg[c].fillna(0).astype(int)
    seg["species"] = seg["species"].fillna("")
    seg["per100"] = 100 * seg.n / seg.len
    return seg, pairs


def classify(seg):
    cls = np.digitize(seg.canopy.values, CANOPY_BREAKS)
    seg["ccls"] = cls
    share_gap = np.where(seg.n > 0, seg.miss_crown / seg.n.clip(lower=1), 0)
    seg["gap_share"] = np.where(seg.n > 0, seg.n_gap / seg.n.clip(lower=1), 0)
    seg["dgap"] = (seg.n > 0) & (share_gap >= GAP_CROWN_SHARE)       # canopy figure unreliable
    seg["dgap_any"] = (seg.n > 0) & (seg.n_gap > 0)                   # at least one incomplete record
    # the headline category
    dens_hi = seg.per100 >= 6        # ≥ 6 trees per 100 m (≈ one row every 15 m on one side or denser)
    young_mix = (seg.n_young + seg.n_small) >= 0.5 * seg.n.clip(lower=1)
    cat = np.full(len(seg), "", dtype=object)
    cat[(seg.n == 0).values] = "no trees"
    m = (seg.n > 0).values
    cat[m & (cls < 2) & dens_hi.values] = "trees, little canopy"
    cat[m & (cls < 2) & ~dens_hi.values] = "few trees, little canopy"
    cat[m & (cls >= 2)] = "effective canopy"
    seg["cat"] = cat
    seg["young_mix"] = young_mix & (seg.n > 0)
    return seg


# ------------------------------------------------------------------ heat
def heat(seg, lst_path):
    import rasterio
    with rasterio.open(lst_path) as src:
        arr = src.read(1).astype("float32")
        nod = src.nodata
        if nod is not None:
            arr[arr == nod] = np.nan
        inv = ~src.transform
        vals = []
        for g in seg.geometry.values:
            n = max(2, int(g.length // 10) + 1)
            ds = np.linspace(0, g.length, n)
            p = shapely.line_interpolate_point(g, ds)
            xs, ys = shapely.get_x(p), shapely.get_y(p)
            cols, rows = inv * (xs, ys)
            cols = np.floor(cols).astype(int); rows = np.floor(rows).astype(int)
            ok = (rows >= 0) & (rows < arr.shape[0]) & (cols >= 0) & (cols < arr.shape[1])
            v = arr[rows[ok], cols[ok]] if ok.any() else np.array([np.nan])
            vals.append(np.nanmean(v) if np.isfinite(v).any() else np.nan)
    seg["lst"] = vals
    med = float(np.nanmedian(seg.lst))
    seg["lst_anom"] = seg.lst - med
    q = np.nanquantile(seg.lst, [1 / 3, 2 / 3])
    seg["hcls"] = np.where(seg.lst.isna(), -1, np.digitize(seg.lst, q))    # 0 cooler, 1 middle, 2 hottest third
    return seg, {"median": round(med, 2), "terciles": [round(float(x), 2) for x in q],
                 "p05": round(float(np.nanquantile(seg.lst, .05)), 2), "p95": round(float(np.nanquantile(seg.lst, .95)), 2)}


def priority(seg):
    """1 = act first: little canopy (<20 %) on the hottest third of streets; 2 = little canopy, middle third;
    3 = little canopy, cooler third or effective canopy on the hottest third; 0 = rest / no heat data."""
    lo = seg.ccls < 2
    hc = seg.get("hcls", pd.Series(-1, index=seg.index))
    p = np.zeros(len(seg), dtype=int)
    p[(lo & (hc == 2)).values] = 1
    p[(lo & (hc == 1)).values] = 2
    p[((lo & (hc == 0)) | (~lo & (hc == 2))).values] = 3
    seg["prio"] = p
    return seg


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trees", default="input/Baumkataster_Stadt_Leipzig.gpkg")
    ap.add_argument("--streets", default="input/Strassennetz.gpkg")
    ap.add_argument("--lst", default="input/lst_summer.tif")
    ap.add_argument("--ortsteile", default="input/ortsteil.gpkg")
    ap.add_argument("--out", default="docs/data")
    ap.add_argument("--work", default="input")
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

    for attr in ("trees", "streets", "ortsteile"):
        v = Path(getattr(a, attr))
        if not v.exists() and v.with_suffix(".geojson").exists():
            setattr(a, attr, str(v.with_suffix(".geojson")))
    t = load_trees(a.trees)
    seg = split_streets(a.streets)
    seg, pairs = canopy(seg, t)
    seg = classify(seg)
    hstats = None
    if Path(a.lst).exists():
        seg, hstats = heat(seg, a.lst)
        print(f"Heat: median LST along streets {hstats['median']} °C, terciles {hstats['terciles']}")
    else:
        print("No LST raster found — heat layer skipped")
        seg["lst"] = np.nan; seg["lst_anom"] = np.nan; seg["hcls"] = -1
    seg = priority(seg)

    # district (Ortsteil) names
    if Path(a.ortsteile).exists():
        ot = gpd.read_file(a.ortsteile).to_crs(25833)
        name_col = next(c for c in ("name", "Name", "ot_name", "bezeichnung", "ortsteil") if c in ot.columns) \
            if any(c in ot.columns for c in ("name", "Name", "ot_name", "bezeichnung", "ortsteil")) else None
        if name_col:
            mid = gpd.GeoDataFrame(geometry=shapely.line_interpolate_point(seg.geometry.values, 0.5, normalized=True), crs=25833)
            j = gpd.sjoin(mid, ot[[name_col, "geometry"]], how="left", predicate="within")
            j = j[~j.index.duplicated()]
            seg["ot"] = j[name_col].fillna("").values
    if "ot" not in seg:
        seg["ot"] = ""

    seg = seg.reset_index(drop=True)
    seg["id"] = seg.index

    # ---------------------------------------------------------------- street-level summary
    seg["_cov"] = seg.canopy * seg.corr_area / 100
    seg["_covm"] = seg.canopy_m * seg.corr_area / 100
    seg["_lstw"] = seg.lst * seg.len
    g = seg.groupby("street", sort=False)
    st = pd.DataFrame({
        "len": g.len.sum(), "n": g.n.sum(), "young": g.n_young.sum(), "small": g.n_small.sum(), "large": g.n_large.sum(),
        "gap": g.n_gap.sum(), "miss_crown": g.miss_crown.sum(),
        "canopy": 100 * g._cov.sum() / g.corr_area.sum(), "canopy_m": 100 * g._covm.sum() / g.corr_area.sum(),
        "lst": g._lstw.sum(min_count=1) / g.apply(lambda d: d.len[d.lst.notna()].sum(), include_groups=False).replace(0, np.nan),
        "pieces": g.size(), "p1": g.prio.apply(lambda p: int((p == 1).sum())),
        "bez": g.bez.agg(lambda v: v.mode().iat[0] if len(v.mode()) else ""),
        "type": g.type.agg(lambda v: v.mode().iat[0] if len(v.mode()) else ""),
        "ot": g.ot.agg(lambda v: ", ".join(sorted(set(x for x in v if x)))[:80]),
    })
    st = st[st.index != ""]
    st["per100"] = 100 * st.n / st.len
    bb = seg.to_crs(4326).groupby("street").geometry.agg(lambda gs: shapely.total_bounds(gs.values).round(5).tolist())
    st["bbox"] = bb

    # contrast story: many young/small trees but little canopy  vs  fewer trees with big crowns
    ROADS = ["Ortsstraße", "Bundesstraße", "Staatsstraße", "Kreisstraße", "Gemeindeverbindungsstraße"]
    real = st.type.isin(ROADS) & ~st.index.str.match(r"^Weg \d")
    cand = st[real & (st.len >= 300) & (st.miss_crown <= 0.1 * st.n.clip(lower=1))]
    A = cand[(cand.per100 >= 8) & ((cand.young + cand.small) >= 0.6 * cand.n)].sort_values("canopy").head(12)
    B = cand[(cand.large >= 0.5 * cand.n.clip(lower=1)) & (cand.n > 0)]
    pairs_story = []
    for name, r in A.iterrows():
        bb_ = B[(B.per100 < r.per100 * 0.75) & (B.canopy > r.canopy * 1.8)]
        if len(bb_):
            # prefer the same district
            same = bb_[bb_.bez == r.bez]
            pick = (same if len(same) else bb_).sort_values("canopy", ascending=False).index[0]
            pairs_story.append({"a": name, "b": pick})
        if len(pairs_story) >= 4:
            break

    # ---------------------------------------------------------------- write web layers
    seg4 = seg.to_crs(4326)
    feats = []
    r1 = lambda x: None if pd.isna(x) else round(float(x), 1)
    for r in seg4.itertuples():
        coords = np.round(np.asarray(r.geometry.coords)[:, :2], 6).tolist()
        p = {"i": int(r.id), "s": r.street, "c": round(float(r.canopy), 1), "cm": round(float(r.canopy_m), 1),
             "k": int(r.ccls), "n": int(r.n), "d": round(float(r.per100), 1), "L": round(float(r.len)),
             "y": int(r.n_young), "sm": int(r.n_small), "lg": int(r.n_large), "o": int(r.n_old),
             "mc": int(r.miss_crown), "mh": int(r.miss_h), "my": int(r.miss_year), "g": int(r.dgap),
             "ga": int(r.dgap_any), "cat": r.cat, "p": int(r.prio), "hk": int(r.hcls),
             "t": r.type, "b": r.bez, "ot": r.ot}
        if not pd.isna(r.lst):
            p["h"] = r1(r.lst); p["ha"] = r1(r.lst_anom)
        if not pd.isna(r.crown_mean): p["cr"] = r1(r.crown_mean)
        if not pd.isna(r.h_med): p["hm"] = r1(r.h_med)
        if not pd.isna(r.year_med): p["ym"] = int(r.year_med)
        if r.species: p["sp"] = r.species
        feats.append({"type": "Feature", "properties": p, "geometry": {"type": "LineString", "coordinates": coords}})
    # from/to street names (itertuples renames 'from'); add separately
    for f, a_, b_ in zip(feats, seg["from"].values, seg["to"].values):
        f["properties"]["f"] = a_; f["properties"]["to"] = b_
    with open(out / "segments.geojson", "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": feats}, fh, ensure_ascii=False, separators=(",", ":"))

    rec = {}
    for name, r in st.iterrows():
        rec[name] = {"L": round(float(r.len)), "n": int(r.n), "d": round(float(r.per100), 1), "c": round(float(r.canopy), 1),
                     "cm": round(float(r.canopy_m), 1), "y": int(r.young), "sm": int(r.small), "lg": int(r.large),
                     "gap": int(r.gap), "mc": int(r.miss_crown), "h": r1(r.lst), "p1": int(r.p1), "k": int(r.pieces),
                     "b": r.bez, "ot": r.ot, "t": r.type, "bb": r.bbox}
    json.dump(rec, open(out / "streets.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

    # ---------------------------------------------------------------- stats
    L = seg.len
    def share(mask): return round(float(L[mask].sum() / L.sum() * 100), 1)
    stats = {
        "trees": {"total": int(len(t)), "near_streets": int(pairs.t.nunique()),
                  "miss_crown": int((~t.crown_ok).sum()), "miss_h": int((~t.h_ok).sum()),
                  "miss_year": int((~t.year_ok).sum()), "any_gap": int(t.gap.sum()),
                  "crown_median": round(float(t.crown.median()), 1)},
        "streets": {"pieces": int(len(seg)), "km": round(float(L.sum() / 1000), 1), "names": int(len(st)),
                    "corridor_m": CORRIDOR, "max_len_m": MAX_LEN,
                    "canopy_city": round(float(seg._cov.sum() / seg.corr_area.sum() * 100), 1),
                    "canopy_city_measured": round(float(seg._covm.sum() / seg.corr_area.sum() * 100), 1),
                    "by_class_km": [round(float(L[seg.ccls == k].sum() / 1000), 1) for k in range(4)],
                    "by_class_share": [share(seg.ccls == k) for k in range(4)],
                    "cat_share": {c: share(seg.cat == c) for c in ["no trees", "few trees, little canopy", "trees, little canopy", "effective canopy"]},
                    "cat_km": {c: round(float(L[seg.cat == c].sum() / 1000), 1) for c in ["no trees", "few trees, little canopy", "trees, little canopy", "effective canopy"]},
                    "data_gap_km": round(float(L[seg.dgap].sum() / 1000), 1), "data_gap_share": share(seg.dgap),
                    "data_gap_any_km": round(float(L[seg.dgap_any].sum() / 1000), 1),
                    "canopy_breaks": CANOPY_BREAKS, "canopy_names": CANOPY_NAMES},
        "heat": hstats,
        "priority_km": [round(float(L[seg.prio == k].sum() / 1000), 1) for k in range(4)],
        "story": pairs_story,
        "matrix": None,
    }
    if hstats:
        stats["matrix"] = [[round(float(L[(seg.ccls == k) & (seg.hcls == h)].sum() / 1000), 1) for h in range(3)] for k in range(4)]
    # rankings
    big = st[real & (st.len >= 400)]
    stats["rank_low_canopy_hot"] = (big[big.lst.notna()].assign(score=lambda d: d.lst - d.canopy / 5)
                                    .sort_values("score", ascending=False).head(10).index.tolist()) if hstats else []
    stats["rank_trees_not_enough"] = (big[(big.per100 >= 6) & (big.miss_crown <= 0.25 * big.n)]
                                      .sort_values("canopy").head(10).index.tolist())
    stats["rank_best"] = big.sort_values("canopy", ascending=False).head(10).index.tolist()
    json.dump(stats, open(out / "stats.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ---------------------------------------------------------------- downloads for QGIS / Excel
    cols = {"id": "segment_id", "street": "street", "from": "from_street", "to": "to_street", "type": "road_type",
            "bez": "stadtbezirk", "ot": "ortsteil", "len": "length_m", "n": "trees_15m", "per100": "trees_per_100m",
            "canopy": "canopy_pct_est", "canopy_m": "canopy_pct_measured_only", "ccls": "canopy_class",
            "cat": "category", "n_young": "trees_planted_since_2011", "n_small": "trees_crown_lt5m",
            "n_large": "trees_crown_ge10m", "n_old": "trees_planted_before_1970", "crown_mean": "crown_mean_m",
            "h_med": "height_median_m", "year_med": "planting_year_median", "miss_crown": "missing_crown",
            "miss_h": "missing_height", "miss_year": "missing_planting_year", "dgap": "data_gap_canopy",
            "gap_share": "share_incomplete_records", "species": "main_species",
            "lst": "lst_summer_c", "lst_anom": "lst_anomaly_c", "hcls": "heat_third", "prio": "greening_priority"}
    tab = seg[list(cols) + ["geometry"]].rename(columns=cols)
    tab["canopy_class"] = tab.canopy_class.map(dict(enumerate(CANOPY_NAMES)))
    for c in ["length_m", "trees_per_100m", "canopy_pct_est", "canopy_pct_measured_only", "crown_mean_m",
              "share_incomplete_records", "lst_summer_c", "lst_anomaly_c"]:
        tab[c] = tab[c].astype(float).round(2)
    with tempfile.TemporaryDirectory() as td:
        gp = Path(td) / "leipzig_street_canopy_segments.gpkg"
        tab.to_file(gp, layer="segments", driver="GPKG")
        with zipfile.ZipFile(out / "street_segments.gpkg.zip", "w", zipfile.ZIP_DEFLATED) as z:
            z.write(gp, gp.name)
    with zipfile.ZipFile(out / "street_segments.csv.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("leipzig_street_canopy_segments.csv", pd.DataFrame(tab.drop(columns="geometry")).to_csv(index=False))

    # ---------------------------------------------------------------- trees for the vector tiles
    t4 = t.to_crs(4326)
    near = np.zeros(len(t), dtype=bool); near[pairs.t.unique()] = True
    with open(Path(a.work) / "trees.geojsonl", "w", encoding="utf-8") as fh:
        for i, r in enumerate(t4.itertuples()):
            p = {"ce": round(float(r.crown_est), 1), "im": int(r.imputed), "st": int(near[i])}
            if r.crown_ok: p["cr"] = round(float(r.crown), 1)
            if r.h_ok: p["h"] = round(float(r.h), 1)
            if r.year_ok: p["y"] = int(r.year)
            if r.name_de: p["de"] = r.name_de
            if r.name_sci: p["sc"] = r.name_sci
            if r.strasse: p["s"] = r.strasse
            p["gp"] = int(r.gap)
            fh.write(json.dumps({"type": "Feature", "properties": p,
                                 "geometry": {"type": "Point", "coordinates": [round(r.geometry.x, 6), round(r.geometry.y, 6)]}},
                                ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({k: stats[k] for k in ("trees", "streets", "heat", "priority_km", "story")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
