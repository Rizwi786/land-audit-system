# Land Audit System – Web App

Select an area on a map -> system downloads the map tiles for that area ->
detects plot boundaries -> unique IDs (PLOT-0001...) -> area in marla / kanal / m2 / sq ft / acre.

## Run (Windows)
Double-click `run.bat`, or:

    pip install -r requirements.txt
    python app.py
    # open http://127.0.0.1:5000

## All of Pakistan, offline and fast (recommended)
Do this ONCE. Afterwards a rectangle in ANY city works offline, no per-city steps:

    pip install -r requirements.txt
    python build_pakistan_tiles.py --download          (downloads the Geofabrik zip, then builds the tiles)
    python build_pakistan_tiles.py                     (if pakistan-latest-free.shp.zip is already in this folder)
    python build_pakistan_tiles.py --bbox 72.85 33.55 73.25 33.85     (only one region: faster, smaller)

It splits buildings / land use / other areas into ~11 km tiles in `data/tiles/`. The app then loads only the
tiles under the rectangle (about 0.1 s the first time, a few ms after). Update later by re-running with a
fresh download. Do NOT commit `data/tiles/` to GitHub (add `data/tiles/` to .gitignore): it is large.
The whole country is too big for Vercel; run it on your computer or a VPS (a server with a disk).
Tests: `python test_tiles.py`

## Local data for ONE area (older, simple way)
The live OSM modes depend on public Overpass servers that are often busy or rate-limited.
Instead, keep your city's OSM data locally:
1. Download a shapefile zip, e.g. Geofabrik: https://download.geofabrik.de/asia/pakistan.html -> `pakistan-latest-free.shp.zip`
   (or a small city extract from BBBike if it offers the Shapefile format).
2. Run (WEST SOUTH EAST NORTH; this box covers Islamabad):

       python prepare_local_data.py pakistan-latest-free.shp.zip --bbox 72.85 33.55 73.25 33.85

   This writes `data/buildings.geojson`, `data/landuse.geojson` and `data/other.geojson`
   (other = every other polygon layer: shops/POIs, parking, transport, places of worship...; each shape
   shows its layer, e.g. "pois: supermarket"). Use `--only other` to (re)build just the extra layers.
3. Restart the app: "Local data: buildings" is selected automatically. Instant, offline.
4. For Vercel: commit the `data` folder (keep files small, GitHub limit is 100 MB per file; clip to your demo area).
Note: this is the same OSM content, just reliable. Where OSM has no plot/building polygons, trace or import them.
Test: `python test_local.py`

## Download the data of a selected area
Draw a rectangle (or search a city and press "Use the current map view as the area"), choose GeoJSON or
Shapefile, press "Download area data (ZIP)". The ZIP has buildings / landuse / other files (each shape with
area in m2, sq ft, marla, kanal, acre) plus a README with the OpenStreetMap attribution (ODbL).
Needs the local data files (see above). Online (Vercel) downloads are limited to about 4 MB.
Test: `python test_export.py`

## Main workflow (works on ANY map)
1. **Trace a plot**: click the polygon tool (⬠, top-left of map), click each corner, click the first point to finish.
   The plot gets a unique ID (PLOT-0001...) and its area in marla / kanal / m2 / sq ft / acre.
2. **Edit**: click the edit tool (pencil), drag corner points, press Save. Area is recalculated.
3. **Import**: "Import file..." accepts GeoJSON, KML, or a Shapefile .zip (with .shp .shx .dbf .prj).
   Shapefiles in UTM / local projections are converted using the .prj file. A plot-number column
   (plot_no, plot, id, name...) is shown as the label.
4. **Auto-detect** (optional): draw a rectangle (▭) then "Detect plots in rectangle".
5. Everything is saved in the browser automatically (survives refresh). Export GeoJSON / CSV.

## Search (top-right of the map)
Type and press Enter:
- a **place / address / sector** (e.g. `G-9/1 Islamabad`) - uses OpenStreetMap Nominatim
- a **plot ID or label** from your own plots (e.g. `PLOT-0003`, `A-12`) - instant, offline
- **coordinates** as `33.6990, 73.0290`
Place search needs internet and is limited to about 1 request per second (OSM policy).

## Use (auto-detect details)
1. Pick a map source (OpenStreetMap, Esri Satellite, or your own tile URL).
2. Click the rectangle tool (top-left of the map) and draw over the area to audit.
3. Click "Detect plots". Polygons, IDs and areas appear on the map and in the table.
4. Click a row to zoom to a plot; use ✕ to remove a wrong polygon.
5. Export GeoJSON / CSV.

Tip: keep the selected area small (a block or a few streets). The app uses the
highest zoom possible (max 19); a large area forces a lower zoom and weak detection.

## Recommended: OSM vector mode (exact boundaries)
Choose Detection mode "OSM vector: buildings" or "land-use areas". The app queries
OpenStreetMap's polygons (Overpass API) for the selected rectangle and returns the exact
drawn shapes - no image processing. Area limit: 2 km2 per request.
Image modes below are only for maps that show plot lines (layout plans) and are weak on satellite photos.
Note: OSM buildings are building footprints, not legal plot boundaries.

## Settings
- Detection mode "Dark boundary lines": maps with black/dark plot lines (layout plans).
- Detection mode "Colour edges": OSM / satellite maps where plots differ by colour.
- Gap sealing: raise it if two plots merge into one.
- Min / Max plot area: filters out tiny shapes (text, symbols) and huge blocks.

## Custom map (client's layout plan)
If the client's layout map is published as tiles (XYZ) or via QGIS/GeoServer,
choose "Custom tile URL" and paste e.g. https://server/tiles/{z}/{x}/{y}.png

## Tests
`python test_import.py` tests file import + area measuring; `python test_vector.py` tests vector mode (exact areas); `python test_mock.py` runs an offline test with synthetic tiles (known plot areas).
Results: ~2% max area error (lines mode), ~6% (colour-edge mode) at 0.25 m/pixel.

## Limits
- Tiles are raster images: boundaries are only as accurate as the pixels (at zoom 19 ~0.25-0.3 m/px).
- Plots with no visible line or colour difference cannot be separated automatically.
- OpenStreetMap's tile servers are for light use only. For production use a licensed
  tile provider or the client's own map server.
- Areas are approximate, not survey-grade.
