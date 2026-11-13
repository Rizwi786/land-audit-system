"""Offline test of /api/export_area: GeoJSON + Shapefile ZIP of a selected area."""
import io, json, math, os, shutil, zipfile
import shapefile
import local_data, app as A

LAT0, LON0 = 33.6844, 73.0479
mlat = 111320.0; mlon = 111320.0 * math.cos(math.radians(LAT0))
def rect(cx, cy, w=10, h=20):
    x0, y0 = LON0 + cx / mlon, LAT0 + cy / mlat; x1, y1 = LON0 + (cx + w) / mlon, LAT0 + (cy + h) / mlat
    return [[x0, y0], [x0, y1], [x1, y1], [x1, y0], [x0, y0]]
def feat(ring, **p): return {"type": "Feature", "properties": p, "geometry": {"type": "Polygon", "coordinates": ring if isinstance(ring[0][0], list) else [ring]}}

hole = [rect(100, 0, 20, 20)[::-1], rect(105, 5, 4, 4)]               # polygon with a 4x4 m hole -> 400-16 = 384 m2
shutil.rmtree("/tmp/ex", ignore_errors=True); os.makedirs("/tmp/ex")
json.dump({"type": "FeatureCollection", "features": [
    feat(rect(0, 0), osm_id="1", name="دکان شاپ", type="retail", layer="buildings"),   # Urdu name
    feat(rect(30, 0), osm_id="2", name="", type="house", layer="buildings"),
    feat(hole, osm_id="3", name="Hall", type="yes", layer="buildings"),
    feat(rect(50000, 50000), osm_id="4", name="far away", type="house", layer="buildings")]}, open("/tmp/ex/buildings.geojson", "w"), ensure_ascii=False)
json.dump({"type": "FeatureCollection", "features": [
    feat(rect(0, 40), osm_id="5", name="Lot", type="parking", layer="traffic"),
    feat(rect(-9000, -9000, 30000, 30000), osm_id="6", name="Pakistan", type="national", layer="adminareas")]}, open("/tmp/ex/other.geojson", "w"))
local_data.DATA_DIR = "/tmp/ex"; local_data._cache.clear()

c = A.app.test_client()
q = [LON0 - 0.0002, LAT0 - 0.0002, LON0 + 0.0020, LAT0 + 0.0010]
r = c.post("/api/export_area", json={"bbox": q, "format": "geojson"})
print("GeoJSON:", r.status_code, r.headers["Content-Type"], r.headers["Content-Disposition"])
z = zipfile.ZipFile(io.BytesIO(r.data)); print("  files:", sorted(z.namelist()))
b = json.loads(z.read("buildings.geojson")); print("  buildings:", [(f["properties"]["name"], f["properties"]["area_sqm"], f["properties"]["area_marla"]) for f in b["features"]])
o = json.loads(z.read("other.geojson")); print("  other:", [(f["properties"]["name"], f["properties"]["layer"]) for f in o["features"]], "(admin boundary left out)")
print("  README:", z.read("README.txt").decode().splitlines()[0][:60], "| attribution included:", "OpenStreetMap contributors" in z.read("README.txt").decode())

r = c.post("/api/export_area", json={"bbox": q, "format": "shapefile"})
z = zipfile.ZipFile(io.BytesIO(r.data)); print("\nShapefile:", r.status_code, sorted(z.namelist()))
rd = shapefile.Reader(shp=io.BytesIO(z.read("buildings.shp")), shx=io.BytesIO(z.read("buildings.shx")), dbf=io.BytesIO(z.read("buildings.dbf")), encoding="utf-8")
recs = [dict(zip([f[0] for f in rd.fields[1:]], x)) for x in rd.records()]
print("  read back:", len(rd), "shapes | fields:", [f[0] for f in rd.fields[1:]][:6], "...")
print("  attributes:", [(x["name"], float(x["area_sqm"])) for x in recs])
print("  hole polygon has", len(rd.shape(2).parts), "rings (outer + hole) | .prj starts:", z.read("buildings.prj")[:14], "| .cpg:", z.read("buildings.cpg"))

print("\nempty area ->", c.post("/api/export_area", json={"bbox": [LON0 + 1, LAT0 + 1, LON0 + 1.01, LAT0 + 1.01]}).get_json())
local_data.DATA_DIR = "/nonexistent"; local_data._cache.clear()
print("no data files ->", c.post("/api/export_area", json={"bbox": q}).status_code)
