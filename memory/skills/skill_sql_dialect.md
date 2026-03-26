# SQL Query Design & Dialect Rules

## Query Design Rules

### General Principles

- **LAT/LNG quality:** If `Latitude` or `Longitude` is `NULL`, skip those coordinates.
- Tracking route data is derived from `Sealine_Tracking` columns (`Pre-POL` / `POL` / `POD` / `Post-POD`).
- Container route data is derived from `Sealine_Container_Event` grouped by container and location.

### Select Only Required Columns

Return only the columns needed to answer the question — no extras.

- If the user asks `'how many tracking...'`, return only the count.
- If a specific query pattern is provided, follow it exactly with no additions.
- Extra columns often require invalid aggregation syntax (e.g., `STRING_AGG(DISTINCT ...)`) or incorrect `GROUP BY` clauses, which break the query.

---

## SQL Server Dialect

Database: **Microsoft SQL Server 2019** (v15.0.4455.2, compatibility level 150). Always generate valid T-SQL for this exact version.

### Pagination / Limit
Use `SELECT TOP N`. Never use `LIMIT`.

### Aggregation
`STRING_AGG(col, sep) WITHIN GROUP (ORDER BY col)` is supported.
Never use `STRING_AGG(DISTINCT ...)` — it is invalid T-SQL and will error. To deduplicate, use a subquery:
```sql
STRING_AGG(col, ', ') WITHIN GROUP (ORDER BY col)
FROM (SELECT DISTINCT col FROM /* source table/subquery */) sub
```

### Table Aliases — Critical
Every alias used in `SELECT` / `WHERE` / `ORDER BY` must be defined in `FROM` / `JOIN`. Never reference undefined aliases.
```sql
-- ❌ Wrong: alias 't' is undefined
SELECT t.TrackNumber FROM Sealine_Tracking h ...

-- ✅ Correct: alias matches the FROM clause
SELECT h.TrackNumber FROM Sealine_Tracking h ...
```

Always verify every `alias.column` reference before generating a query.

### Null Handling
Use `ISNULL(col, val)` or `COALESCE`. Never `IFNULL` or `NVL`.

### Type Conversion
Use `TRY_CAST(x AS type)` or `TRY_CONVERT(type, x)`. Never `SAFE_CAST`.

### Date / Time
- Current time: `GETDATE()` or `SYSDATETIME()`. Never `NOW()`.
- Truncate to date: `CAST(col AS DATE)` or `CONVERT(DATE, col)`.
- Truncate to month: `DATEFROMPARTS(YEAR(col), MONTH(col), 1)`.
- Date difference: `DATEDIFF(unit, start, end)`.
- Date add: `DATEADD(unit, n, date)`.
- Never use `DATE_TRUNC`, `DATE_FORMAT`, or `EXTRACT` — use `YEAR()` / `MONTH()` / `DAY()` instead.

### Conditionals
Use `IIF(cond, a, b)` or `CASE WHEN ... END`. Never use `IF()` as an expression.

### String Functions
- Concatenation: `CONCAT(a, b)` or `a + b`.
- Length: `LEN()`, not `LENGTH()`.
- Position: `CHARINDEX()`, not `INSTR()`. Use `PATINDEX()` for pattern position.
- Split: `STRING_SPLIT(str, delim)` returns a table.

### Regular Expressions
SQL Server has no `REGEXP`. Use `LIKE` or `PATINDEX` with wildcards (`%`, `_`, `[...]`).

### Not Available in SQL Server 2019
Avoid: `GENERATE_SERIES`, `GREATEST` / `LEAST`, `DATE_BUCKET`, `JSON_ARRAYAGG`, `LATERAL` joins (use `CROSS APPLY` instead), `QUALIFY` clause (use a subquery with `WHERE rn = 1` instead).

### Supported Window Functions
`ROW_NUMBER`, `RANK`, `DENSE_RANK`, `NTILE`, `LAG`, `LEAD`, `FIRST_VALUE`, `LAST_VALUE`, and `SUM` / `AVG` / `COUNT` / `MIN` / `MAX` with `OVER (...)`.

### CTEs and Pivots
`WITH cte AS (...) SELECT ...` is fully supported. `PIVOT` / `UNPIVOT`, `FOR XML PATH('')`, and `FOR JSON PATH` are all supported.
