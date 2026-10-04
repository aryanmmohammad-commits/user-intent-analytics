"""Monday brief: a workflow, not an agent (GoalEarn Project 03, step 5).

Code decides every step. Claude does one job, once: writing the sentences.

    1. fetch    the PQA list, the run dates and logged outcomes, from the Revenue Signals
                tools (the same MCP server Claude Code uses, so the same rules apply)
    2. write    one headless Claude Code call turns the facts into a summary, plus a "why"
                and an opener per account
    3. check    code checks every sentence: openers rule, numbers found in the facts, product
                name, money. A sentence that fails is replaced by a safe template line.
    4. publish  briefs/brief_<run date>.html, plus a run record the scorecard reads

Usage, from the project folder, with Cube running:
    .venv-mcp\\Scripts\\python.exe scripts\\monday_brief.py             Claude writes, code checks
    .venv-mcp\\Scripts\\python.exe scripts\\monday_brief.py --no-model  template sentences only

The model call uses the Claude Code command line (claude -p), so it runs on your Claude Code
login: no API key. If claude is not on PATH, the brief falls back to the template writer.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scorecard  # noqa: E402  (same graders as the agent runs)

SERVER = ROOT / "mcp_server" / "revenue_signals.py"
TOOL = "mcp__revenue-signals__"
OUTCOME_BUTTONS = [("meeting_booked", "Meeting booked"), ("not_now", "Not now"),
                   ("wrong_person", "Wrong person"), ("already_talking", "Already talking"),
                   ("no_reply", "No reply")]
TRIAL_OPENER = "You started a trial with us. What did you want to find out?"
GOAL_OPENERS = [
    "What are you hoping to get done with your CI/CD setup this quarter?",
    "What would make this quarter a success for your engineering team?",
    "What is your team focused on shipping next?",
]
MODEL = os.environ.get("BRIEF_MODEL", "sonnet")


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------------------------------------------------------------- 1. fetch

def unpack(result) -> tuple[dict | None, str | None]:
    text = " ".join(getattr(c, "text", "") for c in result.content)
    if result.is_error:
        return None, text
    if result.structured_content is not None:
        return result.structured_content, None
    return json.loads(text), None


async def fetch(steps: list[dict]) -> dict:
    """Every number in the brief comes from these tool calls, recorded as steps."""
    env = dict(os.environ, REVENUE_SIGNALS_LOG="WARNING")
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env=env)
    facts: dict = {}
    async with Client(params) as client:
        async def call(name: str, args: dict) -> dict | None:
            start = now()
            data, err = unpack(await client.call_tool(name, args))
            steps.append({"name": TOOL + name, "input": args, "start": iso(start), "end": iso(now()),
                          "result": err if err else json.dumps(data, indent=2), "error": bool(err)})
            if err:
                raise RuntimeError(f"{name} failed: {err}")
            return data

        facts["list"] = await call("list_pqas", {})
        run_date = facts["list"]["run_date"]
        dates = await call("query_metric", {"measures": ["org_scores.eligible_orgs"],
                                            "time_dimension": "org_scores.run_date", "granularity": "day"})
        days = sorted({(r.get("org_scores.run_date.day") or r.get("org_scores.run_date"))[:10]
                       for r in dates["rows"]})
        earlier = [d for d in days if d < run_date]
        facts["previous_run_date"] = earlier[-1] if earlier else None
        if facts["previous_run_date"]:
            prev = facts["previous_run_date"]
            facts["previous_list"] = await call("list_pqas", {"run_date": prev})
            facts["previous_outcomes"] = await call("query_metric", {
                "measures": ["outcomes.pqa_orgs", "outcomes.called_pqas", "outcomes.meetings_booked"],
                "filters": [{"member": "outcomes.run_date", "operator": "inDateRange", "values": [prev, prev]},
                            {"member": "outcomes.is_pqa", "operator": "equals", "values": ["true"]}]})
        logged = await call("query_metric", {
            "measures": ["outcomes.called_orgs"],
            "dimensions": ["outcomes.organization_id", "outcomes.outcome"],
            "filters": [{"member": "outcomes.run_date", "operator": "inDateRange", "values": [run_date, run_date]},
                        {"member": "outcomes.was_called", "operator": "equals", "values": ["true"]}]})
        facts["logged"] = {r["outcomes.organization_id"]: r["outcomes.outcome"] for r in logged["rows"]}
    return facts


def account_facts(facts: dict) -> list[dict]:
    out = []
    for a in facts["list"]["accounts"]:
        out.append({
            "position": a["position"], "organization_id": a["organization_id"], "plan": a["plan"],
            "users": a["users"], "contact_user_id": a["contact_user_id"],
            "usage_beats_pct": a["usage_beats_pct"], "buying_beats_pct": a["buying_beats_pct"],
            "reasons": [r["text"] for r in a["reasons"]],
            "signals": [r["signal"] for r in a["reasons"]],
            "started_trial": any(r["signal"] == "start_trial" for r in a["reasons"]),
        })
    return out


def movement(facts: dict) -> str:
    prev = facts.get("previous_run_date")
    if not prev:
        return "First scored run: there is no earlier list to compare with."
    now_ids = {a["organization_id"] for a in facts["list"]["accounts"]}
    old_ids = {a["organization_id"] for a in facts["previous_list"]["accounts"]}
    new, gone = sorted(now_ids - old_ids), sorted(old_ids - now_ids)
    row = (facts.get("previous_outcomes") or {}).get("rows") or [{}]
    called = int(float(row[0].get("outcomes.called_pqas") or 0))
    meetings = int(float(row[0].get("outcomes.meetings_booked") or 0))
    return (f"Since the {prev} list: {len(new)} new ({', '.join(new) or 'none'}), "
            f"{len(gone)} dropped out ({', '.join(gone) or 'none'}). "
            f"Last list: {called} of {len(old_ids)} PQAs called, {meetings} meetings booked.")


# ---------------------------------------------------------------- 2. write

def template_text(accounts: list[dict], run_date: str) -> dict:
    goal = iter(GOAL_OPENERS * 10)
    return {
        "summary": f"{len(accounts)} accounts to call from the {run_date} list, best first.",
        "accounts": [{"organization_id": a["organization_id"],
                      "why": "\n".join(a["reasons"]) or "In the top third on both usage and buying.",
                      "opener": TRIAL_OPENER if a["started_trial"] else next(goal)} for a in accounts],
    }


PROMPT = """You write the Monday call brief for the Sales team of our CI/CD platform.
Use only the facts in the JSON below. Return only one JSON object, no code fences, shaped:
{{"summary": "...", "accounts": [{{"organization_id": "...", "why": "...", "opener": "..."}}]}}
with one entry per account, in the same order as the facts.

