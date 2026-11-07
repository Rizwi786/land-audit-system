"""Offline test of /api/search with a fake Nominatim response."""
import app as A

class R:  status_code = 200
class Fake(R):
    def json(self): return [{"display_name": "G-9/1, Islamabad Capital Territory, Pakistan", "lat": "33.6990", "lon": "73.0290",
                             "boundingbox": ["33.69", "33.70", "73.02", "73.04"]}]
calls = []
A.requests.get = lambda url, params=None, headers=None, timeout=None: (calls.append(params) or Fake())
c = A.app.test_client()
r = c.get("/api/search?q=G-9/1&lat=33.68&lon=73.05").get_json()
print(r["results"][0]["name"], r["results"][0]["bbox"])
c.get("/api/search?q=G-9/1&lat=33.68&lon=73.05")
print("nominatim calls (2nd answered from cache):", len(calls), "| viewbox sent:", "viewbox" in calls[0])
print("short query ->", c.get("/api/search?q=a").get_json())
class Bad(R): status_code = 403
A.requests.get = lambda *a, **k: Bad()
print("blocked ->", c.get("/api/search?q=Lahore").status_code, c.get("/api/search?q=Lahore").get_json())
