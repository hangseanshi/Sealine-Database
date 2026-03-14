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
    3: ("SELECT r.TrackNumber, l.Name, TRY_CAST(l.Lat AS FLOAT) AS Lat, TRY_CAST(l.Lng AS FLOAT) AS Lng, "
        "l.Country, r.RouteType, r.Date, r.IsActual, "
        "(SELECT COUNT(DISTINCT ce.Container_NUMBER) FROM Sealine_Container_Event ce "
        "WHERE ce.TrackNumber = r.TrackNumber AND ce.DeletedDt IS NULL) AS ContainerCount "
        "FROM Sealine_Route r "
        "INNER JOIN Sealine_Locations l ON r.TrackNumber = l.TrackNumber AND r.Location_Id = l.Id "
        "WHERE r.TrackNumber IN ('038NY1490725', '038NY1485768') "
        "AND r.RouteType IN ('Pre-Pol', 'Pol', 'Pod', 'Post-Pod') "
        "AND r.DeletedDt IS NULL AND l.DeletedDt IS NULL "
        "AND l.Lat IS NOT NULL AND l.Lng IS NOT NULL "
        "ORDER BY r.TrackNumber, CASE r.RouteType WHEN 'Pre-Pol' THEN 1 WHEN 'Pol' THEN 2 "
        "WHEN 'Pod' THEN 3 WHEN 'Post-Pod' THEN 4 END"),
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
                       json={"message": question}, stream=True, timeout=180) as resp:
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
    "Sealine_Header":          ["H.DELETEDDT IS NULL", "DELETEDDT IS NULL"],
    "Sealine_Route":           ["R.DELETEDDT IS NULL", "SEALINE_ROUTE.DELETEDDT IS NULL"],
    "Sealine_Locations":       ["L.DELETEDDT IS NULL", "SEALINE_LOCATIONS.DELETEDDT IS NULL"],
    "Sealine_Container_Event": ["E.DELETEDDT IS NULL", "SEALINE_CONTAINER_EVENT.DELETEDDT IS NULL"],
}
def check_soft_deletes(sql):
    su = sql.upper()
    return [t for t, filters in TABLES.items() if t.upper() in su and not any(f in su for f in filters)]

def p(s=""): print(s)

p("=== E2E TEST REPORT - Sealine Data Chat ===")
p("Run time: %s" % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
p()

passed = 0; sql_matched = 0; total_warnings = 0; N = 3

# TEST 1
p("Running test 1...")
sqls, db_results, files, raw = run_test("How many tracking are in transit.")
time.sleep(3)
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

# TEST 2
p("\nRunning test 2...")
sqls, db_results, files, raw = run_test("How many in transit tracking depart from Houston.")
time.sleep(3)
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

# TEST 3
p("\nRunning test 3...")
sqls, db_results, files, raw = run_test("show me the route for trackings 038NY1490725, 038NY1485768")
time.sleep(3)
sql = sqls[0] if sqls else "NO SQL CAPTURED"
su  = sql.upper()
html_found   = bool(files) or bool(re.search(r'[\w\-]+\.html', raw))
map_file     = files[0] if files else (re.search(r'[\w\-]+\.html', raw) or [None])[0] or "(in stream)"
driver_ok    = "FROM SEALINE_ROUTE" in su or "SEALINE_ROUTE R" in su
routetype_ok = "ROUTETYPE IN" in su and "PRE-POL" in su
miss  = check_soft_deletes(sql)
sq_label, sq_pct, sq_diff = compare_sql(sql, EXPECTED_SQL[3])
st = "PASS" if (driver_ok and routetype_ok) else "FAIL"
if st == "PASS":  passed += 1
if sq_pct >= 85:  sql_matched += 1
if miss:          total_warnings += len(miss)
p("\nTEST 3 - show me the route for trackings 038NY1490725, 038NY1485768")
p("  Status        : %s" % st)
p("  Map generated : %s" % ("YES - " + str(map_file) if html_found else "NO"))
p("  SQL driver    : %s" % ("Sealine_Route [OK]" if driver_ok else "NOT Sealine_Route [FAIL]"))
p("  RouteType filt: %s" % ("present [OK]" if routetype_ok else "missing [FAIL]"))
p("  Generated SQL : %s" % sql)
p("  Expected SQL  : %s" % EXPECTED_SQL[3])
p("  SQL match     : %s" % sq_label)
for d in sq_diff: p(d)
p("  Soft-delete   : %s" % ("OK" if not miss else "WARNING - missing: " + ", ".join(miss)))

# SUMMARY
p()
p("SUMMARY")
p("  Tests passed  : %d / %d" % (passed, N))
p("  SQL match     : %d / %d (exact or close match >= 85%%)" % (sql_matched, N))
p("  SQL warnings  : %d (soft-delete filters missing)" % total_warnings)
p("  Overall       : %s" % ("ALL PASS" if passed == N else "%d FAILED" % (N - passed)))
