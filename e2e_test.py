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

TABLES = {
    "Sealine_Header":    ["H.DELETEDDT IS NULL",    "SEALINE_HEADER.DELETEDDT IS NULL"],
    "Sealine_Route":     ["R.DELETEDDT IS NULL",    "SEALINE_ROUTE.DELETEDDT IS NULL",
                          "R_FROM.DELETEDDT IS NULL", "R_TO.DELETEDDT IS NULL"],
    "Sealine_Locations": ["L.DELETEDDT IS NULL",    "SEALINE_LOCATIONS.DELETEDDT IS NULL",
                          "L_FROM.DELETEDDT IS NULL", "L_TO.DELETEDDT IS NULL"],
}
def check_soft_deletes(sql):
    su = sql.upper()
    missing = []
    for t, filters in TABLES.items():
        if t.upper() in su and not any(f in su for f in filters):
            missing.append(t)
    return missing

def p(s=""): print(s)

p("=== E2E TEST REPORT - Sealine Data Chat ===")
p("Run time: %s" % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
p()

passed = 0; sql_matched = 0; total_warnings = 0; N = 5

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

# ── SUMMARY ─────────────────────────────────────────────────────────────────
p()
p("=" * 50)
p("SUMMARY")
p("  Tests passed  : %d / %d" % (passed, N))
p("  SQL match     : %d / %d (exact or close match >= 85%%)" % (sql_matched, N))
p("  SQL warnings  : %d (soft-delete filters missing)" % total_warnings)
p("  Overall       : %s" % ("ALL PASS" if passed == N else "%d FAILED" % (N - passed)))
p("=" * 50)
