#!/usr/bin/env python3
"""
Plot Extractor - Phase 1 Prototype
===================================
Layout map image (lines drawn)  ->  polygons  ->  unique IDs  ->  areas.

Two ways to give the map real-world scale:

  A) Geographic mode (north-up image whose corner coordinates are known,
     e.g. a screenshot of a web map or a georeferenced layout):
         --bbox LON_MIN LAT_MIN LON_MAX LAT_MAX
     Pixels are converted to lat/lon, then projected to the correct UTM zone
     before area is calculated.

  B) Scale mode (scanned layout plan with a known scale):
         --mpp METERS_PER_PIXEL
     e.g. if 1 pixel = 0.05 m, pass --mpp 0.05.

Outputs (in --out folder):
    plots.geojson     polygons with id + area properties
    plots.csv         id, area in m2 / sq ft / marla / kanal / acre, perimeter
    annotated.png     visual check for the auditor
    debug_lines.png   what the detector considers "boundary lines"

Usage examples:
    python plot_extractor.py layout.png --mpp 0.05
    python plot_extractor.py layout.png --bbox 73.0400 33.6800 73.0460 33.6850
"""

import argparse
import csv
import json
import math
import os
import sys

import cv2
import numpy as np
from pyproj import Transformer
from shapely.geometry import Polygon, mapping
import shapely

# ---- Unit conversions (Pakistan) -------------------------------------------
SQM_PER_SQFT = 0.09290304
SQM_PER_MARLA = 272.25 * SQM_PER_SQFT      # 25.2929 m2
SQM_PER_KANAL = 20 * SQM_PER_MARLA         # 505.857 m2
SQM_PER_ACRE = 4046.8564224


def utm_epsg_for(lon, lat):
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


# ---- Step 1: detect boundary lines -----------------------------------------
def estimate_line_width(mask):
    """Typical stroke width (px): most common short run-length of line pixels."""
    runs = []
    for m in (mask, mask.T):
        for row in m[:: max(1, m.shape[0] // 300)]:
            d = np.diff(np.concatenate(([0], (row > 0).astype(np.int8), [0])))
            starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
            runs.extend((ends - starts).tolist())
    runs = np.array([r for r in runs if 1 <= r <= 25])
    if runs.size == 0:
        return 1
    return int(np.bincount(runs).argmax())


def build_line_mask(gray, line_thresh, gap_close):
    """Return (sealed_mask, raw_mask). Lines = 255."""
    if line_thresh is None:
        _, raw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    else:
        _, raw = cv2.threshold(gray, line_thresh, 255, cv2.THRESH_BINARY_INV)

    mask = raw
    if gap_close > 0:  # seal small gaps (scan noise, broken lines)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * gap_close + 1,) * 2)
        mask = cv2.dilate(raw, k, iterations=1)
    return mask, raw


# ---- Step 2: find enclosed regions (plots) ---------------------------------
def find_plot_regions(line_mask, min_area_px, max_area_frac, grow_px, epsilon_px):
    h, w = line_mask.shape
    regions = cv2.bitwise_not(line_mask)  # plots = white areas enclosed by lines
    n, labels, stats, _ = cv2.connectedComponentsWithStats(regions, connectivity=4)

    grow_k = None
    if grow_px > 0:
        grow_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow_px + 1,) * 2)

    polys = []
    for i in range(1, n):  # 0 = background label of the inverted image
        x, y, bw, bh, area = stats[i]
        # Skip regions touching the image border (outside world / margins)
        if x == 0 or y == 0 or x + bw >= w or y + bh >= h:
            continue
        if area < min_area_px or area > max_area_frac * w * h:
            continue

        mask = (labels == i).astype(np.uint8) * 255
        if grow_k is not None:  # compensate for line-dilation shrinkage
            mask = cv2.dilate(mask, grow_k)

        # RETR_EXTERNAL fills any holes (plot numbers / text inside the plot)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not cnts:
            continue
        c = max(cnts, key=cv2.contourArea)
        approx = cv2.approxPolyDP(c, epsilon_px, True)
        if len(approx) < 3:
            continue
        pts = approx[:, 0, :].astype(float)
        # contour runs through pixel centres: push out half a pixel
        poly = Polygon(pts).buffer(0.5, join_style=2)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.geom_type != "Polygon":
            continue
        polys.append(poly)
    return polys


