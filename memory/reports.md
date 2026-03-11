# Reusable Reports

All reports are Python scripts in `C:\Users\hangs\OneDrive\GitHub\Sealine-Database\`.
Run any report with: `python <script_name>`
Each report queries live data, saves TXT + Excel, and emails results to `hangseanshi@gmail.com`.

---

## 1. In transit container in different city report

- **Script**: `query_intransit.py`
- **Description**: Shows all IN_TRANSIT tracking numbers where containers are currently located in **more than one city** (their latest actual event locations differ by city/LOCode).
- **Filters**:
  - `Sealine_Header.Status = 'IN_TRANSIT'`
  - `Sealine_Header.Sealine_Code <> 'DHC2'`
  - `Sealine_Container_Event.Actual = 1` (confirmed events only)
  - Soft deletes excluded (`DeletedDt IS NULL`)
- **Output columns**: TrackNumber | Sealine_Code | ContainerCount | DistinctCities | Cities (comma-delimited)
- **Output**: Console/screen only (no file saved, no email sent)

### SQL Query
```sql
WITH LatestActualEvents AS (
    SELECT
        e.TrackNumber,
        e.Container_NUMBER,
        e.Location,
        e.Facility,
        h.Sealine_Code,
        ROW_NUMBER() OVER (
            PARTITION BY e.TrackNumber, e.Container_NUMBER
            ORDER BY TRY_CAST(e.Order_Id AS INT) DESC
        ) AS rn
    FROM Sealine_Container_Event e
    INNER JOIN Sealine_Header h ON e.TrackNumber = h.TrackNumber
    WHERE e.Actual = 1
      AND e.DeletedDt IS NULL
      AND h.Status = 'IN_TRANSIT'
      AND h.DeletedDt IS NULL
      AND h.Sealine_Code <> 'DHC2'
),
LatestWithLocation AS (
    SELECT
        lae.TrackNumber,
        lae.Sealine_Code,
        lae.Container_NUMBER,
        l.Name   AS CityName,
        l.LOCode AS CityCode
    FROM LatestActualEvents lae
    LEFT JOIN Sealine_Locations l
        ON lae.TrackNumber = l.TrackNumber
        AND lae.Location = l.Id
        AND l.DeletedDt IS NULL
    WHERE lae.rn = 1
)
SELECT
    TrackNumber,
    MAX(Sealine_Code)                AS Sealine_Code,
    COUNT(DISTINCT Container_NUMBER) AS ContainerCount,
    COUNT(DISTINCT CityCode)         AS DistinctCities,
    (
        SELECT STRING_AGG(DISTINCT_CITIES.CityName, ', ')
        FROM (
            SELECT DISTINCT CityName
            FROM LatestWithLocation l2
            WHERE l2.TrackNumber = LatestWithLocation.TrackNumber
        ) AS DISTINCT_CITIES
    ) AS Cities
FROM LatestWithLocation
GROUP BY TrackNumber
HAVING COUNT(DISTINCT CityCode) > 1
ORDER BY DistinctCities DESC, ContainerCount DESC
```

---
