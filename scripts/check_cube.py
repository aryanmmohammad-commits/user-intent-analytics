"""Contract check: Cube must give the same numbers as the dbt marts.

Run from the dbt project folder after `dbt build` and `python scripts/export_for_cube.py`,
with Cube running:
    python scripts/check_cube.py

Each check asks Cube for metrics by name and computes the same numbers straight from
dev.duckdb (read-only). The last check requires a description on every measure and
dimension, because the agent reads the model through them. Exit code 1 on any failure.
"""
import datetime
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import duckdb

API = "http://localhost:4000/cubejs-api/v1"
CUBES = ["org_scores", "user_scores", "events", "signal_reasons"]

con = duckdb.connect("dev.duckdb", read_only=True)
results = []


def cube(path, query=None):
    url = f"{API}/{path}"
    if query is not None:
        url += "?query=" + urllib.parse.quote(json.dumps(query))
    for _ in range(30):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            raise RuntimeError(err.read().decode("utf-8", "replace")[:600])
        if body.get("error") == "Continue wait":
            time.sleep(2)
            continue
        if "error" in body:
            raise RuntimeError(body["error"])
        return body
    raise RuntimeError("Cube kept answering Continue wait")


def load(measures, dimensions=None, filters=None, time_dimensions=None):
    query = {"measures": measures}
    if dimensions:
        query["dimensions"] = dimensions
    if filters:
        query["filters"] = filters
    if time_dimensions:
        query["timeDimensions"] = time_dimensions
    return cube("load", query)["data"]


def first_row(measures, **kwargs):
    data = load(measures, **kwargs)
    return [data[0].get(m) for m in measures] if data else [None] * len(measures)


def is_true(member):
    return {"member": member, "operator": "equals", "values": ["true"]}


def sql(query):
    return list(con.execute(query).fetchone())


def same(a, b):
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= 1e-6 * max(1.0, abs(float(b)))


def show(v):
    return "null" if v is None else f"{float(v):g}"


def record(ok, text):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {text}")


def check(name, got, want):
    ok = len(got) == len(want) and all(same(g, w) for g, w in zip(got, want))
    record(ok, f"{name}: cube {', '.join(map(show, got))} | dbt {', '.join(map(show, want))}")


def check_map(name, got, want):
    keys = sorted(set(got) | set(want))
    ok = set(got) == set(want) and all(same(got[k], want[k]) for k in keys)
    pairs = ", ".join(f"{k} {show(got.get(k))}/{show(want.get(k))}" for k in keys)
    record(ok, f"{name} (cube/dbt): {pairs}")


def org_totals():
    m = ["org_scores.eligible_orgs", "org_scores.pqa_count", "org_scores.total_pipelines_run",
         "org_scores.avg_usage_score", "org_scores.avg_buying_score"]
    want = sql("select count(*), count(*) filter (where is_pqa), sum(pipelines_run), "
               "avg(usage_score), avg(buying_score) from fct_org_intent_score")
    check("org_scores (orgs, PQAs, pipelines, avg usage, avg buying)", first_row(m), want)


def orgs_by_quadrant():
    data = load(["org_scores.eligible_orgs"], dimensions=["org_scores.quadrant"])
    got = {r["org_scores.quadrant"]: r["org_scores.eligible_orgs"] for r in data}
    want = dict(con.execute("select quadrant, count(*) from fct_org_intent_score group by 1").fetchall())
    check_map("orgs by quadrant", got, want)


def user_totals():
    m = ["user_scores.user_count", "user_scores.contact_count", "user_scores.avg_user_intent_score"]
    want = sql("select count(*), count(*) filter (where is_contact), avg(user_intent_score) "
               "from fct_user_intent_score")
    check("user_scores (users, contacts, avg user score)", first_row(m), want)


def contacts_of_pqas():
    want = sql("select count(*) from fct_user_intent_score u join fct_org_intent_score o "
               "on o.organization_id = u.organization_id and o.run_date = u.run_date "
               "where o.is_pqa and u.is_contact")
    got = first_row(["user_scores.contact_count"], filters=[is_true("org_scores.is_pqa")])
    check("contacts in PQA orgs (user_scores -> org_scores)", got, want)


def event_totals():
    m = ["events.event_count", "events.active_users", "events.active_orgs"]
    want = sql("select count(*), count(distinct user_id), count(distinct organization_id) "
               "from user_intent_events")
    check("events (events, users, orgs)", first_row(m), want)


def events_by_month():
    data = load(["events.event_count"],
                time_dimensions=[{"dimension": "events.event_timestamp", "granularity": "month"}])
    got = {r["events.event_timestamp.month"][:7]: r["events.event_count"] for r in data}
    want = {}
    for (secs,) in con.execute("select epoch(event_timestamp) from user_intent_events").fetchall():
        key = datetime.datetime.fromtimestamp(secs, datetime.timezone.utc).strftime("%Y-%m")
        want[key] = want.get(key, 0) + 1
    check_map("events by month, UTC", got, want)


def reason_totals():
    m = ["signal_reasons.signal_rows", "signal_reasons.reason_count"]
    want = sql("select count(*), count(*) filter (where is_reason) from fct_org_signal_reasons")
    check("signal_reasons (rows, reasons)", first_row(m), want)


def reasons_of_pqas():
    want = sql("select count(*) from fct_org_signal_reasons r join fct_org_intent_score o "
               "on o.organization_id = r.organization_id and o.run_date = r.run_date "
               "where o.is_pqa and r.is_reason")
    got = first_row(["signal_reasons.reason_count"], filters=[is_true("org_scores.is_pqa")])
    check("reasons of PQAs (signal_reasons -> org_scores)", got, want)


def fan_out_guard():
    true_total, naive_total = sql("""
        with pqa as (select * from fct_org_intent_score where is_pqa),
        reasons as (select * from fct_org_signal_reasons where is_reason)
        select
            (select sum(p.pipelines_run) from pqa as p where exists (
                select 1 from reasons as r
                where r.organization_id = p.organization_id and r.run_date = p.run_date)),
            (select sum(p.pipelines_run) from pqa as p join reasons as r
                on r.organization_id = p.organization_id and r.run_date = p.run_date)
    """)
    got = first_row(["org_scores.total_pipelines_run"],
                    filters=[is_true("org_scores.is_pqa"), is_true("signal_reasons.is_reason")])
    check("fan-out guard, PQA pipelines through the reasons join", got, [true_total])
    print(f"      naive join, one count per reason row: {show(naive_total)} "
          f"= {float(naive_total) / float(true_total):.2f}x the true total")


def descriptions():
    meta = cube("meta")
    found = {c["name"]: c for c in meta["cubes"]}
    missing_cubes = [c for c in CUBES if c not in found]
    undocumented = [m["name"] for c in CUBES if c in found
                    for m in found[c].get("measures", []) + found[c].get("dimensions", [])
                    if not m.get("description")]
    ok = not missing_cubes and not undocumented
    record(ok, "every measure and dimension has a description"
           + ("" if ok else f": missing cubes {missing_cubes}, undocumented {undocumented}"))


CHECKS = [org_totals, orgs_by_quadrant, user_totals, contacts_of_pqas, event_totals,
          events_by_month, reason_totals, reasons_of_pqas, fan_out_guard, descriptions]

if __name__ == "__main__":
    for fn in CHECKS:
        try:
            fn()
        except Exception as err:  # one broken check must not hide the others
            record(False, f"{fn.__name__}: {err}")
    print(f"\n{sum(results)} of {len(results)} checks pass")
    sys.exit(0 if all(results) else 1)