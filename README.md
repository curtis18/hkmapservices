# Hong Kong Map Services

QGIS plugin that adds a Lands Department basemap, with optional place-name labels, or selected CSDI datasets. The two actions are separate, so adding a dataset does not add another basemap.

Open **Web → Hong Kong Map Services**, then **Basemap**, **Datasets**, **Location Search**, **Swipe and Split**, **Convert Coordinates**, or **Cursor Coordinates**. **About** is last, under a separator. The toolbar button opens the same choices.

## Basemap

Choose one basemap and one coordinate system:

| Basemap | What it is | Native tiles |
| --- | --- | --- |
| Imagery Map | Aerial photograph and satellite image PNG tiles | HK1980: 7–20, WGS 84: 0–20 |
| Topographic Map | Vector map drawn as raster PNG tiles | 10–20 |
| Vector Map | Vector tiles with the Lands Department style | 9–15 |

Imagery Map is the default. The default coordinate system is WGS 84 Web Mercator (EPSG:3857). Hong Kong 1980 Grid (EPSG:2326) is also available. The service calls the Web Mercator endpoint WGS84.

Place-name labels can be off, or English, Traditional Chinese, or Simplified Chinese. Traditional Chinese is the default. The label type follows the basemap, and you can change it:

- Imagery Map and Topographic Map start with raster labels (Map Label API).
- Vector Map starts with vector labels (Vector Map Label API).

The zoom ranges above are the last tiles Lands Department publishes. Zooming in further keeps those tiles on the map and enlarges them.

## Datasets

**Datasets** adds only the layers you check, in a group named Hong Kong datasets. Polygons are drawn under lines, and lines under points.

The list is alphabetical and can be searched. It scrolls as more datasets are added. Each row has the checkbox and a **CSDI** link beside the name. The link opens that dataset’s page on the CSDI geoportal, for example `https://portal.csdi.gov.hk/geoportal/?lang=en&datasetId=td_rcd_1671693191724_92214` for Traffic Detectors. The dataset list itself is `datasets.json`, so a new dataset is one object in that file: title, service id, CRS, geometry, marker, color, and field names.

- Barbecue sites (EPSG:2326)
- District boundary (EPSG:2326)
- Fitness rooms (EPSG:2326)
- Libraries (EPSG:2326)
- Outdoor fitness equipment (EPSG:4326)
- Traffic detectors (EPSG:2326)
- Water dispensers (EPSG:4326)

Names follow the language selected in that dialog. Turn on **View → Map Tips** to see the name and the other fields recorded for that dataset. Nothing is checked at first. **Add to map** stays off until at least one dataset is checked, including one hidden by the search box.

## Convert Coordinates

**Convert Coordinates** transforms between HK1980 Grid and WGS84 geographic coordinates on this computer, using the same map projection as QGIS. The columns are Point ID, Northing (m), Easting (m), Latitude, Longitude, and Remarks. Northing, Easting, Latitude, and Longitude accept digits and one dot, and a cell with only a dot is cleared. A loaded cell such as `asdf` is cleared. Results appear on the colored line under the buttons: green when the action finished, amber when something was left out, and red when it did not. Point ID and Remarks stay free text. Type rows, or load a CSV, XLS, or XLSX file. A CSV may be UTF-8, Big5, or GB2312. Column names are matched without regard to letter case, so an older file with Easting before Northing still loads. **Download template** saves a sample `.xlsx` file in that column order. **Transform** fills the other side of each row and the line says Transform finished. Click the add icon on a row, or **Add all points**, to put the coordinates on the map in a layer named Converted points. The line says Added 1 point. for one point, and Added 3 points. when more than one is added. **Map** adds the point. **Fly**, in the next column, moves the map to that row and does not add a point. The line says when the map has moved. Both follow the Transform direction, and use the other coordinate pair only when the selected pair is incomplete. **Save XLSX** writes the table as Excel and leaves the previous file in place if the new file cannot be written.

## Cursor Coordinates

