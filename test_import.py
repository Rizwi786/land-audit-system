"""Offline test: import GeoJSON / KML / Shapefile(zip, UTM 43N) - each has two 10x20 m plots (=200 m2)."""
import io, json, math, zipfile, shapefile
from pyproj import Transformer, CRS
import app as A

LON0, LAT0 = 73.0479, 33.6844
to_utm = Transformer.from_crs(4326, 32643, always_xy=True)
e0, n0 = to_utm.transform(LON0, LAT0)
to_ll = Transformer.from_crs(32643, 4326, always_xy=True)
def rect_utm(i): x = e0 + i*30; return [(x,n0),(x+10,n0),(x+10,n0+20),(x,n0+20),(x,n0)]
def rect_ll(i): return [to_ll.transform(x,y) for x,y in rect_utm(i)]

c = A.app.test_client()
def post(name, data):
    r = c.post("/api/import", data={"file": (io.BytesIO(data), name)}, content_type="multipart/form-data")
    j = r.get_json(); return r.status_code, j

# GeoJSON (lat/lon) with a multipolygon + property label
gj = {"type":"FeatureCollection","features":[
  {"type":"Feature","properties":{"Plot_No":"A-1"},"geometry":{"type":"Polygon","coordinates":[rect_ll(0)]}},
  {"type":"Feature","properties":{"Plot_No":"A-2"},"geometry":{"type":"MultiPolygon","coordinates":[[rect_ll(1)]]}}]}
st, j = post("plots.geojson", json.dumps(gj).encode())
print("GeoJSON", st, [(f["properties"]["label"], f["properties"]["area_sqm"]) for f in j["features"]])

# KML
def kml_pm(name, ring): return f"<Placemark><name>{name}</name><Polygon><outerBoundaryIs><LinearRing><coordinates>{' '.join(f'{x},{y},0' for x,y in ring)}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
kml = f'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>{kml_pm("K-1",rect_ll(0))}{kml_pm("K-2",rect_ll(1))}</Document></kml>'
st, j = post("plots.kml", kml.encode())
print("KML    ", st, [(f["properties"]["label"], f["properties"]["area_sqm"]) for f in j["features"]])

# Shapefile in UTM 43N with .prj, zipped
shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON)
w.field("PLOT_NO", "C", 10)
for i in (0, 1):
    w.poly([list(reversed(rect_utm(i)))])  # clockwise = outer ring
    w.record(f"S-{i+1}")
w.close()
zb = io.BytesIO()
with zipfile.ZipFile(zb, "w") as z:
    z.writestr("plots/plots.shp", shp.getvalue()); z.writestr("plots/plots.shx", shx.getvalue())
    z.writestr("plots/plots.dbf", dbf.getvalue()); z.writestr("plots/plots.prj", CRS.from_epsg(32643).to_wkt())
st, j = post("plots.zip", zb.getvalue())
print("SHP zip", st, [(f["properties"]["label"], f["properties"]["area_sqm"], f["properties"]["area_marla"]) for f in j["features"]])

# Shapefile without .prj in UTM coords -> must give a clear error
zb2 = io.BytesIO()
with zipfile.ZipFile(zb2, "w") as z:
    z.writestr("p.shp", shp.getvalue()); z.writestr("p.shx", shx.getvalue()); z.writestr("p.dbf", dbf.getvalue())
st, j = post("noprj.zip", zb2.getvalue()); print("No .prj", st, j.get("error"))
st, j = post("x.pdf", b"x"); print("Bad type", st, j.get("error"))

# measure endpoint (manual tracing uses this)
r = c.post("/api/measure", json={"geometries":[{"type":"Polygon","coordinates":[rect_ll(0)]}]})
print("measure", r.status_code, r.get_json()["props"][0]["area_sqm"], r.get_json()["props"][0]["area_marla"])
