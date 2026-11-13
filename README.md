# Hong Kong Map Services

QGIS plugin that adds a Lands Department basemap, with optional place-name labels, or selected CSDI datasets. The two actions are separate, so adding a dataset does not add another basemap.

Open **Web → Hong Kong Map Services**, then **Basemap**, **Datasets**, **Location Search**, **Download Files**, **Swipe and Split**, **Convert Coordinates**, **Convert FGDB / GPKG Files**, **Export SVG**, or **Cursor Coordinates**. **About** is last, under a separator. The toolbar button opens the same choices.

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

Names follow the language selected in that dialog. Where a dataset has no Simplified Chinese field, the Traditional Chinese name is shown. Turn on **View → Map Tips** to see the name and the other fields recorded for that dataset. Nothing is checked at first. **Add to map** stays off until at least one dataset is checked, including one hidden by the search box.

Labels use the first installed font for the language: Microsoft JhengHei, PingFang HK/TC, or Noto Sans HK/TC/CJK TC for Traditional Chinese; Microsoft YaHei, PingFang SC, or Noto Sans SC/CJK SC for Simplified Chinese; and Segoe UI, Helvetica, Noto Sans, or DejaVu Sans for English. Fonts differ by operating system, so labels can look slightly different on Windows, macOS, and Linux. On Linux, install a CJK font package if labels show missing glyphs.

## Convert Coordinates

**Convert Coordinates** transforms between HK1980 Grid and WGS84 geographic coordinates on this computer, using the same map projection as QGIS. The columns are Point ID, Northing (m), Easting (m), Latitude, Longitude, and Remarks. Northing, Easting, Latitude, and Longitude accept digits and one dot, and a cell with only a dot is cleared. A loaded cell such as `asdf` is cleared. Results appear on the colored line under the buttons: green when the action finished, amber when something was left out, and red when it did not. Point ID and Remarks stay free text. Type rows, or load a CSV, XLS, or XLSX file. A CSV may be UTF-8, Big5, or GB2312. Column names are matched without regard to letter case, so an older file with Easting before Northing still loads. **Download template** saves a sample `.xlsx` file in that column order. **Transform** fills the other side of each row and the line says Transform finished. Click the add icon on a row, or **Add all points**, to put the coordinates on the map in a layer named Converted points. The line says Added 1 point. for one point, and Added 3 points. when more than one is added. **Map** adds the point. **Fly**, in the next column, moves the map to that row and does not add a point. The line says when the map has moved. Both follow the Transform direction, and use the other coordinate pair only when the selected pair is incomplete. **Save XLSX** writes the table as Excel and leaves the previous file in place if the new file cannot be written.

## Convert FGDB / GPKG Files

**Convert FGDB / GPKG Files** copies point, line, and polygon layers between a File Geodatabase and a GeoPackage. **Convert** stays off until a source, a destination folder, and **GeoPackage** or **File Geodatabase** are set. The source and destination boxes change only through their buttons. A folder of File Geodatabases is converted one database at a time, and each database is written into the destination folder under its own name. **Add Northing, Easting, Latitude, and Longitude** and **Add Length and Area** are separate and stay off until checked. A point uses that point. A line or a polygon uses the centre of the geometry. Length is in metres, and area is in square metres, measured on the ground at that location. Web Mercator and latitude/longitude layers use an ellipsoidal measurement, so the distances do not grow with the map projection. **Settings** chooses the decimal places. **Skip tables without geometry** is off until checked, so those tables are copied unchanged. Layers are written feature by feature, so all the columns are not held in memory at once. Field types, widths, precision, aliases, and comments are kept where the destination format supports them. A new database is written beside the destination and moved into place only after the write succeeds. If the move fails, the previous File Geodatabase is put back. The line under the buttons says Processing... while the conversion runs. It is green when the conversion finishes, amber when a layer or a coordinate was left out, and red when nothing is written.

## Download Files

**Download Files** reads a URL column from a GeoPackage or a File Geodatabase and saves each file into a folder. Choose the layer, then the column. **Download** stays off until the source, the column, and the folder are set. A complete file already in the folder is skipped. A file that is not complete is never changed: each transfer is written to `name.part` and moved to the final name only when it finishes. An incomplete transfer also writes `name.part.json`, which records its address and server response. A `.part` file from an earlier run continues only when that record matches the current address; a `.part` file from another program is left alone and a new name is used. A matching record with a different ETag or length starts again from zero. A resume reply is checked against the bytes already in the `.part` file; if the server starts at a different offset, the file is downloaded again from the beginning. A name that differs only in letter case from an existing file gets `_2`, `_3`, and so on, so an existing file is not touched. A finished file is moved into place without replacing a file that appeared in the meantime. A transfer that receives no data for 60 seconds is abandoned. A finished `.zip` must open as a valid archive, and a finished `.tif` must open in GDAL. The archive and image checks read only a limited amount of data, so a very large file does not freeze the download. Any other file is complete when its size matches the server length. The line under the buttons says `Downloading 3 of 40: name.tif` while the transfer runs. The map window stays responsive. If a server's TLS handshake fails, the download stops at that row, reports the host and the reason, and does not leave a `.part` or `.part.json` file for that attempt. Closing the window stops after the current piece and leaves the `.part` file, so a later run can continue it. The transfer uses the proxy in QGIS network settings.

