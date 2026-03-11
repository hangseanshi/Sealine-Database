# Sealine-Database Memory

## Active Schema Reference
Using **sealineDB_schema.md** as authoritative schema knowledge for all database interactions.

## Connection Details
- **Type**: SQL Server
- **Server**: ushou102-exap1
- **Database**: searates
- **Credentials**: See connections.md

## Core Data Model
**Sealine_Header (PK: TrackNumber)** — central shipment record
- Status: IN_TRANSIT, DELIVERED, UNKNOWN, PLANNED, CANCELLED, COMPLETED
- All other tables use TrackNumber as logical FK (no enforced constraints)

**Child Tables (1:N from Header)**:
- **Sealine_Container** (~4 per shipment) — containers in shipment
- **Sealine_Container_Event** — port/facility events per container
  - **Order_Id**: sequence order of events (1=earliest, increments chronologically)
  - **Location FK**: Container_Event.Location → Sealine_Locations.Id (same TrackNumber)
  - **Facility FK**: Container_Event.Facility → Sealine_Facilities.Id (same TrackNumber)
  - **Actual flag**: 1=confirmed date, 0=estimated date
- **Sealine_Locations** — route stops (keyed by Id)
- **Sealine_Vessels** — carriers/vessels (1:N)
- **Sealine_Route** — scheduled/actual dates (ETD/ETA/ATD/ATA)
  - Location_Id → Sealine_Locations.Id (same TrackNumber)
- **Sealine_Facilities** — port/terminal facility data (keyed by Id)

**Request/API Tables**:
- **Searates_Request_Tracking** — active tracking requests (isActive bit)
- **ResponseLog** — API call history (raw JSON responses)
- **Sealine_Tracking_Response** — batch responses

**Reference Tables**:
- **Carrier_Sealine_Mapaping** — carrier↔code mappings
- **Response_Sealine_Mapping** — sealine code overrides
- **API_Configuration** — API keys, limits, retry logic

## User Preferences
- Always show SQL queries used in responses for transparency
- Reports should be saved as both `.txt` and `.xlsx` (formatted with blue header row)
- Reusable reports are stored as Python scripts and catalogued in `memory/reports.md`
- Email reports as Excel attachments to `hangseanshi@gmail.com` using Gmail API

## Email / Output Capabilities
- **Gmail API**: OAuth2 via `token.json` in `C:\Users\hangs\OneDrive\GitHub\OpenExxon\`
- **Gmail address**: loaded from `OpenExxon/.env` → `GMAIL_ADDRESS`
- **Excel**: `openpyxl` — blue header (#1F4788), freeze pane row 1
- **Always send to**: hangseanshi@gmail.com unless told otherwise

## Reusable Reports
See `memory/reports.md` for full catalogue of saved reports.

## Critical Implementation Notes
1. **Lat/Lng are VARCHAR** — always cast: `TRY_CAST(Lat AS FLOAT)`
2. **No FK constraints** — relationships are logical, handle NULL/orphans
3. **Soft deletes** — DeletedDt column indicates deleted rows
4. **Archive tables** — Container_Event has multiple snapshot versions (_02May2025, _Revised, etc.)
5. **Container_Event location hierarchy**:
   - Use `Facility` if present (specific port/terminal)
   - Fall back to `Location` if Facility is NULL (general route stop)
6. **Container_Event.Actual flag**:
   - `Actual = 1` → Date is actual/confirmed event date
   - `Actual = 0` → Date is estimated/predictive
7. **Sealine_Route.IsActual** — same flag logic (1=actual, 0=planned)
