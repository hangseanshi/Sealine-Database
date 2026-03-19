# sealineDB Schema — searates database

## Connection
See connections.md for credentials.

## MANDATORY RULE: Soft-Delete Filtering
The following tables use soft deletes via a `DeletedDt` column:
**Sealine_Header**, **Sealine_Route**, **Sealine_Locations**, **Sealine_Container**, **Sealine_Container_Event**, **Sealine_Facilities**.

**EVERY query that references any of these tables MUST include `<alias>.DeletedDt IS NULL` in the WHERE or JOIN condition for EACH table/alias used.** This is not optional — omitting it returns deleted/stale rows and produces wrong results.

Example: if you write `FROM Sealine_Header h INNER JOIN Sealine_Route r ...`, you MUST include both `h.DeletedDt IS NULL` AND `r.DeletedDt IS NULL`.

## Relationships (logical, no FK constraints enforced in DB)

```
Sealine_Header.TrackNumber (PK)
    ├── 1:N → Sealine_Vessels.TrackNumber
    ├── 1:N → Sealine_Locations.TrackNumber
    ├── 1:N → Sealine_Container.TrackNumber
    ├── 1:N → Sealine_Container_Event.TrackNumber
    ├── 1:N → Sealine_Facilities.TrackNumber
    ├── 1:N → Sealine_Route.TrackNumber
    └── 1:N → Sealine_Tracking_Response.TrackNumber

Searates_Request_Tracking.TrackingNo → Sealine_Header.TrackNumber
ResponseLog.Id → Searates_Request_Tracking.LastResponseId

v_sealine_container_count (TrackNumber, Location_Id) → Sealine_Locations (TrackNumber, Id)
v_sealine_tracking_count (TrackNumber, Location_Id) → Sealine_Locations (TrackNumber, Id)
v_sealine_container_route (TrackNumber, Location_Id) → Sealine_Locations (TrackNumber, Id)
```

**IMPORTANT JOIN RULE:**
- `v_sealine_container_route.LocationName` is a display-only column — NEVER join on it.
- To join `v_sealine_container_route` with `Sealine_Locations`, ALWAYS use:
  `v.TrackNumber = l.TrackNumber AND v.Location_Id = l.Id`

---

## Core Tables

### Sealine_Header
Primary tracking record per shipment.
| Column | Type | Notes |
|--------|------|-------|
| TrackNumber | varchar(100) NOT NULL | PK |
| Type | varchar(10) NOT NULL | e.g. BK (Booking) |
| Sealine_Code | varchar(100) NOT NULL | e.g. CMDU, LMCU, DHC2 |
| Sealine_Name | varchar(500) | e.g. CMA CGM, DHL Global Forwarding |
| API_Status | varchar(500) | success / error |
| Status | varchar(100) | IN_TRANSIT, DELIVERED, UNKNOWN, PLANNED, CANCELLED, COMPLETED |
| Is_Status_From_Sealine | int | 1=from carrier, 0=internal |
| Updated_Date | datetime | |
| CreatedOn | datetime | |
| UpdatedDT | datetime | |
| DeletedDt | datetime | soft delete |

### Sealine_Locations
Route stops per shipment.
| Column | Type | Notes |
|--------|------|-------|
| TrackNumber | varchar(100) NOT NULL | FK → Sealine_Header |
| Type | varchar(10) NOT NULL | |
| Sealine_Code | varchar(100) NOT NULL | |
| Name | varchar(1000) | Location name |
| State | varchar(500) | |
| Country | varchar(100) | |
| Country_Code | varchar(50) | |
| LOCode | varchar(100) | UN/LOCODE e.g. USHOU, SGSIN |
| Lat | varchar(100) | stored as string — use TRY_CAST(Lat AS FLOAT) |
| Lng | varchar(100) | stored as string — use TRY_CAST(Lng AS FLOAT) |
| Timezone | varchar(100) | |
| CreatedOn / UpdatedDT / DeletedDt | datetime | |

