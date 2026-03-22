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
        e.[Container Name] AS Container_NUMBER,
        e.[Location Name] AS LocationName,
        e.[Location LOCode] AS LocationCode,
        t.Sealine_Code,
        ROW_NUMBER() OVER (
            PARTITION BY e.TrackNumber, e.[Container Name]
            ORDER BY e.[Event Sequence ID] DESC
        ) AS rn
    FROM Sealine_Container_Event e
    INNER JOIN Sealine_Tracking t ON e.TrackNumber = t.TrackNumber
    WHERE e.[Event Ocurred] = 'Yes'
      AND t.[Tracking Status] IN ('Pending Departure', 'Departed from Origin')
      AND t.Sealine_Code <> 'DHC2'
),
LatestWithLocation AS (
    SELECT
        TrackNumber,
        Sealine_Code,
        Container_NUMBER,
        LocationName  AS CityName,
        LocationCode  AS CityCode
    FROM LatestActualEvents
    WHERE rn = 1
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
