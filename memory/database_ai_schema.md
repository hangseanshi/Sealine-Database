# Core Data Model

## `Sealine_Tracking`

**Primary Key:** `TrackNumber`

Central shipment record containing route stops and their statuses.

| Field | Description |
|-------|-------------|
| `TrackNumber` | Global sealine tracking number. |
| `Sealine Code` | Sealine carrier code. |
| `Sealine Name` | Sealine carrier full name. |
| `Delivery Number` | SAP delivery number associated with the shipment. |
| `Release Number` | SAP release number associated with the shipment. |
| `No Of Containers` | Total number of containers in this tracking. |
| `Tracking Status` | Current tracking status. Values: `Pending Departure`, `Departed from Origin`, `Arrived Destination`, `Delivered`. |

### Route Stop Fields

Each route stop — **Pre-POL**, **POL**, **POD**, and **Post-POD** — shares the same set of fields. **Pre-POL** and **Post-POD** are optional; **POL** and **POD** are mandatory for all trackings.

| Field | Description |
|-------|-------------|
| `{Stop} City` | City of the stop. |
| `{Stop} State` | State or province of the stop. |
| `{Stop} Country` | Full country name of the stop. |
| `{Stop} Country Code` | 2-character country code. |
| `{Stop} Latitude` | Latitude coordinate of the stop. |
| `{Stop} Longitude` | Longitude coordinate of the stop. |
| `{Stop} LOCode` | Unique port code of the stop. |
| `{Stop} Date` | Date when the tracking reaches this stop. See **Date Label Rules** below. |
| `{Stop} isActual` | Whether the date is confirmed (`1`) or estimated (`0`). See **Date Label Rules** below. |
| `{Stop} Occurred` | Whether the tracking has physically reached this stop (`Yes` / `No`). |

**Date Label Rules based on `isActual`:**

| Stop | Label when `isActual = 1` | Label when `isActual = 0` |
|------|---------------------------|---------------------------|
| `POL Date` | `ATD` (Actual Time of Departure) | `ETD` (Estimated Time of Departure) |
| `POD Date` | `ATA` (Actual Time of Arrival) | `ETA` (Estimated Time of Arrival) |
| `Pre-POL Date` | Actual Date | Estimated Date |
| `Post-POD Date` | Actual Date | Estimated Date |

---

## `Sealine_Container_Event`

**Primary Key:** `TrackNumber`, `Container Name`, `Event Sequence ID`

Container-level events for each `TrackNumber`, recording each container's movements and activities along its route.

| Field | Description |
|-------|-------------|
| `TrackNumber` | FK → `Sealine_Tracking.TrackNumber`. |
| `Container Name` | Name or ID of the container. |
| `Container ISO Code` | ISO code for the container type. |
| `Container Size Type` | Descriptive name of the container type. |
| `Event Sequence ID` | Chronological order of events; lower = earlier. Defines the route sequence: `1 → 2 → 3 → …`. |
| `Location Name` | Name of the location where the event occurs. |
| `Location Country Code` | 2-character country code of the event location. |
| `Location LOCode` | LOCode of the event location (may be empty for non-standard locations). |
| `Location Latitude` | Latitude coordinate of the event location. |
| `Location Longitude` | Longitude coordinate of the event location. |
| `Event Description` | Description of the event provided by the carrier. Use for display only. |
| `Event Type` | Type or category of the event provided by the carrier. Use for display only. |
| `Event Code` | Code of the event provided by the carrier. Use for display only. |
| `Event Status` | Status of the event provided by the carrier. Use for display only. |
| `Event Date` | Date when the event occurs. |
| `Event Date isActual` | Whether the event date is confirmed (`1`) or estimated (`0`). |
| `Transport Type` | Mode of transport for this event (`Land` / `Sea`). |
| `Vessel Name` | Name of the vessel involved, if applicable. |
| `Vessel Voyage` | Voyage identifier, if applicable. |
| `Location Type` | Route stop classification of this location: `Pre-POL`, `POL`, `POD`, `Post-POD`, or a comma-delimited combination. Blank or `TRANSIT` for intermediate stops. |
| `Event Occurred` | Whether the event has already taken place (`Yes` / `No`). |

---

## Critical Implementation Notes

### 1. `Sealine_Tracking`

#### Route Structure

- The tracking route always follows this sequence: **Pre-POL → POL → POD → Post-POD**.
- **Pre-POL** and **Post-POD** are optional. **POL** and **POD** are mandatory for all trackings.