### Sealine_Vessels
Vessels associated with a shipment (1:N).
| Column | Type | Notes |
|--------|------|-------|
| TrackNumber | varchar(100) NOT NULL | FK → Sealine_Header |
| Type | varchar(10) NOT NULL | |
| Sealine_Code | varchar(100) NOT NULL | |
| Id | bigint NOT NULL | |
| Name | varchar(500) | Vessel name |
| imo | varchar(500) | IMO number |
| call_sign | varchar(500) | |
| mmsi | varchar(500) | |
| flag | varchar(100) | Country flag code |
| CreatedOn / UpdatedDT / DeletedDt | datetime | |

### Sealine_Container
All containers belonging to a shipment. Child of Sealine_Header via TrackNumber (1:N). A shipment can have multiple containers (~4 avg; 72,722 rows across 16,910 TrackNumbers).
| Column | Type | Notes |
|--------|------|-------|
| TrackNumber | varchar(100) NOT NULL | FK → Sealine_Header |
| Type | varchar(10) NOT NULL | e.g. `BK` (Booking) |
| Sealine_Code | varchar(100) NOT NULL | Carrier code |
| Container_NUMBER | varchar(100) NOT NULL | Container number; may be `UNKNOWN` |
| Iso_Code | varchar(100) | Container ISO type code |
| Size_Type | varchar(500) | e.g. `20GP`, `40HC` |
| Status | varchar(100) | e.g. `DELIVERED`, `IN_TRANSIT` |
| Is_Status_From_Sealine | int | 1 = from carrier, 0 = internal |
| CreatedOn | datetime | |
| UpdatedDT | datetime | |
| DeletedDt | datetime | Soft delete |

### Sealine_Container_Event
Container-level tracking events. **⭐ AUTHORITATIVE SOURCE FOR CONTAINER ROUTES.**
Use this table (not Sealine_Route) when querying a tracking number's full route — all stops, transshipments, arrivals, and departures are recorded here per container.

| Column | Type | Notes |
|--------|------|-------|
| TrackNumber | varchar(100) NOT NULL | |
| Sealine_Code / RequestType / Type | varchar | |
| Container_NUMBER | varchar(100) NOT NULL | |
| Order_id | varchar(100) NOT NULL | Chronological sequence — ORDER BY TRY_CAST(Order_Id AS INT) ASC |
| Location / Facility | varchar(100) | FK → Sealine_Locations.Id / Sealine_Facilities.Id (same TrackNumber). **Facility = physical terminal (preferred); Location = general route stop (fallback when Facility is NULL)** |
| Description | varchar(100) | Event description (e.g. "Export Loaded on Vessel") |
| Event_type / Event_Code | varchar(100) | |
| Status | varchar(100) | |
| Date | datetime | Event date |
| Actual | int | 1=confirmed actual date, 0=estimated/future |
| Is_Additional_Event | int | |
| Transport_Type | varchar(100) | |
| Vessel / Voyage | varchar(100) | |
| CreatedOn / UpdatedDT / DeletedDt | datetime | |

**Location priority rule:**
- If `Facility` is populated → join `Sealine_Facilities` on `Facility = Id` → use `f.name` as the physical location
- If `Facility` is NULL → join `Sealine_Locations` on `Location = Id` → use `l.Name` as the fallback location
- Always use `COALESCE(f.name, l.Name)` to implement this automatically

**Route query pattern:**
```sql
SELECT e.Container_NUMBER, e.Date, e.Actual,
       COALESCE(f.name, l.Name) AS Location,   -- Facility (physical terminal) takes priority over Location (general stop)
       e.Description
FROM Sealine_Container_Event e
LEFT JOIN Sealine_Facilities f ON e.TrackNumber = f.TrackNumber AND e.Facility = f.Id AND f.DeletedDt IS NULL
LEFT JOIN Sealine_Locations  l ON e.TrackNumber = l.TrackNumber AND e.Location = l.Id AND l.DeletedDt IS NULL
WHERE e.TrackNumber = '<track>'
  AND e.DeletedDt IS NULL
ORDER BY e.Container_NUMBER, TRY_CAST(e.Order_Id AS INT)
```

