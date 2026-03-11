import pyodbc

# --- DB connection ---
conn = pyodbc.connect(
    'DRIVER={ODBC Driver 17 for SQL Server};'
    'SERVER=ushou102-exap1;DATABASE=searates;UID=sean;PWD=4peiling;'
)
cursor = conn.cursor()

sql = """
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
        ON lae.TrackNumber = l.TrackNumber AND lae.Location = l.Id AND l.DeletedDt IS NULL
    WHERE lae.rn = 1
)
SELECT
    TrackNumber,
    MAX(Sealine_Code)                AS Sealine_Code,
    COUNT(DISTINCT Container_NUMBER) AS ContainerCount,
    COUNT(DISTINCT CityCode)         AS DistinctCities,
    (
        SELECT STRING_AGG(DISTINCT_CITIES.CityName, ', ')
        FROM (SELECT DISTINCT CityName FROM LatestWithLocation l2 WHERE l2.TrackNumber = LatestWithLocation.TrackNumber) AS DISTINCT_CITIES
    )                                AS Cities
FROM LatestWithLocation
GROUP BY TrackNumber
HAVING COUNT(DISTINCT CityCode) > 1
ORDER BY DistinctCities DESC, ContainerCount DESC
"""

cursor.execute(sql)
rows = cursor.fetchall()
conn.close()

# --- Print to console ---
header = f'{"TrackNumber":<30} {"Sealine":>8} {"Containers":>10} {"Cities":>8}  City List'
separator = '-' * 130
lines = [
    f'Total: {len(rows)} IN_TRANSIT tracking numbers with containers in different cities',
    '',
    header,
    separator,
]
for r in rows:
    cities = r[4] if r[4] else 'Unknown'
    lines.append(f'{r[0]:<30} {str(r[1]):>8} {r[2]:>10} {r[3]:>8}  {cities}')

print('\n'.join(lines))
