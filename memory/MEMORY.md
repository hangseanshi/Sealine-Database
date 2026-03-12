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

## Agent Tools (Web Chat UI + Terminal)
The `ClaudeChat` agent has 3 registered tools available in both the web chat and terminal:
1. **`execute_sql`** — run live SELECT queries against sealineDB
2. **`create_excel`** — generate a formatted .xlsx file from tabular data (blue header, frozen row, auto-width columns). Returns the temp file path.
3. **`send_email`** — send email via Gmail API with optional Excel attachment. Uses OpenExxon credentials.

**Typical agent workflow for "run report and email as Excel":**
1. Agent calls `execute_sql` to get the data
2. Agent calls `create_excel` with columns + rows → gets filepath
3. Agent calls `send_email` with filepath as `attachment_path`

## Reusable Reports
See `memory/reports.md` for full catalogue of saved reports.

## Critical Implementation Notes
1. **Lat/Lng are VARCHAR** — always cast: `TRY_CAST(Lat AS FLOAT)`
2. **No FK constraints** — relationships are logical, handle NULL/orphans
3. **Soft deletes** — DeletedDt column indicates deleted rows
4. **Archive tables** — Container_Event has multiple snapshot versions (_02May2025, _Revised, etc.)
5. **Container_Event location hierarchy** — always use `COALESCE(f.name, l.Name)`:
   - **Facility** (populated) → join `Sealine_Facilities` on `Facility = Id` → `f.name` = physical terminal (preferred)
   - **Location** (fallback) → join `Sealine_Locations` on `Location = Id` → `l.Name` = general route stop (used only when Facility is NULL)
6. **Container_Event.Actual flag**:
   - `Actual = 1` → Date is actual/confirmed event date
   - `Actual = 0` → Date is estimated/predictive
7. **Sealine_Route** — scheduled ETD/ETA dates only (NOT the full container route):
   - `RouteType` describes the stop role: `Pre-Pol` → `Pol` (Port of Loading) → `Pod` (Port of Discharge) → `Post-Pod`
   - `Location_Id` → `Sealine_Locations.Id` (same TrackNumber) for place name and coordinates
   - `Date` + `IsActual` derive the shipping term:
     - `Pol` + IsActual=0 → **ETD** (Estimated Time of Departure)
     - `Pol` + IsActual=1 → **ATD** (Actual Time of Departure)
     - `Pod` + IsActual=0 → **ETA** (Estimated Time of Arrival)
     - `Pod` + IsActual=1 → **ATA** (Actual Time of Arrival)
8. **Route queries — use Sealine_Container_Event as the authoritative source**:
   - `Sealine_Container_Event` holds the **true, complete route** for each container
   - All port stops, transshipments, arrivals, and departures are recorded here
   - Order events by `TRY_CAST(Order_Id AS INT) ASC` to get chronological route
   - Use `Actual = 1` for confirmed stops, `Actual = 0` for estimated/future stops
   - Join to `Sealine_Facilities` (via Facility) or `Sealine_Locations` (via Location) for place names and coordinates
   - `Sealine_Route` only has scheduled ETD/ETA dates — NOT the full stop-by-stop route
