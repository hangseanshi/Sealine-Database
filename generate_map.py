import csv
import json

# Read CSV data
csv_file = r'C:\Users\hangs\OneDrive\GitHub\Sealine-Database\warzone_shipments.csv'
rows = []
with open(csv_file, 'r', encoding='utf-8') as f:
    reader = csv.reader(f)
    headers = next(reader)
    for row in reader:
        rows.append(row)

# Group by war zone
zone_data = {}
for row in rows:
    zone = row[9]
    if zone not in zone_data:
        zone_data[zone] = []
    zone_data[zone].append(row)

# Prepare marker data
markers = []
for row in rows:
    try:
        lat = float(row[12])
        lng = float(row[13])
        markers.append({
            'lat': lat,
            'lng': lng,
            'title': row[5],  # Container Number
            'info': {
                'trackNumber': row[0],
                'sealineCode': row[1],
                'sealineName': row[2],
                'status': row[3],
                'containerNumber': row[5],
                'sizeType': row[6],
                'isoCode': row[7],
                'location': row[8],
                'warZone': row[9],
                'eventDate': row[10],
                'containerStatus': row[11],
            }
        })
    except (ValueError, IndexError):
        pass

# Define war zones
war_zones = {
    'Red Sea': {
        'coords': [[12, 32], [12, 45], [28, 45], [28, 32]],
        'color': '#FF0000',
        'description': 'Red Sea War Zone'
    },
    'Gulf of Aden': {
        'coords': [[10, 42], [10, 52], [16, 52], [16, 42]],
        'color': '#FF4500',
        'description': 'Gulf of Aden War Zone'
    },
    'Persian Gulf': {
        'coords': [[22, 48], [22, 60], [30, 60], [30, 48]],
        'color': '#FFA500',
        'description': 'Persian Gulf War Zone'
    },
    'Eastern Mediterranean': {
        'coords': [[29, 28], [29, 37], [38, 37], [38, 28]],
        'color': '#8B0000',
        'description': 'Eastern Mediterranean War Zone'
    },
}

# Save HTML file
html_file = r'C:\Users\hangs\OneDrive\GitHub\Sealine-Database\warzone_map.html'

