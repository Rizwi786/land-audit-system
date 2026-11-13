"""Offline test: whole-country tiles. Two far-apart 'cities', a house on a tile border, a huge landuse polygon."""
import io, json, math, os, shutil, subprocess, sys, time, zipfile
import shapefile
from pyproj import CRS

def rect(lon, lat, w_m=10, h_m=20):
    mlat = 111320.0; mlon = 111320.0 * math.cos(math.radians(lat))
    return [(lon, lat), (lon, lat + h_m / mlat), (lon + w_m / mlon, lat + h_m / mlat), (lon + w_m / mlon, lat), (lon, lat)]

def write_layer(zf, name, polys, rows, fields=("osm_id", "code", "fclass", "name", "type")):
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON)
    for f in fields: w.field(f, "C", 60)
    for p, r in zip(polys, rows): w.poly([p]); w.record(*r)
    w.close()
    for ext, b in ((".shp", shp), (".shx", shx), (".dbf", dbf)): zf.writestr(f"{name}{ext}", b.getvalue())
    zf.writestr(f"{name}.prj", CRS.from_epsg(4326).to_wkt())

ISL, LHR = (73.0479, 33.6844), (74.3587, 31.5204)
shutil.rmtree("/tmp/tl", ignore_errors=True); os.makedirs("/tmp/tl")
with zipfile.ZipFile("/tmp/tl/pk.shp.zip", "w") as zf:
    b, rows = [], []
    for i in range(5):                                    # 5 buildings in each city
        b.append(rect(ISL[0] + i * 0.0003, ISL[1])); rows.append((f"I{i}", "1500", "building", f"Isl{i}", "house"))
        b.append(rect(LHR[0] + i * 0.0003, LHR[1])); rows.append((f"L{i}", "1500", "building", f"Lhr{i}", "house"))
    b.append(rect(73.0999995, 33.70)); rows.append(("BORDER", "1500", "building", "Border house", "house"))   # straddles lon 73.1
    write_layer(zf, "gis_osm_buildings_a_free_1", b, rows)
    big = [(70.0, 28.0), (70.0, 31.0), (73.0, 31.0), (73.0, 28.0), (70.0, 28.0)]                              # 3 x 3 degrees
    write_layer(zf, "gis_osm_landuse_a_free_1", [big, rect(ISL[0] - 0.001, ISL[1] - 0.001, 300, 300)],
                [("L1", "7201", "forest", "Huge forest", ""), ("L2", "7202", "residential", "Block", "")])
    write_layer(zf, "gis_osm_pois_a_free_1", [rect(ISL[0] + 0.001, ISL[1] + 0.001)], [("P1", "2501", "supermarket", "Shop", "")])
    write_layer(zf, "gis_osm_adminareas_a_free_1", [big], [("A1", "9", "national", "Pakistan", "")])

t0 = time.time()
o = subprocess.run([sys.executable, "build_pakistan_tiles.py", "--zip", "/tmp/tl/pk.shp.zip", "--out", "/tmp/tl/data"], capture_output=True, text=True)
print(o.stdout.strip().splitlines()[-2]); print(o.stderr.strip()[-300:])
idx = json.load(open("/tmp/tl/data/tiles/index.json"))
print("index:", {k: len(v) for k, v in idx["kinds"].items()}, "| tile size", idx["tile"], "| built", idx["built"])
print("tile files:", sorted(os.listdir("/tmp/tl/data/tiles/buildings")))

import local_data, app as A
local_data.DATA_DIR = "/tmp/tl/data"; local_data._cache.clear(); local_data._tiles.clear()
c = A.app.test_client()
print("\ncapabilities:", c.get("/api/capabilities").get_json())
def detect(lonlat, mode="local_building", pad=0.002):
    bb = [lonlat[0] - 0.0005, lonlat[1] - 0.0005, lonlat[0] + pad, lonlat[1] + 0.002]
    return c.post("/api/detect", json={"bbox": bb, "mode": mode}).get_json()
r = detect(ISL); print("Islamabad:", r["n_plots"], [f["properties"]["label"] for f in r["features"]], "| areas", {f["properties"]["area_sqm"] for f in r["features"]})
r = detect(LHR); print("Lahore   :", r["n_plots"], [f["properties"]["label"] for f in r["features"]])
r = c.post("/api/detect", json={"bbox": [73.0995, 33.699, 73.1005, 33.701], "mode": "local_building"}).get_json()
print("border house (stored in 2 tiles) returned once:", r["n_plots"], [f["properties"]["label"] for f in r["features"]])
r = c.post("/api/detect", json={"bbox": [73.0, 33.68, 73.1, 33.69], "mode": "local_landuse"}).get_json()
print("landuse near Islamabad:", r["n_plots"], [(f["properties"]["label"]) for f in r["features"]], "(huge forest kept in its centre tile only)")
r = detect(ISL, "local_other"); print("other areas:", [f["properties"]["label"] for f in r["features"]], "(admin boundary skipped)")
r = c.post("/api/detect", json={"bbox": [66.9, 24.8, 67.1, 25.0], "mode": "local_building"}).get_json()
print("Karachi (no data built):", r["n_plots"], r["warnings"][0][:60])
local_data.MAX_TILES_PER_QUERY = 2
r = c.post("/api/detect", json={"bbox": [61, 24, 77, 37], "mode": "local_building"}); print("too many tiles (limit lowered to 2) ->", r.status_code, r.get_json().get("error", "")[:78])
local_data.MAX_TILES_PER_QUERY = 150

# speed: 2nd query is served from the in-memory tile cache
t = time.time(); [detect(ISL) for _ in range(20)]; print("20 repeated detects: %.0f ms each" % ((time.time() - t) / 20 * 1000))

o = subprocess.run([sys.executable, "build_pakistan_tiles.py", "--zip", "/tmp/tl/pk.shp.zip", "--out", "/tmp/tl/data", "--only", "buildings"], capture_output=True, text=True)
idx = json.load(open("/tmp/tl/data/tiles/index.json"))
print("\n--only buildings rebuild keeps the other kinds:", {k: len(v) for k, v in idx["kinds"].items()}, "| error:", o.stderr.strip()[-100:] or "none")
o = subprocess.run([sys.executable, "build_pakistan_tiles.py", "--zip", "/tmp/tl/pk.shp.zip", "--out", "/tmp/tl/data2", "--bbox", "72.9", "33.5", "73.3", "33.9"], capture_output=True, text=True)
idx = json.load(open("/tmp/tl/data2/tiles/index.json")); print("--bbox (Islamabad only):", {k: len(v) for k, v in idx["kinds"].items()}, "(Lahore tiles not built)")
