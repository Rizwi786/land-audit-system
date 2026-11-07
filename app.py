"""
Land Audit System - Web App (Phase 2 prototype)
Select an area on the map -> system fetches map tiles for that area ->
detects plot boundaries -> assigns unique IDs -> calculates areas.

Run:  python app.py      then open  http://127.0.0.1:5000
"""
import base64
import hmac
import io
import json
import math
import os
import re
import time
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
import requests
import shapely
from flask import Flask, Response, jsonify, request, send_from_directory
from pyproj import Transformer
from shapely.geometry import mapping, shape

from plot_extractor import (area_units, build_line_mask, estimate_line_width,
                            find_plot_regions, order_polygons, utm_epsg_for)

app = Flask(__name__, static_folder="static")

TILE = 256
MAX_TILES = 36            # safety limit per request
MAX_ZOOM = 19
# Set your real contact email (needed by OSM/Overpass usage policies):
#   Windows:  set LAND_AUDIT_CONTACT=you@yourcompany.com   (before python app.py)
CONTACT = os.environ.get("LAND_AUDIT_CONTACT", "land-audit-prototype")
HEADERS = {"User-Agent": f"LandAuditSystem/0.2 (+{CONTACT})",
           "Accept": "*/*",
           "Referer": "http://127.0.0.1:5000/"}

TILE_SOURCES = {
    "osm": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    "esri": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
}


# ---------------- Optional password gate (for sharing a public link) ----------------
# Set APP_PASSWORD in the environment to require a login (any username, this password).
APP_PASSWORD = os.environ.get("APP_PASSWORD")


@app.before_request
def password_gate():
    if not APP_PASSWORD or request.path == "/healthz":   # /healthz is open for the host's health check
        return None
    a = request.authorization
    if a and hmac.compare_digest((a.password or "").encode(), APP_PASSWORD.encode()):
        return None
    return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="Land Audit System"'})


# ---------------- Web-Mercator helpers ----------------
def lonlat_to_world_px(lon, lat, z):
    n = TILE * (2 ** z)
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def world_px_to_lonlat(x, y, z):
    n = TILE * (2 ** z)
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


def ground_res(lat, z):
    """metres per pixel at a latitude / zoom"""
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** z)


