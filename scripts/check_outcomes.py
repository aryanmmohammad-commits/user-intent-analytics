"""Contract check for the outcomes loop: Cube's outcomes cube must match dbt's fct_org_outcomes.

Run after dbt build and the export, with Cube running, from the dbt project folder:
    python scripts/check_outcomes.py
Exit code 1 on any failure.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import duckdb

API = os.environ.get("CUBE_API_URL", "http://localhost:4000/cubejs-api/v1").rstrip("/")
results = []


def record(ok, text):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {text}")


def cube(path, query=None):
    url = f"{API}/{path}" + ("" if query is None else "?query=" + urllib.parse.quote(json.dumps(query)))
    for _ in range(30):
        with urllib.request.urlopen(url, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if body.get("error") == "Continue wait":
            time.sleep(2)
            continue
        if "error" in body:
            raise RuntimeError(body["error"])
        return body
    raise RuntimeError("Cube kept answering Continue wait")


def num(v):
    return None if v is None else round(float(v), 6)


con = duckdb.connect("dev.duckdb", read_only=True)


def table(name):
    """schema.name of a dbt model, wherever dbt built it (views count too)."""
    found = con.execute("select table_schema from information_schema.tables where table_name = ?", [name]).fetchall()
    if len(found) != 1:
        raise SystemExit(f"STOP: {name}: expected 1 table or view, found {len(found)}. Run dbt build first.")
    return f"{found[0][0]}.{name}"


OUT, SCORES, STG = table("fct_org_outcomes"), table("fct_org_intent_score"), table("stg_sales_ops__pqa_outcomes")
latest = str(con.execute(f"select max(run_date) from {OUT}").fetchone()[0])[:10]
on_day = {"member": "outcomes.run_date", "operator": "inDateRange", "values": [latest, latest]}

# 1. Totals on the latest run date
dbt = con.execute(f"""
    select count(*), count(*) filter (where is_pqa), count(*) filter (where was_called),
           count(*) filter (where is_pqa and was_called), count(*) filter (where was_reached),
           count(*) filter (where meeting_booked)
    from {OUT} where run_date = cast(? as date)""", [latest]).fetchone()
names = ["scored_orgs", "pqa_orgs", "called_orgs", "called_pqas", "reached_orgs", "meetings_booked"]
row = cube("load", {"measures": [f"outcomes.{n}" for n in names], "filters": [on_day]})["data"][0]
got = [num(row[f"outcomes.{n}"]) for n in names]
want = [num(v) for v in dbt]
record(got == want, f"outcomes on {latest} ({', '.join(names)}): cube {got} | dbt {want}")

# 2. The grain matches the score mart: one row per scored org per run date
scores = con.execute(f"select count(*) from {SCORES}").fetchone()[0]
rows = con.execute(f"select count(*) from {OUT}").fetchone()[0]
record(scores == rows, f"fct_org_outcomes has one row per scored org and run date: {rows} | scores {scores}")

# 3. Test rows never count
raw_tests = con.execute(f"select count(*) from {STG} where is_test").fetchone()[0]
raw_real = con.execute(f"select count(*) from {STG} where not is_test").fetchone()[0]
logged = con.execute(f"select coalesce(sum(n_times_logged), 0) from {OUT}").fetchone()[0]
orphans = con.execute(f"""
    select count(*) from {STG} o
    left join {SCORES} s on s.organization_id = o.organization_id and s.run_date = o.run_date
    where not o.is_test and s.organization_id is null""").fetchone()[0]
record(int(logged) == raw_real - orphans,
       f"logged rows in the mart = real rows in staging: {int(logged)} | {raw_real - orphans} "
       f"({raw_tests} test rows left out, {orphans} orphans)")

# 4. Outcomes by name, Cube vs dbt
dbt_by = dict(con.execute(f"""
    select outcome, count(*) from {OUT} where run_date = cast(? as date) group by 1""",
                          [latest]).fetchall())
cube_by = {r["outcomes.outcome"]: int(float(r["outcomes.scored_orgs"])) for r in
           cube("load", {"measures": ["outcomes.scored_orgs"], "dimensions": ["outcomes.outcome"],
                         "filters": [on_day]})["data"]}
record(cube_by == dbt_by, f"outcomes by name on {latest}: cube {cube_by} | dbt {dbt_by}")

# 5. Every member has a description (the agent reads them)
meta = {c["name"]: c for c in cube("meta")["cubes"]}
members = meta.get("outcomes", {}).get("measures", []) + meta.get("outcomes", {}).get("dimensions", [])
missing = [m["name"] for m in members if not m.get("description")]
record(members and not missing, f"outcomes cube: {len(members)} members, all described"
       + ("" if not missing else f"; missing {missing}"))
con.close()

print(f"\n{sum(results)} of {len(results)} checks pass")
sys.exit(0 if results and all(results) else 1)
