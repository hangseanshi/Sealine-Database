import os, json, pyodbc

SCRIPT_DIR = r'C:\Users\hangs\OneDrive\GitHub\Sealine-Database'
TRACK_NUMBER = '00010987'
CONTAINER    = 'PCIU2950770'

conn = pyodbc.connect('DRIVER={ODBC Driver 17 for SQL Server};SERVER=ushou102-exap1;DATABASE=searates;UID=sean;PWD=4peiling;')
cursor = conn.cursor()

# Include soft-deleted events — shipment is DELIVERED/archived
cursor.execute("""
    SELECT e.Container_NUMBER, TRY_CAST(e.Order_Id AS INT) AS Seq,
           COALESCE(f.name, l.Name) AS LocationName,
           COALESCE(TRY_CAST(f.Lat AS FLOAT), TRY_CAST(l.Lat AS FLOAT)) AS Lat,
           COALESCE(TRY_CAST(f.Lng AS FLOAT), TRY_CAST(l.Lng AS FLOAT)) AS Lng,
           e.Date, e.Actual, e.Description, h.Sealine_Code, h.Status
    FROM Sealine_Container_Event e
    INNER JOIN Sealine_Header h ON e.TrackNumber = h.TrackNumber
    LEFT JOIN Sealine_Facilities f ON e.TrackNumber = f.TrackNumber AND e.Facility = f.Id
    LEFT JOIN Sealine_Locations  l ON e.TrackNumber = l.TrackNumber AND e.Location  = l.Id
    WHERE e.TrackNumber = ? AND e.Container_NUMBER = ?
    ORDER BY TRY_CAST(e.Order_Id AS INT)
""", (TRACK_NUMBER, CONTAINER))
rows = cursor.fetchall()
conn.close()

containers   = {CONTAINER: []}
all_coords   = []
sealine_code = rows[0][8] if rows else ''
status       = rows[0][9] if rows else ''

# Deduplicate by (seq, lat, lng)
seen = set()
for r in rows:
    cnum, seq, loc, lat, lng, date, actual, desc, _, _ = r
    if lat is None or lng is None:
        continue
    key = (seq, round(lat, 4), round(lng, 4))
    if key in seen:
        continue
    seen.add(key)
    dt_str = date.strftime('%Y-%m-%d') if date else ''
    containers[cnum].append({
        'seq': seq or 0, 'loc': loc or 'Unknown',
        'lat': lat, 'lng': lng, 'date': dt_str,
        'actual': bool(actual), 'desc': desc or ''
    })
    all_coords.append([lat, lng])

print(f'Events plotted: {len(containers[CONTAINER])}')
for e in containers[CONTAINER]:
    print(f'  Seq={e["seq"]:>3}  {e["loc"]:<40} ({e["lat"]:>8.3f},{e["lng"]:>8.3f})  {e["date"]}  {"Actual" if e["actual"] else "Est"}  {e["desc"]}')

avg_lat = sum(c[0] for c in all_coords) / len(all_coords)
avg_lng = sum(c[1] for c in all_coords) / len(all_coords)

containers_json = json.dumps(containers)
track_js   = json.dumps(TRACK_NUMBER)
sealine_js = json.dumps(sealine_code)
status_js  = json.dumps(status + ' (archived)')