with open(html_file, 'w', encoding='utf-8') as f:
    f.write('''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sealine Database - War Zone Container Tracking Map</title>
    <script src="https://maps.googleapis.com/maps/api/js?key=AIzaSyCzKYTkGxdY0JnVQdqzxLW_5w3XQGX7TI8&libraries=markerclusterer"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
        }

        .container {
            display: flex;
            height: 100vh;
            gap: 10px;
            padding: 10px;
        }

        #map {
            flex: 1;
            border-radius: 8px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
        }

        .sidebar {
            width: 280px;
            background: white;
            border-radius: 8px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
            overflow-y: auto;
            padding: 15px;
        }

        .sidebar h2 {
            color: #1f4788;
            font-size: 18px;
            margin-bottom: 15px;
            border-bottom: 3px solid #667eea;
            padding-bottom: 10px;
        }

        .legend-item {
            margin-bottom: 12px;
            padding: 10px;
            border-left: 4px solid;
            border-radius: 4px;
            background-color: #f5f5f5;
            cursor: pointer;
            transition: all 0.3s;
        }

        .legend-item:hover {
            transform: translateX(5px);
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
        }

        .legend-item.red-sea {
            border-color: #FF0000;
        }

        .legend-item.gulf-aden {
            border-color: #FF4500;
        }

        .legend-item.persian-gulf {
            border-color: #FFA500;
        }

        .legend-item.eastern-med {
            border-color: #8B0000;
        }

        .legend-item-title {
            font-weight: bold;
            color: #1f4788;
            font-size: 13px;
        }

        .legend-item-count {
            font-size: 12px;
            color: #666;
            margin-top: 4px;
        }

        .info-window {
            max-width: 300px;
            font-size: 12px;
        }

        .info-window h3 {
            color: #1f4788;
            font-size: 14px;
            margin-bottom: 8px;
            border-bottom: 2px solid #667eea;
            padding-bottom: 5px;
        }

        .info-row {
            display: flex;
            justify-content: space-between;
            margin-bottom: 4px;
            padding: 3px 0;
        }

        .info-label {
            font-weight: bold;
            color: #333;
        }

        .info-value {
            color: #666;
            word-break: break-word;
            text-align: right;
            max-width: 150px;
        }

        .stats {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 12px;
            border-radius: 6px;
            margin-bottom: 15px;
            font-size: 12px;
        }

        .stats-row {
            margin-bottom: 6px;
            display: flex;
            justify-content: space-between;
        }

        .stats-row strong {
            color: #fff;
        }

        .total-containers {
            font-size: 18px;
            font-weight: bold;
            text-align: center;
            margin-top: 10px;
            padding-top: 10px;
            border-top: 1px solid rgba(255, 255, 255, 0.3);
        }
    </style>
</head>
<body>
    <div class="container">
        <div id="map"></div>

        <div class="sidebar">
            <h2>🌍 War Zones</h2>

            <div class="stats">
                <div class="stats-row">
                    <span>Total Containers:</span>
                    <strong>''' + str(len(markers)) + '''</strong>
                </div>
                <div class="stats-row">
                    <span>Total Shipments:</span>
                    <strong>''' + str(len(set(row[0] for row in rows))) + '''</strong>
                </div>
                <div class="stats-row">
                    <span>Status:</span>
                    <strong>IN TRANSIT</strong>
                </div>
            </div>

            <legend id="legend"></legend>
        </div>
    </div>

    <script>
        // Initialize map
        const map = new google.maps.Map(document.getElementById('map'), {
            zoom: 5,
            center: { lat: 20, lng: 40 },
            mapTypeId: 'terrain',
            styles: [
                {
                    "featureType": "water",
                    "elementType": "geometry",
                    "stylers": [
                        {"color": "#e9e9e9"},
                        {"lightness": 17}
                    ]
                },
                {
                    "featureType": "landscape",
                    "elementType": "geometry",
                    "stylers": [
                        {"color": "#f5f5f5"},
                        {"lightness": 20}
                    ]
                }
            ]
        });

        // War zone data
        const warZones = ''' + json.dumps(war_zones) + ''';

        // Draw war zones on map
        Object.entries(warZones).forEach(([zoneName, zoneData]) => {
            const coords = zoneData.coords.map(([lat, lng]) => ({lat, lng}));

            const polygon = new google.maps.Polygon({
                paths: coords,
                strokeColor: zoneData.color,
                strokeOpacity: 0.8,
                strokeWeight: 3,
                fillColor: zoneData.color,
                fillOpacity: 0.15,
                map: map,
                title: zoneName
            });

            // Add zone label
            const bounds = new google.maps.LatLngBounds();
            coords.forEach(coord => bounds.extend(coord));
            const center = bounds.getCenter();

            const label = new google.maps.Marker({
                position: center,
                map: map,
                label: {
                    text: zoneName.toUpperCase(),
                    color: zoneData.color,
                    fontSize: '13px',
                    fontWeight: 'bold'
                },
                icon: {
                    path: google.maps.SymbolPath.CIRCLE,
                    scale: 0,
                    strokeColor: 'transparent'
                }
            });
        });

        // Marker data
        const markers = ''' + json.dumps(markers) + ''';

        // Create markers with clustering
        const markerCluster = new markerClusterer.MarkerClusterer({map});
        const mapMarkers = [];

        const infoWindows = {};

        markers.forEach((markerData, index) => {
            const marker = new google.maps.Marker({
                position: {lat: markerData.lat, lng: markerData.lng},
                title: markerData.title,
                map: map
            });

            // Set marker color based on war zone
            const zoneColors = {
                'Red Sea': '#FF0000',
                'Gulf of Aden': '#FF4500',
                'Persian Gulf': '#FFA500',
                'Eastern Mediterranean': '#8B0000'
            };

            const zoneColor = zoneColors[markerData.info.warZone] || '#4285F4';
            marker.setIcon({
                path: 'M 0,-25 Q 25,0 25,25 Q 0,45 0,45 Q -25,25 -25,0 Q -25,-25 0,-25 z',
                fillColor: zoneColor,
                fillOpacity: 0.8,
                strokeColor: '#fff',
                strokeWeight: 2,
                scale: 0.6
            });

            // Create info window
            const infoContent = `
                <div class="info-window">
                    <h3>📦 ${markerData.info.containerNumber}</h3>
                    <div class="info-row">
                        <span class="info-label">Track #:</span>
                        <span class="info-value">${markerData.info.trackNumber}</span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">Sealine:</span>
                        <span class="info-value">${markerData.info.sealineCode}</span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">Location:</span>
                        <span class="info-value">${markerData.info.location}</span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">War Zone:</span>
                        <span class="info-value"><strong>${markerData.info.warZone}</strong></span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">Size/Type:</span>
                        <span class="info-value">${markerData.info.sizeType}</span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">Event Date:</span>
                        <span class="info-value">${markerData.info.eventDate}</span>
                    </div>
                    <div class="info-row">
                        <span class="info-label">Status:</span>
                        <span class="info-value">${markerData.info.containerStatus}</span>
                    </div>
                </div>
            `;

            const infoWindow = new google.maps.InfoWindow({
                content: infoContent
            });

            marker.addListener('click', () => {
                // Close all open info windows
                Object.values(infoWindows).forEach(iw => iw.close());
                infoWindow.open(map, marker);
                infoWindows[index] = infoWindow;
            });

            mapMarkers.push(marker);
        });

        markerCluster.addMarkers(mapMarkers);

        // Build legend
        const legend = document.getElementById('legend');
        const legendData = ''' + json.dumps({zone: len(zone_data[zone]) for zone in zone_data.keys()}) + ''';

        Object.entries(legendData).forEach(([zone, count]) => {
            const zoneClasses = {
                'Red Sea': 'red-sea',
                'Gulf of Aden': 'gulf-aden',
                'Persian Gulf': 'persian-gulf',
                'Eastern Mediterranean': 'eastern-med'
            };

            const item = document.createElement('div');
            item.className = 'legend-item ' + (zoneClasses[zone] || '');
            item.innerHTML = `
                <div class="legend-item-title">${zone}</div>
                <div class="legend-item-count">📦 ${count} containers</div>
            `;

            item.addEventListener('click', () => {
                const bounds = new google.maps.LatLngBounds();
                mapMarkers.forEach(marker => {
                    bounds.extend(marker.getPosition());
                });

                if (bounds.getNorthEast().equals(bounds.getSouthWest())) {
                    map.setCenter(bounds.getCenter());
                    map.setZoom(6);
                } else {
                    map.fitBounds(bounds);
                }
            });

            legend.appendChild(item);
        });
    </script>
</body>
</html>
''')

print('[OK] Interactive Google Map created')
print('File: %s' % html_file)
print('Total markers: %d' % len(markers))
print('Total shipments: %d' % len(set(row[0] for row in rows)))
