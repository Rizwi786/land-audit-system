"""Local vector data (OSM buildings / land use / other areas) stored in the 'data' folder.

Two layouts, both offline and free of public-server limits:

1. TILES (whole country; built once by build_pakistan_tiles.py):
       data/tiles/index.json, data/tiles/<buildings|landuse|other>/<x>_<y>.ndjson
   A query loads only the few tiles under the rectangle (cached in memory).
2. SINGLE FILES (one area; made by prepare_local_data.py):
       data/buildings.geojson, data/landuse.geojson, data/other.geojson

Tiles are used when they exist for a kind, otherwise the single file.
Each feature is a polygon with properties: osm_id, name, type, layer.
"""
import json
import math
import os
from collections import OrderedDict

from shapely.geometry import box, shape
from shapely.strtree import STRtree

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
FILES = {"building": "buildings.geojson", "landuse": "landuse.geojson", "other": "other.geojson"}
KIND_DIR = {"building": "buildings", "landuse": "landuse", "other": "other"}
MAX_TILES_PER_QUERY = 150
TILE_CACHE_SIZE = 64
_cache = {}                 # single-file data and the tile index (cleared by tests when DATA_DIR changes)
_tiles = OrderedDict()      # recently used tiles


def filename(kind):
    return FILES[kind]


def tile_index():
    """The tiles' index.json as a dict, or None when no tiles were built."""
    key = ("index", DATA_DIR)
    if key not in _cache:
        p = os.path.join(DATA_DIR, "tiles", "index.json")
        _cache[key] = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None
    return _cache[key]


def _have_tiles(kind):
    idx = tile_index()
    return bool(idx and idx.get("kinds", {}).get(KIND_DIR[kind]))


def available(kind):
    return _have_tiles(kind) or os.path.exists(os.path.join(DATA_DIR, FILES[kind]))


# ---------- single file ----------
def _load_file(kind):
    key = ("file", DATA_DIR, kind)
    if key not in _cache:
        with open(os.path.join(DATA_DIR, FILES[kind]), encoding="utf-8") as f:
            gj = json.load(f)
        geoms, props = [], []
        for ft in gj.get("features", []):
            if not ft.get("geometry"):
                continue
            g = shape(ft["geometry"])
            if g.geom_type in ("Polygon", "MultiPolygon") and not g.is_empty:
                geoms.append(g)
                props.append(ft.get("properties") or {})
        _cache[key] = (geoms, props, STRtree(geoms))
    return _cache[key]


# ---------- tiles ----------
def _load_tile(kind, ix, iy):
    key = (DATA_DIR, kind, ix, iy)
    if key in _tiles:
        _tiles.move_to_end(key)
        return _tiles[key]
    path = os.path.join(DATA_DIR, "tiles", KIND_DIR[kind], f"{ix}_{iy}.ndjson")
    geoms, props = [], []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                ft = json.loads(line)
                g = shape(ft["geometry"])
                if g.geom_type in ("Polygon", "MultiPolygon") and not g.is_empty:
                    geoms.append(g)
                    props.append(ft.get("properties") or {})
    entry = (geoms, props, STRtree(geoms))
    _tiles[key] = entry
    while len(_tiles) > TILE_CACHE_SIZE:
        _tiles.popitem(last=False)
    return entry


def _query_tiles(kind, bbox):
    idx = tile_index()
    size = idx["tile"]
    have = idx["kinds"][KIND_DIR[kind]]
    w, s, e, n = bbox
    ix0, iy0 = math.floor((w + 180) / size), math.floor((s + 90) / size)
    ix1, iy1 = math.floor((e + 180) / size), math.floor((n + 90) / size)
    wanted = []
    for key in have:
        ix, iy = (int(v) for v in key.split("_"))
        if ix0 <= ix <= ix1 and iy0 <= iy <= iy1:
            wanted.append((ix, iy))
    if len(wanted) > MAX_TILES_PER_QUERY:
        raise RuntimeError(f"This area covers {len(wanted)} map tiles: select a smaller area "
                           f"(limit {MAX_TILES_PER_QUERY} tiles, about 11 km each).")
    q, seen, out = box(*bbox), set(), []
    for ix, iy in sorted(wanted):
        geoms, props, tree = _load_tile(kind, ix, iy)
        for i in sorted(tree.query(q, predicate="intersects")):
            p = props[int(i)]
            key = (p.get("osm_id"), p.get("layer"), tuple(round(v, 7) for v in geoms[int(i)].bounds))
            if key in seen:                         # shapes crossing a tile border are stored in both tiles
                continue
            seen.add(key)
            out.append((geoms[int(i)], p))
    return out


def query(kind, bbox):
    """Polygons (lon/lat) that intersect bbox = (west, south, east, north)."""
    if _have_tiles(kind):
        return _query_tiles(kind, bbox)
    geoms, props, tree = _load_file(kind)
    idx = tree.query(box(*bbox), predicate="intersects")
    return [(geoms[int(i)], props[int(i)]) for i in sorted(idx)]


def polygons(geom):
    return list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
