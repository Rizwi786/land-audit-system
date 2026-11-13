#!/usr/bin/env python3
"""
Prepare local OSM data for the Land Audit app.

Input : a Geofabrik or BBBike shapefile .zip (layers such as gis_osm_buildings_a_free_1.shp,
        gis_osm_landuse_a_free_1.shp, gis_osm_pois_a_free_1.shp ...  or  buildings.shp, landuse.shp ...)
Output: in the --out folder (default "data"):
        buildings.geojson   building footprints
        landuse.geojson     land-use areas
        other.geojson       every other polygon layer (shops/POIs, parking, transport, places of worship ...)
                            each feature says which layer it came from

Usage:
    python prepare_local_data.py pakistan-latest-free.shp.zip --bbox 72.85 33.55 73.25 33.85
    python prepare_local_data.py pakistan-latest-free.shp.zip --bbox ... --only other     (just the extra layers)
    python prepare_local_data.py islamabad.shp.zip                                       (small city extract)

--bbox is WEST SOUTH EAST NORTH in lat/lon. Keep it as small as you need (small files load fast).
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import zipfile

import shapefile  # pyshp

POLYGON_TYPES = (5, 15, 25, 31)   # polygon, polygonZ, polygonM, multipatch
SKIP_LAYERS = {"adminareas", "protected_areas", "places"}   # boundaries / localities: not plots (use --keep-all-layers to include)


def base_name(member):
    return os.path.basename(member)[:-4].lower()


def find_layer(names, keyword):
    """The polygon layer (.shp) whose name contains keyword; prefer Geofabrik's '_a_' (areas)."""
    cands = [n for n in names if n.lower().endswith(".shp") and keyword in base_name(n)]
    if not cands:
        return None
    cands.sort(key=lambda n: ("_a_" not in base_name(n), len(n)))
    return cands[0]


def other_layers(names):
    """Every .shp that is not buildings/landuse. Geofabrik: only the '_a_' (area) layers."""
    shps = [n for n in names if n.lower().endswith(".shp") and "building" not in base_name(n) and "landuse" not in base_name(n)]
    areas = [n for n in shps if "_a_" in base_name(n)]
    return areas if areas else shps


def short_layer(member):
    b = base_name(member)
    for junk in ("gis_osm_", "_a_free_1", "_free_1", "_a_free", "_free"):
        b = b.replace(junk, "")
    return b


def round_coords(c, nd=6):
    if isinstance(c, (list, tuple)):
        return [round(c[0], nd), round(c[1], nd)] if c and isinstance(c[0], (int, float)) else [round_coords(x, nd) for x in c]
    return c


def iter_layer(zf, shp_member, bbox, stats):
    """Generator: yields (feature, (x0, y0, x1, y1)) for every polygon of a layer, one at a time
    (so a whole country never has to fit in memory). stats gets total / bad / not_polygon."""
    tmp = tempfile.mkdtemp()
    try:
        base = shp_member[:-4]
        prj = None
        for ext in (".shp", ".shx", ".dbf", ".prj"):
            m = next((n for n in zf.namelist() if n.lower() == (base + ext).lower()), None)
            if m:
                zf.extract(m, tmp)
                if ext == ".prj":
                    prj = os.path.join(tmp, m)
        if prj and os.path.exists(prj):
            wkt = open(prj, errors="ignore").read()
            try:
                from pyproj import CRS
                is_latlon = CRS.from_wkt(wkt).is_geographic
            except Exception:                      # unreadable .prj: fall back to a text check
                is_latlon = "PROJCS" not in wkt.upper() and "PROJCRS" not in wkt.upper()
            if not is_latlon:
                sys.exit(f"{shp_member}: coordinates are not lat/lon (EPSG:4326). Use the Geofabrik/BBBike lat/lon shapefile.")
        r = shapefile.Reader(os.path.join(tmp, base))
        if r.shapeType not in POLYGON_TYPES:
            r.close()
            stats["not_polygon"] = True
            return
        layer = short_layer(shp_member)
        fields = [f[0] for f in r.fields[1:]]
        total = len(r)
        stats["total"], stats["bad"] = total, 0
        for i in range(total):
            if i and i % 500000 == 0:
                print(f"  ... {i:,} of {total:,} records read")
            try:
                try:
                    shp = r.shape(i, bbox=bbox) if bbox else r.shape(i)   # skips shapes outside the box quickly
                except TypeError:                                          # older pyshp without bbox argument
                    shp = r.shape(i)
                if shp is None or shp.shapeType == 0:
                    continue
                if bbox:
                    x0, y0, x1, y1 = shp.bbox
                    if x1 < bbox[0] or x0 > bbox[2] or y1 < bbox[1] or y0 > bbox[3]:
                        continue
                rec = dict(zip(fields, r.record(i)))
                geom = shp.__geo_interface__
            except Exception:
                stats["bad"] += 1   # broken record (e.g. a polygon with no points): skip it, keep going
                continue
            if geom["type"] not in ("Polygon", "MultiPolygon"):
                continue
            geom = {"type": geom["type"], "coordinates": round_coords(geom["coordinates"])}
            nm = rec.get("name")
            yield ({"type": "Feature", "geometry": geom, "properties": {
                "osm_id": str(rec.get("osm_id") or ""),
                "name": nm.strip() if isinstance(nm, str) else "",
                "type": str(rec.get("type") or rec.get("fclass") or ""),
                "layer": layer}}, tuple(shp.bbox))
        r.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def read_layer(zf, shp_member, bbox):
    """Return (features, total_records, broken_records) or None if the layer is not polygons."""
    stats = {}
    feats = [f for f, _ in iter_layer(zf, shp_member, bbox, stats)]
    if stats.get("not_polygon"):
        return None
    return feats, stats.get("total", 0), stats.get("bad", 0)


