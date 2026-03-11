# Sealine Database — Relationships & Join Logic

All relationships are **logical** (no enforced FK constraints in DB). Always join on `TrackNumber` AND the specific ID columns.

## Primary Relationships

### Sealine_Header (Root Entity)
```
Sealine_Header (TrackNumber) — 1:N relationships:
├── Sealine_Container (TrackNumber)
├── Sealine_Container_Event (TrackNumber)
├── Sealine_Locations (TrackNumber)
├── Sealine_Vessels (TrackNumber)
├── Sealine_Route (TrackNumber)
├── Sealine_Facilities (TrackNumber)
└── Sealine_Tracking_Response (TrackNumber)
```

### Container Event Location Chain

**Sealine_Container_Event** references both locations and facilities:

```sql
-- Full container event with location details
SELECT
    e.*,
    COALESCE(f.name, l.Name) AS EventLocation,
    l.Country, l.LOCode,
    TRY_CAST(l.Lat AS FLOAT) AS Lat,
    TRY_CAST(l.Lng AS FLOAT) AS Lng
FROM Sealine_Container_Event e
LEFT JOIN Sealine_Facilities f
    ON e.TrackNumber = f.TrackNumber
    AND e.Facility = f.Id
LEFT JOIN Sealine_Locations l
    ON e.TrackNumber = l.TrackNumber
    AND e.Location = l.Id
ORDER BY e.Order_Id ASC
```

**Location hierarchy:**
- If `Facility` is present → specific port/terminal detail (f.name)
- If `Facility` is NULL → use `Location` (general route stop, l.Name)

### Route Location Reference

```sql
-- Route with location details
SELECT
    r.*,
    l.Name, l.Country, l.LOCode,
    TRY_CAST(l.Lat AS FLOAT) AS Lat,
    TRY_CAST(l.Lng AS FLOAT) AS Lng
FROM Sealine_Route r
LEFT JOIN Sealine_Locations l
    ON r.TrackNumber = l.TrackNumber
    AND r.Location_Id = l.Id
```

## Key Flags & Indicators

### Actual vs Estimated Dates

**Sealine_Container_Event.Actual**
- `= 1` → Date is actual/confirmed event date (what really happened)
- `= 0` → Date is estimated/forecasted (prediction)

**Sealine_Route.IsActual**
- `= 1` → Actual arrival/departure (ATD/ATA)
- `= 0` → Planned/scheduled (ETD/ETA)

### Event Sequencing

**Sealine_Container_Event.Order_Id**
- Chronological sequence for each container
- Always start with `Order_Id = 1` (earliest event)
- Increments for subsequent events
- Use for timeline reconstruction independent of Date values

## Request & API Flow

```
Searates_Request_Tracking (TrackingNo)
    ├── Carrier lookup → Carrier_Sealine_Mapaping.Carrier
    │                  → Sealine_Header (via SealineCode)
    │
    └── LastResponseId → ResponseLog.Id
                      → ResponseLog.Response (raw JSON)

Sealine_Tracking_Response
    ├── batch → batch processing identifier
    └── TrackNumber → Sealine_Header
```

## Data Quality Notes

1. **No enforced referential integrity** — handle NULLs and orphans gracefully
2. **Soft deletes** — `DeletedDt IS NULL` to filter active records
3. **Lat/Lng casting** — always use `TRY_CAST(Lat AS FLOAT)` and `TRY_CAST(Lng AS FLOAT)`
4. **Archive tables** exist for Container_Event (snapshots from different dates)
5. **Multiple Location/Facility records per shipment** — use Order_Id or dates to sequence
