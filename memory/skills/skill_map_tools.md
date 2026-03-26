# Map Tools & Visualization

## Map Tool Selection

There are exactly four map tools. Select based on intent:

### 1. `show_tracking_routes`
Visualise one or more tracking number routes.

### 2. `show_container_routes`
Visualise one or more container routes.

### 3. `show_location_map`
Show, highlight, mark, or pin any city, port, or location on a map.
Triggers: `'<location> in the map'`, `'show me <location> on a map'`, `'where is <port/city>'`, `'<LOCode> in the map'`.

### 4. `show_choropleth_map`
Shade countries by count or amount intensity.

**Decision shortcut:** If the user provides any port name, city name, or LOCode followed by `'in the map'`, `'on the map'`, or `'on a map'` → always use `show_location_map`.

> **Never use `generate_plot` for map requests.** Use the dedicated tools listed above.

---

## Route Map — Tracking (`show_tracking_routes`)

**Trigger:** User asks to show tracking number(s) on a map, view a route, or find where a shipment is going — with no mention of containers.

Call `show_tracking_routes` directly — no `execute_sql` needed.

Supply **one** filter: `track_numbers=[...]` or `subquery='SELECT TrackNumber FROM ...'`.
```python
show_tracking_routes(track_numbers=['038NY1485768'], title='Route Map')
```

If `show_tracking_routes` returns `'MAP_TRUNCATED'`, append exactly this line to your reply:
> Some of the routes cannot be shown in the map due to the map limitations.

---

## Route Map — Container (`show_container_routes`)

**Trigger:** User mentions `'container(s)'` in a map context, or asks for containers of a tracking number.

Call `show_container_routes` directly — no `execute_sql` needed.

Supply **one** filter:

| Filter Parameter | Example |
|---|---|
| `track_numbers=[...]` | `track_numbers=['038VH9465510']` |
| `container_numbers=[...]` | `container_numbers=['MSDU1234567']` |
| `track_number_subquery='SELECT TrackNumber FROM ...'` | |
| `container_number_subquery='SELECT [Container Name] FROM ...'` | |

```python
show_container_routes(track_numbers=['038VH9465510'], title='Container Routes')
```

---

## Location Bubble Map (`show_location_map`)

**Trigger:** Any request to show a city, port, location, or landmark on a map — including:
`'<name> in the map'`, `'<name> on the map'`, `'show <name> on a map'`, `'where is <port>'`, `'mark <city>'`, `'highlight <location>'`, and city/port-level bubble data (e.g., `'top 10 ports by container count'`).

**Workflow — always two steps:**

**Step 1:** Call `geocode_location(query='<place name>')` to get lat/lon from OpenStreetMap. Use the first result (highest relevance). Never use `execute_sql` to look up coordinates for location maps.

**Step 2:** Immediately call `show_location_map` with the lat/lon returned in Step 1.

**Single location example** — `'Jawaharlal Nehru, IN (INNSA) in the map'`:
```python
# Step 1
geocode_location(query='Jawaharlal Nehru Port India')
# → lat: 18.9497, lon: 72.9503

# Step 2
show_location_map(
    title='Jawaharlal Nehru Port (INNSA)',
    locations=[{"name": "Jawaharlal Nehru (INNSA)", "lat": 18.9497, "lon": 72.9503}]
)
```

**Multi-location bubble:** Geocode each location individually, then call `show_location_map` with all results in a single call.

**Bubble with value:**
```python
show_location_map(
    title='Top POL Ports',
    locations=[{"name": "Houston", "lat": 29.75, "lon": -95.36, "value": 450}],
    value_label='Trackings'
)
```

---

## Choropleth Map (`show_choropleth_map`)

**Trigger:** User wants a world map where countries are shaded darker by a count or amount.

**Workflow:**

**Step 1:** Run `execute_sql` to get country + count data.

**Step 2:** Call `show_choropleth_map`.
```python
show_choropleth_map(
    title='Trackings by Country',
    data=[{"country": "China", "value": 1200}],
    color='blue',
    value_label='Trackings'
)
```

- Country names are case-insensitive; ISO alpha-2 codes (e.g., `'CN'`) also work.
- Colour guide: `'blue'` for general metrics, `'red'` for risk/volume, `'green'` for positive metrics.
- Never pass lat/lon or route arrays to `show_choropleth_map` — only country names and values.

---

## Country Highlight

Both `show_tracking_routes` and `show_container_routes` support an optional `highlight_regions` parameter to shade countries on the map.
```python
show_tracking_routes(
    track_numbers=['038NY1485768'],
    highlight_regions=[{"name": "China", "color": "rgba(220,50,50,0.28)"}]
)
```

- Country names are case-insensitive; ISO alpha-2 codes (e.g., `'CN'`, `'US'`) also work.
- If no colour is supplied, the default is `rgba(255,165,0,0.30)` (orange).
- Use red tones (`rgba(220,50,50,0.28)`) for destination or highlighted countries.
- Use blue tones (`rgba(31,71,136,0.25)`) for origin or key countries.

---

## War Zones on Maps

Both `show_tracking_routes` and `show_container_routes` automatically overlay war zone regions (Red Sea/Gulf of Aden and Black Sea) on every route map. No additional tool call or query is required. If the user asks to highlight or add war zones to a map, call the appropriate route tool as normal and confirm that war zones are already displayed.

**Colour guide for custom war/risk zones:**

| Zone Type | Colour |
|---|---|
| Active war zone | `rgba(255,0,0,0.25)` (semi-transparent red) |
| High-risk area | `rgba(255,140,0,0.25)` (orange) |

**Known war/conflict zone coordinates:**

| Zone | Latitude Polygon | Longitude Polygon |
|---|---|---|
| Red Sea / Yemen | `[12, 15, 20, 25, 28, 25, 20, 15, 12]` | `[40, 38, 37, 38, 42, 48, 50, 48, 40]` |
| Gaza / Israel | `[29, 29, 33, 33, 29]` | `[34, 36, 36, 34, 34]` |
| Ukraine | `[44, 44, 52, 52, 44]` | `[22, 40, 40, 22, 22]` |
| Sudan | `[8, 8, 23, 23, 8]` | `[22, 38, 38, 22, 22]` |

Always include zone names that clearly describe the risk.

---

## Arrows and Connections

When displaying any ordered journey, include `arrows=true` so directional arrow lines are drawn between consecutive stops. When the user requests arrows between specific points, use `connections=[[from_idx, to_idx], ...]` to define the pairs explicitly.