Rules:
- summary: at most two sentences for the rep. The only numbers allowed are the account count and the run date.
- why: one sentence telling the rep why this account stands out, rewritten from its reasons.
  Use only numbers that appear in its reasons. Never add numbers or dates of your own.
- opener: one open question about the buyer's own goals. Never mention pricing, page visits, docs,
  pipelines, invites, parallelism, counts, numbers, dates or months. Never assume a problem or a need
  (no "unclear", "slow", "struggle", "pain"). Only when started_trial is true may you name the trial
  plainly, for example "You started a trial with us. What did you want to find out?"
- Never call the product GoalEarn; say "our CI/CD platform". No prices or money amounts.

Facts:
{facts}
"""


def find_claude() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for guess in (Path.home() / ".local" / "bin" / "claude.exe", Path.home() / ".local" / "bin" / "claude"):
        if guess.exists():
            return str(guess)
    return None


def write_with_claude(accounts: list[dict], run_date: str) -> tuple[dict | None, dict, str]:
    """One headless Claude Code call. Returns (text or None, call info, note)."""
    claude = find_claude()
    if not claude:
        return None, {}, "Claude Code command line (claude) not found on PATH"
    facts = {"run_date": run_date, "account_count": len(accounts),
             "accounts": [{k: a[k] for k in ("organization_id", "plan", "users", "reasons", "started_trial")}
                          for a in accounts]}
    cmd = [claude, "-p", "--output-format", "json", "--model", MODEL, "--tools", "",
           "--strict-mcp-config", "--no-session-persistence", "--max-turns", "1"]
    try:
        with tempfile.TemporaryDirectory() as empty:  # no project files, no MCP servers, no tools
            proc = subprocess.run(cmd, input=PROMPT.format(facts=json.dumps(facts, indent=1)),
                                  capture_output=True, text=True, encoding="utf-8", timeout=300, cwd=empty)
    except (OSError, subprocess.TimeoutExpired) as err:
        return None, {}, f"claude did not run: {err}"
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        return None, {}, f"claude returned no JSON (exit {proc.returncode}): {(proc.stderr or proc.stdout)[:200]}"
    info = {"usage": out.get("usage") or {}, "cost_usd": out.get("total_cost_usd"),
            "models": sorted((out.get("modelUsage") or {}).keys()), "duration_ms": out.get("duration_ms")}
    if out.get("is_error"):
        return None, info, f"claude reported an error: {str(out.get('result'))[:200]}"
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (out.get("result") or "").strip())
    try:
        written = json.loads(text)
        ids = [w["organization_id"] for w in written["accounts"]]
        assert isinstance(written["summary"], str)
        assert all(isinstance(w["why"], str) and isinstance(w["opener"], str) for w in written["accounts"])
    except (ValueError, KeyError, TypeError, AssertionError):
        return None, info, "claude's text was not the JSON shape asked for"
    if ids != [a["organization_id"] for a in accounts]:
        return None, info, "claude's accounts did not match the list"
    return written, info, "ok"


# ---------------------------------------------------------------- 3. check

def opener_problems(text: str) -> list[str]:
    why = [label for label, pat in scorecard.TRACKED if re.search(pat, text, re.I)]
    if re.search(scorecard.ASSUMED_NEED, text, re.I):
        why.append("assumes a need")
    return why


def text_problems(text: str, evidence_steps: list[dict]) -> list[str]:
    why = []
    ev = scorecard.evidence(text, evidence_steps)
    if ev["missing"]:
        why.append("numbers not in the facts: " + ", ".join(ev["missing"]))
    if re.search(r"\bgoal\s?earn\b", text, re.I):
        why.append('calls the product "GoalEarn"')
    if re.search("[$\u20ac\u00a3]\\s?\\d|\\b\\d[\\d,.]*\\s?(?:usd|dollars|eur)\\b", text, re.I):
        why.append("money amount")
    return why


def check_and_fix(written: dict, accounts: list[dict], run_date: str, steps: list[dict]) -> tuple[dict, list[dict]]:
    """Keep each sentence that passes; replace each one that fails with the template line."""
    safe = template_text(accounts, run_date)
    fixed = {"summary": written["summary"], "accounts": []}
    replaced = []
    tool_steps = [s for s in steps if s["name"].startswith(TOOL)]
    problems = text_problems(written["summary"], tool_steps)
    if problems:
        replaced.append({"organization_id": None, "field": "summary", "original": written["summary"],
                         "why": "; ".join(problems)})
        fixed["summary"] = safe["summary"]
    for w, s, a in zip(written["accounts"], safe["accounts"], accounts):
        entry = {"organization_id": w["organization_id"], "why": w["why"], "opener": w["opener"]}
        problems = text_problems(w["why"], tool_steps)
        if problems:
            replaced.append({"organization_id": a["organization_id"], "field": "why", "original": w["why"],
                             "why": "; ".join(problems)})
            entry["why"] = s["why"]
        problems = opener_problems(w["opener"]) + [p for p in text_problems(w["opener"], tool_steps)
                                                   if not p.startswith("numbers")]
        if a["started_trial"] is False and re.search(r"\btrial\b", w["opener"], re.I):
            problems.append("names a trial this account did not start")
        if problems:
            replaced.append({"organization_id": a["organization_id"], "field": "opener", "original": w["opener"],
                             "why": "; ".join(problems)})
            entry["opener"] = s["opener"]
        fixed["accounts"].append(entry)
    return fixed, replaced


# ---------------------------------------------------------------- 4. publish

def brief_text(facts: dict, accounts: list[dict], text: dict) -> str:
    """Plain-text brief: what the scorecard checks and what goes into the run record."""
    lst = facts["list"]
    lines = [f"Monday brief, list of {lst['run_date']}", lst["label"], movement(facts), text["summary"], ""]
    for a, t in zip(accounts, text["accounts"]):
        lines += [f"{a['position']}. {a['organization_id']} ({a['plan']}, {a['users']} users), "
                  f"ask for {a['contact_user_id']}", f"Why: {t['why']}", f"Opener: {t['opener']}", ""]
    lines.append("No revenue, price or plan-history data exists, so accounts are not ranked by deal size.")
    return "\n".join(lines)


STYLE = scorecard.STYLE + """
.acct{display:grid;grid-template-columns:44px 1fr;gap:4px 16px}
.pos{font-family:Bahnschrift,"Segoe UI",sans-serif;font-size:26px;font-weight:600;color:var(--route);line-height:1}
.acct h2{margin:0 0 2px}
.because{color:var(--ink);margin:8px 0;white-space:pre-line}
.opener{border-left:3px solid var(--route);padding:6px 12px;margin:10px 0;font-style:italic}
.tag{font-size:12px;color:var(--mute);font-style:normal}
.sig{display:inline-block;font-size:12px;border:1px solid var(--line);border-radius:999px;padding:0 8px;margin:0 4px 4px 0;color:var(--mute)}
.buttons{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.buttons button{font:inherit;font-size:13px;border:1px solid var(--line);background:var(--bg);color:var(--ink);
border-radius:6px;padding:5px 10px;cursor:pointer}
.buttons button:hover{border-color:var(--route)}
.logged{font-size:13px;font-weight:600;color:var(--pass)}
#toast{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);background:var(--ink);color:var(--card);
padding:8px 14px;border-radius:8px;font-size:14px;opacity:0;transition:opacity .2s}
#toast.on{opacity:1}
@media (max-width:640px){.acct{grid-template-columns:1fr}}
"""

SCRIPT = """
function copyLine(t){
  var done=function(){var el=document.getElementById('toast');el.textContent='Copied: '+t+'  Paste it into Claude Code.';
    el.className='on';setTimeout(function(){el.className='';},2600);};
  if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(t).then(done,function(){fallback(t);done();});}
  else{fallback(t);done();}
}
function fallback(t){var a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();
  try{document.execCommand('copy');}catch(e){}document.body.removeChild(a);}