#### Location Display Format

**Format:** `{City}/{Country Code}({LOCode})`

**Example:** `Houston/US(USHOU)`

Apply this format consistently to: `Pre-POL Location`, `POL Location`, `POD Location`, and `Post-POD Location`.

#### Identifying the Latest Actual Location

The `*_Occurred` columns — `Pre-POL Occurred`, `POL Occurred`, `POD Occurred`, `Post-POD Occurred` — indicate whether the tracking has physically reached each stop.

To find the latest actual location, scan stops in **reverse order** — **Post-POD → POD → POL → Pre-POL** — and return the first stop where `Occurred = Yes`.

#### Displaying `{Stop}` Date

**Format:** `{Stop Date in YYYY-MM-DD} {Date Indicator}`

The **Date Indicator** is determined as follows:

| Condition | Indicator | Meaning |
|-----------|-----------|---------|
| `{Stop} isActual = 1` AND `{Stop} Occurred = Yes` | `*A*` | Actual date; stop has already been reached. |
| `{Stop} isActual = 1` AND `{Stop} Occurred = No` | `(A)` | Actual date; stop has not yet been reached. |
| `{Stop} isActual = 0` | `(E)` | Estimated date. |

**Examples:**

- `2025-03-01 <A>` — actual date; stop has already been reached.
- `2025-03-01 (A)` — actual date; stop has not yet been reached.
- `2025-03-01 (E)` — estimated date.

#### Aliases for Tracking Status

- **Open / In Transit / Active / Not Delivered:** Any `[Tracking Status]` <> `'Delivered'`. Always use `<> 'Delivered'` in SQL — never filter to a single status value like `= 'Departed from Origin'`.
- **Shipped / Left / Departed:** `[Tracking Status]` = `'Departed from Origin'`.

**CRITICAL — "depart from <location>" vs status filter:**
When the user says "depart from Houston" or "from Houston", this refers to the **POL location** (`[POL City]`, `[POL Country]`, `[POL LOCode]`), NOT the `[Tracking Status]` value. The word "depart" in a location context is a location filter, not a status filter. For example, "in transit tracking depart from Houston" means: `[Tracking Status] <> 'Delivered' AND [POL City] LIKE '%Houston%'`.

---

### 2. `Sealine_Container_Event`

#### Container Reference Format

**Format:** `{TrackNumber}-{ContainerName}`

**Example:** Container `CAAU9988821` under TrackNumber `038VH9472368` → `038VH9472368-CAAU9988821`

#### Location Display Format

**Format:** `{Location Name}/{Location Country Code}({Location Type}:{Location LOCode})`

| Case | Example |
|------|---------|
| Single location type | `Houston/US(POL:USHOU)` |
| Multiple location types | `Houston/US(Pre-POL,POL:USHOU)` |

#### Event Ordering

Events always occur in ascending order of `Event Sequence ID` (lower ID = earlier event).

#### Determining Whether a Container Has Reached a Location

For a given `TrackNumber`, container, and location, check all associated events at that location:

- If **any** event has `Event Occurred = Yes` → the container **has reached** that location.
- If **no** event has `Event Occurred = Yes` → the container **has not reached** that location. If the previous location has at least one event with `Event Occurred = Yes`, the container is considered to have **departed from that previous location**.

#### Route Stops and Route Lines

- **Route Stops:** The unique locations derived from the container's events.
- **Route Lines:** Directional segments connecting consecutive `Event Sequence ID` values (e.g., `1→2`, `2→3`, `3→4`).
  - The lower `Event Sequence ID` end is the **start location** (also called **Event Start Location**).
  - The immediately higher `Event Sequence ID` end is the **end location** (also called **Event End Location**).

#### Identifying the Latest Known Container Location

Scan events from the **highest `Event Sequence ID` downward**; the first record with `Event Occurred = Yes` is the container's latest known location.

#### Location Types

- Containers share the same stop types as the `TrackNumber`: `Pre-POL`, `POL`, `POD`, `Post-POD`.
- Any additional intermediate stops are classified as **`TRANSIT`**.
- **Arrived at destination:** any event where `Location Type` contains `POD` and `Event Occurred = Yes`.
- **Departed from origin:** any event where `Location Type` contains `POL` (but **not** `Pre-POL`) and `Event Occurred = Yes`.

#### Event Date

**Format:** `{Event Date in YYYY-MM-DD} {Event Date Indicator}`

The **Event Date Indicator** is determined as follows:

