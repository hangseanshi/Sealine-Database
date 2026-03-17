import requests, json, time, re, difflib
from datetime import datetime

BASE = "http://localhost:8080/api"

# Expected SQL from test plan doc
EXPECTED_SQL = {
    1: "SELECT COUNT(*) AS InTransitCount FROM Sealine_Header WHERE Status = 'IN_TRANSIT' AND DeletedDt IS NULL",

    2: ("SELECT COUNT(DISTINCT r.TrackNumber) AS DeparturesFromHouston "
        "FROM Sealine_Header h "
        "INNER JOIN Sealine_Route r ON h.TrackNumber = r.TrackNumber "
        "INNER JOIN Sealine_Locations l ON r.TrackNumber = l.TrackNumber AND r.Location_Id = l.Id "
        "WHERE h.Status = 'IN_TRANSIT' AND r.RouteType = 'Pol' AND l.Name LIKE '%Houston%' "
        "AND h.DeletedDt IS NULL AND r.DeletedDt IS NULL AND l.DeletedDt IS NULL"),

    3: ("SELECT TrackNumber, Lat, Lng, LocationName, RouteType, MinOrderId, NoOfContainers, EventLines "
        "FROM v_sealine_tracking_route "
        "WHERE TrackNumber IN ('038NY1490725', '038NY1485768') "
        "ORDER BY TrackNumber, MinOrderId ASC"),

    4: ("SELECT v.Container_NUMBER, v.TrackNumber, v.Lat, v.Lng, v.LocationName, "
        "v.MinOrderId, v.EventLines, v.Vessel "
        "FROM v_sealine_container_route v "
        "WHERE v.TrackNumber IN ('038VH9479901', '038VH9465510') "
        "AND v.Lat IS NOT NULL AND v.Lng IS NOT NULL "
        "ORDER BY v.TrackNumber, v.Container_NUMBER, v.MinOrderId ASC"),

    5: ("SELECT DISTINCT h.TrackNumber "
        "FROM Sealine_Header h "
        "INNER JOIN Sealine_Route r_from ON h.TrackNumber = r_from.TrackNumber AND r_from.DeletedDt IS NULL "
        "INNER JOIN Sealine_Locations l_from ON r_from.TrackNumber = l_from.TrackNumber AND r_from.Location_Id = l_from.Id AND l_from.DeletedDt IS NULL "
        "INNER JOIN Sealine_Route r_to ON h.TrackNumber = r_to.TrackNumber AND r_to.DeletedDt IS NULL "
        "INNER JOIN Sealine_Locations l_to ON r_to.TrackNumber = l_to.TrackNumber AND r_to.Location_Id = l_to.Id AND l_to.DeletedDt IS NULL "
        "WHERE h.DeletedDt IS NULL "
        "AND l_from.Name = 'Houston' AND r_from.RouteType = 'Pol' "
        "AND l_to.Country = 'China' AND r_to.RouteType = 'Pod' "
        "AND r_to.Date BETWEEN GETDATE() AND DATEADD(DAY, 7, GETDATE())"),

    6: ("SELECT l.Country AS Country, COUNT(h.TrackNumber) AS TrackingCount "
        "FROM Sealine_Header h "
        "INNER JOIN Sealine_Route r ON h.TrackNumber = r.TrackNumber AND r.DeletedDt IS NULL "
        "INNER JOIN Sealine_Locations l ON r.TrackNumber = l.TrackNumber AND r.Location_Id = l.Id AND l.DeletedDt IS NULL "
        "WHERE h.DeletedDt IS NULL AND r.RouteType = 'Pod' "
        "GROUP BY l.Country ORDER BY TrackingCount DESC"),

    7: None,  # no SQL — geocode/map lookup only

    8: ("WITH t AS (SELECT r.*, ROW_NUMBER() OVER (PARTITION BY r.Container_NUMBER ORDER BY r.MinOrderId DESC) rn "
        "FROM v_sealine_container_route r "
        "LEFT JOIN sealine_header h ON (h.TrackNumber = r.TrackNumber AND h.DeletedDt IS NULL) "
        "WHERE r.isTransitLocation = 'Y' AND h.status = 'IN_TRANSIT' AND r.eventLines LIKE '%(A)%') "
        "SELECT trackNumber, COUNT(DISTINCT locationName) AS DistinctTransitLocations FROM t "
        "WHERE t.rn = 1 AND NOT EXISTS (SELECT 1 FROM v_sealine_container_route r1 "
        "WHERE r1.Container_NUMBER = t.Container_NUMBER AND r1.EventLines LIKE '%POD%' AND r1.EventLines LIKE '%(A)%') "
        "GROUP BY trackNumber HAVING COUNT(DISTINCT locationName) > 1 ORDER BY DistinctTransitLocations DESC"),

    9: ("WITH pod_locations AS ( "
        "SELECT DISTINCT h.TrackNumber, l.Name AS POD_Location, "
        "TRY_CAST(l.Lat AS FLOAT) AS Lat, TRY_CAST(l.Lng AS FLOAT) AS Lng "
        "FROM Sealine_Header h "
        "INNER JOIN Sealine_Route r ON h.TrackNumber = r.TrackNumber AND r.DeletedDt IS NULL "
        "INNER JOIN Sealine_Locations l ON r.TrackNumber = l.TrackNumber AND r.Location_Id = l.Id AND l.DeletedDt IS NULL "
        "WHERE h.Status = 'IN_TRANSIT' AND r.RouteType = 'Pod' AND r.DeletedDt IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM Sealine_Route r2 WHERE r2.TrackNumber = h.TrackNumber "
        "AND r2.RouteType IN ('Pod', 'Post POD') AND r2.IsActual = 1 AND r2.DeletedDt IS NULL) ), "
        "war_zone_pod AS ( SELECT TrackNumber, POD_Location, Lat, Lng FROM pod_locations "
        "WHERE (Lat BETWEEN 29 AND 33.5 AND Lng BETWEEN 33.8 AND 36.5) OR "
        "(Lat BETWEEN 41 AND 48 AND Lng BETWEEN 28 AND 42) OR "
        "(Lat BETWEEN 12 AND 28 AND Lng BETWEEN 32 AND 52) OR "
        "(Lat BETWEEN 8 AND 23 AND Lng BETWEEN 22 AND 38) ) "
        "SELECT TrackNumber, POD_Location, Lat, Lng FROM war_zone_pod ORDER BY TrackNumber"),
}