> Archive copies: Sealine_Container_Event_02May2025, Sealine_Container_Event_All, Sealine_Container_Event_Revised, Sealine_Container_Event_Revised_28APR2025

### Sealine_Route
Scheduled/actual ETD/ETA dates per route stop. **Not a full stop-by-stop route — use Sealine_Container_Event for the complete container route.**

| Column | Type | Notes |
|--------|------|-------|
| TrackNumber | varchar(100) NOT NULL | FK → Sealine_Header |
| Type / Sealine_Code | varchar | |
| RouteType | varchar(100) NOT NULL | Stop role — see values below |
| Location_Id | int | FK → Sealine_Locations.Id (same TrackNumber) |
| Date | datetime | ETD or ETA date for this stop |
| IsActual | int | 1=actual date, 0=planned/estimated |
| Predictive_ETA | varchar(100) | |
| CreatedOn / UpdatedDT / DeletedDt | datetime | |

**RouteType values:**
| RouteType | Meaning |
|-----------|---------|
| `Pre-Pol` | Stop **before** the Port of Loading (origin inland/feeder stop) |
| `Pol` | **Port of Loading** — the place where cargo is loaded onto the main vessel |
| `Pod` | **Port of Discharge** — the destination port where cargo is unloaded |
| `Post-Pod` | Stop **after** the Port of Discharge (destination inland/feeder stop) |

**Date interpretation — RouteType × IsActual:**
| RouteType | IsActual | Shipping Term | Meaning |
|-----------|----------|---------------|---------|
| `Pol` | 0 | **ETD** | Estimated Time of Departure |
| `Pol` | 1 | **ATD** | Actual Time of Departure |
| `Pod` | 0 | **ETA** | Estimated Time of Arrival |
| `Pod` | 1 | **ATA** | Actual Time of Arrival |
| `Pre-Pol` | 0/1 | Estimated/Actual departure from origin feeder stop |
| `Post-Pod` | 0/1 | Estimated/Actual arrival at destination feeder stop |

```sql
-- Derive shipping term label from RouteType + IsActual
CASE
    WHEN r.RouteType = 'Pol' AND r.IsActual = 0 THEN 'ETD'
    WHEN r.RouteType = 'Pol' AND r.IsActual = 1 THEN 'ATD'
    WHEN r.RouteType = 'Pod' AND r.IsActual = 0 THEN 'ETA'
    WHEN r.RouteType = 'Pod' AND r.IsActual = 1 THEN 'ATA'
    ELSE r.RouteType + CASE WHEN r.IsActual = 1 THEN ' (Actual)' ELSE ' (Estimated)' END
END AS DateLabel
```

**Join pattern to get location name:**
```sql
SELECT r.RouteType, r.Date, r.IsActual, l.Name, l.LOCode, l.Country
FROM Sealine_Route r
LEFT JOIN Sealine_Locations l
    ON r.TrackNumber = l.TrackNumber AND r.Location_Id = l.Id AND l.DeletedDt IS NULL
WHERE r.TrackNumber = '<track>'
  AND r.DeletedDt IS NULL
ORDER BY CASE r.RouteType
    WHEN 'Pre-Pol'  THEN 1
    WHEN 'Pol'      THEN 2
    WHEN 'Pod'      THEN 3
    WHEN 'Post-Pod' THEN 4
    ELSE 5 END
```

### Sealine_Facilities
Port/terminal facilities per shipment.
| Column | Type | Notes |
|--------|------|-------|
| TrackNumber | varchar(100) NOT NULL | |
| Type / Sealine_Code | varchar | |
| Id | bigint NOT NULL | |
| name | varchar(1000) | |
| Country_Code | varchar(50) | |
| Locode / Bic_Code / Smdg_Code | varchar(100) | |
| Lat / lng | varchar(100) | |
| CreatedOn / UpdatedDT / DeletedDt | datetime | |

