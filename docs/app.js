/* Trees Are Not Enough · Leipzig — #30DayMapChallenge 2026 · Day 2 · Lines */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const fmt = (v, d = 0) => v == null || Number.isNaN(v) ? "–" : Number(v).toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d });
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ------------------------------------------------------------------ palette
  const CAN = ["#c4512c", "#eaa14b", "#8dbd66", "#23704a"];             // very low · low · moderate · good
  const CAN_NAMES = ["very low", "low", "moderate", "good"];
  const CAN_RANGE = ["< 5 %", "5–15 %", "15–30 %", "≥ 30 %"];
  const NOTREE = "#b8b1a4", GAP = "#7a3fa6";
  const PRIO = { 0: "#d8d2c6", 1: "#9e0f1f", 2: "#e5603e", 3: "#f6b78c" };
  const PRIO_NAMES = { 1: "Act first", 2: "High", 3: "Watch", 0: "Lower" };
  const PRIO_TXT = {
    1: "Little canopy (< 15 %) on one of Leipzig's hottest streets",
    2: "Little canopy (< 15 %), middle third for heat",
    3: "Little canopy on a cooler street, or good canopy on a hot one",
    0: "Canopy ≥ 15 % and not in the hottest third"
  };
  const TREES_STOPS = [[0, "#efeadf"], [3, "#cfe3b8"], [8, "#83c07c"], [15, "#2f8b57"], [25, "#0d4d2e"]];
  // species groups (same order as pipeline/build_streets.py GENERA + conifers + other)
  const SPC = ["#e07b39", "#d2b737", "#86b04f", "#2e6b3f", "#d8789b", "#4d8fc9", "#3f9384", "#a98bd1", "#b5523b", "#93aaa3", "#1f4a3a", "#a7a28c"];
  const SPN = [["Maple", "Ahorn"], ["Lime / linden", "Linde"], ["Ash", "Esche"], ["Oak", "Eiche"], ["Cherry & plum", "Kirsche"],
               ["Hornbeam", "Hainbuche"], ["Plane", "Platane"], ["Black locust", "Robinie"], ["Horse chestnut", "Rosskastanie"],
               ["Poplar", "Pappel"], ["Conifers", "Nadelbäume"], ["Other species", "andere"]];
  const spColor = () => ["match", ["get", "g"], ...SPC.slice(0, 11).flatMap((c, i) => [i, c]), SPC[11]];
  const HEAT_NAMES = ["cooler third", "middle third", "hottest third"];
  const BIV = [ // rows = canopy class, cols = heat third
    ["#e8a37f", "#d8643f", "#a3151f"],
    ["#efc79c", "#e59a62", "#c85a33"],
    ["#c7dcae", "#b9c88d", "#a6a25b"],
    ["#86b98b", "#5a9a68", "#3e7a47"]];

  const state = { mode: "canopy", view: "lines", base: "map", heat: false, gaps: true, notree: true, opac: 0.95,
                  cell: null, onlyGaps: false, street: null, rank: "rank_trees_not_enough", genus: null };
  let map, SEG, STREETS, STATS, HEAT = null, hoverId = null, popup;
  const byStreet = new Map();

  // ------------------------------------------------------------------ load
  const j = (u) => fetch(u).then((r) => { if (!r.ok) throw new Error(u + " " + r.status); return r.json(); });
  Promise.all([j("data/segments.geojson"), j("data/streets.json"), j("data/stats.json"),
               j("data/heat.json").catch(() => null)]).then(([seg, streets, stats, heat]) => {
    SEG = seg; STREETS = streets; STATS = stats; HEAT = heat && heat.corners ? heat : null;
    SEG.features.forEach((f) => { const s = f.properties.s; if (!byStreet.has(s)) byStreet.set(s, []); byStreet.get(s).push(f); });
    initMap();
    initPanel();
  }).catch((e) => { $("#loading").textContent = "Could not load the data: " + e.message; });

  // ------------------------------------------------------------------ expressions
  function heatColor(prop = "h") {
    if (!HEAT) return "#bbb";
    const { lo, hi, stops } = HEAT.scale;
    const e = ["interpolate", ["linear"], ["get", prop]];
    stops.forEach(([t, c]) => e.push(lo + t * (hi - lo), c));
    return ["case", ["has", prop], e, "#cfc9bd"];
  }
  function lineColor() {
    if (state.cell) {
      const [k, h] = state.cell;
      return ["case", ["all", ["==", ["get", "k"], k], ["==", ["get", "hk"], h]], BIV[k][h], "rgba(160,150,135,0.25)"];
    }
    switch (state.mode) {
      case "trees": { const e = ["interpolate", ["linear"], ["get", "d"]]; TREES_STOPS.forEach(([v, c]) => e.push(v, c)); return e; }
      case "heat": return heatColor();
      case "prio": return ["match", ["get", "p"], 1, PRIO[1], 2, PRIO[2], 3, PRIO[3], PRIO[0]];
      default: return ["step", ["get", "c"], CAN[0], 5, CAN[1], 15, CAN[2], 30, CAN[3]];
    }
  }
  const W = (base, add = 0) => ["interpolate", ["exponential", 1.6], ["zoom"], 10, base * 0.6 + add * 0.5, 13, base * 1.4 + add, 16, base * 4.5 + add, 18, base * 10 + add];
  function segFilter() {
    const f = ["all"];
    const allStreets = state.mode === "heat" || state.mode === "prio" || state.cell;
    if (!allStreets) f.push([">", ["get", "n"], 0]);
    if (state.onlyGaps) f.push(["==", ["get", "g"], 1]);
    return f;
  }

  // ------------------------------------------------------------------ map
  function initMap() {
    const protocol = new pmtiles.Protocol();
    maplibregl.addProtocol("pmtiles", protocol.tile);
    const dark = matchMedia("(prefers-color-scheme: dark)").matches;
    map = new maplibregl.Map({
      container: "map", hash: true, maxPitch: 70, attributionControl: { compact: true },
      center: [12.375, 51.337], zoom: 12.3, minZoom: 9.5, maxZoom: 19,
      style: `https://basemaps.cartocdn.com/gl/${dark ? "dark-matter" : "positron"}-gl-style/style.json`
    });
    window._map = map;
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "bottom-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
    popup = new maplibregl.Popup({ closeButton: true, maxWidth: "370px", offset: 10 });

    let ready = false;
    const setup = () => {
      if (ready || !map.isStyleLoaded()) return;
      ready = true;
      const baseSymbols = map.getStyle().layers.filter((l) => l.type === "symbol").map((l) => l.id);
      map.addSource("sat", { type: "raster", tileSize: 256, maxzoom: 20, attribution: "DOP20 © GeoSN, dl-de/by-2-0",
        tiles: ["https://geodienste.sachsen.de/wms_geosn_dop-rgb/guest?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS=sn_dop_020&STYLES=&CRS=EPSG:3857&BBOX={bbox-epsg-3857}&WIDTH=256&HEIGHT=256&FORMAT=image/jpeg"] });
      map.addLayer({ id: "sat", type: "raster", source: "sat", layout: { visibility: "none" }, paint: { "raster-fade-duration": 150 } });
      if (HEAT) {
        map.addSource("heat", { type: "image", url: "data/heat.png", coordinates: HEAT.corners });
        map.addLayer({ id: "heat", type: "raster", source: "heat", paint: { "raster-opacity": 0, "raster-resampling": "linear", "raster-fade-duration": 0 } });
      }
      map.addSource("seg", { type: "geojson", data: SEG, promoteId: "i" });
      map.addSource("trees", { type: "vector", url: "pmtiles://" + new URL("data/trees.pmtiles", location.href).href });
      map.addSource("crowns", { type: "vector", url: "pmtiles://" + new URL("data/crowns.pmtiles", location.href).href });
      map.addLayer({ id: "seg-none", type: "line", source: "seg", filter: ["==", ["get", "n"], 0],
        layout: { "line-cap": "round" }, paint: { "line-color": NOTREE, "line-width": W(0.55), "line-opacity": 0.75 } });
      map.addLayer({ id: "seg-case", type: "line", source: "seg", layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": dark ? "#111" : "#fff", "line-width": W(1.2, 1.4), "line-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 1, 0] } });
      map.addLayer({ id: "seg", type: "line", source: "seg", layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": lineColor(), "line-width": W(1.2), "line-opacity": state.opac } });
      map.addLayer({ id: "seg-gap", type: "line", source: "seg", filter: ["==", ["get", "g"], 1],
        paint: { "line-color": GAP, "line-width": W(1.2, 1.5), "line-dasharray": [1.2, 1.1], "line-opacity": 0.95 } });
      map.addLayer({ id: "seg-hl", type: "line", source: "seg", filter: ["in", ["get", "s"], ["literal", []]],
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": dark ? "#fff" : "#1f1d1a", "line-width": W(1.2, 5), "line-opacity": 0.9, "line-gap-width": 0 } });
      map.moveLayer("seg-hl", "seg");
      // trees: circle sized to crown radius in metres
      const mpp = (z) => 156543.03 * Math.cos(51.34 * Math.PI / 180) / Math.pow(2, z);
      map.addLayer({ id: "trees", type: "circle", source: "trees", "source-layer": "t", minzoom: 11,
        layout: { visibility: "none" },
        paint: {
          "circle-radius": ["interpolate", ["exponential", 2], ["zoom"],
            11, ["max", 1, ["/", ["/", ["get", "ce"], 2], mpp(11)]], 19, ["/", ["/", ["get", "ce"], 2], mpp(19)]],
          "circle-color": spColor(),
          "circle-opacity": ["case", ["==", ["get", "im"], 1], 0.35, 0.7], "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 13, 0, 16, 0.8],
          "circle-stroke-color": ["case", ["==", ["get", "gp"], 1], GAP, "rgba(20,60,30,0.6)"] } });
      map.addLayer({ id: "crowns", type: "fill-extrusion", source: "crowns", "source-layer": "c", minzoom: 15,
        layout: { visibility: "none" },
        paint: { "fill-extrusion-color": ["case", ["==", ["get", "k"], 0], "#6e5643", spColor()],
                 "fill-extrusion-height": ["get", "h"], "fill-extrusion-base": ["get", "b"], "fill-extrusion-opacity": 0.93,
                 "fill-extrusion-vertical-gradient": true } });
      if (STATS.buildings) {
        map.addSource("bld", { type: "vector", url: "pmtiles://" + new URL("data/buildings.pmtiles", location.href).href });
        map.addLayer({ id: "bld", type: "fill-extrusion", source: "bld", "source-layer": "b", minzoom: 13,
          layout: { visibility: "none" },
          paint: { "fill-extrusion-color": dark ? "#4a4741" : "#ddd6ca", "fill-extrusion-height": ["get", "h"],
                   "fill-extrusion-base": 0, "fill-extrusion-opacity": 0.78 } }, "crowns");
      }
      baseSymbols.forEach((id) => map.moveLayer(id));      // street names stay readable above the lines
      bindMap();
      render();
      $("#loading").classList.add("done");
      try { if (!localStorage.getItem("d2-intro")) $("#intro").hidden = false; } catch (e) { $("#intro").hidden = false; }
      const q = new URLSearchParams(location.search).get("street");
      if (q && STREETS[q]) showStreet(q);
    };
    map.on("load", setup);
    map.on("styledata", () => setTimeout(setup, 0));
    const poll = setInterval(() => { setup(); if (ready) clearInterval(poll); }, 400);
  }

  function render() {
    if (!map || !map.getLayer("seg")) return;
    map.setPaintProperty("seg", "line-color", lineColor());
    map.setPaintProperty("seg", "line-opacity", state.opac);
    map.setFilter("seg", segFilter());
    const showNone = state.notree && !state.onlyGaps && !state.cell && (state.mode === "canopy" || state.mode === "trees");
    map.setLayoutProperty("seg-none", "visibility", showNone ? "visible" : "none");
    map.setLayoutProperty("seg-gap", "visibility", state.gaps ? "visible" : "none");
    map.setLayoutProperty("sat", "visibility", state.base === "sat" ? "visible" : "none");
    if (map.getLayer("heat")) map.setPaintProperty("heat", "raster-opacity", state.heat ? (state.base === "sat" ? 0.55 : 0.68) : 0);
    // over a coloured background (heat layer or aerial photo) every line gets a dark outline so it stays readable
    const outlined = state.heat || state.base === "sat";
    map.setPaintProperty("seg-case", "line-color", outlined ? "#1d1b18" : (matchMedia("(prefers-color-scheme: dark)").matches ? "#111" : "#fff"));
    map.setPaintProperty("seg-case", "line-opacity", outlined ? ["case", ["boolean", ["feature-state", "hover"], false], 1, 0.85] : ["case", ["boolean", ["feature-state", "hover"], false], 1, 0]);
    map.setFilter("seg-case", segFilter());
    const gf = state.genus === null ? null : ["==", ["get", "g"], state.genus];
    map.setFilter("trees", gf);
    map.setFilter("crowns", gf);
    const v = state.view;
    map.setLayoutProperty("trees", "visibility", v === "trees" ? "visible" : "none");
    map.setLayoutProperty("crowns", "visibility", v === "3d" ? "visible" : "none");
    if (map.getLayer("bld")) map.setLayoutProperty("bld", "visibility", v === "3d" ? "visible" : "none");
    document.querySelectorAll("#modeSeg button").forEach((b) => b.classList.toggle("on", b.dataset.mode === state.mode));
    document.querySelectorAll("#viewSeg button").forEach((b) => b.classList.toggle("on", b.dataset.view === state.view));
    document.querySelectorAll("#baseSeg button").forEach((b) => b.classList.toggle("on", b.dataset.base === state.base));
    $("#heatBtn").classList.toggle("on", state.heat);
    $("#heatBtn").setAttribute("aria-pressed", state.heat);
    legend();
    modeHint();
    drawMatrix();
  }

  // ------------------------------------------------------------------ interaction
  function bindMap() {
    const tip = $("#tip");
    map.on("mousemove", "seg", (e) => {
      const f = e.features[0];
      if (hoverId !== null) map.setFeatureState({ source: "seg", id: hoverId }, { hover: false });
      hoverId = f.id; map.setFeatureState({ source: "seg", id: hoverId }, { hover: true });
      map.getCanvas().style.cursor = "pointer";
      const p = f.properties;
      tip.innerHTML = `<b>${esc(p.s)}</b> <span class="mono">${fmt(p.c, 0)} % canopy</span>` +
        `<small>${p.n} trees within 15 m · ${fmt(p.d, 1)} per 100 m${p.h != null ? ` · ${fmt(p.h, 1)} °C surface` : ""}</small>` +
        (p.g ? `<small style="color:${GAP}">data gap: ${p.mc} of ${p.n} trees without crown size</small>` : "");
      place(tip, e.point);
    });
    map.on("mouseleave", "seg", () => {
      if (hoverId !== null) map.setFeatureState({ source: "seg", id: hoverId }, { hover: false });
      hoverId = null; tip.hidden = true; map.getCanvas().style.cursor = "";
    });
    map.on("mousemove", "seg-none", (e) => {
      if (hoverId !== null) return;
      const p = e.features[0].properties;
      tip.innerHTML = `<b>${esc(p.s)}</b><small>No registered tree within 15 m${p.h != null ? ` · ${fmt(p.h, 1)} °C surface` : ""}</small>`;
      place(tip, e.point); map.getCanvas().style.cursor = "pointer";
    });
    map.on("mouseleave", "seg-none", () => { if (hoverId === null) { tip.hidden = true; map.getCanvas().style.cursor = ""; } });
    map.on("mousemove", "trees", (e) => {
      if (hoverId !== null) return;
      const p = e.features[0].properties;
      tip.innerHTML = `<b>${esc(p.de || p.sc || "Tree")}</b> <small><i class="dot" style="background:${SPC[p.g ?? 11]}"></i>${esc(p.sc || "")} · ${SPN[p.g ?? 11][0]}</small>` +
        `<small>crown ${p.cr != null ? fmt(p.cr, 0) + " m" : "<i>missing</i> (est. " + fmt(p.ce, 0) + " m)"} · height ${p.h != null ? fmt(p.h, 0) + " m" : "<i>missing</i>"} · planted ${p.y || "<i>unknown</i>"}</small>`;
      place(tip, e.point); map.getCanvas().style.cursor = "pointer";
    });
    map.on("mousemove", "crowns", (e) => {
      if (hoverId !== null) return;
      const p = e.features[0].properties;
      tip.innerHTML = `<b>${esc(p.de || SPN[p.g][0])}</b><small><i class="dot" style="background:${SPC[p.g]}"></i>${SPN[p.g][0]} · ${SPN[p.g][1]} · top at ${fmt(p.h, 0)} m${p.im ? " · crown size estimated" : ""}</small>`;
      place(tip, e.point); map.getCanvas().style.cursor = "pointer";
    });
    map.on("mouseleave", "crowns", () => { if (hoverId === null) { tip.hidden = true; map.getCanvas().style.cursor = ""; } });
    map.on("mouseleave", "trees", () => { if (hoverId === null) { tip.hidden = true; map.getCanvas().style.cursor = ""; } });
    map.on("click", (e) => {
      const fs = map.queryRenderedFeatures([[e.point.x - 4, e.point.y - 4], [e.point.x + 4, e.point.y + 4]], { layers: ["seg", "seg-none", "seg-gap"].filter((l) => map.getLayoutProperty(l, "visibility") !== "none") });
      if (!fs.length) return;
      const p = fs[0].properties;
      popup.setLngLat(e.lngLat).setHTML(segmentHTML(p)).addTo(map);
      showStreet(p.s, false);
    });
  }
  function place(tip, pt) {
    tip.hidden = false;
    const r = $("#map").getBoundingClientRect();
    tip.style.left = Math.min(pt.x, r.width - tip.offsetWidth - 30) + "px";
    tip.style.top = pt.y + "px";
  }

  function canClass(c) { return c < 5 ? 0 : c < 15 ? 1 : c < 30 ? 2 : 3; }
  function segmentHTML(p) {
    const k = canClass(p.c);
    const tag = (col, txt) => `<span class="tag" style="background:${col}">${txt}</span>`;
    let catTxt = p.n === 0 ? "No registered tree within 15 m"
      : p.cat === "trees, little canopy" ? "Trees are planted, but they shade little"
      : p.cat === "few trees, little canopy" ? "Few trees and little shade" : "Effective canopy";
    const young = p.n ? Math.round(100 * p.y / p.n) : 0;
    const rows = [
      ["Length of this piece", `${fmt(p.L)} m`],
      ["Trees within 15 m", `${p.n} <small>(${fmt(p.d, 1)} per 100 m)</small>`],
      ["Canopy cover, estimate", `${fmt(p.c, 1)} %`],
      ["… measured crowns only", `${fmt(p.cm, 1)} %`],
      ["Planted since 2011", `${p.y}${p.n ? ` <small>(${young} %)</small>` : ""}`],
      ["Crown < 5 m / ≥ 10 m", `${p.sm} / ${p.lg}`],
      ["Planted before 1970", `${p.o}`],
      ["Median crown · height", `${p.cr != null ? fmt(p.cr, 1) + " m" : "–"} · ${p.hm != null ? fmt(p.hm, 0) + " m" : "–"}`],
      ["Median planting year", p.ym || "–"],
    ];
    if (p.h != null) rows.push(["Summer surface temp.", `${fmt(p.h, 1)} °C <small>(${p.ha > 0 ? "+" : ""}${fmt(p.ha, 1)} vs. median street)</small>`]);
    const gaps = p.n ? `<div class="gapline${p.g ? " on" : ""}">Records without crown <b>${p.mc}</b> · height <b>${p.mh}</b> · planting year <b>${p.my}</b>${p.g ? "<br><b>Data gap:</b> canopy is uncertain here — partly estimated." : ""}</div>` : "";
    return `<div class="pop"><b style="font-size:15px">${esc(p.s)}</b>
      <small>${p.f || p.to ? `between ${esc(p.f || "?")} and ${esc(p.to || "?")} · ` : ""}${esc(p.t)}${p.ot ? " · " + esc(p.ot) : p.b ? " · " + esc(p.b) : ""}</small>
      ${tag(p.n ? CAN[k] : NOTREE, p.n ? `${CAN_NAMES[k]} canopy · ${fmt(p.c, 0)} %` : "no registered trees")}
      ${p.hk >= 0 ? tag(PRIO[p.p], `${PRIO_NAMES[p.p]} · ${HEAT_NAMES[p.hk]}`) : ""}
      <div class="verdict">${catTxt}${p.n && p.cat === "trees, little canopy" && young >= 50 ? " — most of them are young." : "."}</div>
      <table class="ptab">${rows.map(([a, b]) => `<tr><td>${a}</td><td>${b}</td></tr>`).join("")}</table>
      ${p.sp ? `<small>Main species: ${esc(p.sp)}</small>` : ""}${gaps}</div>`;
  }

  // ------------------------------------------------------------------ street detail
  function showStreet(name, fly = true) {
    const s = STREETS[name];
    if (!s) return;
    state.street = name;
    map.setFilter("seg-hl", ["in", ["get", "s"], ["literal", [name]]]);
    if (fly) map.fitBounds([[s.bb[0], s.bb[1]], [s.bb[2], s.bb[3]]], { padding: padding(), maxZoom: 17, duration: 900 });
    const feats = byStreet.get(name) || [];
    const km = [0, 0, 0, 0]; let none = 0;
    feats.forEach((f) => { const p = f.properties; if (p.n === 0) none += p.L; else km[p.k] += p.L; });
    const tot = s.L || 1;
    const bar = km.map((v, i) => `<i style="width:${100 * v / tot}%;background:${CAN[i]}"></i>`).join("") +
      `<i style="width:${100 * none / tot}%;background:${NOTREE}"></i>`;
    const young = s.n ? Math.round(100 * (s.y) / s.n) : 0;
    const k = canClass(s.c);
    $("#detail").hidden = false;
    $("#detail").innerHTML = `<button class="close" aria-label="Close">×</button>
      <h3>${esc(name)}</h3><p class="hint" style="margin:0">${esc(s.t || "")}${s.ot ? " · " + esc(s.ot) : s.b ? " · " + esc(s.b) : ""} · ${fmt(s.L)} m in ${s.k} pieces</p>
      <div class="kpis">
        <div class="kpi"><b style="color:${CAN[k]}">${fmt(s.c, 0)} %</b><span>canopy cover (est.)</span></div>
        <div class="kpi"><b>${fmt(s.n)}</b><span>trees within 15 m</span></div>
        <div class="kpi"><b>${fmt(s.d, 1)}</b><span>trees per 100 m</span></div>
        <div class="kpi"><b>${young} %</b><span>planted since 2011</span></div>
        <div class="kpi"><b>${fmt(s.lg)}</b><span>large crowns ≥ 10 m</span></div>
        <div class="kpi"><b>${s.h != null ? fmt(s.h, 1) + "°" : "–"}</b><span>summer surface °C</span></div>
      </div>
      <div class="lcbar">${bar}</div>
      <div class="sbar-l"><span>share of street length by canopy class</span><span>${s.p1 ? `<b style="color:${PRIO[1]}">${s.p1} pieces “act first”</b>` : ""}</span></div>
      <p class="verdict">${verdict(s)}</p>
      ${s.gap ? `<p class="hint">${s.gap} of ${s.n} trees have an incomplete record (crown ${s.mc ? s.mc + " missing" : "complete"}). Canopy measured from known crowns only: ${fmt(s.cm, 1)} %.</p>` : ""}`;
    $("#detail .close").onclick = () => { $("#detail").hidden = true; state.street = null; map.setFilter("seg-hl", ["in", ["get", "s"], ["literal", []]]); };
    if (fly && innerWidth > 860) $("#detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  function verdict(s) {
    if (!s.n) return "No registered street tree within 15 m — the whole street is open to the sun (or its trees are not in the city's register).";
    const young = s.n ? s.y / s.n : 0, small = s.n ? s.sm / s.n : 0;
    if (s.c < 15 && s.d >= 6) return `<b>Trees are not enough here:</b> ${fmt(s.d, 1)} trees per 100 m, but only ${fmt(s.c, 0)} % canopy` +
      (young >= .5 || small >= .5 ? ` — ${Math.round(100 * Math.max(young, small))} % are ${young >= small ? "young" : "small-crowned"}. Shade will come only if they survive and grow.` : ".");
    if (s.c < 15) return `Few trees and little shade: ${fmt(s.d, 1)} trees per 100 m, ${fmt(s.c, 0)} % canopy.`;
    if (s.d < 6) return `Fewer trees, real shade: ${fmt(s.c, 0)} % canopy from only ${fmt(s.d, 1)} trees per 100 m — ${s.lg} of them have crowns of 10 m or more.`;
    return `An effectively shaded street: ${fmt(s.c, 0)} % canopy.`;
  }
  function padding() { return innerWidth > 860 ? { top: 60, bottom: 90, left: 420, right: 60 } : 40; }

  // ------------------------------------------------------------------ panel
  function initPanel() {
    const st = STATS.streets, tr = STATS.trees;
    $("#lede").innerHTML = `Leipzig registers <b>${fmt(tr.total)} trees</b>. But ten young trees do not shade a street like four old ones. We cut <b>${fmt(st.km)} km</b> of streets into <b>${fmt(st.pieces)}</b> pieces of at most 100&nbsp;m and estimated how much of each lies under a tree crown.`;
    $("#loading").textContent = `Loading ${fmt(st.pieces)} street pieces…`;
    const dl = $("#streetList");
    Object.keys(STREETS).sort((a, b) => a.localeCompare(b, "de")).forEach((n) => { const o = document.createElement("option"); o.value = n; dl.appendChild(o); });
    const go = () => {
      const v = $("#qStreet").value.trim();
      let name = STREETS[v] ? v : Object.keys(STREETS).find((n) => n.toLowerCase() === v.toLowerCase())
        || Object.keys(STREETS).find((n) => n.toLowerCase().startsWith(v.toLowerCase()));
      if (!v || !name) { $("#qMsg").textContent = v ? `No street called “${v}” in the network.` : ""; return; }
      $("#qMsg").textContent = ""; $("#qStreet").value = name; showStreet(name);
    };
    $("#qGo").onclick = go;
    $("#qStreet").addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    $("#qStreet").addEventListener("change", () => { if (STREETS[$("#qStreet").value]) go(); });

    document.querySelectorAll("#modeSeg button").forEach((b) => b.onclick = () => {
      if ((b.dataset.mode === "heat" || b.dataset.mode === "prio") && !HEAT) return;
      state.mode = b.dataset.mode; state.cell = null; render();
    });
    if (!HEAT) document.querySelectorAll('#modeSeg [data-mode="heat"], #modeSeg [data-mode="prio"]').forEach((b) => { b.disabled = true; b.title = "Heat data not available"; });
    document.querySelectorAll("#viewSeg button").forEach((b) => b.onclick = () => {
      state.view = b.dataset.view;
      if (state.view === "3d") { if (map.getZoom() < 15.5) map.easeTo({ zoom: 16, pitch: 60, bearing: -20, duration: 1200 }); else map.easeTo({ pitch: 60, duration: 800 }); }
      else if (map.getPitch() > 0) map.easeTo({ pitch: 0, bearing: 0, duration: 700 });
      if (state.view === "trees" && map.getZoom() < 14) map.easeTo({ zoom: 15, duration: 900 });
      render();
    });
    document.querySelectorAll("#baseSeg button").forEach((b) => b.onclick = () => { state.base = b.dataset.base; render(); });
    $("#heatBtn").onclick = () => { if (!HEAT) return; state.heat = !state.heat; render(); };
    if (!HEAT) { $("#heatBtn").disabled = true; $("#heatBtn").title = "Heat data not available"; $("#heatMethod").hidden = true; }
    $("#opac").oninput = (e) => { state.opac = e.target.value / 100; render(); };
    $("#gapTog").onchange = (e) => { state.gaps = e.target.checked; render(); };
    $("#noTreeTog").onchange = (e) => { state.notree = e.target.checked; render(); };
    $("#introGo").onclick = () => { $("#intro").hidden = true; try { localStorage.setItem("d2-intro", "1"); } catch (e) {} };

    story(); bigNumbers(); rank(); gaps(); downloads();
    document.querySelectorAll("#rankSeg button").forEach((b) => b.onclick = () => {
      state.rank = b.dataset.r; document.querySelectorAll("#rankSeg button").forEach((x) => x.classList.toggle("on", x === b)); rank();
    });
    if (!HEAT) document.querySelector('#rankSeg [data-r="rank_low_canopy_hot"]').disabled = true;
    const m = location.hostname.match(/^([^.]+)\.github\.io$/);
    if (m) { const repo = location.pathname.split("/").filter(Boolean)[0]; if (repo) $("#repoLink").href = `https://github.com/${m[1]}/${repo}`; }
    $("#coverage").innerHTML = `<b>Coverage.</b> ${fmt(tr.total)} trees in the Baumkataster, ${fmt(tr.near_streets)} of them within 15 m of a street. ${fmt(st.names)} street names, ${fmt(st.km)} km. ` +
      (HEAT ? `Heat: ${HEAT.scenes} clear Landsat scenes, summers ${HEAT.years}. ` : "") +
      (STATS.buildings ? `3D: ${fmt(STATS.buildings.n)} LoD2 buildings.` : "");
  }

  function story() {
    const box = $("#story");
    const pairs = (STATS.story || []).filter((p) => STREETS[p.a] && STREETS[p.b]);
    if (!pairs.length) { box.innerHTML = '<p class="hint">No matching pair found.</p>'; return; }
    const maxD = Math.max(...pairs.flatMap((p) => [STREETS[p.a].d, STREETS[p.b].d]));
    box.innerHTML = pairs.map((p, i) => {
      const A = STREETS[p.a], B = STREETS[p.b];
      const row = (n, s, tag) => `<div class="sr"><div class="sn"><b>${esc(n)}</b><small>${tag}</small></div>
        <div class="sb"><span>trees / 100 m</span><i style="width:${100 * s.d / maxD}%;background:#6a8f5a"></i><em class="mono">${fmt(s.d, 1)}</em></div>
        <div class="sb"><span>canopy</span><i style="width:${Math.max(1, s.c * 2)}%;background:${CAN[canClass(s.c)]}"></i><em class="mono">${fmt(s.c, 0)} %</em></div></div>`;
      const ya = Math.round(100 * Math.max(A.y, A.sm) / Math.max(1, A.n));
      return `<button class="pair" data-i="${i}">
        ${row(p.a, A, `${A.n} trees · ${ya} % young or small-crowned`)}
        ${row(p.b, B, `${B.n} trees · ${B.lg} with crowns ≥ 10 m`)}
      </button>`;
    }).join("");
    box.querySelectorAll(".pair").forEach((b) => b.onclick = () => {
      const p = pairs[+b.dataset.i], A = STREETS[p.a], B = STREETS[p.b];
      box.querySelectorAll(".pair").forEach((x) => x.classList.toggle("on", x === b));
      state.mode = "canopy"; state.cell = null; render();
      map.setFilter("seg-hl", ["in", ["get", "s"], ["literal", [p.a, p.b]]]);
      map.fitBounds([[Math.min(A.bb[0], B.bb[0]), Math.min(A.bb[1], B.bb[1])], [Math.max(A.bb[2], B.bb[2]), Math.max(A.bb[3], B.bb[3])]], { padding: padding(), maxZoom: 16, duration: 1000 });
      showStreet(p.a, false);
    });
  }

  function bigNumbers() {
    const pk = STATS.priority_km;
    if (HEAT && pk) {
      $("#bigNum").innerHTML = `<em>${fmt(pk[1])} km</em> of street`;
      $("#bigHint").innerHTML = `have little canopy (under 15 %) <b>and</b> belong to Leipzig's hottest third of streets on summer days. Another ${fmt(pk[2])} km have little canopy in the middle third. Click a square to show those streets only.`;
    } else {
      const s = STATS.streets;
      $("#bigNum").innerHTML = `<em>${fmt(s.by_class_share[0] + s.by_class_share[1], 0)} %</em> of street length`;
      $("#bigHint").textContent = "has less than 15 % canopy cover in its 30 m corridor.";
    }
  }

  function drawMatrix() {
    const box = $("#matrix");
    if (!STATS.matrix) { box.innerHTML = ""; return; }
    const M = STATS.matrix, tot = M.flat().reduce((a, b) => a + b, 0);
    let h = `<div class="mx-corner">canopy ↓<br>heat →</div>` + HEAT_NAMES.map((n, i) => `<div class="mx-col">${["cooler", "middle", "hottest"][i]}<small>third</small></div>`).join("");
    for (let k = 3; k >= 0; k--) {
      h += `<div class="mx-row"><i class="sw" style="background:${CAN[k]}"></i>${CAN_NAMES[k]}<small>${CAN_RANGE[k]}</small></div>`;
      for (let c = 0; c < 3; c++) {
        const on = state.cell && state.cell[0] === k && state.cell[1] === c;
        const dim = state.cell && !on;
        const dk = (k === 0 && c >= 1) || (k === 1 && c === 2);
        h += `<button class="mx-cell${on ? " on" : ""}${dim ? " dim" : ""}" data-k="${k}" data-c="${c}" style="background:${BIV[k][c]};color:${dk ? "#fff" : "#2b2925"}"><b>${fmt(M[k][c])}</b><span>km · ${fmt(100 * M[k][c] / tot, 0)} %</span></button>`;
      }
    }
    box.innerHTML = h;
    box.querySelectorAll(".mx-cell").forEach((b) => b.onclick = () => {
      const k = +b.dataset.k, c = +b.dataset.c;
      state.cell = state.cell && state.cell[0] === k && state.cell[1] === c ? null : [k, c];
      render();
    });
  }

  function rank() {
    const list = (STATS[state.rank] || []).filter((n) => STREETS[n]);
    const vals = list.map((n) => STREETS[n]);
    $("#rank").innerHTML = list.map((n, i) => {
      const s = STREETS[n];
      const right = state.rank === "rank_low_canopy_hot" ? `${fmt(s.h, 1)}° · ${fmt(s.c, 0)} %` : state.rank === "rank_best" ? `${fmt(s.c, 0)} %` : `${fmt(s.d, 0)}/100 m · ${fmt(s.c, 0)} %`;
      return `<li data-n="${esc(n)}"><span class="n">${i + 1}</span><span>${esc(n)}<small>${esc(s.ot || s.b || "")}</small></span><span class="bar" style="background:${CAN[canClass(s.c)]};width:${Math.max(6, Math.min(100, s.c * 2.5))}%"></span><span class="v mono">${right}</span></li>`;
    }).join("") || '<li>No data.</li>';
    $("#rank").querySelectorAll("li[data-n]").forEach((li) => li.onclick = () => showStreet(li.dataset.n));
  }

  function gaps() {
    const t = STATS.trees, s = STATS.streets;
    const pct = (v) => fmt(100 * v / t.total, 0) + " %";
    $("#gaps").innerHTML = `<div class="gapgrid">
      <div><b class="mono">${fmt(t.miss_crown)}</b><span>trees without a usable crown diameter (${pct(t.miss_crown)})</span></div>
      <div><b class="mono">${fmt(t.miss_h)}</b><span>without height (${pct(t.miss_h)})</span></div>
      <div><b class="mono">${fmt(t.miss_year)}</b><span>without planting year (${pct(t.miss_year)})</span></div></div>
      <p class="hint">None of them is dropped. Missing crowns are estimated from similar trees, so canopy is not understated. <b>${fmt(s.data_gap_km, 1)} km</b> of street are marked as data gaps (≥ 25 % of their trees without a crown size; <span style="color:${GAP};font-weight:600">dashed purple</span>), and ${fmt(s.data_gap_any_km)} km have at least one incomplete record.</p>
      <label class="switch"><input type="checkbox" id="onlyGap"><span class="knob"></span><span>Show only data-gap streets</span></label>`;
    $("#onlyGap").onchange = (e) => { state.onlyGaps = e.target.checked; if (state.onlyGaps) { state.gaps = true; $("#gapTog").checked = true; } render(); };
  }

  function downloads() {
    const items = [["data/street_segments.gpkg.zip", "Street pieces · GeoPackage"], ["data/street_segments.csv.zip", "Street pieces · CSV"],
                   ["data/streets.json", "Street summary · JSON"]];
    if (HEAT) items.push(["data/lst_summer_leipzig.tif", "Surface temperature · GeoTIFF"]);
    $("#downloads").innerHTML = `<span>Open the data in QGIS or Excel:</span>` + items.map(([u, t]) => `<a href="${u}" download>${t}</a>`).join("");
  }

  // ------------------------------------------------------------------ legend + hints
  function modeHint() {
    const h = {
      canopy: "Estimated share of the 30 m street corridor under tree crowns. Hover a line for details.",
      trees: "Registered trees within 15 m per 100 m of street — compare with canopy: density is not shade.",
      heat: "Typical summer surface temperature along the street (Landsat, around 10:30).",
      prio: "Greening priority: little canopy combined with high surface heat."
    };
    $("#modeHint").textContent = state.cell ? `Showing ${CAN_NAMES[state.cell[0]]} canopy × ${HEAT_NAMES[state.cell[1]]} only. Click the square again to clear.` : h[state.mode];
  }
  function legend() {
    const L = $("#legend");
    let h = "";
    if (state.cell) {
      const [k, c] = state.cell;
      h = `<div class="lg-title">${CAN_NAMES[k]} canopy · ${HEAT_NAMES[c]}</div><div class="lg-row"><i class="sq" style="background:${BIV[k][c]}"></i>${fmt(STATS.matrix[k][c])} km of street</div><div class="lg-sel"><button id="lgClear">Show all streets</button></div>`;
    } else if (state.mode === "canopy") {
      h = `<div class="lg-title">Canopy cover of the street</div><div class="lg-sub">share of the 30 m corridor (15 m each side) under tree crowns, per ≤ 100 m piece</div>` +
        CAN.map((c, i) => `<div class="lg-row"><i class="sq ln" style="background:${c}"></i>${CAN_NAMES[i]} <small>${CAN_RANGE[i]} · ${fmt(STATS.streets.by_class_km[i])} km</small></div>`).reverse().join("");
    } else if (state.mode === "trees") {
      h = `<div class="lg-title">Registered trees per 100 m</div><div class="lg-sub">stems within 15 m of the centre line</div>
        <div class="lg-bar" style="background:linear-gradient(90deg,${TREES_STOPS.map(([v, c]) => `${c} ${100 * v / 25}%`).join(",")})"></div><div class="lg-ticks"><span>0</span><span>8</span><span>15</span><span>25+</span></div>`;
    } else if (state.mode === "heat") {
      h = heatLegend("Summer surface temperature along the street");
    } else {
      h = `<div class="lg-title">Greening priority</div><div class="lg-sub">canopy class × heat third of the street</div>` +
        [1, 2, 3, 0].map((p) => `<div class="lg-row"><i class="sq ln" style="background:${PRIO[p]}"></i><b>${PRIO_NAMES[p]}</b><small>${PRIO_TXT[p]} · ${fmt(STATS.priority_km[p])} km</small></div>`).join("");
    }
    if (!state.cell && (state.mode === "canopy" || state.mode === "trees") && state.notree)
      h += `<div class="lg-row"><i class="sq ln thin" style="background:${NOTREE}"></i>no registered tree within 15 m <small>${fmt(STATS.streets.cat_km["no trees"])} km</small></div>`;
    if (state.gaps) h += `<div class="lg-row"><i class="sq ln dash"></i>data gap — canopy partly estimated <small>${fmt(STATS.streets.data_gap_km, 1)} km</small></div>`;
    if (state.heat && state.mode !== "heat") h += `<div class="lg-sel">${heatLegend("Heat layer (background)", true)}</div>`;
    if (state.view === "3d" || state.view === "trees") {
      const cnt = (STATS.species || {}).counts || [];
      h += `<div class="lg-sel"><div class="lg-title" style="font-size:12px">Trees by species <small class="hint">click to show one</small></div><div class="spgrid">` +
        SPN.map(([en, de], i) => `<button class="sp${state.genus === i ? " on" : ""}${state.genus !== null && state.genus !== i ? " dim" : ""}" data-g="${i}"><i style="background:${SPC[i]}"></i>${en}<small>${cnt[i] ? fmt(cnt[i]) : ""}</small></button>`).join("") + `</div>` +
        (state.view === "3d" ? `<div class="hint">Trees drawn from the register: real height and crown diameter; crown shape round, conical (conifers) or columnar (“Säulen-/Fastigiata” cultivars). Grey blocks = LoD2 buildings. Zoom in to street level.</div>`
                             : `<div class="hint">Circles at real crown size. Faded = crown size missing, estimated; purple ring = incomplete record.</div>`) + `</div>`;
    }
    L.innerHTML = h;
    const c = $("#lgClear"); if (c) c.onclick = () => { state.cell = null; render(); };
    L.querySelectorAll(".sp").forEach((b) => b.onclick = () => { const g = +b.dataset.g; state.genus = state.genus === g ? null : g; render(); });
  }
  function heatLegend(title, small) {
    if (!HEAT) return "";
    const { lo, hi, stops } = HEAT.scale;
    return `<div class="lg-title"${small ? ' style="font-size:12px"' : ""}>${title}</div>${small ? "" : '<div class="lg-sub">Landsat 8/9, clear summer days, median; land surface, not air</div>'}
      <div class="lg-bar" style="background:linear-gradient(90deg,${stops.map(([t, c]) => `${c} ${t * 100}%`).join(",")})"></div>
      <div class="lg-ticks"><span>${fmt(lo, 0)} °C</span><span>${fmt((lo + hi) / 2, 0)}</span><span>${fmt(hi, 0)} °C</span></div>`;
  }
})();
