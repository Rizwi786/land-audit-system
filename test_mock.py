"""Offline test: fake map tiles with known plots, compare detected vs true area."""
import math, numpy as np, cv2
from shapely.geometry import box
from shapely import transform
from pyproj import Transformer
import app as A

LAT0, LON0 = 33.6844, 73.0479
mlat = 111320.0
mlon = 111320.0 * math.cos(math.radians(LAT0))
# 3 x 2 grid of plots, each 10 m x 20 m, starting at LON0/LAT0
plots = []
for r in range(2):
    for c in range(3):
        w = LON0 + (c * 10) / mlon; e = LON0 + ((c + 1) * 10) / mlon
        s = LAT0 + (r * 20) / mlat; n = LAT0 + ((r + 1) * 20) / mlat
        plots.append((w, s, e, n))

tr = Transformer.from_crs(4326, 32643, always_xy=True).transform
truth = [box(*p) for p in plots]
truth_sqm = [transform(g, lambda a: np.column_stack(tr(a[:, 0], a[:, 1]))).area for g in truth]

OUTLINE = True

def fake_tile(url, z, tx, ty):
    t = np.full((256, 256, 3), 255, np.uint8)
    for (w, s, e, n), idx in zip(plots, range(len(plots))):
        x0, y1 = A.lonlat_to_world_px(w, s, z); x1, y0 = A.lonlat_to_world_px(e, n, z)
        p0 = (int(round(x0 - tx * 256)), int(round(y0 - ty * 256)))
        p1 = (int(round(x1 - tx * 256)), int(round(y1 - ty * 256)))
        col = (235, 225, 210) if ((idx // 3) + (idx % 3)) % 2 == 0 else (200, 225, 240)
        cv2.rectangle(t, p0, p1, col, -1)                    # alternating pale fills
        if OUTLINE: cv2.rectangle(t, p0, p1, (60, 60, 60), 2)  # outline
    return t
A.fetch_tile = fake_tile

bbox = [LON0 - 15 / mlon, LAT0 - 10 / mlat, LON0 + 45 / mlon, LAT0 + 50 / mlat]
for mode, OUTLINE in (("lines", True), ("edges", True), ("edges", False)):
    print("--- outline drawn:", OUTLINE)
    res = A.detect(bbox, "mock", mode=mode, gap_close=2, min_area_m2=20)
    got = [f["properties"]["area_sqm"] for f in res["features"]]
    print(f"[{mode}] zoom={res['zoom']} res={res['resolution_m_px']} m/px  plots={res.get('n_plots')}")
    print("  true :", [round(x, 1) for x in truth_sqm])
    print("  found:", got)
    print("  max error %:", round(max(abs(g - truth_sqm[0]) for g in got) / truth_sqm[0] * 100, 2))
c = A.app.test_client()
A.fetch_tile = fake_tile
r = c.post("/api/detect", json={"bbox": bbox, "source": "osm", "gap_close": 2, "min_area_m2": 20})
print("API status", r.status_code, "plots", r.get_json().get("n_plots"))
