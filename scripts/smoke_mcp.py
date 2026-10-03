"""Smoke test for the Revenue Signals MCP server.

Starts mcp_server/revenue_signals.py the way Claude Code does (a subprocess talking over
stdio), lists its tools, calls each one, and checks the answers against Cube directly.
Run from the dbt project folder, with Cube running:
    .venv-mcp\\Scripts\\python.exe scripts\\smoke_mcp.py
Exit code 1 on any failure.
"""
import asyncio
import json
import os
import sys
import urllib.parse
import urllib.request

from mcp import Client
from mcp.client.stdio import StdioServerParameters

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "mcp_server", "revenue_signals.py")
API = os.environ.get("CUBE_API_URL", "http://localhost:4000/cubejs-api/v1").rstrip("/")
EXPECTED_TOOLS = {"list_metrics", "list_pqas", "explain_account", "query_metric"}
results = []


def record(ok, text):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {text}")


def cube_load(query):
    url = f"{API}/load?query=" + urllib.parse.quote(json.dumps(query))
    for _ in range(30):
        with urllib.request.urlopen(url, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if body.get("error") == "Continue wait":
            import time
            time.sleep(2)
            continue
        if "error" in body:
            raise RuntimeError(body["error"])
        return body["data"]
    raise RuntimeError("Cube kept answering Continue wait")


def unpack(result):
    """Return (data, error_text) from a tool result."""
    text = " ".join(getattr(c, "text", "") for c in result.content)
    if result.is_error:
        return None, text
    if result.structured_content is not None:
        return result.structured_content, None
    return json.loads(text), None


async def main():
    env = dict(os.environ, REVENUE_SIGNALS_LOG="WARNING")  # keep the server's query log out of this output
    params = StdioServerParameters(command=sys.executable, args=[SERVER], env=env)
    async with Client(params) as client:
        tools = await client.list_tools()
        tools = tools.tools if hasattr(tools, "tools") else tools
        names = {t.name for t in tools}
        record(names == EXPECTED_TOOLS, f"tools listed: {', '.join(sorted(names))}")
        read_only = all(t.annotations and t.annotations.read_only_hint for t in tools)
        record(read_only, "every tool is marked read-only")

        # The answers Cube gives directly, to compare the tools against
        latest = cube_load({
            "measures": ["org_scores.eligible_orgs"],
            "timeDimensions": [{"dimension": "org_scores.run_date", "granularity": "day"}],
            "order": {"org_scores.run_date": "desc"}, "limit": 1,
        })[0]["org_scores.run_date.day"][:10]
        on_day = {"member": "org_scores.run_date", "operator": "inDateRange", "values": [latest, latest]}
        totals = cube_load({"measures": ["org_scores.pqa_count", "org_scores.eligible_orgs"], "filters": [on_day]})[0]
        want_pqas = int(float(totals["org_scores.pqa_count"]))
        want_reasons = int(float(cube_load({
            "measures": ["signal_reasons.reason_count"],
            "filters": [{"member": "org_scores.is_pqa", "operator": "equals", "values": ["true"]},
                        {"member": "signal_reasons.run_date", "operator": "inDateRange", "values": [latest, latest]}],
        })[0]["signal_reasons.reason_count"]))

        # 1. list_pqas
        data, err = unpack(await client.call_tool("list_pqas", {}))
        if err:
            record(False, f"list_pqas: {err}")
            return
        got_reasons = sum(len(a["reasons"]) for a in data["accounts"])
        record(data["pqa_count"] == want_pqas and got_reasons == want_reasons,
               f"list_pqas on {data['run_date']}: {data['pqa_count']} PQAs, {got_reasons} reasons "
               f"| Cube {want_pqas}, {want_reasons}")
        record(all(a["contact_user_id"] and a["reasons"] for a in data["accounts"]),
               "every PQA has a contact and at least one reason")
        record("unvalidated" in data["label"], "the answer carries the v0, unvalidated label")
        first = data["accounts"][0]

        # 2. explain_account on the top PQA: the receipt must match the total
        acct, err = unpack(await client.call_tool("explain_account", {"org_id": first["organization_id"]}))
        if err:
            record(False, f"explain_account: {err}")
        else:
            points = sum(s["points"] or 0 for s in acct["signals"])
            total = acct["usage_score"] + acct["buying_score"]
            reasons = sorted(s["reason_text"] for s in acct["signals"] if s["is_reason"])
            same_reasons = reasons == sorted(r["text"] for r in first["reasons"])
            record(acct["is_pqa"] and abs(points - total) < 0.02 and same_reasons,
                   f"explain_account {first['organization_id']}: box {acct['box']}, "
                   f"{len(acct['signals'])} signals, points {points:.2f} vs score {total:.2f}, "
                   f"reasons match list_pqas: {same_reasons}")
            change = acct["change_since_previous_run"]
            record(bool(change), f"change since previous run: {change if isinstance(change, str) else json.dumps(change)}")

        # 3. explain_account guardrails
        miss, err = unpack(await client.call_tool("explain_account", {"org_id": "org_9999"}))
        record(err is None and miss and miss["scored"] is False, "org_9999 answered as not scored, no error")
        _, err = unpack(await client.call_tool("explain_account", {"org_id": "DROP TABLE users"}))
        record(err is not None, "a malformed org_id is refused")

        # 4. query_metric: PQAs by plan add up to the PQA count
        rows, err = unpack(await client.call_tool("query_metric", {
            "measures": ["org_scores.pqa_count"], "dimensions": ["org_scores.plan_type"]}))
        if err:
            record(False, f"query_metric pqa_count by plan: {err}")
        else:
            split = {r["org_scores.plan_type"]: r["org_scores.pqa_count"] for r in rows["rows"]}
            record(sum(split.values()) == want_pqas and rows.get("pinned_run_date") == latest,
                   f"query_metric PQAs by plan (pinned to {rows.get('pinned_run_date')}): {split}")

        # 5. query_metric: events by month
        rows, err = unpack(await client.call_tool("query_metric", {
            "measures": ["events.event_count"], "time_dimension": "events.event_timestamp",
            "granularity": "month"}))
        if err:
            record(False, f"query_metric events by month: {err}")
        else:
            months = len(rows["rows"])
            total = sum(r["events.event_count"] for r in rows["rows"])
            want = int(float(cube_load({"measures": ["events.event_count"]})[0]["events.event_count"]))
            record(total == want, f"query_metric events by month: {months} months, {total} events | Cube {want}")

        # 6. query_metric refuses a metric that is not on the menu
        _, err = unpack(await client.call_tool("query_metric", {"measures": ["org_scores.revenue"]}))
        record(err is not None and "not on the menu" in err, f"unknown metric refused: {(err or '')[:110]}")

        # 7. list_metrics: the menu every query is checked against
        menu, err = unpack(await client.call_tool("list_metrics", {}))
        if err:
            record(False, f"list_metrics: {err}")
        else:
            counts = {c: len(v["measures"]) + len(v["dimensions"]) for c, v in menu["cubes"].items()}
            record(set(counts) >= {"org_scores", "user_scores", "events", "signal_reasons"},
                   f"list_metrics: {counts}")


def first_error(err):
    while isinstance(err, BaseExceptionGroup) and err.exceptions:
        err = err.exceptions[0]
    return err


if __name__ == "__main__":
    try:
        urllib.request.urlopen(f"{API}/meta", timeout=10).read()
    except Exception as err:
        record(False, f"Cube is not reachable at {API} ({err}). Start Docker Desktop and goalearn-cube first.")
        sys.exit(1)
    try:
        asyncio.run(main())
    except Exception as err:  # the server failing to start must show, not hide
        err = first_error(err)
        record(False, f"smoke test stopped: {type(err).__name__}: {err}")
    print(f"\n{sum(results)} of {len(results)} checks pass")
    sys.exit(0 if results and all(results) else 1)