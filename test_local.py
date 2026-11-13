"""Offline test: Geofabrik-style shapefile zip -> prepare_local_data.py -> local mode in the app."""
import io, json, math, os, shutil, subprocess, sys, zipfile, shapefile
from pyproj import CRS

LAT0, LON0 = 33.6844, 73.0479
mlat = 111320.0; mlon = 111320.0 * math.cos(math.radians(LAT0))
def rect(cx, cy, w=10, h=20):   # metres east/north of origin
    x0, y0 = LON0 + cx / mlon, LAT0 + cy / mlat; x1, y1 = LON0 + (cx + w) / mlon, LAT0 + (cy + h) / mlat
    return [(x0, y0), (x0, y1), (x1, y1), (x1, y0), (x0, y0)]       # clockwise = outer ring

def write_layer(zf, name, polys, rows):
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON)
    for f in ("osm_id", "code", "fclass", "name", "type"): w.field(f, "C", 60)
    for p, r in zip(polys, rows): w.poly([p]); w.record(*r)
    w.close()
    for ext, b in ((".shp", shp), (".shx", shx), (".dbf", dbf)): zf.writestr(f"{name}{ext}", b.getvalue())
    zf.writestr(f"{name}.prj", CRS.from_epsg(4326).to_wkt())

shutil.rmtree("/tmp/lt", ignore_errors=True); os.makedirs("/tmp/lt")
with zipfile.ZipFile("/tmp/lt/pk.shp.zip", "w") as zf:
    inside = [rect(i * 30, 0) for i in range(3)]
    far = [rect(50000, 50000)]                                          # ~50 km away: must be dropped
    write_layer(zf, "gis_osm_buildings_a_free_1", inside + far,
                [("1", "1500", "building", "School A", "school"), ("2", "1500", "building", "", "house"),
                 ("3", "1500", "building", "", ""), ("4", "1500", "building", "Far", "")])
    write_layer(zf, "gis_osm_landuse_a_free_1", [rect(-20, -20, 120, 60)], [("9", "7201", "residential", "Block", "")])
    # "other" areas: two shop/POI polygons next to the buildings (one far away) + a parking area
    write_layer(zf, "gis_osm_pois_a_free_1", [rect(5, 40), rect(50000, 50000)], [("7", "2501", "supermarket", "Al Fatah", ""), ("8", "2000", "x", "far", "")])
    write_layer(zf, "gis_osm_traffic_a_free_1", [rect(40, 40, 20, 30)], [("11", "5260", "parking", "", "")])
    write_layer(zf, "gis_osm_adminareas_a_free_1", [rect(-5000, -5000, 20000, 20000)], [("50", "9999", "national", "Country", "")])  # covers everything
    write_layer(zf, "gis_osm_natural_a_free_1", [rect(-3000, -3000, 6000, 6000)], [("51", "4101", "forest", "Big forest", "")])      # ~36 km2
    zf.writestr("gis_osm_roads_free_1.shp", b"")   # a non-area layer name: must be ignored (no _a_)

bb = [LON0 - 0.01, LAT0 - 0.01, LON0 + 0.01, LAT0 + 0.01]
out = subprocess.run([sys.executable, "prepare_local_data.py", "/tmp/lt/pk.shp.zip", "--bbox", *map(str, bb), "--out", "/tmp/lt/data"],
                     capture_output=True, text=True); print(out.stdout.strip()); print(out.stderr.strip())

import local_data, app as A
local_data.DATA_DIR = "/tmp/lt/data"; local_data._cache.clear()
c = A.app.test_client()
print("\ncapabilities:", c.get("/api/capabilities").get_json())
q = [LON0 - 0.0002, LAT0 - 0.0002, LON0 + 0.0012, LAT0 + 0.0003]
r = c.post("/api/detect", json={"bbox": q, "mode": "local_building"}).get_json()
print("local buildings:", r["n_plots"], [(f["properties"]["plot_id"], f["properties"]["label"], f["properties"]["area_sqm"]) for f in r["features"]], "| warnings:", r["warnings"])
r = c.post("/api/detect", json={"bbox": q, "mode": "local_landuse"}).get_json()
print("local land use :", r["n_plots"], [(f["properties"]["label"], f["properties"]["type"], f["properties"]["area_sqm"]) for f in r["features"]])
r = c.post("/api/detect", json={"bbox": [LON0 + 0.2, LAT0 + 0.2, LON0 + 0.21, LAT0 + 0.21], "mode": "local_building"}).get_json()
print("empty area     :", r["n_plots"], r["warnings"])
local_data.DATA_DIR = "/nonexistent"; local_data._cache.clear()
r = c.post("/api/detect", json={"bbox": q, "mode": "local_building"}); print("missing file   :", r.status_code, r.get_json()["error"][:80])

import subprocess as sp
print("\n--- other layers")
o = sp.run([sys.executable, "prepare_local_data.py", "/tmp/lt/pk.shp.zip", "--bbox", *map(str, bb), "--out", "/tmp/lt/data2", "--only", "other"], capture_output=True, text=True)
print([l for l in o.stdout.splitlines() if l.startswith("other areas")]); print(o.stderr.strip()[-200:])
import local_data as LD
LD.DATA_DIR = "/tmp/lt/data2"; LD._cache.clear()
print("files made with --only other:", sorted(os.listdir("/tmp/lt/data2")))
q2 = [LON0 - 0.0002, LAT0 - 0.0002, LON0 + 0.0012, LAT0 + 0.0010]      # reaches the shapes 40-70 m north
r = c.post("/api/detect", json={"bbox": q2, "mode": "local_other"}).get_json()
print("local other:", r["n_plots"], [(f["properties"]["label"], f["properties"]["area_sqm"]) for f in r["features"]])
print("capabilities:", c.get("/api/capabilities").get_json())

print("\n--- boundary / huge areas")
o = sp.run([sys.executable, "prepare_local_data.py", "/tmp/lt/pk.shp.zip", "--bbox", *map(str, bb), "--out", "/tmp/lt/data3", "--only", "other"], capture_output=True, text=True)
print("default prepare :", [l for l in o.stdout.splitlines() if l.startswith(("skipping", "other areas"))])
o = sp.run([sys.executable, "prepare_local_data.py", "/tmp/lt/pk.shp.zip", "--bbox", *map(str, bb), "--out", "/tmp/lt/data4", "--only", "other", "--keep-all-layers"], capture_output=True, text=True)
print("keep-all prepare:", [l for l in o.stdout.splitlines() if l.startswith("other areas")])
LD.DATA_DIR = "/tmp/lt/data4"; LD._cache.clear()
r = c.post("/api/detect", json={"bbox": q2, "mode": "local_other"}).get_json()
print("app filters them:", r["n_plots"], [f["properties"]["label"] for f in r["features"]], "| warnings:", r["warnings"])