# ---- Step 3: pixel -> real world ------------------------------------------
class Georef:
    def __init__(self, width, height, bbox=None, mpp=None):
        self.w, self.h, self.bbox, self.mpp = width, height, bbox, mpp
        if bbox:
            lon0, lat0, lon1, lat1 = bbox
            self.epsg = utm_epsg_for((lon0 + lon1) / 2, (lat0 + lat1) / 2)
            self.to_utm = Transformer.from_crs(4326, self.epsg, always_xy=True).transform
        elif mpp is None:
            raise ValueError("Provide either --bbox or --mpp")

    def pixel_to_geo(self, poly):
        """Pixel polygon -> lon/lat polygon (geographic mode only)."""
        lon0, lat0, lon1, lat1 = self.bbox
        f = lambda a: np.column_stack([lon0 + a[:, 0] / self.w * (lon1 - lon0),
                                       lat1 - a[:, 1] / self.h * (lat1 - lat0)])
        return shapely.transform(poly, f)

    def metric_polygon(self, px_poly):
        """Return (output_polygon_for_geojson, metric_polygon_in_meters)."""
        if self.bbox:
            geo = self.pixel_to_geo(px_poly)
            metric = shapely.transform(geo, lambda a: np.column_stack(self.to_utm(a[:, 0], a[:, 1])))
            return geo, metric
        # scale mode: local metric coordinates, y flipped so north is up
        s = self.mpp
        local = shapely.transform(px_poly, lambda a: np.column_stack([a[:, 0] * s, (self.h - a[:, 1]) * s]))
        return local, local


# ---- Step 4: ordering, IDs, stats -----------------------------------------
def order_polygons(polys, row_tol_px):
    """Top-to-bottom, left-to-right reading order, so IDs are predictable."""
    items = [(p, p.centroid.y, p.centroid.x) for p in polys]
    items.sort(key=lambda t: t[1])
    rows, cur, last_y = [], [], None
    for it in items:
        if last_y is None or abs(it[1] - last_y) <= row_tol_px:
            cur.append(it)
            last_y = it[1] if last_y is None else (last_y + it[1]) / 2
        else:
            rows.append(cur)
            cur, last_y = [it], it[1]
    if cur:
        rows.append(cur)
    out = []
    for r in rows:
        out.extend(sorted(r, key=lambda t: t[2]))
    return [t[0] for t in out]


def area_units(sqm):
    return {
        "area_sqm": round(sqm, 2),
        "area_sqft": round(sqm / SQM_PER_SQFT, 2),
        "area_marla": round(sqm / SQM_PER_MARLA, 2),
        "area_kanal": round(sqm / SQM_PER_KANAL, 3),
        "area_acre": round(sqm / SQM_PER_ACRE, 4),
    }