def normalize_sql(sql):
    return re.sub(r'\s+', ' ', sql.upper().strip().rstrip(';')).strip()

def compare_sql(actual, expected):
    a = normalize_sql(actual)
    e = normalize_sql(expected)
    if a == e:
        return "MATCH", 100, []
    ratio = difflib.SequenceMatcher(None, a, e).ratio()
    pct = round(ratio * 100)
    a_words, e_words = a.split(), e.split()
    diff = list(difflib.ndiff(e_words, a_words))
    removed = [w[2:] for w in diff if w.startswith('- ')]
    added   = [w[2:] for w in diff if w.startswith('+ ')]
    diff_lines = []
    if removed: diff_lines.append("    - (in expected, not actual) : " + ' '.join(removed))
    if added:   diff_lines.append("    + (in actual, not expected)  : " + ' '.join(added))
    label = "CLOSE MATCH" if pct >= 85 else "MISMATCH"
    return "%s (%d%%)" % (label, pct), pct, diff_lines

def run_test(question):
    session_id = requests.post("%s/sessions" % BASE, json={"title": "E2E Test"}, timeout=10).json()["session_id"]
    sqls, results, files, raw_parts = [], [], [], []
    with requests.post("%s/sessions/%s/messages" % (BASE, session_id),
                       json={"message": question}, stream=True, timeout=300) as resp:
        for line in resp.iter_lines():
            if not line: continue
            raw = line.decode("utf-8", errors="replace")
            if not raw.startswith("data:"): continue
            try:
                data = json.loads(raw[5:].strip())
                if "query" in data and "tool" in data:    sqls.append(data["query"])
                elif "result" in data and "tool" in data: results.append(data["result"])
                elif "file" in data:                      files.append(data["file"])
                raw_parts.append(json.dumps(data))
            except: pass
    return sqls, results, files, "\n".join(raw_parts)