| Condition | Indicator | Meaning |
|-----------|-----------|---------|
| `Event Date isActual = 1` AND `Event Occurred = Yes` | `*A*` | Actual date; event has already occurred. |
| `Event Date isActual = 1` AND `Event Occurred = No` | `(A)` | Actual date; event has not yet occurred. |
| `Event Date isActual = 0` | `(E)` | Estimated date. |

**Examples:**

- `2025-03-01 <A>` — actual date; event has already occurred.
- `2025-03-01 (A)` — actual date; event has not yet occurred.
- `2025-03-01 (E)` — estimated date.

---

## Data Query Rules

### In Transit Status — Highest Priority

When filtering for 'in transit', 'active', 'open', or 'not delivered' trackings, ALWAYS use `[Tracking Status] <> 'Delivered'`. NEVER use `= 'Departed from Origin'` as a substitute for in-transit — that excludes 'Pending Departure' and 'Arrived Destination' trackings.

When the user says 'depart from <location>' or 'from <location>', this is a **POL LOCATION filter** (`[POL City]`, `[POL Country]`, `[POL LOCode]`), NOT a `Tracking Status` filter.

**Example:** 'in transit tracking depart from Houston' → `WHERE [Tracking Status] <> 'Delivered' AND [POL City] LIKE '%Houston%'`

### Location Filtering

**`'from <location>'`** — check all POL columns:
```sql
[POL City]         LIKE '%<location>%'
OR [POL Country]      LIKE '%<location>%'
OR [POL Country Code] LIKE '%<location>%'
OR [POL LOCode]       LIKE '%<location>%'
```

**`'to <location>'`** — check all POD columns:
```sql
[POD City]         LIKE '%<location>%'
OR [POD Country]      LIKE '%<location>%'
OR [POD Country Code] LIKE '%<location>%'
OR [POD LOCode]       LIKE '%<location>%'
```

This ensures matches on city names (e.g., `'Houston'`), country names (e.g., `'China'`), country codes (e.g., `'CN'`), and port codes (e.g., `'USHOU'`).

### Container Number Format

Container numbers match exactly **4 uppercase letters + 7 digits** (e.g., `MSDU1234567`, `GAOU6335790`). Anything else (e.g., `DALA71196300`, `038VH9486166`) is a tracking number.

- For tracking numbers: use `v.TrackNumber = '<value>'` as the filter.
- For container numbers: use `v.Container_NUMBER IN ('<c1>', '<c2>', ...)` as the filter.

---

## Predefined Query Templates

### Tracking Split-Route In Transit

When user asks about "Split Route" or "Split Route In Transit", IMMEDIATELY execute the query below using `execute_sql`. Do NOT ask for clarification. Do NOT use `show_tracking_routes`. Do NOT generate a map.

- Only check `TrackNumber` in `Sealine_Tracking` where `[Tracking Status] = 'Departed from Origin'`.
- Find the last event (by `[Event Sequence ID]` descending) for each container where `[Event Occurred] = Yes`.
- If these events span more than one distinct `[Location LOCode]`, the tracking has a split route.
- Display `TrackNumber`, `[No Of Containers]`, `[No Of Locations]`, and `[Split Routes]`.

```sql
WITH trackings AS (
    SELECT *
    FROM sealine_tracking
    WHERE [Tracking Status] = 'Departed from Origin'
),
latestEvent AS (
    SELECT *
          ,ROW_NUMBER() OVER (
               PARTITION BY TrackNumber, [Container Name]
               ORDER BY [Event Sequence ID] DESC
           ) AS rn
    FROM sealine_container_event
),
splitroutes AS (
    SELECT latestEvent.TrackNumber
          ,trackings.[No Of Containers]
          ,COUNT(DISTINCT latestEvent.[Location LOCode]) AS [No Of Locations]
    FROM latestEvent
        ,trackings
    WHERE latestEvent.TrackNumber = trackings.TrackNumber
      AND latestEvent.rn = 1
    GROUP BY latestEvent.TrackNumber
            ,trackings.[No Of Containers]
    HAVING COUNT(DISTINCT latestEvent.[Location LOCode]) > 1
),
uniqueLocations AS (
    SELECT DISTINCT TrackNumber
                   ,[Location LOCode]
                   ,COUNT(DISTINCT [Container Name]) AS cnt
    FROM latestEvent
    WHERE rn = 1
    GROUP BY TrackNumber
            ,[Location LOCode]
)
SELECT splitroutes.*
      ,STRING_AGG(
           uniqueLocations.[Location LOCode]
               + '(' + CAST(uniqueLocations.cnt AS VARCHAR) + ')',
           ','
       ) WITHIN GROUP (ORDER BY uniqueLocations.[Location LOCode]) AS [Split Routes]
FROM splitroutes
    ,uniqueLocations
WHERE splitroutes.TrackNumber = uniqueLocations.TrackNumber
GROUP BY splitroutes.TrackNumber
        ,splitroutes.[No Of Containers]
        ,splitroutes.[No Of Locations];
```

