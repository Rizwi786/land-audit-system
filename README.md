# Land Audit System – Web App

Select an area on a map -> system downloads the map tiles for that area ->
detects plot boundaries -> unique IDs (PLOT-0001...) -> area in marla / kanal / m2 / sq ft / acre.

## Run (Windows)
Double-click `run.bat`, or:

    pip install -r requirements.txt
    python app.py
    # open http://127.0.0.1:5000

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