def draw_annotated(img_bgr, polys_px, ids, areas_sqm):
    out = img_bgr.copy()
    overlay = out.copy()
    rng = np.random.default_rng(7)
    for p, pid, a in zip(polys_px, ids, areas_sqm):
        color = tuple(int(c) for c in rng.integers(60, 230, 3))
        pts = np.array(p.exterior.coords, dtype=np.int32)
        cv2.fillPoly(overlay, [pts], color)
    out = cv2.addWeighted(overlay, 0.35, out, 0.65, 0)
    for p, pid, a in zip(polys_px, ids, areas_sqm):
        pts = np.array(p.exterior.coords, dtype=np.int32)
        cv2.polylines(out, [pts], True, (0, 0, 200), 2)
        cx, cy = int(p.centroid.x), int(p.centroid.y)
        cv2.putText(out, pid.replace("PLOT-", "P"), (cx - 22, cy - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
        cv2.putText(out, f"{a:.0f}m2", (cx - 22, cy + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1, cv2.LINE_AA)
    return out


# ---- Main ------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Extract plots from a layout map image.")
    ap.add_argument("image")
    ap.add_argument("--out", default="output")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--bbox", nargs=4, type=float,
                   metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"))
    g.add_argument("--mpp", type=float, help="meters per pixel")
    ap.add_argument("--roi", nargs=4, type=int, metavar=("X", "Y", "W", "H"),
                    help="auditor-selected area in pixels (crop). Georef is adjusted automatically.")
    ap.add_argument("--min-area", type=float, default=0.0005,
                    help="min plot size as fraction of image area (default 0.0005)")
    ap.add_argument("--max-area", type=float, default=0.25,
                    help="max plot size as fraction of image area (default 0.25)")
    ap.add_argument("--gap-close", type=int, default=4,
                    help="seals line gaps up to ~2x this many px (default 4; raise for poor scans)")
    ap.add_argument("--line-width", type=float, default=None,
                    help="boundary line width in px (default: auto-estimated)")
    ap.add_argument("--simplify", type=float, default=1.5,
                    help="polygon simplification tolerance in px (default 1.5)")
    ap.add_argument("--line-thresh", type=int, default=None,
                    help="gray threshold for lines (default: auto/Otsu)")
    ap.add_argument("--id-prefix", default="PLOT-")
    args = ap.parse_args()

    img = cv2.imread(args.image)
    if img is None:
        sys.exit(f"Cannot read image: {args.image}")
    H, W = img.shape[:2]
    bbox = args.bbox

    # Auditor ROI: crop and shrink the bbox to match
    ox = oy = 0
    if args.roi:
        x, y, w, h = args.roi
        if bbox:
            lon0, lat0, lon1, lat1 = bbox
            bbox = [lon0 + x / W * (lon1 - lon0),
                    lat1 - (y + h) / H * (lat1 - lat0),
                    lon0 + (x + w) / W * (lon1 - lon0),
                    lat1 - y / H * (lat1 - lat0)]
        img = img[y:y + h, x:x + w]
        H, W = img.shape[:2]
        ox, oy = x, y

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    line_mask, raw_mask = build_line_mask(gray, args.line_thresh, args.gap_close)
    lw = args.line_width if args.line_width else estimate_line_width(raw_mask)
    # Regions are shrunk by gap_close (sealing) + half the line width.
    # Grow them back so each plot boundary sits on the line CENTRE.
    grow = args.gap_close + int(round(lw / 2))
    print(f"Estimated line width: {lw:.0f}px  |  boundary compensation: {grow}px")
    polys_px = find_plot_regions(line_mask, args.min_area * W * H, args.max_area,
                                 grow, args.simplify)
    if not polys_px:
        sys.exit("No plots detected. Try --gap-close, --min-area or --line-thresh.")

    median_h = float(np.median([p.bounds[3] - p.bounds[1] for p in polys_px]))
    polys_px = order_polygons(polys_px, row_tol_px=median_h * 0.5)

    geo = Georef(W, H, bbox=bbox, mpp=args.mpp)
    os.makedirs(args.out, exist_ok=True)

    features, rows, ids, areas = [], [], [], []
    for i, ppx in enumerate(polys_px, 1):
        pid = f"{args.id_prefix}{i:04d}"
        out_poly, metric = geo.metric_polygon(ppx)
        sqm = metric.area
        units = area_units(sqm)
        props = {"plot_id": pid, **units, "perimeter_m": round(metric.length, 2),
                 "vertices": len(ppx.exterior.coords) - 1, "status": "auto_detected"}
        features.append({"type": "Feature", "properties": props, "geometry": mapping(out_poly)})
        rows.append(props)
        ids.append(pid)
        areas.append(sqm)

    crs_name = "EPSG:4326" if bbox else "local-metric"
    with open(os.path.join(args.out, "plots.geojson"), "w") as f:
        json.dump({"type": "FeatureCollection", "name": "plots", "crs_note": crs_name,
                   "features": features}, f, indent=2)
    with open(os.path.join(args.out, "plots.csv"), "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wr.writeheader()
        wr.writerows(rows)
    cv2.imwrite(os.path.join(args.out, "annotated.png"),
                draw_annotated(img, polys_px, ids, areas))
    cv2.imwrite(os.path.join(args.out, "debug_lines.png"), line_mask)

    print(f"Detected {len(rows)} plots  |  total area {sum(areas):,.1f} m2")
    print(f"Outputs in: {os.path.abspath(args.out)}")
    print(f"{'ID':<11}{'m2':>10}{'marla':>9}{'kanal':>8}")
    for r in rows[:15]:
        print(f"{r['plot_id']:<11}{r['area_sqm']:>10}{r['area_marla']:>9}{r['area_kanal']:>8}")
    if len(rows) > 15:
        print(f"... and {len(rows) - 15} more (see plots.csv)")


if __name__ == "__main__":
    main()
