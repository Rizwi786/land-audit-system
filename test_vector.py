"""Offline test of OSM vector mode with a fake Overpass response."""
import math, app as A
LAT0, LON0 = 33.6844, 73.0479
mlat = 111320.0; mlon = 111320.0 * math.cos(math.radians(LAT0))

def rect(c, r, w=10, h=20):
    x0, y0 = LON0 + c*w/mlon, LAT0 + r*h/mlat
    x1, y1 = LON0 + (c+1)*w/mlon, LAT0 + (r+1)*h/mlat
    pts = [(x0,y0),(x1,y0),(x1,y1),(x0,y1),(x0,y0)]
    return [{"lat": y, "lon": x} for x, y in pts]

els = []
n = 1
for r in range(2):
    for c in range(3):
        els.append({"type":"way","id":n,"tags":{"building":"house"},"geometry":rect(c,r)}); n += 1
# multipolygon with a 4x4 m hole: 20x20 outer, area should be 400 - 16 = 384
outer = rect(5,0,20,20); inner = [{"lat":LAT0+8/mlat+p*0,"lon":LON0+110/mlon} for p in range(0)]
x0,y0 = LON0+100/mlon, LAT0+8/mlat
hole = [{"lat":y0+dy/mlat,"lon":x0+dx/mlon} for dx,dy in [(0,0),(4,0),(4,4),(0,4),(0,0)]]
big = [{"lat":LAT0+(0)/mlat+dy/mlat,"lon":LON0+(100+dx)/mlon} for dx,dy in [(0,0),(20,0),(20,20),(0,20),(0,0)]]
els.append({"type":"relation","id":99,"tags":{"building":"yes","name":"Hall"},
            "members":[{"type":"way","role":"outer","geometry":big},{"type":"way","role":"inner","geometry":hole}]})
A.fetch_overpass = lambda q: {"elements": els}

bbox = [LON0-0.0001, LAT0-0.0001, LON0+0.002, LAT0+0.001]
res = A.detect_vector(bbox, "building")
print("plots:", res["n_plots"], "total m2:", res["total_area_sqm"])
for f in res["features"]:
    p = f["properties"]; print(p["plot_id"], p["osm_id"], p["area_sqm"], p["area_marla"], p["name"])
c = A.app.test_client()
r = c.post("/api/detect", json={"bbox": bbox, "mode": "vector_building"})
print("API", r.status_code, r.get_json()["n_plots"])
r = c.post("/api/detect", json={"bbox": [73.0,33.6,73.1,33.7], "mode": "vector_building"})
print("Too-large area ->", r.status_code, r.get_json()["error"])