Always use this pattern when the question involves `'latest location'`, `'current location'`, `'where is the container now'`, or comparing container positions.

**CRITICAL — "containers at different ports":** Compare each container's latest location against other containers within the same tracking — NOT against the tracking's POD. Use `COUNT(DISTINCT [Location LOCode]) > 1` grouped by `TrackNumber`.

### War Zone POD Tracking

Query `Sealine_Tracking` directly using POD coordinates:
```sql
SELECT
    TrackNumber,
    [POD City]      AS POD_Location,
    [POD Latitude]  AS Lat,
    [POD Longitude] AS Lng
FROM Sealine_Tracking
WHERE [Tracking Status] <> 'Delivered'
  AND [POD Latitude]  IS NOT NULL
  AND [POD Longitude] IS NOT NULL
  AND (
      ([POD Latitude] BETWEEN 29   AND 33.5 AND [POD Longitude] BETWEEN 33.8 AND 36.5) OR
      ([POD Latitude] BETWEEN 41   AND 48   AND [POD Longitude] BETWEEN 28   AND 42  ) OR
      ([POD Latitude] BETWEEN 12   AND 28   AND [POD Longitude] BETWEEN 32   AND 52  ) OR
      ([POD Latitude] BETWEEN 8    AND 23   AND [POD Longitude] BETWEEN 22   AND 38  )
  )
ORDER BY TrackNumber
```

**Enforcement rules:**
1. Use `Sealine_Tracking` and `Sealine_Container_Event` only — no other tables exist.
2. Do not flatten this into a single `SELECT`. The 2-CTE structure is mandatory.
3. Do not omit `NOT EXISTS`.
4. Return exactly four columns: `TrackNumber`, `POD_Location`, `Lat`, `Lng`.
5. Do not alter the geographic boundaries: `(29–33.5, 33.8–36.5)`, `(41–48, 28–42)`, `(12–28, 32–52)`, `(8–23, 22–38)`.
6. Any deviation will produce incorrect results. This is non-negotiable.

### Word Shipping Labels SQL

**Step 1 — Run this SQL** (replace `<FILTER>` with e.g., `e.TrackNumber = '<value>'`):
```sql
SELECT
    e.[Location Name]                                          AS DisplayName,
    e.[Location Country Code]                                  AS Country_Code,
    e.[Location LOCode]                                        AS LOCode,
    e.TrackNumber + '-' + e.[Container Name]                   AS Container_NUMBER,
    e.TrackNumber,
    e.[Event Sequence ID]                                      AS MinOrderId,
    e.[Event Description] + ':' + CONVERT(varchar, CAST(e.[Event Date] AS DATE), 23)
        + CASE
            WHEN e.[Event Date isActual] = 1 AND e.[Event Occurred] = 'Yes'               THEN ' *A*'
            WHEN e.[Event Date isActual] = 1 AND (e.[Event Occurred] = 'No'
                                               OR e.[Event Occurred] IS NULL)             THEN ' (A)'
            ELSE ' (E)'
          END                                                  AS EventLines
FROM Sealine_Container_Event e
WHERE <FILTER>
  AND e.[Location Latitude] IS NOT NULL
ORDER BY e.[Location Name], e.[Container Name], e.[Event Sequence ID] ASC
```

**Step 2 — Group results:**
1. Group by `(DisplayName + Country_Code + LOCode)`.
2. Within each location, group containers by `Container_NUMBER` in first-appearance order.
3. Build `locations` array with `name`, `country_code`, `locode`, and `containers`. Each container has `container_number` and `events` list.

**Step 3 — Parse `EventLines`:**
Split on `CHAR(10)`. For each line: `date` = first 10 chars, `actual` = `true` if `'(A)'` present (must be boolean), `description` = text after `': '`.

### Container Stop Label Format

```
<ContainerNumber>
<LocationName>/<CountryCode> (<LocationType>:<LOCode>)
<EventLines>
```

`EventLines` is aggregated with `CHAR(10)` newline separators — pass directly to the renderer.