"""


def brief_html(facts: dict, accounts: list[dict], text: dict, replaced: list[dict], writer: str, run_id: str) -> str:
    e = html.escape
    lst = facts["list"]
    swapped = {(r["organization_id"], r["field"]) for r in replaced}
    cards = []
    for a, t in zip(accounts, text["accounts"]):
        org = a["organization_id"]
        logged = facts["logged"].get(org)
        buttons = "".join(
            f"<button onclick=\"copyLine('Log outcome for {org}: {label.lower()}')\">{label}</button>"
            for _, label in OUTCOME_BUTTONS)
        cards.append(
            f"<section class='card acct'><div class='pos'>{a['position']}</div><div>"
            f"<h2>{e(org)}</h2><p class='meta' style='margin:0'>{e(a['plan'])} plan, {a['users']} users. "
            f"Ask for <b>{e(a['contact_user_id'])}</b>. Usage beats {a['usage_beats_pct']}%, "
            f"buying beats {a['buying_beats_pct']}% of eligible orgs.</p>"
            f"<p class='because'>{e(t['why'])}"
            + (" <span class='tag'>(template line)</span>" if (org, "why") in swapped else "") + "</p>"
            + "".join(f"<span class='sig'>{e(s)}</span>" for s in a["signals"])
            + f"<p class='opener'>{e(t['opener'])}"
            + (" <span class='tag'>(safe template opener)</span>" if (org, "opener") in swapped else "") + "</p>"
            + (f"<p class='logged'>Logged: {e(logged.replace('_', ' '))}</p>" if logged else "")
            + f"<div class='buttons'>{buttons}</div></div></section>")
    head = (f"<h1>Monday brief</h1><p class='meta'>List of {e(lst['run_date'])}. {e(lst['label'])}</p>"
            f"<section class='card'><p style='margin:0 0 8px'><b>{e(text['summary'])}</b></p>"
            f"<p class='meta' style='margin:0 0 6px'>{e(movement(facts))}</p>"
            f"<p class='meta' style='margin:0'>After a call, click what happened. That copies one line; paste it "
            f"into Claude Code, which records it with log_outcome after asking you first.</p></section>")
    foot = (f"<p class='foot'>Written by: {e(writer)}. "
            f"{len(replaced)} sentence(s) replaced by template lines after the code checks. "
            f"No revenue, price or plan-history data exists, so accounts are not ranked by deal size. "
            f"<a href='../runs/{e(run_id)}.html'>Scorecard for this run</a>.</p><div id='toast'></div>")
    body = head + "".join(cards) + foot
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Monday brief {e(lst['run_date'])}</title><style>{STYLE}</style>"
            f"<script>{SCRIPT}</script></head><body><main>{body}</main></body></html>")


# ---------------------------------------------------------------- the workflow

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Write the Monday brief (a fixed workflow).")
    ap.add_argument("--no-model", action="store_true", help="template sentences only, no Claude call")
    args = ap.parse_args()
    os.chdir(ROOT)

    started = now()
    steps: list[dict] = []
    try:
        facts = asyncio.run(fetch(steps))
    except Exception as err:
        while isinstance(err, BaseExceptionGroup) and err.exceptions:
            err = err.exceptions[0]
        print(f"STOP: fetching the list failed: {err}")
        return 1
    run_date = facts["list"]["run_date"]
    accounts = account_facts(facts)
    print(f"1. fetch    {len(accounts)} PQAs on the {run_date} list; {movement(facts)}")

    # 2. write
    start = now()
    info: dict = {}
    if args.no_model:
        written, note = None, "template writer chosen (--no-model)"
    else:
        written, info, note = write_with_claude(accounts, run_date)
    writer = f"Claude ({MODEL}, one headless Claude Code call)" if written else f"template ({note})"
    if written is None:
        written = template_text(accounts, run_date)
    steps.append({"name": "write", "input": {"accounts": len(accounts), "model": MODEL if info else None},
                  "start": iso(start), "end": iso(now()), "result": None, "error": False,
                  "summary": writer + (f", ${info['cost_usd']:.4f}" if info.get("cost_usd") is not None else "")})
    print(f"2. write    {writer}")

    # 3. check
    start = now()
    text, replaced = check_and_fix(written, accounts, run_date, steps)
    steps.append({"name": "check", "input": {"sentences": 1 + 2 * len(accounts)}, "start": iso(start),
                  "end": iso(now()), "result": None, "error": False,
                  "summary": f"{len(replaced)} of {1 + 2 * len(accounts)} sentences replaced by template lines"})
    print(f"3. check    {len(replaced)} of {1 + 2 * len(accounts)} sentences replaced by template lines")
    for r in replaced:
        print(f"            {r['organization_id'] or 'summary'} {r['field']}: {r['why']}")

    # 4. publish
    start = now()
    run_id = f"brief-{run_date}-{started.strftime('%H%M%S')}"
    (ROOT / "briefs").mkdir(exist_ok=True)
    page = ROOT / "briefs" / f"brief_{run_date}.html"
    page.write_text(brief_html(facts, accounts, text, replaced, writer, run_id), encoding="utf-8")
    steps.append({"name": "publish", "input": {"file": f"briefs/{page.name}"}, "start": iso(start),
                  "end": iso(now()), "result": None, "error": False, "summary": f"wrote briefs/{page.name}"})
    record = {
        "kind": "workflow", "name": "monday-brief", "question": f"Monday brief for the {run_date} list",
        "started_at": iso(started), "ended_at": iso(now()), "steps": steps,
        "answer": brief_text(facts, accounts, text), "openers": [t["opener"] for t in text["accounts"]],
        "writer": writer, "replaced": replaced, "usage": info.get("usage") or None,
        "cost_usd": info.get("cost_usd"), "models": info.get("models") or [],
    }
    records = ROOT / "runs" / "records"
    records.mkdir(parents=True, exist_ok=True)
    (records / f"{run_id}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"4. publish  briefs/{page.name} and runs/records/{run_id}.json")

    runs, _ = scorecard.build(scorecard.records_folder(None), ROOT / "runs")
    mine = next(r for r in runs if r["id"] == run_id)
    failed = ", ".join(c["name"] for c in mine["checks"] if c["ok"] is False) or "none"
    print(f"\nScorecard: finished {'yes' if mine['finished'] else 'NO'}, met its goal "
          f"{'yes' if mine['goal'] else 'NO'}, evidence {mine['evidence']['pct']:g}%, failed checks: {failed}")
    print(f"Open: briefs/{page.name}   and   runs/{run_id}.html")
    return 0 if mine["goal"] else 2


if __name__ == "__main__":
    sys.exit(main())