**Unzip** extracts every `.zip` that sits directly in the save folder. Each archive is unpacked into a temporary folder inside the save folder, and its files are then moved beside the zip under their own names, without the folders inside the archive. A file that is already there is replaced; this is intentional for a newer download from CSDI, and no backup or second copy is made. A folder that already exists is never walked, merged, or removed, and the zip files are kept. The whole archive is checked before anything is written: paths that leave the folder, alternate data streams, reserved Windows names, symbolic links, duplicate final names, and attempts to replace a zip, `.part`, or `.part.json` file are reported instead of written. Member count and free space are checked first, and a failed archive leaves no partial files. Unzip cannot be cancelled once it starts and must finish before the window closes; **Minimize** keeps working. The line under the buttons shows the zip and file being unpacked.

## Export SVG

**Export SVG** writes the current map view to one SVG for Adobe Illustrator. It does not write an Illustrator `.ai` file. Each visible point, line, and polygon layer becomes its own layer, in the same top-to-bottom order as the map. Turn a layer off, or turn its group off, before exporting if it should not appear. Consecutive raster or vector-tile layers, including Imagery, Topographic Map, Vector Map, and raster place-name labels, are combined into one 300 dpi picture at their place in that order. A map with no raster layer does not embed a blank picture. Symbol colour, opacity, transparent fills, simple widths, and dash patterns are kept. Canvas rotation is applied to the drawing and to the north arrow. The page is A3 landscape, 420 mm by 297 mm, when the map is wider than it is tall, and A3 portrait, 297 mm by 420 mm, when the map is taller. The map keeps its shape inside the page. **Include scale bar** and **Include north arrow** are on until unchecked. The scale bar uses the ground width at the centre of the view. The line under the buttons says Processing... while the export runs.

## Cursor Coordinates

**Cursor Coordinates** opens a dock. Moving the mouse over the map shows HK1980 Grid northing and easting, and WGS84 latitude and longitude. Under each latitude and longitude, the same line ends with `(DMS)`, for example `22° 21' 13.32" (DMS)`. Those rows use the same spacing as Northing and Easting. **Settings** chooses the decimal places. HK1980 starts at 0 decimal places, rounded. Latitude and longitude start at 6. **Hold (Ctrl+Alt+Shift+H)** keeps the numbers still while the mouse moves. Ctrl+Shift+H stays QGIS Hide All Layers, and Ctrl+Alt+H stays Processing History. The same shortcut works while the map has focus. One press stays on until the next press, and the status says **Held**. **Copy** on each row copies that number only: northing and easting without `m`, latitude and longitude without the degree sign, and a DMS row as `22° 21' 13.32"` without the `(DMS)` word. Close the dock, or choose the menu item again, to hide it. The dock uses the same local calculation as **Convert Coordinates**.

## Location Search

**Location Search** sits near the top of the menu, above **Download Files**. It looks up a place, address, or facility through the Lands Department location service. Every match is listed, with the English and Chinese lines both visible. Click one to fly the map there and drop a point-of-interest marker on a layer named Location search. Earlier markers stay on the map. If the search fails, the message stays on the dialog.

## Swipe and Split

**Swipe and Split** sits under **Download Files**. It compares a layer or a whole group on each side. The mode is **Swipe** by default. **Split** is the other mode, in the same dock. The mode radios stay off while the tool is active. Deactivate it, change mode, then activate again.

In Swipe mode, vertical splits left and right and horizontal splits top and bottom. Both sides keep the same extent. Drag the arrow handle in the middle of the divider, use the position slider, or choose 0%, 25%, 50%, 75%, or 100%. The slider and the percent move with the handle. The dock opens at the top of the right-hand side so the mode description stays visible.

In Split mode, a second map opens in the same project. Vertical places the maps side by side. Horizontal stacks them. The two areas stay at 50/50, so the position control is visible and locked at 50%. Pan or zoom on either map and the other follows.

**Swap Layers** exchanges the two sides. **Refresh Layers** reads the legend again. Opacity fades only the chosen side, and the other side stays fully opaque, even when both sides draw the same layer. Horizontal direction names those sides Top and Bottom.