TABLES = ["Sealine_Header", "Sealine_Route", "Sealine_Locations"]

def check_soft_deletes(sql):
    """Check that every table joined in the SQL has a soft-delete filter applied."""
    su = sql.upper()
    missing = []
    for t in TABLES:
        t_upper = t.upper()
        if t_upper not in su:
            continue
        # Find all aliases assigned to this table: "JOIN/FROM TableName [AS] alias"
        aliases = set()
        for m in re.finditer(r'\b' + t_upper + r'(?:\s+AS)?\s+(\w+)', su):
            a = m.group(1)
            if a not in ('ON', 'WHERE', 'AND', 'SET', 'INNER', 'LEFT', 'RIGHT'):
                aliases.add(a)
        # Also accept table name used directly (no alias)
        if t_upper + '.DELETEDDT IS NULL' in su:
            continue
        # Check that at least one alias has .DELETEDDT IS NULL
        found = any(a + '.DELETEDDT IS NULL' in su for a in aliases)
        # Or the bare column form: "DELETEDDT IS NULL" appears immediately after a WHERE/AND
        # (handles: WHERE DeletedDt IS NULL with no alias, e.g. Test 1)
        if not found and not aliases and 'DELETEDDT IS NULL' in su:
            found = True
        if not found:
            missing.append(t)
    return missing

def p(s=""): print(s)