def choose_zoom(bbox, max_zoom=MAX_ZOOM):
    w, s, e, n = bbox
    for z in range(max_zoom, 0, -1):
        x0, y1 = lonlat_to_world_px(w, s, z)
        x1, y0 = lonlat_to_world_px(e, n, z)
        tx = int(x1 // TILE) - int(x0 // TILE) + 1
        ty = int(y1 // TILE) - int(y0 // TILE) + 1
        if tx * ty <= MAX_TILES:
            return z
    return 1


# ---------------- Tile fetching ----------------
def fetch_tile(url_tpl, z, x, y):
    """Return a BGR tile image. Replaced by a mock in tests."""
    url = url_tpl.format(z=z, x=x, y=y)
    for _ in range(2):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
                if img is not None:
                    return img
        except requests.RequestException:
            pass
    raise RuntimeError(f"Could not download tile {z}/{x}/{y}")


def stitch(bbox, z, url_tpl):
    """Download + stitch tiles, crop exactly to bbox. Returns (image, origin_px)."""
    w, s, e, n = bbox
    px0, py1 = lonlat_to_world_px(w, s, z)   # left, bottom
    px1, py0 = lonlat_to_world_px(e, n, z)   # right, top
    tx0, tx1 = int(px0 // TILE), int(px1 // TILE)
    ty0, ty1 = int(py0 // TILE), int(py1 // TILE)
    coords = [(tx, ty) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]
    with ThreadPoolExecutor(8) as ex:
        tiles = list(ex.map(lambda c: fetch_tile(url_tpl, z, c[0], c[1]), coords))
    canvas = np.zeros(((ty1 - ty0 + 1) * TILE, (tx1 - tx0 + 1) * TILE, 3), np.uint8)
    for (tx, ty), t in zip(coords, tiles):
        t = cv2.resize(t, (TILE, TILE)) if t.shape[:2] != (TILE, TILE) else t
        canvas[(ty - ty0) * TILE:(ty - ty0 + 1) * TILE, (tx - tx0) * TILE:(tx - tx0 + 1) * TILE] = t
    ox, oy = tx0 * TILE, ty0 * TILE          # world px of canvas top-left
    cx0, cy0 = int(round(px0 - ox)), int(round(py0 - oy))
    cx1, cy1 = int(round(px1 - ox)), int(round(py1 - oy))
    return canvas[cy0:cy1, cx0:cx1].copy(), (ox + cx0, oy + cy0)


# ---------------- Detection ----------------
def edge_mask(img_bgr, gap_close):
    """For coloured maps (OSM/satellite): boundaries = colour edges."""
    blur = cv2.GaussianBlur(img_bgr, (3, 3), 0)
    # run per colour channel so low-contrast colour fills still give an edge
    raw = np.zeros(img_bgr.shape[:2], np.uint8)
    for ch in cv2.split(blur):
        raw |= cv2.Canny(ch, 25, 70)
    mask = raw
    if gap_close > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * gap_close + 1,) * 2)
        mask = cv2.dilate(raw, k)
    return mask, raw


def detect(bbox, tile_url, mode="lines", gap_close=3, min_area_m2=30.0,
           max_area_m2=50000.0, zoom=None, id_prefix="PLOT-"):
    w, s, e, n = bbox
    z = zoom or choose_zoom(bbox)
    img, (ox, oy) = stitch(bbox, z, tile_url)
    H, W = img.shape[:2]
    lat_c = (s + n) / 2
    res = ground_res(lat_c, z)

    gray = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (3, 3), 0)
    if mode == "edges":
        mask, raw = edge_mask(img, gap_close)
    else:
        mask, raw = build_line_mask(gray, None, gap_close)
    lw = estimate_line_width(raw)
    # lines mode: grow back half the stroke; edges mode: a 1px Canny edge
    grow = gap_close + (1 if mode == "edges" else int(round(lw / 2)))

    min_px = max(20.0, min_area_m2 / (res * res))
    max_frac = min(0.9, max_area_m2 / (res * res) / (W * H))
    polys = find_plot_regions(mask, min_px, max_frac, grow, 1.5)

    warnings = []
    if z < 17:
        warnings.append(f"Zoom {z} is too low for reliable plot detection. Select a smaller area.")
    if not polys:
        return {"type": "FeatureCollection", "features": [], "zoom": z,
                "resolution_m_px": round(res, 3), "warnings": warnings + ["No plots detected."]}

    med_h = float(np.median([p.bounds[3] - p.bounds[1] for p in polys]))
    polys = order_polygons(polys, med_h * 0.5)

    epsg = utm_epsg_for((w + e) / 2, lat_c)
    to_utm = Transformer.from_crs(4326, epsg, always_xy=True).transform
    to_geo = lambda a: np.column_stack(
        [[world_px_to_lonlat(ox + x, oy + y, z)[0] for x, y in a],
         [world_px_to_lonlat(ox + x, oy + y, z)[1] for x, y in a]])

    feats, total = [], 0.0
    for i, p in enumerate(polys, 1):
        geo = shapely.transform(p, to_geo)
        metric = shapely.transform(geo, lambda a: np.column_stack(to_utm(a[:, 0], a[:, 1])))
        sqm = metric.area
        total += sqm
        props = {"plot_id": f"{id_prefix}{i:04d}", **area_units(sqm),
                 "perimeter_m": round(metric.length, 2), "status": "auto_detected"}
        feats.append({"type": "Feature", "properties": props, "geometry": mapping(geo)})

    return {"type": "FeatureCollection", "features": feats, "zoom": z,
            "resolution_m_px": round(res, 3), "n_plots": len(feats),
            "total_area_sqm": round(total, 2), "line_width_px": lw, "warnings": warnings}



# ---------------- Vector mode (OSM / Overpass) ----------------
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
MAX_VECTOR_KM2 = 2.0


def fetch_overpass(query):
    """POST an Overpass query (tries several public servers). Replaced by a mock in tests."""
    errors = []
    for url in OVERPASS_URLS:
        try:
            r = requests.post(url, data={"data": query}, headers=HEADERS, timeout=70)
            if r.status_code == 200:
                return r.json()
            errors.append(f"{url.split('/')[2]}: HTTP {r.status_code}")
        except (requests.RequestException, ValueError) as ex:
            errors.append(f"{url.split('/')[2]}: {type(ex).__name__}")
    raise RuntimeError("All Overpass servers failed (" + "; ".join(errors) + "). Try again in a minute.")


def _ring(geom):
    return [(p["lon"], p["lat"]) for p in geom]


def _element_polygons(el):
    """OSM way or multipolygon relation -> list of shapely polygons (lon/lat)."""
    from shapely.geometry import LineString, Polygon
    from shapely.ops import polygonize, unary_union
    if el["type"] == "way":
        pts = _ring(el.get("geometry", []))
        if len(pts) >= 4 and pts[0] == pts[-1]:
            p = Polygon(pts)
            return [p if p.is_valid else p.buffer(0)]
        return []
    if el["type"] == "relation":
        outers = [LineString(_ring(m["geometry"])) for m in el.get("members", [])
                  if m.get("role") == "outer" and m.get("geometry") and len(m["geometry"]) > 1]
        inners = [LineString(_ring(m["geometry"])) for m in el.get("members", [])
                  if m.get("role") == "inner" and m.get("geometry") and len(m["geometry"]) > 1]
        if not outers:
            return []
        out = list(polygonize(unary_union(outers)))
        if inners:
            holes = unary_union(list(polygonize(unary_union(inners))))
            out = [o.difference(holes) for o in out]
        res = []
        for g in out:
            res.extend(list(g.geoms) if g.geom_type == "MultiPolygon" else [g])
        return [g for g in res if g.geom_type == "Polygon" and not g.is_empty]
    return []


def detect_vector(bbox, kind="building", id_prefix="PLOT-"):
    w, s, e, n = bbox
    lat_c = (s + n) / 2
    epsg = utm_epsg_for((w + e) / 2, lat_c)
    to_utm = Transformer.from_crs(4326, epsg, always_xy=True).transform
    box_m = shapely.transform(shapely.box(w, s, e, n),
                              lambda a: np.column_stack(to_utm(a[:, 0], a[:, 1])))
    if box_m.area / 1e6 > MAX_VECTOR_KM2:
        raise RuntimeError(f"Area too large ({box_m.area/1e6:.1f} km2). Select under {MAX_VECTOR_KM2} km2.")

    bb = f"({s},{w},{n},{e})"
    if kind == "plot":
        sel = f'way["place"="plot"]{bb};relation["place"="plot"]{bb};'
    elif kind == "landuse":
        sel = (f'way["landuse"~"residential|commercial|retail|industrial|institutional|education"]{bb};'
               f'relation["landuse"~"residential|commercial|retail|industrial|institutional|education"]{bb};')
    else:
        sel = f'way["building"]{bb};relation["building"]{bb};'
    data = fetch_overpass(f"[out:json][timeout:50];({sel});out geom;")

    items = []
    for el in data.get("elements", []):
        for poly in _element_polygons(el):
            geo_m = shapely.transform(poly, lambda a: np.column_stack(to_utm(a[:, 0], a[:, 1])))
            if geo_m.area < 1:
                continue
            tags = el.get("tags", {})
            items.append((poly, geo_m, f'{el["type"]}/{el["id"]}', tags))
    if not items:
        return {"type": "FeatureCollection", "features": [], "n_plots": 0, "mode": "vector",
                "warnings": [{"plot": "No place=plot polygons here. Numbered dotted lines on OSM are usually address ranges, not plots. Try 'buildings'.",
                                      "landuse": "No land-use polygons here. Try 'buildings' or a larger area.",
                                      "building": "No building polygons in OSM for this area. Try 'land use', another area, or trace/import plots."}[kind]]}

    # reading order: top-to-bottom, left-to-right (in metres)
    order = order_polygons([it[1] for it in items],
                           row_tol_px=float(np.median([it[1].bounds[3] - it[1].bounds[1] for it in items])) * 0.5)
    pos = {id(g): i for i, g in enumerate(order)}
    items.sort(key=lambda it: pos[id(it[1])])

    feats, total = [], 0.0
    for i, (geo, metric, osm_id, tags) in enumerate(items, 1):
        sqm = metric.area
        total += sqm
        props = {"plot_id": f"{id_prefix}{i:04d}", **area_units(sqm),
                 "perimeter_m": round(metric.length, 2), "status": "osm_vector",
                 "osm_id": osm_id,
                 "type": tags.get("building") or tags.get("landuse") or tags.get("place") or "",
                 "name": tags.get("name", ""),
                 # plot / house number shown as the label in the table and on the map
                 "label": tags.get("ref") or tags.get("addr:housenumber") or tags.get("name", "")}
        feats.append({"type": "Feature", "properties": props, "geometry": mapping(geo)})
    return {"type": "FeatureCollection", "features": feats, "n_plots": len(feats), "mode": "vector",
            "total_area_sqm": round(total, 2), "warnings": []}



# ---------------- Measure + Import ----------------
def measure_geometry(geom):
    """Shapely polygon in lon/lat -> area/perimeter properties (UTM zone of its own centroid)."""
    c = geom.centroid
    to_utm = Transformer.from_crs(4326, utm_epsg_for(c.x, c.y), always_xy=True).transform
    m = shapely.transform(geom, lambda a: np.column_stack(to_utm(a[:, 0], a[:, 1])))
    return {**area_units(m.area), "perimeter_m": round(m.length, 2)}


LABEL_KEYS = ("plot_no", "plotno", "plot_num", "plot", "plot_id", "id", "name", "label")


def _label(props):
    low = {str(k).lower(): v for k, v in (props or {}).items()}
    for k in LABEL_KEYS:
        if low.get(k) not in (None, ""):
            return str(low[k])
    return ""


def _polys(geom):
    geom = shapely.force_2d(geom)
    if geom.geom_type == "Polygon":
        return [geom]
    if geom.geom_type == "MultiPolygon":
        return list(geom.geoms)
    return []


def _parse_geojson(raw):
    data = json.loads(raw.decode("utf-8-sig"))
    crs = 4326
    m = re.search(r"EPSG:+(\d+)", json.dumps(data.get("crs", {})))
    if m:
        crs = int(m.group(1))
    if data.get("type") == "FeatureCollection":
        feats = data["features"]
    elif data.get("type") == "Feature":
        feats = [data]
    else:
        feats = [{"type": "Feature", "geometry": data, "properties": {}}]
    return [(shape(f["geometry"]), f.get("properties") or {}) for f in feats if f.get("geometry")], crs


def _local(tag):
    return tag.split("}")[-1]


def _parse_kml(raw):
    root = ET.fromstring(raw)
    def ring(el):
        for c in el.iter():
            if _local(c.tag) == "coordinates" and c.text:
                pts = [tuple(map(float, t.split(",")[:2])) for t in c.text.split() if "," in t]
                return pts
        return []
    out = []
    for pm in root.iter():
        if _local(pm.tag) != "Placemark":
            continue
        name = ""
        for ch in pm:
            if _local(ch.tag) == "name":
                name = (ch.text or "").strip()
        for pg in pm.iter():
            if _local(pg.tag) != "Polygon":
                continue
            outer, holes = [], []
            for b in pg:
                if _local(b.tag) == "outerBoundaryIs":
                    outer = ring(b)
                elif _local(b.tag) == "innerBoundaryIs":
                    holes.append(ring(b))
            if len(outer) >= 3:
                from shapely.geometry import Polygon
                out.append((Polygon(outer, [h for h in holes if len(h) >= 3]), {"name": name}))
    return out, 4326


def _parse_shapefile_zip(raw):
    import shapefile
    from pyproj import CRS
    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = zf.namelist()
    shp = next((n for n in names if n.lower().endswith(".shp")), None)
    if not shp:
        raise RuntimeError("No .shp file found inside the zip.")
    base = shp[:-4].lower()
    def get(ext):
        n = next((x for x in names if x.lower() == base + ext), None)
        return io.BytesIO(zf.read(n)) if n else None
    r = shapefile.Reader(shp=get(".shp"), shx=get(".shx"), dbf=get(".dbf"))
    crs = 4326
    prj = get(".prj")
    if prj:
        c = CRS.from_wkt(prj.read().decode("utf-8", "ignore"))
        crs = c.to_epsg() or c
    fields = [f[0] for f in r.fields[1:]]
    out = []
    for sr in r.iterShapeRecords():
        if sr.shape.shapeType == 0:
            continue
        out.append((shape(sr.shape.__geo_interface__), dict(zip(fields, sr.record))))
    return out, crs


def import_file(filename, raw):
    n = filename.lower()
    if n.endswith((".geojson", ".json")):
        items, crs = _parse_geojson(raw)
    elif n.endswith(".kml"):
        items, crs = _parse_kml(raw)
    elif n.endswith(".zip"):
        items, crs = _parse_shapefile_zip(raw)
    else:
        raise RuntimeError("Unsupported file type. Use .geojson, .kml, or a .zip containing a Shapefile.")
    tr = None
    if crs != 4326:
        tr = Transformer.from_crs(crs, 4326, always_xy=True).transform
    feats, skipped = [], 0
    for geom, props in items:
        if tr:
            geom = shapely.transform(geom, lambda a: np.column_stack(tr(a[:, 0], a[:, 1])))
        for p in _polys(geom):
            x0, y0, x1, y1 = p.bounds
            if not (-180 <= x0 and x1 <= 180 and -90 <= y0 and y1 <= 90):
                raise RuntimeError("Coordinates are not lat/lon. For Shapefiles include the .prj file; "
                                   "for GeoJSON use lat/lon (EPSG:4326).")
            if not p.is_valid:
                p = p.buffer(0)
                if p.is_empty or p.geom_type != "Polygon":
                    skipped += 1
                    continue
            meas = measure_geometry(p)
            feats.append({"type": "Feature",
                          "properties": {**meas, "label": _label(props), "status": "imported"},
                          "geometry": mapping(p)})
    warn = [f"{skipped} invalid shape(s) skipped."] if skipped else []
    if not feats:
        warn.append("No polygons found in the file.")
    return {"type": "FeatureCollection", "features": feats, "n_plots": len(feats), "warnings": warn}



# ---------------- Place search (Nominatim) ----------------
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_search_cache, _last_search = {}, [0.0]


def nominatim_search(q, lat=None, lon=None):
    """Geocode text with OSM Nominatim (policy: max 1 request/second, cached)."""
    key = (q.lower(), round(lat or 0, 1), round(lon or 0, 1))
    if key in _search_cache:
        return _search_cache[key]
    wait = 1.1 - (time.time() - _last_search[0])
    if wait > 0:
        time.sleep(wait)
    params = {"q": q, "format": "jsonv2", "limit": 6}
    if lat is not None and lon is not None:       # prefer results near the current map view
        params["viewbox"] = f"{lon-0.5},{lat+0.5},{lon+0.5},{lat-0.5}"
    r = requests.get(NOMINATIM_URL, params=params, headers=HEADERS, timeout=15)
    _last_search[0] = time.time()
    if r.status_code != 200:
        raise RuntimeError(f"Search service returned HTTP {r.status_code}")
    out = []
    for it in r.json():
        bb = it.get("boundingbox")                # [south, north, west, east]
        out.append({"name": it.get("display_name", ""), "lat": float(it["lat"]), "lon": float(it["lon"]),
                    "bbox": [float(bb[2]), float(bb[0]), float(bb[3]), float(bb[1])] if bb else None})
    _search_cache[key] = out
    return out


# ---------------- Routes ----------------
@app.route("/healthz")
def healthz():
    return "ok"


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/detect", methods=["POST"])
def api_detect():
    d = request.get_json(force=True)
    try:
        bbox = [float(v) for v in d["bbox"]]           # west, south, east, north
        if str(d.get("mode", "")).startswith("vector"):
            kind = {"vector_landuse": "landuse", "vector_plot": "plot"}.get(d["mode"], "building")
            return jsonify(detect_vector(bbox, kind))
        src = d.get("source", "osm")
        url = d.get("custom_url") if src == "custom" else TILE_SOURCES.get(src)
        if not url:
            return jsonify(error="Unknown tile source"), 400
        result = detect(bbox, url, mode=d.get("mode", "lines"),
                        gap_close=int(d.get("gap_close", 3)),
                        min_area_m2=float(d.get("min_area_m2", 30)),
                        max_area_m2=float(d.get("max_area_m2", 50000)),
                        zoom=int(d["zoom"]) if d.get("zoom") else None)
        return jsonify(result)
    except Exception as ex:                             # keep the UI informative
        return jsonify(error=str(ex)), 500



@app.route("/api/measure", methods=["POST"])
def api_measure():
    d = request.get_json(force=True)
    try:
        return jsonify(props=[measure_geometry(shape(g)) for g in d.get("geometries", [])])
    except Exception as ex:
        return jsonify(error=str(ex)), 500


@app.route("/api/import", methods=["POST"])
def api_import():
    f = request.files.get("file")
    if not f:
        return jsonify(error="No file received"), 400
    try:
        raw = f.read()
        if len(raw) > 30 * 1024 * 1024:
            return jsonify(error="File too large (limit 30 MB)"), 400
        return jsonify(import_file(f.filename or "", raw))
    except Exception as ex:
        return jsonify(error=f"Import failed: {ex}"), 400


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify(results=[])
    try:
        lat = float(request.args["lat"]) if "lat" in request.args else None
        lon = float(request.args["lon"]) if "lon" in request.args else None
        return jsonify(results=nominatim_search(q, lat, lon))
    except Exception as ex:
        return jsonify(error=str(ex)), 502


if __name__ == "__main__":
    os.makedirs("static", exist_ok=True)
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)), debug=False)
