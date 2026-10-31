"""
Download every input from the official sources into ./input (used by the GitHub Action).

  python pipeline/fetch_inputs.py leipzig   # trees, street network, Ortsteile, city boundary (WFS)
  python pipeline/fetch_inputs.py lod2      # 3D building models LoD2, Saxony (GeoSN), Leipzig tiles

Sources
  Baumkataster, Stadt Leipzig   WFS https://geodienste.leipzig.de/l3/OpenData/wfs  layer OpenData:Baeume
  Straßennetz, Stadt Leipzig    same WFS, layer OpenData:Strassen_Segmente
  Ortsteile / Stadtgrenze       same WFS, layers OpenData:ortsteil, OpenData:stadtgrenze
  LoD2 Sachsen (CityGML)        https://www.geodaten.sachsen.de/batch-download-4719.html  © GeoSN, dl-de/by-2-0
"""
import re, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

IN = Path("input")
WFS = "https://geodienste.leipzig.de/l3/OpenData/wfs"
LAYERS = {"Baeume": "Baumkataster_Stadt_Leipzig.gpkg", "Strassen_Segmente": "Strassennetz.gpkg",
          "ortsteil": "ortsteil.gpkg", "stadtgrenze": "stadtgrenze.gpkg"}
S = requests.Session()
S.headers["User-Agent"] = "30DayMapChallenge-research (github.com/barisparlatangiller)"

# 2 km tiles of the LoD2 batch download for "Kreisfreie Stadt Leipzig" (lower-left corner, km, UTM 33)
TILES = """33306_5678 33306_5680 33306_5682 33306_5684 33306_5686 33306_5688 33306_5690 33306_5692 33306_5694
33308_5678 33308_5680 33308_5682 33308_5684 33308_5686 33308_5688 33308_5690 33308_5692 33308_5694 33308_5696 33308_5698
33310_5678 33310_5680 33310_5682 33310_5684 33310_5686 33310_5688 33310_5690 33310_5692 33310_5694 33310_5696 33310_5698 33310_5700
33312_5680 33312_5682 33312_5684 33312_5686 33312_5688 33312_5690 33312_5692 33312_5694 33312_5696 33312_5698 33312_5700
33314_5682 33314_5684 33314_5686 33314_5688 33314_5690 33314_5692 33314_5694 33314_5696 33314_5698 33314_5700
33316_5682 33316_5684 33316_5686 33316_5688 33316_5690 33316_5692 33316_5694 33316_5696 33316_5698 33316_5700
33318_5682 33318_5684 33318_5686 33318_5688 33318_5690 33318_5692 33318_5694 33318_5696 33318_5698 33318_5700
33320_5682 33320_5684 33320_5686 33320_5688 33320_5690 33320_5692 33320_5694 33320_5696 33320_5698 33320_5700 33320_5702
33322_5680 33322_5682 33322_5684 33322_5686 33322_5688 33322_5690 33322_5692 33322_5694 33322_5696 33322_5698 33322_5700 33322_5702
33324_5680 33324_5682 33324_5684 33324_5686 33324_5688 33324_5690 33324_5692 33324_5694 33324_5696 33324_5698 33324_5700 33324_5702
33326_5684 33326_5686 33326_5688 33326_5690 33326_5692 33328_5686 33328_5688 33328_5690""".split()
BATCH_PAGE = "https://www.geodaten.sachsen.de/batch-download-4719.html"
FALLBACK_SHARE = "https://geocloud.landesvermessung.sachsen.de/public.php/dav/files/AyJqXpJAZJXomCb/"


def get(url, **kw):
    for i in range(5):
        try:
            r = S.get(url, timeout=900, **kw)
            r.raise_for_status()
            return r
        except Exception as e:
            print(f"  retry {i+1}: {e}", flush=True)
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"failed: {url}")


def leipzig():
    IN.mkdir(exist_ok=True)
    for layer, name in LAYERS.items():
        p = {"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature", "TYPENAMES": f"OpenData:{layer}",
             "OUTPUTFORMAT": "gpkg"}
        r = get(WFS, params=p)
        if not r.content.startswith(b"SQLite format 3"):
            # fall back to GeoJSON
            p["OUTPUTFORMAT"] = "application/json"
            r = get(WFS, params=p)
            name = name.replace(".gpkg", ".geojson")
        (IN / name).write_bytes(r.content)
        print(f"{layer}: {len(r.content)/1e6:.1f} MB → input/{name}", flush=True)


def lod2():
    out = IN / "lod2"; out.mkdir(parents=True, exist_ok=True)
    base = FALLBACK_SHARE
    try:
        html = get(BATCH_PAGE).text
        m = re.findall(r'(https://geocloud\.landesvermessung\.sachsen\.de/public\.php/dav/files/[A-Za-z0-9]+/)lod2_\d+_\d+_2_sn_citygml\.zip', html)
        if m:
            base = m[0]
    except Exception as e:
        print("batch page not readable, using the stored share link:", e)
    urls = [f"{base}lod2_{t}_2_sn_citygml.zip" for t in TILES]
    print(f"LoD2 Sachsen: {len(urls)} tiles from {base}", flush=True)

    def dl(u):
        p = out / u.rsplit("/", 1)[-1]
        if p.exists() and p.stat().st_size > 0:
            return 0
        try:
            with S.get(u, stream=True, timeout=900) as r:
                r.raise_for_status()
                with open(p, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            return p.stat().st_size
        except Exception as e:
            print("  failed", u, e, flush=True)
            if p.exists():
                p.unlink()
            return 0

    total = 0
    with ThreadPoolExecutor(6) as ex:
        for i, n in enumerate(ex.map(dl, urls), 1):
            total += n
            if i % 20 == 0 or i == len(urls):
                print(f"  {i}/{len(urls)}, {total/1e9:.2f} GB new", flush=True)
    have = len(list(out.glob("*.zip")))
    print(f"LoD2 tiles on disk: {have}/{len(urls)}")
    if have == 0:
        raise SystemExit("no LoD2 tiles")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    for name, fn in (("leipzig", leipzig), ("lod2", lod2)):
        if what in (name, "all"):
            fn()