## About

**About** is the last menu item, under a separator. The name and version are centered. Info describes the basemaps, datasets, location search, coordinate conversion, file conversion, file downloads, archive unzipping, SVG export, cursor readout, and Swipe and Split, then lists the developer, issue tracker, and source code. The License tab starts at the GNU General Public License, in a fixed-width font so that heading stays centered.

## Install

The minimum QGIS version is 3.28. Tested with QGIS 3.34.13 and QGIS 4.2.3. `qgisMaximumVersion` is 4.99, so the same plugin remains listed after a later QGIS upgrade.

Copy this folder to the QGIS profile plugins directory:

```
%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\hkmapservices
```

Restart QGIS, then enable **Hong Kong Map Services** in **Plugins → Manage and Install Plugins**. On QGIS 4 the profile folder uses `QGIS4` instead of `QGIS3`.

## Network, proxies, and timeouts

All requests follow **Settings → Options → Network**: raster basemaps, vector basemaps and labels, CSDI datasets, Location Search, and Download Files. HTTP and SOCKS5 proxies are supported, including a username and password configured in QGIS.

When a proxy is enabled, add `http://127.0.0.1` to **No proxy for (URLs starting with)**. QGIS reads vector tiles through a small helper on the local computer, and without this entry it sends that loopback request to the proxy, so the vector basemap cannot load.

The plugin limits service checks to 30 seconds when it opens a vector-tile service or a dataset, and 30 seconds for each HK80 tile request. If the service does not answer, a warning or error appears in the QGIS message bar instead of leaving QGIS waiting. Location Search has its own 30-second limit and shows the message in the dialog. A file download is abandoned after 60 seconds without data.

XYZ basemap tiles and vector tiles already on the map are fetched by QGIS in the background. If they fail after the layer was added, the reason is in **View → Panels → Log Messages → Network**, where QGIS's own network timeout (default 60 seconds) applies.

After changing proxy settings, reload the vector basemap, or restart QGIS, so saved layers and the local helper use the new settings.

## Troubleshooting

**Linux reports `SSL handshake failed` in Download Files.** Some download hosts, including `open.hkmapservice.gov.hk`, send an incomplete TLS certificate chain. Windows fills in the missing intermediate certificate automatically, but Qt/OpenSSL on Linux does not. The download stops at the first TLS failure and does not leave a `.part` or `.part.json` file for it.

On Debian or Ubuntu, run the one-time script from the source repository with sudo:

```
sudo scripts/install-hkms-intermediate-ca.sh
```

Or download it from [scripts/install-hkms-intermediate-ca.sh](https://github.com/curtis18/hkmapservices/blob/main/scripts/install-hkms-intermediate-ca.sh). The script downloads the Hongkong Post intermediate certificate, checks it against the existing root, installs it in the system trust store, and verifies the download host. Restart QGIS afterwards. The script is not included in the plugin zip. The intermediate certificate is valid until 2032; if the host changes its certificate authority, update the script or ask the data provider to send the full chain.

## Attribution

Map from Lands Department.

Data from CSDI.

The imagery layer also records: Aerial Photograph from Lands Department; Guangdong-Hong Kong-Macao Greater Bay Area Satellite Image 2024 (Satellite Images from Landsat); Pan-Pearl River Delta Satellite Image 2016 (Satellite Images from Landsat); Globe Satellite Image 2001 (Satellite Images from MODIS).

## Tile grid

Hong Kong 1980 raster tiles are 256×256 PNG images. Their z/x/y index uses the Lands Department grid: origin `(-4786700, 8353100)`, level-0 cell `78271.516964 × 512` metres. Tile `11/287/385` is central Hong Kong. The level-11 cell is about 38.219 m across one of the 512 reference pixels; each PNG pixel covers twice that distance.

WGS 84 raster tiles use the normal XYZ provider. HK80 raster tiles are opened through a GDAL TMS description stored in the QGIS profile under `hkmapservices`.

Vector tiles are read through a local proxy started by the plugin. Lands Department advertises a tilemap URL that currently returns 404, and QGIS then refuses the service. The proxy removes that entry and forwards the tiles using the QGIS proxy settings. Keep the plugin enabled to reopen a saved vector basemap.

## Development checks

```
python tests\test_catalog.py
python tests\test_transform.py
python tests\test_locations.py
python tests\test_compare.py
python tests\test_download.py
python tests\test_files.py
python tests\test_compat.py
python tests\test_vectorproxy.py
```

Layer checks use `tests\smoke_qgis.py` with the QGIS 3.34.13 launcher `bin\python-qgis-ltr.bat` and the QGIS 4.2.3 launcher.

## License

GPL-2.0-or-later. See [LICENSE](LICENSE).
