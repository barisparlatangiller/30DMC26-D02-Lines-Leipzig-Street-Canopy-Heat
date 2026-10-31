# Trees Are Not Enough · Leipzig

**#30DayMapChallenge 2026 · Day 2 · Lines**

**Which Leipzig streets lack effective tree canopy, even where trees are planted? Where does low canopy meet summer heat?**

🌍 **Live map:** https://barisparlatangiller.github.io/30DMC26-D02-Lines-Leipzig-Street-Canopy-Heat/

Leipzig's tree register lists about 188,000 trees. A count does not tell you how much shade a street gets, though. A street with many young, small trees can have less canopy than a street with a few old trees with large crowns. This map cuts the whole street network into pieces of at most 100 m. Each piece is drawn as a line and coloured by its **estimated canopy cover**. Hover or click any line to see the trees behind that number.

## What the map shows

| | |
|---|---|
| **Lines** | 24,000+ street pieces (≤ 100 m), coloured by canopy cover, trees per 100 m, summer surface heat or greening priority |
| **Street detail** | trees within 15 m, trees/100 m, canopy (estimated and measured-only), young / small / large trees, species, median age and height, missing records, surface temperature |
| **Count is not cover** | automatically selected street pairs: many young, small-crowned trees vs. fewer large crowns |
| **Heat** | Landsat 8/9 summer land-surface temperature as a map layer and per street; canopy × heat matrix and priority classes |
| **Data gaps** | trees without crown size, height or planting year are kept and counted; streets where ≥ 25 % of trees lack a crown size are marked (dashed purple) |
| **3D** | tree crowns as floating cylinders (height and crown diameter from the register) plus LoD2 buildings |

## Method

1. **Streets.** The official Leipzig street network (`Strassen_Segmente`, EPSG:25833) is merged per segment and cut into equal pieces of at most 100 m.
2. **Corridor.** Around each piece a flat-ended buffer of 15 m on each side (30 m wide). A tree counts for a piece when its stem lies inside.
3. **Canopy.** Every tree gets a crown disc with diameter `kr_durchm`. Canopy cover = area(union of crown discs ∩ corridor) ÷ corridor area. Crowns of trees standing just outside the corridor count if they reach into it.
4. **Missing values.** Values outside plausible ranges (crown 0.5–30 m, height 1–45 m, planting year 1700–2026) are treated as missing. Missing crowns are estimated from the median of the same genus and height class, then the genus, then the city. Both the estimate and a measured-only lower bound are published.
5. **Classes.** Canopy: very low < 5 %, low 5–15 %, moderate 15–30 %, good ≥ 30 %. *"Trees, little canopy"* = canopy < 15 % with ≥ 6 trees per 100 m.
6. **Heat.** Landsat 8/9 Collection 2 Level-2 surface temperature (ST_B10), June–August 2022–2025, scenes with < 20 % cloud and ≥ 70 % of the city clear after QA masking. Each scene is converted into anomalies from its own city median, and the per-pixel median anomaly is added back to the median scene temperature. Values are sampled every 10 m along each street. Heat thirds are relative to all street pieces.
7. **Priority.** 1 = canopy < 15 % on the hottest third of streets; 2 = canopy < 15 %, middle third; 3 = canopy < 15 % on the cooler third, or canopy ≥ 15 % on the hottest third.

Everything is rebuilt from the official sources by the GitHub Action (`.github/workflows/build-and-deploy.yml`) on every push.

```bash
pip install -r requirements.txt            # + tippecanoe
python pipeline/fetch_inputs.py leipzig    # WFS: trees, streets, Ortsteile, boundary
python pipeline/build_heat.py              # Landsat LST → input/lst_summer.tif, docs/data/heat.png
python pipeline/build_streets.py           # street pieces, canopy, gaps, heat → docs/data
python pipeline/fetch_inputs.py lod2       # optional: LoD2 tiles (GeoSN)
python pipeline/lod2_to_footprints.py --lod2 input/lod2 --out input/leipzig_lod2.parquet
python pipeline/build_tiles.py             # trees / crowns / buildings PMTiles
```

### Open the results in QGIS

From the map's download links:

- `street_segments.gpkg.zip`: every street piece with all attributes
- `street_segments.csv.zip`: the same table without geometry
- `lst_summer_leipzig.tif`: surface temperature in °C (EPSG:25833, 30 m)

## Limits

- The Baumkataster covers trees managed by the City of Leipzig. Trees on private ground, in courtyards and in front gardens are not included, and the city states that recording is not yet complete. "No registered trees" is therefore not always "no trees".
- Crowns are modelled as circles. Real crowns are irregular, get pruned, and change with the seasons.
- Landsat measures the temperature of **surfaces** (roofs, asphalt, crowns) around 10:30 local time. That is not air temperature and not felt heat. The city's own climate model (Stadtklimaanalyse 2022) is only published as PDF maps and an online viewer, not as open data.

## Data & licences

- Baumkataster, Straßennetz, Ortsteile, Stadtgrenze © Stadt Leipzig, via the [Open-Data-Portal Leipzig](https://opendata.leipzig.de/dataset/baumkataster-stadt-leipzig1) and WFS `geodienste.leipzig.de/l3/OpenData/wfs`.
- Landsat 8/9 Collection 2 Level-2, courtesy of the U.S. Geological Survey (public domain), accessed via the [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/dataset/landsat-c2-l2).
- 3D-Stadtmodell LoD2 and DOP20 aerial photo © Staatsbetrieb Geobasisinformation und Vermessung Sachsen (GeoSN), [dl-de/by-2-0](https://www.govdata.de/dl-de/by-2-0).
- Basemap © CARTO, © OpenStreetMap contributors.

Code: MIT. Barış Parlatangiller, built with AI assistance (Claude, Anthropic).