---

## Request / API Tables

### Searates_Request_Tracking
Active tracking requests sent to carriers.
| Column | Type | Notes |
|--------|------|-------|
| TrackingNo | varchar(100) NOT NULL | |
| RequestType | varchar(20) NOT NULL | |
| SealineCode | varchar(100) | |
| Carrier | varchar(100) NOT NULL | |
| Source_Name / Source_Record_Id | varchar(100) | Source system reference |
| Delivery_Number / Release_Number | varchar | |
| Batch | varchar(100) | |
| Tracking_Status | varchar(500) | |
| LastResponseId | bigint | → ResponseLog.Id |
| LastAPICallDate | datetime | |
| LastAPIStatus | varchar(100) | |
| CreatedDt | datetime | |
| isActive | bit | 1=active tracking |
| Message | varchar(5000) | |

> Searates_Request_Tracking_Deleted — archive of deleted requests (same schema)

### ResponseLog
API call log.
| Column | Type | Notes |
|--------|------|-------|
| Id | bigint NOT NULL | PK |
| TrackingNo | varchar(100) | |
| RequestType | varchar(10) | |
| Carrier | varchar(100) | |
| URL | varchar(max) | |
| API_Status | varchar(100) | |
| Tracking_Status | varchar(100) | |
| Response | varchar(max) | Raw JSON response |
| StartDate / EndDate | datetime | |
| RetryCnt | int | |
| Batch | varchar(500) | |

### Sealine_Tracking_Response
Batch tracking responses.
| Column | Type |
|--------|------|
| batch | varchar(100) |
| TrackNumber | varchar(100) |
| RequestType | varchar(100) |
| Carrier | varchar(100) |
| Response | varchar(max) |
| Status | varchar(100) |

---

## Reference / Mapping Tables

### API_Configuration
| Column | Notes |
|--------|-------|
| API_NAME | e.g. Sealine_Response |
| URL | Template URL with {key}, {number}, {type}, {sealine} placeholders |
| API_Key | K-23FF78E9-90DA-488C-978A-E98369E87695 |
| MaxRetryCnt | 10 |
| RetryInMin | 20 |
| Daily_API_Limit | 5000 |

### Carrier_Sealine_Mapaping
Maps carrier codes to sealine codes.
| Column | Type |
|--------|------|
| Carrier | varchar(100) |
| Sealine_Code | varchar(100) |
| isDefault | int |

### Response_Sealine_Mapping
Overrides sealine code from API response.
| Column | Type |
|--------|------|
| TrackingNo | varchar(100) |
| NewSealineCode | varchar(100) |

### _EDS_Shipline_code
External carrier code reference.
| Column | Type |
|--------|------|
| shipline_name | varchar(500) |
| carrier_code | varchar(10) |
| createdDT | datetime |

---

## Views

### v_sealine_tracking_route
Tracking-level locations and events. One row per unique location per TrackNumber, with all events for that location pre-aggregated into `EventLines`.

| Column | Type | Notes |
|--------|------|-------|
| TrackNumber | varchar | FK → Sealine_Header |
| Lat | varchar | Latitude (use TRY_CAST AS FLOAT) |
| Lng | varchar | Longitude (use TRY_CAST AS FLOAT) |
| LocationName | varchar | Display name of the location |
| RouteType | varchar | Stop role: `PRE-POL`, `POL`, `POD`, `POST-POD` |
| MinOrderId | int | Lowest order sequence at this location — use ORDER BY MinOrderId ASC for chronological route |
| NoOfContainers | int | Number of containers at this stop |
| EventLines | varchar | All events at this location, delimited by `<BR>` |

**EventLines format** — each event is a 3-part string:
```
<RouteType>:<date> (A/E)
```
- `<RouteType>` — `PRE-POL`, `POL`, `POD`, or `POST-POD`
- `<date>` — the date when the tracking was assigned that route type (e.g. `2026-02-15`)
- `(A)` — **Actual**: date is confirmed, event has happened
- `(E)` — **Estimated**: date is not yet confirmed, event has not happened yet