html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Route: """ + TRACK_NUMBER + """ / """ + CONTAINER + """</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
  html,body{margin:0;padding:0;height:100%;font-family:Arial,sans-serif;background:#0d1f33;color:#cde;}
  #header{padding:8px 16px;background:#0f2744;border-bottom:2px solid #2e5f8a;display:flex;align-items:center;gap:14px;flex-wrap:wrap;}
  #header h1{font-size:15px;color:#7eb8e6;margin:0;white-space:nowrap;}
  #header span{font-size:11px;color:#aac8e0;}
  #map{height:calc(100vh - 44px);}
  .popup-box{font-size:12px;min-width:220px;line-height:1.6;}
  .popup-title{font-weight:bold;font-size:13px;color:#1a3a6c;border-bottom:1px solid #ccc;padding-bottom:4px;margin-bottom:6px;}
  .popup-row{display:flex;justify-content:space-between;gap:8px;}
  .popup-label{color:#555;font-weight:bold;}
  .popup-val{color:#222;text-align:right;}
  .actual{color:#22aa55;font-weight:bold;}
  .estimated{color:#cc8800;font-weight:bold;}
</style>
</head>
<body>
<div id="header">
  <h1>&#128230; """ + TRACK_NUMBER + """ &nbsp;|&nbsp; """ + CONTAINER + """</h1>
  <span id="hdr-info"></span>
</div>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://unpkg.com/leaflet-polylinedecorator@1.6.0/dist/leaflet.polylineDecorator.js"></script>
<script>
const TRACK      = """ + track_js + """;
const SEALINE    = """ + sealine_js + """;
const STATUS     = """ + status_js + """;
const CONTAINERS = """ + containers_json + """;

document.getElementById('hdr-info').textContent =
  SEALINE + ' \u2022 ' + STATUS;

const map = L.map('map', {center: [""" + str(avg_lat) + """, """ + str(avg_lng) + """], zoom: 3, preferCanvas: false});
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
  {attribution: '&copy; OpenStreetMap &copy; CARTO', maxZoom: 18}).addTo(map);

const COLORS = ['#4ec9ff','#ff6b6b','#69ff47','#ffe119','#f58231','#b96ff5','#42d4f4','#f032e6'];
let ci = 0;

Object.entries(CONTAINERS).forEach(([cnum, events]) => {
  const col = COLORS[ci++ % COLORS.length];
  const pts = events.map(e => [e.lat, e.lng]);

  if (pts.length > 1) {
    // Draw full polyline for visual continuity
    L.polyline(pts, {color: col, weight: 3, opacity: 0.85}).addTo(map);
    // Add an arrow to every individual segment
    for (let i = 0; i < pts.length - 1; i++) {
      const seg = L.polyline([pts[i], pts[i+1]], {color: col, weight: 3, opacity: 0}).addTo(map);
      L.polylineDecorator(seg, {
        patterns: [{
          offset: '50%', repeat: 0,
          symbol: L.Symbol.arrowHead({
            pixelSize: 14, polygon: false,
            pathOptions: {color: col, fillOpacity: 1, weight: 2}
          })
        }]
      }).addTo(map);
    }
  }

  events.forEach((e, i) => {
    const isFirst = i === 0, isLast = i === events.length - 1;
    const marker = L.circleMarker([e.lat, e.lng], {
      radius: isFirst || isLast ? 10 : 6,
      fillColor: isFirst ? '#22dd66' : isLast ? '#ff4444' : col,
      color: '#fff', weight: 2, opacity: 1,
      fillOpacity: isFirst || isLast ? 1 : 0.85
    }).addTo(map);

    const badge = e.actual
      ? '<span class="actual">&#10003; Actual</span>'
      : '<span class="estimated">&#9711; Estimated</span>';

    marker.bindPopup(
      '<div class="popup-box">' +
      '<div class="popup-title">&#128230; ' + TRACK + '</div>' +
      '<div class="popup-row"><span class="popup-label">Container</span><span class="popup-val">' + cnum + '</span></div>' +
      '<div class="popup-row"><span class="popup-label">Order ID</span><span class="popup-val">' + e.seq + '</span></div>' +
      '<div class="popup-row"><span class="popup-label">Location</span><span class="popup-val">' + e.loc + '</span></div>' +
      '<div class="popup-row"><span class="popup-label">Date</span><span class="popup-val">' + e.date + ' ' + badge + '</span></div>' +
      '<div class="popup-row"><span class="popup-label">Event</span><span class="popup-val">' + e.desc + '</span></div>' +
      '</div>'
    );
  });
});

const legend = L.control({position: 'bottomleft'});
legend.onAdd = () => {
  const d = L.DomUtil.create('div', '');
  d.style.cssText = 'background:#0f2744;padding:8px 12px;border-radius:6px;border:1px solid #2e5f8a;font-size:11px;color:#cde;line-height:1.8;';
  d.innerHTML = '<b style="color:#7eb8e6">Legend</b><br>' +
    '<span style="color:#22dd66">&#9679;</span> Origin &nbsp;' +
    '<span style="color:#ff4444">&#9679;</span> Destination<br>' +
    '&#10230; Arrow = travel direction';
  return d;
};
legend.addTo(map);
</script>
</body>
</html>"""

filename = 'route_00010987_PCIU2950770.html'
filepath = os.path.join(SCRIPT_DIR, filename)
with open(filepath, 'w', encoding='utf-8') as f:
    f.write(html)
print(f'\nSaved: /files/{filename}')