def write_geojson(feats, out_path):
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f, separators=(",", ":"))
    return os.path.getsize(out_path) / 1e6


def main():
    ap = argparse.ArgumentParser(description="Prepare local OSM data from a shapefile zip.")
    ap.add_argument("zipfile")
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    ap.add_argument("--out", default="data")
    ap.add_argument("--keep-all-layers", action="store_true", help="also keep boundary/locality layers in other.geojson")
    ap.add_argument("--only", nargs="+", choices=["buildings", "landuse", "other"],
                    help="process only these outputs (default: all three)")
    a = ap.parse_args()
    want = set(a.only or ["buildings", "landuse", "other"])
    os.makedirs(a.out, exist_ok=True)
    wrote = False
    with zipfile.ZipFile(a.zipfile) as zf:
        names = zf.namelist()
        for kw, out, label, key in (("building", "buildings.geojson", "buildings", "buildings"),
                                    ("landuse", "landuse.geojson", "land use", "landuse")):
            if key not in want:
                continue
            shp = find_layer(names, kw)
            if not shp:
                print(f"(no {label} layer found in the zip)")
                continue
            print(f"reading {shp} ...")
            res = read_layer(zf, shp, a.bbox)
            if res is None:
                print(f"{label}: not a polygon layer, skipped")
                continue
            feats, total, bad = res
            mb = write_geojson(feats, os.path.join(a.out, out))
            extra = f", {bad:,} broken record(s) skipped" if bad else ""
            print(f"{label}: {len(feats):,} polygons kept out of {total:,}{extra}  ->  {os.path.join(a.out, out)}  ({mb:.1f} MB)")
            wrote = True
        if "other" in want:
            allfeats, per_layer = [], {}
            for shp in other_layers(names):
                if short_layer(shp) in SKIP_LAYERS and not a.keep_all_layers:
                    print(f"skipping {shp} (boundaries / localities)")
                    continue
                print(f"reading {shp} ...")
                res = read_layer(zf, shp, a.bbox)
                if res is None:
                    print("  (not a polygon layer, skipped)")
                    continue
                feats, total, bad = res
                per_layer[short_layer(shp)] = len(feats)
                allfeats.extend(feats)
            if per_layer:
                mb = write_geojson(allfeats, os.path.join(a.out, "other.geojson"))
                print("other areas: " + ", ".join(f"{k}={v:,}" for k, v in per_layer.items()) +
                      f"  ->  {os.path.join(a.out, 'other.geojson')}  ({mb:.1f} MB)")
                wrote = True
            else:
                print("(no other polygon layers found in the zip)")
        if not wrote:
            sys.exit("Nothing written: no matching shapefile layers found in the zip.")
    print("\nDone. Restart the app and pick a 'Local data' mode.")


if __name__ == "__main__":
    main()