**Example EventLines value:**
```
POL:2026-02-15 (A)<BR>POD:2026-03-10 (E)
```

---

### Defining "Departed" (left origin)

> ⭐ **ALWAYS use `v_sealine_tracking_route` for any question about departure, arrival, left origin, or reached destination. NEVER use `Sealine_Route` or `IsActual` for these questions.**

A tracking is considered **departed** when its POL row has no `(E)` entries remaining in EventLines.

**Rules:**
- `RouteType LIKE '%POL%' AND RouteType <> 'PRE-POL'` — matches `POL` but explicitly excludes `PRE-POL` (inland feeder stop before the main port)
- `EventLines NOT LIKE '%(E)%'` — no estimated events remain, meaning departure is confirmed

**Query pattern:**
```sql
-- All departed trackings
SELECT *
FROM v_sealine_tracking_route
WHERE RouteType LIKE '%POL%'
  AND RouteType <> 'PRE-POL'
  AND EventLines NOT LIKE '%(E)%'
```

> ⚠️ Always use `RouteType <> 'PRE-POL'` — never treat a PRE-POL stop as a departure.

---

### Defining "Arrived" (reached destination)

A tracking is considered **arrived** when its POD row has no `(E)` entries remaining in EventLines.

**Rules:**
- `RouteType LIKE '%POD%'` — matches POD stops
- `EventLines NOT LIKE '%(E)%'` — no estimated events remain, meaning arrival is confirmed

**Query pattern:**
```sql
-- All arrived trackings
SELECT *
FROM v_sealine_tracking_route
WHERE RouteType LIKE '%POD%'
  AND EventLines NOT LIKE '%(E)%'
```

---

### v_sealine_container_route
Container-level locations and events. One row per unique location per container, with all events for that location pre-aggregated into `EventLines`.

| Column | Type | Notes |
|--------|------|-------|
| TrackNumber | varchar | FK → Sealine_Header |
| Container_NUMBER | varchar | Container number |
| Lat | varchar | Latitude (use TRY_CAST AS FLOAT) |
| Lng | varchar | Longitude (use TRY_CAST AS FLOAT) |
| LocationName | varchar | Display name of the location |
| MinOrderId | int | Lowest order sequence — use ORDER BY MinOrderId ASC for chronological route |
| EventLines | varchar | All events at this location (CHAR(10) newline-separated) |
| Vessel | varchar | Vessel name at this stop |
| isTransitLocation | varchar | `'Y'` = transit/transshipment stop; `'N'` or NULL = origin or destination |

**Column constraints:**
- `Country_Code`, `LOCode`, `Location`, `Facility`, `Order_Id` do **NOT** exist in this view
- Use `LocationName` directly — do not join to `Sealine_Locations` for the name
- `isTransitLocation = 'Y'` (string, not `1` or `true`)
- EventLines here uses `(A)` to indicate actual events: `EventLines LIKE '%(A)%'`

---

## Common Query Patterns

### Safe lat/lng cast
```sql
TRY_CAST(Lat AS FLOAT), TRY_CAST(Lng AS FLOAT)
```

### Mid-East war zone bounding boxes
```sql
-- Red Sea
(TRY_CAST(Lat AS FLOAT) BETWEEN 12 AND 28 AND TRY_CAST(Lng AS FLOAT) BETWEEN 32 AND 45)
-- Gulf of Aden
(TRY_CAST(Lat AS FLOAT) BETWEEN 10 AND 16 AND TRY_CAST(Lng AS FLOAT) BETWEEN 42 AND 52)
-- Persian Gulf
(TRY_CAST(Lat AS FLOAT) BETWEEN 22 AND 30 AND TRY_CAST(Lng AS FLOAT) BETWEEN 48 AND 60)
-- Eastern Mediterranean
(TRY_CAST(Lat AS FLOAT) BETWEEN 29 AND 38 AND TRY_CAST(Lng AS FLOAT) BETWEEN 28 AND 37)
```