**Cursor Coordinates** opens a dock. Moving the mouse over the map shows HK1980 Grid northing and easting, and WGS84 latitude and longitude. Under each latitude and longitude, the same line ends with `(DMS)`, for example `22° 21' 13.32" (DMS)`. Those rows use the same spacing as Northing and Easting. **Settings** chooses the decimal places. HK1980 starts at 0 decimal places, rounded. Latitude and longitude start at 6. **Hold (Ctrl+Alt+Shift+H)** keeps the numbers still while the mouse moves. Ctrl+Shift+H stays QGIS Hide All Layers, and Ctrl+Alt+H stays Processing History. The same shortcut works while the map has focus. One press stays on until the next press, and the status says **Held**. **Copy** on each row copies that number only: northing and easting without `m`, latitude and longitude without the degree sign, and a DMS row as `22° 21' 13.32"` without the `(DMS)` word. Close the dock, or choose the menu item again, to hide it. The dock uses the same local calculation as **Convert Coordinates**.

## Location Search

**Location Search** sits above **Convert Coordinates**. It looks up a place, address, or facility through the Lands Department location service. Every match is listed, with the English and Chinese lines both visible. Click one to fly the map there and drop a point-of-interest marker on a layer named Location search. Earlier markers stay on the map. If the search fails, the message stays on the dialog.

## Swipe and Split

**Swipe and Split** sits under **Location Search**. It compares a layer or a whole group on each side. The mode is **Swipe** by default. **Split** is the other mode, in the same dock. The mode radios stay off while the tool is active. Deactivate it, change mode, then activate again.

In Swipe mode, vertical splits left and right and horizontal splits top and bottom. Both sides keep the same extent. Drag the arrow handle in the middle of the divider, use the position slider, or choose 0%, 25%, 50%, 75%, or 100%. The slider and the percent move with the handle. The dock opens at the top of the right-hand side so the mode description stays visible.

In Split mode, a second map opens in the same project. Vertical places the maps side by side. Horizontal stacks them. The two areas stay at 50/50, so the position control is visible and locked at 50%. Pan or zoom on either map and the other follows.

**Swap Layers** exchanges the two sides. **Refresh Layers** reads the legend again. Opacity fades only the chosen side, and the other side stays fully opaque, even when both sides draw the same layer. Horizontal direction names those sides Top and Bottom.

## About

**About** is the last menu item, under a separator. The name and version are centered. Info describes the basemaps, datasets, location search, coordinate conversion, cursor readout, and Swipe and Split, then lists the developer, issue tracker, and source code. The License tab starts at the GNU General Public License, in a fixed-width font so that heading stays centered.

## Install

The minimum QGIS version is 3.28. Tested with QGIS 3.34.13 and QGIS 4.2.3. `qgisMaximumVersion` is 4.99, so the same plugin remains listed after a later QGIS upgrade.

Copy this folder to the QGIS profile plugins directory:

```
%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\hkmapservices
```

Restart QGIS, then enable **Hong Kong Map Services** in **Plugins → Manage and Install Plugins**. On QGIS 4 the profile folder uses `QGIS4` instead of `QGIS3`.

## Attribution

Map from Lands Department.

Data from CSDI.

The imagery layer also records: Aerial Photograph from Lands Department; Guangdong-Hong Kong-Macao Greater Bay Area Satellite Image 2024 (Satellite Images from Landsat); Pan-Pearl River Delta Satellite Image 2016 (Satellite Images from Landsat); Globe Satellite Image 2001 (Satellite Images from MODIS).

## Tile grid

Hong Kong 1980 raster tiles are 256×256 PNG images. Their z/x/y index uses the Lands Department grid: origin `(-4786700, 8353100)`, level-0 cell `78271.516964 × 512` metres. Tile `11/287/385` is central Hong Kong. The level-11 cell is about 38.219 m across one of the 512 reference pixels; each PNG pixel covers twice that distance.

WGS 84 raster tiles use the normal XYZ provider. HK80 raster tiles are opened through a GDAL TMS description stored in the QGIS profile under `hkmapservices`.

Vector tiles are read through a local proxy started by the plugin. Lands Department advertises a tilemap URL that currently returns 404, and QGIS then refuses the service. The proxy removes that entry and forwards the tiles. Keep the plugin enabled to reopen a saved vector basemap.

## Development checks

```
python tests\test_catalog.py
python tests\test_transform.py
python tests\test_locations.py
python tests\test_compare.py
```

Layer checks use `tests\smoke_qgis.py` with the QGIS 3.34.13 launcher `bin\python-qgis-ltr.bat` and the QGIS 4.2.3 launcher.

## License

GPL-2.0-or-later. See [LICENSE](LICENSE).
