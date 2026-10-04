"""Smoke test for the Revenue Signals MCP server.

Starts mcp_server/revenue_signals.py the way Claude Code does (a subprocess talking over
stdio), lists its tools, calls each one, and checks the answers against Cube directly.
Run from the dbt project folder, with Cube running:
    .venv-mcp\\Scripts\\python.exe scripts\\smoke_mcp.py
Exit code 1 on any failure.
"""
import asyncio
import csv
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import urllib.parse
import urllib.request

from mcp import Client
from mcp.client.stdio import StdioServerParameters

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "mcp_server", "revenue_signals.py")
API = os.environ.get("CUBE_API_URL", "http://localhost:4000/cubejs-api/v1").rstrip("/")
EXPECTED_TOOLS = {"list_metrics", "list_pqas", "explain_account", "query_metric", "log_outcome",
                  "review_outcomes", "propose_weight_change"}
WRITE_TOOLS = {"log_outcome", "propose_weight_change"}
TEST_OUTCOMES = os.path.join(tempfile.mkdtemp(prefix="smoke_outcomes_"), "outcomes.csv")  # never the real log
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
    env = dict(os.environ, REVENUE_SIGNALS_LOG="WARNING",  # keep the server's query log out of this output
               REVENUE_SIGNALS_OUTCOMES=TEST_OUTCOMES)
    params = StdioServerParameters(command=sys.executable, args=[SERVER], env=env)
    async with Client(params) as client:
        tools = await client.list_tools()
        tools = tools.tools if hasattr(tools, "tools") else tools
        names = {t.name for t in tools}
        record(names == EXPECTED_TOOLS, f"tools listed: {', '.join(sorted(names))}")
        read_only = all(t.annotations and t.annotations.read_only_hint for t in tools if t.name not in WRITE_TOOLS)
        writes_safe = all(t.annotations and not t.annotations.read_only_hint and not t.annotations.destructive_hint
                          for t in tools if t.name in WRITE_TOOLS)
        record(read_only and writes_safe, "read tools are marked read-only; the 2 write tools are marked not destructive")
        record("not the product" in (client.instructions or ""),
               "server instructions say GoalEarn is not the product name")

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
        record("Never mention" in data.get("openers_rule", ""), "list_pqas carries the openers rule")
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
            record("Never mention" in acct.get("openers_rule", ""), "explain_account carries the openers rule")
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

        # 7. log_outcome writes one test row to a temporary file, and refuses bad input
        logged, err = unpack(await client.call_tool("log_outcome", {
            "org_id": first["organization_id"], "outcome": "meeting_booked", "note": "smoke test", "test": True}))
        with open(TEST_OUTCOMES, newline="", encoding="utf-8") as f:
            saved = list(csv.DictReader(f))
        record(err is None and len(saved) == 1 and saved[0]["is_test"] == "true"
               and saved[0]["outcome"] == "meeting_booked" and saved[0]["run_date"] == latest,
               f"log_outcome appended 1 test row for {first['organization_id']} on {latest} (temporary file)")
        _, err_outcome = unpack(await client.call_tool("log_outcome", {"org_id": "org_0057", "outcome": "maybe"}))
        _, err_org = unpack(await client.call_tool("log_outcome", {"org_id": "org_9999", "outcome": "not_now"}))
        with open(TEST_OUTCOMES, newline="", encoding="utf-8") as f:
            still = len(list(csv.DictReader(f)))
        record(err_outcome is not None and err_org is not None and still == 1,
               "log_outcome refuses an unknown outcome and an unscored org, and writes nothing for them")

        # 8. review_outcomes and the evidence gate: no weight change without enough called accounts
        review, err = unpack(await client.call_tool("review_outcomes", {}))
        if err:
            record(False, f"review_outcomes: {err}")
        else:
            t = review["totals"]
            record(t["scored_orgs"] > 0 and t["called_orgs"] <= t["scored_orgs"] and "evidence" in review["verdict"],
                   f"review_outcomes {review['window']['since']}..{review['window']['until']}: "
                   f"{t['called_orgs']} called of {t['scored_orgs']} scored; {review['verdict']}")
            before = subprocess.run(["git", "-C", ROOT, "branch", "--list", "proposal/*"],
                                    capture_output=True, text=True).stdout
            _, err = unpack(await client.call_tool("propose_weight_change", {
                "signal": "view_docs", "new_weight": 2,
                "rationale": "Smoke test: this call must be refused while the evidence gate is closed."}))
            after = subprocess.run(["git", "-C", ROOT, "branch", "--list", "proposal/*"],
                                   capture_output=True, text=True).stdout
            if review["enough_evidence"]:
                record(True, "evidence gate is open, so the refusal check is skipped (real outcomes exist)")
            else:
                record(err is not None and "Refused" in err and before == after,
                       "propose_weight_change is refused while the evidence gate is closed, and no branch appears")

        # 9. list_metrics: the menu every query is checked against
        menu, err = unpack(await client.call_tool("list_metrics", {}))
        if err:
            record(False, f"list_metrics: {err}")
        else:
            counts = {c: len(v["measures"]) + len(v["dimensions"]) for c, v in menu["cubes"].items()}
            record(set(counts) >= {"org_scores", "user_scores", "events", "signal_reasons", "outcomes"},
                   f"list_metrics: {counts}")


def proposal_mechanics():
    """The branch-making part of propose_weight_change, on a throwaway repo (no Cube, no gate)."""
    sys.path.insert(0, os.path.join(ROOT, "mcp_server"))
    import revenue_signals as rs
    repo = Path(tempfile.mkdtemp(prefix="smoke_repo_"))
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout
    git("init", "-q")
    git("config", "user.email", "smoke@example.com")
    git("config", "user.name", "smoke test")
    (repo / "seeds").mkdir()
    seed = "event_name,weight,family\nview_docs,1,usage\nstart_trial,5,buying\n"
    (repo / "seeds" / "intent_signal_weights.csv").write_text(seed, encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "seed")
    out = rs._open_proposal(repo, "view_docs", 2, "Smoke test rationale long enough to pass.", "evidence")
    on_branch = git("show", f"{out['branch']}:seeds/intent_signal_weights.csv")
    on_main = (repo / "seeds" / "intent_signal_weights.csv").read_text(encoding="utf-8")
    worktrees = git("worktree", "list").count("\n")
    record(on_branch == seed.replace("view_docs,1,", "view_docs,2,") and on_main == seed and worktrees == 1
           and not out["pushed"],
           f"proposal mechanics on a throwaway repo: branch {out['branch']} changes 1 weight "
           f"({out['old_weight']:g} -> {out['new_weight']:g}), main untouched, worktree cleaned up")


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
    try:
        proposal_mechanics()
    except Exception as err:
        record(False, f"proposal mechanics: {type(err).__name__}: {err}")
    print(f"\n{sum(results)} of {len(results)} checks pass")
    sys.exit(0 if results and all(results) else 1)