p("=== E2E TEST REPORT - Sealine Data Chat ===")
p("Run time: %s" % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
p()

passed = 0; sql_matched = 0; total_warnings = 0; N = 9

# ── TEST 1 ──────────────────────────────────────────────────────────────────
p("Running test 1...")
sqls, db_results, files, raw = run_test("How many tracking are in transit.")
time.sleep(2)
sql   = sqls[0] if sqls else "NO SQL CAPTURED"
dout  = db_results[0].strip() if db_results else "NO RESULT"
nums  = re.findall(r'\d+', dout.replace(",",""))
count = int(nums[0]) if nums else None
lo, hi = 9000, 10000
st    = "PASS" if count is not None and lo <= count <= hi else "FAIL"
miss  = check_soft_deletes(sql)
sq_label, sq_pct, sq_diff = compare_sql(sql, EXPECTED_SQL[1])
if st == "PASS":  passed += 1
if sq_pct >= 85:  sql_matched += 1
if miss:          total_warnings += len(miss)
p("\nTEST 1 - How many tracking are in transit.")
p("  Status        : %s" % st)
p("  Expected range: %s - %s" % ("{:,}".format(lo), "{:,}".format(hi)))
p("  Actual count  : %s" % (count if count is not None else "UNKNOWN"))
p("  Generated SQL : %s" % sql)
p("  Expected SQL  : %s" % EXPECTED_SQL[1])
p("  SQL match     : %s" % sq_label)
for d in sq_diff: p(d)
p("  Soft-delete   : %s" % ("OK" if not miss else "WARNING - missing: " + ", ".join(miss)))

# ── TEST 2 ──────────────────────────────────────────────────────────────────
p("\nRunning test 2...")
sqls, db_results, files, raw = run_test("How many in transit tracking depart from Houston.")
time.sleep(2)
sql   = sqls[0] if sqls else "NO SQL CAPTURED"
dout  = db_results[0].strip() if db_results else "NO RESULT"
nums  = re.findall(r'\d+', dout.replace(",",""))
count = int(nums[0]) if nums else None
lo, hi = 6000, 7000
st    = "PASS" if count is not None and lo <= count <= hi else "FAIL"
miss  = check_soft_deletes(sql)
sq_label, sq_pct, sq_diff = compare_sql(sql, EXPECTED_SQL[2])
if st == "PASS":  passed += 1
if sq_pct >= 85:  sql_matched += 1
if miss:          total_warnings += len(miss)
p("\nTEST 2 - How many in transit tracking depart from Houston.")
p("  Status        : %s" % st)
p("  Expected range: %s - %s" % ("{:,}".format(lo), "{:,}".format(hi)))
p("  Actual count  : %s" % (count if count is not None else "UNKNOWN"))
p("  Generated SQL : %s" % sql)
p("  Expected SQL  : %s" % EXPECTED_SQL[2])
p("  SQL match     : %s" % sq_label)
for d in sq_diff: p(d)
p("  Soft-delete   : %s" % ("OK" if not miss else "WARNING - missing: " + ", ".join(miss)))

# ── TEST 3 ──────────────────────────────────────────────────────────────────
p("\nRunning test 3...")
sqls, db_results, files, raw = run_test("show me the route for trackings 038NY1490725, 038NY1485768")
time.sleep(2)
sql      = sqls[0] if sqls else "NO SQL CAPTURED"
su       = sql.upper()
html_found   = bool(files) or bool(re.search(r'[\w\-]+\.html', raw))
map_file     = files[0] if files else "(in stream)"
view_ok      = "V_SEALINE_TRACKING_ROUTE" in su
sq_label, sq_pct, sq_diff = compare_sql(sql, EXPECTED_SQL[3])
st = "PASS" if (view_ok and html_found) else "FAIL"
if st == "PASS":  passed += 1
if sq_pct >= 85:  sql_matched += 1
p("\nTEST 3 - show me the route for trackings 038NY1490725, 038NY1485768")
p("  Status        : %s" % st)
p("  Map generated : %s" % ("YES - " + str(map_file) if html_found else "NO"))
p("  View used     : %s" % ("v_sealine_tracking_route [OK]" if view_ok else "WRONG VIEW [FAIL]"))
p("  Generated SQL : %s" % sql)
p("  Expected SQL  : %s" % EXPECTED_SQL[3])
p("  SQL match     : %s" % sq_label)
for d in sq_diff: p(d)

# ── TEST 4 ──────────────────────────────────────────────────────────────────
p("\nRunning test 4...")
sqls, db_results, files, raw = run_test("show me the containers route map for tracking 038VH9479901 and 038VH9465510")
time.sleep(2)
sql      = sqls[0] if sqls else "NO SQL CAPTURED"
su       = sql.upper()
html_found   = bool(files) or bool(re.search(r'[\w\-]+\.html', raw))
map_file     = files[0] if files else "(in stream)"
view_ok      = "V_SEALINE_CONTAINER_ROUTE" in su
vessel_ok    = "VESSEL" in su
no_bad_cols  = "COUNTRY_CODE" not in su and "LOCODE" not in su
sq_label, sq_pct, sq_diff = compare_sql(sql, EXPECTED_SQL[4])
st = "PASS" if (view_ok and html_found and vessel_ok and no_bad_cols) else "FAIL"
if st == "PASS":  passed += 1
if sq_pct >= 85:  sql_matched += 1
p("\nTEST 4 - show me the containers route map for tracking 038VH9479901 and 038VH9465510")
p("  Status        : %s" % st)
p("  Map generated : %s" % ("YES - " + str(map_file) if html_found else "NO"))
p("  View used     : %s" % ("v_sealine_container_route [OK]" if view_ok else "WRONG VIEW [FAIL]"))
p("  Vessel col    : %s" % ("present [OK]" if vessel_ok else "MISSING [FAIL]"))
p("  Bad cols      : %s" % ("none [OK]" if no_bad_cols else "Country_Code/LOCode present [FAIL]"))
p("  Generated SQL : %s" % sql)
p("  Expected SQL  : %s" % EXPECTED_SQL[4])
p("  SQL match     : %s" % sq_label)
for d in sq_diff: p(d)

# ── TEST 5 ──────────────────────────────────────────────────────────────────
p("\nRunning test 5...")
sqls, db_results, files, raw = run_test("list all the tracking numbers from houston to china and will arrive in next 7 days")
time.sleep(2)
sql      = sqls[0] if sqls else "NO SQL CAPTURED"
su       = sql.upper()
houston_ok   = "HOUSTON" in su
china_ok     = "CHINA" in su
date_ok      = "GETDATE" in su and "7" in su
pol_ok       = "POL" in su
pod_ok       = "POD" in su
miss         = check_soft_deletes(sql)
sq_label, sq_pct, sq_diff = compare_sql(sql, EXPECTED_SQL[5])
st = "PASS" if (houston_ok and china_ok and date_ok and pol_ok and pod_ok and not miss) else "FAIL"
if st == "PASS":  passed += 1
if sq_pct >= 85:  sql_matched += 1
if miss:          total_warnings += len(miss)
p("\nTEST 5 - list all tracking numbers from Houston to China arriving in next 7 days")
p("  Status        : %s" % st)
p("  Houston filter: %s" % ("OK" if houston_ok else "MISSING [FAIL]"))
p("  China filter  : %s" % ("OK" if china_ok   else "MISSING [FAIL]"))
p("  7-day window  : %s" % ("OK" if date_ok    else "MISSING [FAIL]"))
p("  POL/POD types : %s" % ("OK" if (pol_ok and pod_ok) else "MISSING [FAIL]"))
p("  Generated SQL : %s" % sql)
p("  Expected SQL  : %s" % EXPECTED_SQL[5])
p("  SQL match     : %s" % sq_label)
for d in sq_diff: p(d)
p("  Soft-delete   : %s" % ("OK" if not miss else "WARNING - missing: " + ", ".join(miss)))

# ── TEST 6 ──────────────────────────────────────────────────────────────────
p("\nRunning test 6...")
sqls, db_results, files, raw = run_test("show me all active tracking count by POD country in a shaded country map")
time.sleep(2)
sql      = sqls[0] if sqls else "NO SQL CAPTURED"
su       = sql.upper()
html_found   = bool(files) or bool(re.search(r'[\w\-]+\.html', raw))
country_ok   = "COUNTRY" in su
pod_ok       = "POD" in su
group_ok     = "GROUP BY" in su
miss         = check_soft_deletes(sql)
sq_label, sq_pct, sq_diff = compare_sql(sql, EXPECTED_SQL[6])
st = "PASS" if (country_ok and pod_ok and group_ok and html_found and not miss) else "FAIL"
if st == "PASS":  passed += 1
if sq_pct >= 85:  sql_matched += 1
if miss:          total_warnings += len(miss)
p("\nTEST 6 - show me all active tracking count by POD country in a shaded country map")
p("  Status        : %s" % st)
p("  Map generated : %s" % ("YES" if html_found else "NO [FAIL]"))
p("  Country col   : %s" % ("OK" if country_ok else "MISSING [FAIL]"))
p("  POD filter    : %s" % ("OK" if pod_ok     else "MISSING [FAIL]"))
p("  GROUP BY      : %s" % ("OK" if group_ok   else "MISSING [FAIL]"))
p("  Generated SQL : %s" % sql)
p("  Expected SQL  : %s" % EXPECTED_SQL[6])
p("  SQL match     : %s" % sq_label)
for d in sq_diff: p(d)
p("  Soft-delete   : %s" % ("OK" if not miss else "WARNING - missing: " + ", ".join(miss)))

# ── TEST 7 ──────────────────────────────────────────────────────────────────
p("\nRunning test 7...")
sqls, db_results, files, raw = run_test("show me Jawaharlal Nehru, IN (INNSA) in the map")
time.sleep(2)
html_found = bool(files) or bool(re.search(r'[\w\-]+\.html', raw))
innsa_ok   = "INNSA" in raw.upper() or "JAWAHARLAL" in raw.upper() or "NHAVA" in raw.upper()
st = "PASS" if (html_found and innsa_ok) else "FAIL"
if st == "PASS": passed += 1
sql_matched += 1  # no SQL expected, count as matched
p("\nTEST 7 - show me Jawaharlal Nehru, IN (INNSA) in the map")
p("  Status        : %s" % st)
p("  Map generated : %s" % ("YES" if html_found else "NO [FAIL]"))
p("  Port found    : %s" % ("YES" if innsa_ok   else "NO [FAIL]"))
p("  (No SQL expected for this test)")

# ── TEST 8 ──────────────────────────────────────────────────────────────────
p("\nRunning test 8...")
sqls, db_results, files, raw = run_test("any tracking have containers traveling in different transit locations?")
time.sleep(2)
sql      = sqls[0] if sqls else "NO SQL CAPTURED"
su       = sql.upper()
dout     = db_results[0].strip() if db_results else "NO RESULT"
nums     = re.findall(r'\d+', dout.replace(",",""))
count    = int(nums[0]) if nums else None
transit_ok   = "ISTRANSITLOCATION" in su or "TRANSIT" in su
view_ok      = "V_SEALINE_CONTAINER_ROUTE" in su
having_ok    = "HAVING" in su
sq_label, sq_pct, sq_diff = compare_sql(sql, EXPECTED_SQL[8])
st = "PASS" if (transit_ok and view_ok and having_ok and count is not None and count < 50) else "FAIL"
if st == "PASS":  passed += 1
if sq_pct >= 85:  sql_matched += 1
p("\nTEST 8 - any tracking have containers traveling in different transit locations?")
p("  Status        : %s" % st)
p("  Result count  : %s (expected < 50)" % (count if count is not None else "UNKNOWN"))
p("  Transit filter: %s" % ("OK" if transit_ok else "MISSING [FAIL]"))
p("  View used     : %s" % ("v_sealine_container_route [OK]" if view_ok else "WRONG [FAIL]"))
p("  HAVING clause : %s" % ("OK" if having_ok  else "MISSING [FAIL]"))
p("  Generated SQL : %s" % sql)
p("  Expected SQL  : %s" % EXPECTED_SQL[8])
p("  SQL match     : %s" % sq_label)
for d in sq_diff: p(d)

# ── TEST 9 ──────────────────────────────────────────────────────────────────
p("\nRunning test 9...")
sqls, db_results, files, raw = run_test("show me a list of tracking with their POD location that is in transit and not delivered. And the POD location is in the war zones.")
time.sleep(2)
sql      = sqls[0] if sqls else "NO SQL CAPTURED"
su       = sql.upper()
dout     = db_results[0].strip() if db_results else "NO RESULT"
nums     = re.findall(r'\d+', dout.replace(",",""))
count    = int(nums[0]) if nums else None
warzone_ok   = any(x in su for x in ["WAR", "GAZA", "UKRAINE", "RED SEA", "SUDAN", "LAT BETWEEN", "LNG BETWEEN"])
pod_ok       = "POD" in su
transit_ok   = "IN_TRANSIT" in su
miss         = check_soft_deletes(sql)
sq_label, sq_pct, sq_diff = compare_sql(sql, EXPECTED_SQL[9])
st = "PASS" if (warzone_ok and pod_ok and transit_ok and not miss) else "FAIL"
if st == "PASS":  passed += 1
if sq_pct >= 85:  sql_matched += 1
if miss:          total_warnings += len(miss)
p("\nTEST 9 - tracking with POD in war zones, in transit and not delivered")
p("  Status        : %s" % st)
p("  Result count  : %s (expected < 500)" % (count if count is not None else "UNKNOWN"))
p("  War zone bbox : %s" % ("OK" if warzone_ok  else "MISSING [FAIL]"))
p("  POD filter    : %s" % ("OK" if pod_ok      else "MISSING [FAIL]"))
p("  IN_TRANSIT    : %s" % ("OK" if transit_ok  else "MISSING [FAIL]"))
p("  Generated SQL : %s" % sql)
p("  Expected SQL  : %s" % EXPECTED_SQL[9])
p("  SQL match     : %s" % sq_label)
for d in sq_diff: p(d)
p("  Soft-delete   : %s" % ("OK" if not miss else "WARNING - missing: " + ", ".join(miss)))

# ── SUMMARY ─────────────────────────────────────────────────────────────────
p()
p("=" * 50)
p("SUMMARY")
p("  Tests passed  : %d / %d" % (passed, N))
p("  SQL match     : %d / %d (exact or close match >= 85%%)" % (sql_matched, N))
p("  SQL warnings  : %d (soft-delete filters missing)" % total_warnings)
p("  Overall       : %s" % ("ALL PASS" if passed == N else "%d FAILED" % (N - passed)))
p("=" * 50)
