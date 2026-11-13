#!/usr/bin/env python3
"""
One-time setup: split the WHOLE country's OSM data into small map tiles, so the app can answer a
rectangle anywhere in Pakistan offline and fast (it loads only the few tiles under the rectangle).

    python build_pakistan_tiles.py                       (uses pakistan-latest-free.shp.zip in this folder)
    python build_pakistan_tiles.py --download            (downloads it from Geofabrik first)
    python build_pakistan_tiles.py --zip C:\\path\\file.shp.zip
    python build_pakistan_tiles.py --bbox 72.85 33.55 73.25 33.85      (only one region: much faster / smaller)
    python build_pakistan_tiles.py --only buildings      (rebuild just one kind: buildings landuse other)

Result:  data/tiles/index.json  and  data/tiles/<buildings|landuse|other>/<x>_<y>.ndjson
Run it again later with a fresh download to update the data. After it finishes just start the app:
the "Local data" modes use the tiles automatically for every city.
"""
import argparse
import json
import math
import os
import shutil
import sys
import time
import zipfile
from collections import defaultdict

from prepare_local_data import SKIP_LAYERS, find_layer, iter_layer, other_layers, short_layer

GEOFABRIK_URL = "https://download.geofabrik.de/asia/pakistan-latest-free.shp.zip"
MAX_SPAN = 9          # a shape touching more tiles than this is stored only in the tile of its centre
KIND_DIR = {"buildings": "buildings", "landuse": "landuse", "other": "other"}


def download(url, dest):
    import requests
    print(f"downloading {url}\n  -> {dest}  (a few hundred MB; this can take a while)")
    with requests.get(url, stream=True, timeout=60, headers={"User-Agent": "LandAuditSystem/1.0"}) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done, last = 0, 0
        with open(dest + ".part", "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                done += len(chunk)
                if done - last >= 50 * (1 << 20):
                    last = done
                    print(f"  {done/1e6:,.0f} MB" + (f" of {total/1e6:,.0f} MB" if total else ""))
    os.replace(dest + ".part", dest)
    print("download complete")


class TileWriter:
    """Collects features per tile in memory and appends them to .ndjson files in batches."""

    def __init__(self, root, flush_at=100000):
        self.root, self.flush_at, self.n = root, flush_at, 0
        self.buf = defaultdict(list)
        self.counts = defaultdict(lambda: defaultdict(int))

    def add(self, kind, ix, iy, line):
        self.buf[(kind, ix, iy)].append(line)
        self.counts[kind][f"{ix}_{iy}"] += 1
        self.n += 1
        if self.n >= self.flush_at:
            self.flush()

    def flush(self):
        for (kind, ix, iy), lines in self.buf.items():
            d = os.path.join(self.root, KIND_DIR[kind])
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f"{ix}_{iy}.ndjson"), "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        self.buf.clear()
        self.n = 0


def tiles_for(bbox, size):
    x0, y0, x1, y1 = bbox
    ix0, iy0 = math.floor((x0 + 180) / size), math.floor((y0 + 90) / size)
    ix1, iy1 = math.floor((x1 + 180) / size), math.floor((y1 + 90) / size)
    if (ix1 - ix0 + 1) * (iy1 - iy0 + 1) > MAX_SPAN:          # very large shape: centre tile only
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        return [(math.floor((cx + 180) / size), math.floor((cy + 90) / size))]
    return [(ix, iy) for ix in range(ix0, ix1 + 1) for iy in range(iy0, iy1 + 1)]


def main():
    ap = argparse.ArgumentParser(description="Build offline map tiles for the whole country.")
    ap.add_argument("--zip", default="pakistan-latest-free.shp.zip")
    ap.add_argument("--download", action="store_true", help="download the Geofabrik zip if it is not there")
    ap.add_argument("--out", default="data")
    ap.add_argument("--tile", type=float, default=0.1, help="tile size in degrees (default 0.1 = about 11 km)")
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("WEST", "SOUTH", "EAST", "NORTH"),
                    help="only this region (default: everything)")
    ap.add_argument("--only", nargs="+", choices=list(KIND_DIR), help="rebuild only these kinds")
    ap.add_argument("--keep-all-layers", action="store_true", help="also keep boundary/locality layers")
    a = ap.parse_args()

    if not os.path.exists(a.zip):
        if not a.download:
            sys.exit(f"{a.zip} not found. Put the Geofabrik zip here, give --zip PATH, or add --download.")
        download(GEOFABRIK_URL, a.zip)

    root = os.path.join(a.out, "tiles")
    idx_path = os.path.join(root, "index.json")
    index = {"kinds": {}}
    if os.path.exists(idx_path):
        index = json.load(open(idx_path, encoding="utf-8"))
        if a.only and abs(index.get("tile", a.tile) - a.tile) > 1e-9:
            sys.exit("Existing tiles use another --tile size; rebuild everything (without --only).")
    kinds = a.only or list(KIND_DIR)
    for k in kinds:                                            # start these kinds from scratch
        shutil.rmtree(os.path.join(root, KIND_DIR[k]), ignore_errors=True)
        index["kinds"].pop(KIND_DIR[k], None)
    os.makedirs(root, exist_ok=True)

    w = TileWriter(root)
    t0 = time.time()
    with zipfile.ZipFile(a.zip) as zf:
        names = zf.namelist()
        plan = []
        if "buildings" in kinds:
            plan.append(("buildings", find_layer(names, "building")))
        if "landuse" in kinds:
            plan.append(("landuse", find_layer(names, "landuse")))
        if "other" in kinds:
            for shp in other_layers(names):
                if short_layer(shp) in SKIP_LAYERS and not a.keep_all_layers:
                    print(f"skipping {shp} (boundaries / localities)")
                    continue
                plan.append(("other", shp))
        for kind, shp in plan:
            if not shp:
                print(f"(no {kind} layer in the zip)")
                continue
            print(f"\nreading {shp} ...")
            stats, kept, last = {}, 0, time.time()
            for feat, bb in iter_layer(zf, shp, a.bbox, stats):
                line = json.dumps(feat, separators=(",", ":"), ensure_ascii=False)
                for ix, iy in tiles_for(bb, a.tile):
                    w.add(kind, ix, iy, line)
                kept += 1
                if kept % 100000 == 0:
                    print(f"  {kept:,} shapes written  ({kept / max(time.time() - t0, 1):,.0f}/s overall)")
            w.flush()
            if stats.get("not_polygon"):
                print("  (not a polygon layer, skipped)")
                continue
            extra = f", {stats.get('bad', 0):,} broken record(s) skipped" if stats.get("bad") else ""
            print(f"{short_layer(shp)}: {kept:,} shapes kept out of {stats.get('total', 0):,}{extra}")

    for kind, tiles in w.counts.items():
        index["kinds"].setdefault(KIND_DIR[kind], {}).update(tiles)
    index.update({"tile": a.tile, "source": os.path.basename(a.zip), "built": time.strftime("%Y-%m-%d %H:%M"),
                  "region": a.bbox})
    json.dump(index, open(idx_path, "w", encoding="utf-8"))

    size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(root) for f in fs) / 1e6
    print("\nDone in %.0f s. Tiles per kind: %s. Total size: %.0f MB" % (
        time.time() - t0, {k: len(v) for k, v in index["kinds"].items()}, size))
    print("Start the app (python app.py) and pick a 'Local data' mode: every city in this data now works offline.")


if __name__ == "__main__":
    main()
