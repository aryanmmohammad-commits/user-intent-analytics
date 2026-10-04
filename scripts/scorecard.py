"""Run scorecards for the Revenue Signals agent and workflows (GoalEarn Project 03, steps 4b-5).

Reads two kinds of run and checks every answer with the same graders:
  - agent runs: Claude Code's conversation records for this project folder, split into
    runs (one question -> the tools Claude called -> the answer)
  - workflow runs: run records the Monday brief writes to runs/records/*.json
Then writes:

    runs/index.html        every run, newest first, with its verdicts
    runs/<run id>.html     one scorecard per run
    runs/agent_runs.csv    one row per run (grain: one run)

A run can finish and still fail its goal: the plane lands, at the wrong airport.
So each card keeps three verdicts apart:
    Finished      an answer came back and no tool failed
    Met its goal  every check below passed
    Evidence      the share of numbers and dates in the answer found in that run's tool results

Usage, from the project folder (plain Python 3.10+, no packages needed):
    python scripts/scorecard.py
    python scripts/scorecard.py --records <folder with .jsonl files>
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import os
import re
import sys
from pathlib import Path

GRADER_VERSION = "checks v0 (code only)"
EVIDENCE_TARGET = 95.0
SCORE_TOOL_PREFIX = "mcp__revenue-signals__"

MONTHS = r"january|february|march|april|june|july|august|september|october|november|december"
TRACKED = [
    ("mentions pricing", r"pricing"),
    ("mentions page visits", r"\bpages?\b|\bvisit"),
    ("mentions what we watched", r"looked at|been on our"),
    ("mentions docs reading", r"\bdocs?\b|documentation"),
    ("mentions activity", r"pipeline|invite|parallel"),
    ("contains a number", r"\d"),
    ("contains a date", r"\b(" + MONTHS + r")\b|\byesterday\b|\blast (week|month)\b"),
]
ASSUMED_NEED = r"unclear|\bslow|struggl|\bpain|problem|frustrat|confus|stuck|bottleneck"
QUOTED = re.compile('["\u201c]([^"\u201c\u201d]{6,}?)["\u201d]')
BULLET = re.compile(r"^([-*+]|\d+[.)])\s+")


# ---------------------------------------------------------------- reading the records

def records_folder(cli_value: str | None) -> Path | None:
    """Claude Code's records for this folder, or None when there are none (a fresh clone)."""
    if cli_value:
        return Path(cli_value)
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    projects = home / "projects"
    want = re.sub(r"[^A-Za-z0-9]", "-", str(Path.cwd())).lower()
    if projects.is_dir():
        for d in projects.iterdir():
            if d.is_dir() and d.name.lower() == want:
                return d
        found = sorted((d for d in projects.iterdir() if d.is_dir() and "goalearn" in d.name.lower()),
                       key=lambda d: d.stat().st_mtime, reverse=True)
        if found:
            return found[0]
    return None


def workflow_runs(folder: Path) -> list[dict]:
    """Runs the Monday brief recorded (runs/records/*.json), in the same shape as agent runs."""
    runs = []
    for path in sorted(folder.glob("*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        steps = [{"id": None, "name": s["name"], "input": s.get("input") or {}, "start": when(s.get("start")),
                  "end": when(s.get("end")), "result": s.get("result"), "error": bool(s.get("error")),
                  "summary": s.get("summary")} for s in rec.get("steps", [])]
        runs.append({
            "kind": "workflow", "session": rec.get("name", "workflow"), "id": path.stem,
            "question": rec.get("question", path.stem), "start": when(rec.get("started_at")),
            "end": when(rec.get("ended_at")), "steps": steps, "texts": [], "answer": rec.get("answer", ""),
            "openers": rec.get("openers"), "usage": {"model call": rec["usage"]} if rec.get("usage") else {},
            "models": set(rec.get("models") or []), "thinking": 0, "cost_usd": rec.get("cost_usd"),
            "writer": rec.get("writer"), "source": f"runs/records/{path.name}",
        })
    return runs


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def blocks_of(event: dict) -> list:
    msg = event.get("message")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content or [] if isinstance(b, dict)]


TAGGED = re.compile(r"<([A-Za-z][\w-]*)[^>]*>.*?</\1>", re.S)


def prompt_text(event: dict) -> str | None:
    """The user's own words, or None when this user line is a tool result, a command or a note
    Claude Code added (opened file, command output, interruption)."""
    if event.get("type") != "user" or event.get("isSidechain") or event.get("isMeta") \
            or event.get("isCompactSummary"):
        return None
    blocks = blocks_of(event)
    if any(b.get("type") == "tool_result" for b in blocks):
        return None
    texts = [TAGGED.sub("", b.get("text", "")).strip() for b in blocks if b.get("type") == "text"]
    texts = [t for t in texts if t and not t.startswith(("<", "[Request interrupted", "Caveat:"))]
    return "\n".join(texts) if texts else None


def result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def when(ts: str | None) -> dt.datetime | None:
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def split_runs(events: list[dict], session: str) -> list[dict]:
    runs: list[dict] = []
    run = None
    for e in events:
        if e.get("isSidechain"):
            continue
        text = prompt_text(e)
        if text is not None:
            run = {"kind": "agent", "session": session, "question": text, "start": when(e.get("timestamp")),
                   "end": when(e.get("timestamp")), "steps": [], "texts": [], "usage": {},
                   "models": set(), "thinking": 0}
            runs.append(run)
            continue
        if run is None:
            continue
        ts = when(e.get("timestamp"))
        if e.get("type") == "assistant":
            msg = e.get("message") or {}
            if msg.get("model"):
                run["models"].add(msg["model"])
            usage = msg.get("usage")
            if isinstance(usage, dict):
                slot = run["usage"].setdefault(msg.get("id") or e.get("uuid"), {})
                for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"):
                    if isinstance(usage.get(k), (int, float)):
                        slot[k] = max(slot.get(k, 0), usage[k])
            for b in blocks_of(e):
                kind = b.get("type")
                if kind == "thinking":
                    run["thinking"] += 1
                elif kind == "text" and b.get("text", "").strip():
                    run["texts"].append((len(run["steps"]), b["text"]))
                elif kind == "tool_use":
                    run["steps"].append({"id": b.get("id"), "name": b.get("name", "?"),
                                         "input": b.get("input") or {}, "start": ts, "end": None,
                                         "result": None, "error": False})
            if ts:
                run["end"] = ts
        elif e.get("type") == "user":
            for b in blocks_of(e):
                if b.get("type") != "tool_result":
                    continue
                for step in run["steps"]:
                    if step["id"] == b.get("tool_use_id"):
                        step["result"] = result_text(b)
                        step["error"] = bool(b.get("is_error"))
                        step["end"] = ts
    for n, r in enumerate(runs, start=1):
        r["id"] = f"{session[:8]}-{n:02d}"
        # The answer is what Claude wrote after its last tool call.
        last = len(r["steps"])
        r["answer"] = "\n\n".join(t for pos, t in r["texts"] if pos == last).strip()
    return runs


# ---------------------------------------------------------------- evidence

ID_LIKE = re.compile(r"\b[A-Za-z]+_\d+\b|\b[A-Za-z]+\d+[A-Za-z0-9_]*\b|#\d+")
FULL_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
SHORT_DATE = re.compile(r"(?<![\d-])\d{2}-\d{2}(?![\d-])")
NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w])|(?<![\w.,])\d+(?:\.\d+)?(?![\w])")


def claims_in(text: str) -> tuple[set[str], set[str], set[str]]:
    """Numbers, full dates and MM-DD dates a piece of text states."""
    dates = set(FULL_DATE.findall(text))
    text = FULL_DATE.sub(" ", re.sub(r"\d{4}-\d{2}-\d{2}T[\d:.]+Z?", " ", text))
    shorts = set(SHORT_DATE.findall(text))
    text = SHORT_DATE.sub(" ", text)
    text = ID_LIKE.sub(" ", text)
    text = re.sub(r"(?m)^\s*([-*+]|\d+[.)])\s", " ", text)
    nums = {n.replace(",", "") for n in NUMBER.findall(text)}
    return nums, dates, shorts


def evidence_pool(steps: list[dict]) -> tuple[list[float], set[str]]:
    numbers: list[float] = []
    dates: set[str] = set()

    def walk(value, key=""):
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            numbers.append(float(value))
        elif isinstance(value, str):
            dates.update(FULL_DATE.findall(value))
            n, d, _ = claims_in(value)
            numbers.extend(float(x) for x in n)
            dates.update(d)
        elif isinstance(value, dict):
            for k, v in value.items():
                numbers.extend(float(x) for x in re.findall(r"_(\d+)d$", str(k)))
                walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)

    for s in steps:
        if not s["result"] or s["error"]:
            continue
        try:
            walk(json.loads(s["result"]))
        except ValueError:
            walk(s["result"])
    return numbers, dates


def found(claim: str, pool: list[float]) -> bool:
    value = float(claim)
    places = len(claim.split(".")[1]) if "." in claim else 0
    half_step = 0.5 * 10 ** (-places) + 1e-9
    return any(abs(value - p) <= half_step for p in pool)


def evidence(answer: str, steps: list[dict]) -> dict:
    nums, dates, shorts = claims_in(answer)
    pool, pool_dates = evidence_pool(steps)
    missing = [n for n in sorted(nums, key=float) if not found(n, pool)]
    missing += [d for d in sorted(dates) if d not in pool_dates]
    missing += [s for s in sorted(shorts) if not any(d.endswith("-" + s) for d in pool_dates)]
    total = len(nums) + len(dates) + len(shorts)
    pct = 100.0 if total == 0 else round(100.0 * (total - len(missing)) / total, 1)
    return {"total": total, "found": total - len(missing), "missing": missing, "pct": pct}


# ---------------------------------------------------------------- checks

def openers_in(answer: str) -> list[str]:
    lines = answer.splitlines()
    marks = [i for i, line in enumerate(lines) if "opener" in line.lower()]
    if not marks:
        return []
    out: list[str] = []
    for line in lines[marks[-1] + 1:]:
        s = line.strip()
        if not s:
            if out:
                break
            continue
        if not BULLET.match(s):
            break
        quoted = QUOTED.findall(s)
        out.extend(quoted if quoted else [BULLET.sub("", s)])
    return out


def run_dates_in(steps: list[dict]) -> set[str]:
    dates = set()
    for s in steps:
        if s["result"] and not s["error"]:
            dates.update(re.findall(r'"(?:pinned_)?run_date"\s*:\s*"(\d{4}-\d{2}-\d{2})', s["result"]))
    return dates


def check(run: dict) -> list[dict]:
    answer, steps = run["answer"], run["steps"]
    checks = []

    def add(name, actual, target, ok, details=()):
        checks.append({"name": name, "actual": actual, "target": target, "ok": ok, "details": list(details)})

    errors = [s for s in steps if s["error"]]
    add("No tool failed", f"{len(errors)} failed" if errors else "0 failed", "0 failed", not errors)

    dates = run_dates_in(steps)
    if dates:
        stated = sorted(d for d in dates if d in answer)
        add("States the run date", ", ".join(stated) or "not stated", " or ".join(sorted(dates)), bool(stated))
    else:
        add("States the run date", "no score data used", "only when scores are used", None)

    if any(s["name"].startswith(SCORE_TOOL_PREFIX) for s in steps):
        labelled = "unvalidated" in answer.lower()
        add("Labels the score v0, unvalidated", "labelled" if labelled else "missing", "labelled", labelled)

    ev = run["evidence"]
    add("Numbers found in tool results", f"{ev['pct']:g}% ({ev['found']} of {ev['total']})",
        f">= {EVIDENCE_TARGET:g}%", ev["pct"] >= EVIDENCE_TARGET)

    money = re.findall("[$\u20ac\u00a3]\\s?\\d[\\d,.]*|\\b\\d[\\d,.]*\\s?(?:usd|dollars|eur)\\b", answer, re.I)
    add("No money amounts invented", ", ".join(money) or "none", "none", not money)

    named = re.findall(r"\bgoal\s?earn\b", answer, re.I)
    add('Product never called "GoalEarn"', f"{len(named)} time{'s' if len(named) > 1 else ''}" if named else "never",
        "never", not named)

    openers = run.get("openers") if run.get("openers") is not None else openers_in(answer)
    if openers:
        broken = []
        for o in openers:
            why = [label for label, pat in TRACKED if re.search(pat, o, re.I)]
            if re.search(ASSUMED_NEED, o, re.I):
                why.append("assumes a need")
            if why:
                broken.append(f'"{o}" ({", ".join(why)})')
        add("Openers follow the rule", f"{len(openers) - len(broken)} of {len(openers)} ok", "all ok",
            not broken, broken)
    else:
        add("Openers follow the rule", "no openers", "only when openers are given", None)
    return checks


# ---------------------------------------------------------------- summaries

def short_tool(name: str) -> str:
    return name[len(SCORE_TOOL_PREFIX):] if name.startswith(SCORE_TOOL_PREFIX) else name


def step_summary(step: dict) -> str:
    if step.get("summary"):
        return step["summary"]
    if step["error"]:
        return "failed: " + (step["result"] or "")[:90]
    try:
        data = json.loads(step["result"] or "")
    except ValueError:
        return "done" if step["name"] == "ToolSearch" else (step["result"] or "no result")[:60]
    if not isinstance(data, dict):
        return "done"
    name = short_tool(step["name"])
    if name == "list_pqas":
        return f"{data.get('pqa_count')} PQAs of {data.get('eligible_orgs')} eligible, run {data.get('run_date')}"
    if name == "explain_account":
        if not data.get("scored", True):
            return "not scored"
        return f"{data.get('organization_id')}: box {data.get('box')}, {len(data.get('signals', []))} signals"
    if name == "query_metric":
        return f"{data.get('row_count')} rows"
    if name == "list_metrics":
        return f"{len(data.get('cubes', {}))} cubes on the menu"
    return "done"


def seconds(a, b) -> float | None:
    return None if not a or not b else max(0.0, (b - a).total_seconds())


def tokens(run: dict) -> tuple[int, int, int]:
    used_in = cached = used_out = 0
    for u in run["usage"].values():
        used_in += u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)
        cached += u.get("cache_read_input_tokens", 0)
        used_out += u.get("output_tokens", 0)
    return used_in, cached, used_out


def local(t: dt.datetime | None) -> str:
    return t.astimezone().strftime("%d %b %Y, %H:%M") if t else "unknown"


# ---------------------------------------------------------------- pages

STYLE = """
:root{--bg:#eef1f5;--card:#fff;--ink:#16202c;--mute:#5d6875;--line:#d5dbe3;--route:#2c5aa0;
--pass:#1f7a4a;--passbg:#e3f3ea;--fail:#b3261e;--failbg:#fbe7e5;--gap:#9a5b00;--gapbg:#fbf0dc;--na:#6b7480;--nabg:#eceff3}
@media (prefers-color-scheme:dark){:root{--bg:#10151b;--card:#18202a;--ink:#e4e9ef;--mute:#9aa5b1;--line:#2c3744;
--route:#82a8e6;--pass:#6fd39b;--passbg:#163626;--fail:#f19089;--failbg:#3d1c1a;--gap:#e8b25c;--gapbg:#3a2c12;--na:#a3adb8;--nabg:#232c37}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 "Segoe UI",system-ui,sans-serif}
main{max-width:980px;margin:0 auto;padding:28px 18px 56px}
h1,h2,.big,.stat b{font-family:Bahnschrift,"DIN Alternate","Segoe UI",sans-serif;font-weight:600;letter-spacing:.01em}
h1{font-size:30px;line-height:1.2;margin:4px 0 6px;max-width:30ch}
h2{font-size:19px;margin:0 0 12px}
a{color:var(--route)}
.meta{color:var(--mute);font-size:14px;margin:0 0 22px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:20px 22px;margin:0 0 18px}
.verdicts{display:grid;grid-template-columns:repeat(3,1fr);gap:0;padding:0;overflow:hidden}
.verdict{padding:18px 22px;border-right:1px solid var(--line)}
.verdict:last-child{border-right:0}
.verdict span{display:block;color:var(--mute);font-size:13px}
.big{font-size:28px;line-height:1.15}
.pass{color:var(--pass)}.fail{color:var(--fail)}.gap{color:var(--gap)}.na{color:var(--na)}
.verdict p{margin:6px 0 0;font-size:13px;color:var(--mute)}
.route{list-style:none;margin:6px 0 0;padding:0;position:relative}
.route li{position:relative;padding:0 0 18px 30px}
.route li:before{content:"";position:absolute;left:8px;top:6px;width:10px;height:10px;border-radius:50%;
background:var(--card);border:3px solid var(--route)}
.route li:after{content:"";position:absolute;left:14px;top:20px;bottom:-4px;width:2px;background:var(--route);opacity:.45}
.route li:last-child:after{display:none}
.route li.end:before{background:var(--route)}
.route li.bad:before{border-color:var(--fail)}
.route .what{font-weight:600}
.route .how{color:var(--mute);font-size:13.5px;word-break:break-word}
.stats{display:flex;flex-wrap:wrap;gap:10px 28px}
.stat b{display:block;font-size:22px}
.stat span{color:var(--mute);font-size:13px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;color:var(--mute);font-weight:600;border-bottom:1px solid var(--line);padding:6px 8px}
td{border-bottom:1px solid var(--line);padding:9px 8px;vertical-align:top}
tr:last-child td{border-bottom:0}
.chip{display:inline-block;border-radius:999px;padding:1px 10px;font-size:12.5px;font-weight:600;white-space:nowrap}
.chip.pass{background:var(--passbg)}.chip.fail{background:var(--failbg)}.chip.gap{background:var(--gapbg)}.chip.na{background:var(--nabg)}
.bar{height:8px;border-radius:4px;background:var(--line);overflow:hidden;margin:8px 0 10px}
.bar i{display:block;height:100%;background:var(--route)}
.answer{white-space:pre-wrap;font-size:14px;max-height:none}
details summary{cursor:pointer;font-weight:600}
.foot{color:var(--mute);font-size:12.5px;margin-top:26px}
.checks td:first-child{width:26%}.checks td:nth-child(3){white-space:nowrap}
.why{margin:6px 0 0;padding-left:18px;color:var(--fail)}.why li{margin:3px 0}
@media (max-width:640px){.verdicts{grid-template-columns:1fr}.verdict{border-right:0;border-bottom:1px solid var(--line)}h1{font-size:24px}
.checks th:nth-child(3),.checks td:nth-child(3){display:none}}
"""


def page(title: str, body: str) -> str:
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{STYLE}</style></head><body><main>{body}</main></body></html>")


def chip(ok, yes="Pass", no="Fail", none="Not applicable") -> str:
    if ok is None:
        return f"<span class='chip na'>{none}</span>"
    return f"<span class='chip {'pass' if ok else 'fail'}'>{yes if ok else no}</span>"


def run_page(run: dict) -> str:
    e = html.escape
    finished, goal, ev = run["finished"], run["goal"], run["evidence"]
    t_in, t_cached, t_out = tokens(run)
    took = seconds(run["start"], run["end"])
    failed = [c["name"] for c in run["checks"] if c["ok"] is False]

    verdicts = (
        "<section class='card verdicts'>"
        f"<div class='verdict'><span>Finished</span><div class='big {'pass' if finished else 'fail'}'>"
        f"{'Yes' if finished else 'No'}</div><p>{'An answer came back, no tool failed' if finished else 'No answer, or a tool failed'}</p></div>"
        f"<div class='verdict'><span>Met its goal</span><div class='big {'pass' if goal else 'fail'}'>"
        f"{'Yes' if goal else 'No'}</div><p>{e('All checks passed' if goal else 'Failed: ' + ', '.join(failed))}</p></div>"
        f"<div class='verdict'><span>Evidence</span><div class='big {'pass' if ev['pct'] >= EVIDENCE_TARGET else 'gap'}'>"
        f"{ev['pct']:g}%</div><p>{ev['found']} of {ev['total']} distinct numbers and dates found in tool results</p></div>"
        "</section>")

    stops = [f"<li><div class='what'>Question</div><div class='how'>{e(run['question'][:300])}</div></li>"]
    for s in run["steps"]:
        dur = seconds(s["start"], s["end"])
        args = json.dumps(s["input"], ensure_ascii=False)
        args = "" if args in ("{}", "null") else (args if len(args) <= 90 else args[:87] + "...")
        how = " / ".join(x for x in (args, step_summary(s), f"{dur:.1f} s" if dur is not None else "") if x)
        label = short_tool(s["name"]) + (" (Claude Code loads tool definitions)" if s["name"] == "ToolSearch" else "")
        stops.append(f"<li class='{'bad' if s['error'] else ''}'><div class='what'>{e(label)}</div>"
                     f"<div class='how'>{e(how)}</div></li>")
    stops.append(f"<li class='end'><div class='what'>Answer</div><div class='how'>"
                 f"{len(run['answer'].split())} words</div></li>")
    route = f"<section class='card'><h2>How the run went</h2><ol class='route'>{''.join(stops)}</ol></section>"

    rows = "".join(f"<tr><td>{e(c['name'])}</td><td>{e(c['actual'])}"
                   + ("<ul class='why'>" + "".join(f"<li>{e(d)}</li>" for d in c["details"]) + "</ul>"
                      if c["details"] else "")
                   + f"</td><td>{e(c['target'])}</td><td>{chip(c['ok'])}</td></tr>" for c in run["checks"])
    checks = (f"<section class='card'><h2>Checks</h2><table class='checks'><tr><th>Check</th><th>This run</th>"
              f"<th>Target</th><th>Result</th></tr>{rows}</table></section>")

    missing = ", ".join(ev["missing"]) or "none"
    evid = (f"<section class='card'><h2>Evidence</h2><div class='bar'><i style='width:{ev['pct']}%'></i></div>"
            f"<p>{ev['found']} of {ev['total']} distinct numbers and dates in the answer appear in the results of the tools "
            f"this run called. Not found: <b>{e(missing)}</b>.</p><p class='meta' style='margin:0'>A value that is not "
            "found was either worked out from other numbers (check the arithmetic) or came from outside the tools. "
            "v0 checks that each value appears somewhere in the results, not that it sits next to the right account."
            "</p></section>")

    tool_calls = sum(1 for s in run["steps"] if s["result"] is not None or s["name"].startswith("mcp__"))
    stats = [("tool calls", str(tool_calls)), ("model turns", str(len(run["usage"]))),
             ("seconds", f"{took:.0f}" if took is not None else "?"),
             ("input tokens", f"{t_in:,}"), ("of them read from cache", f"{t_cached:,}"),
             ("output tokens", f"{t_out:,}"), ("thinking steps", str(run["thinking"]))]
    usage = ("<section class='card'><h2>Usage</h2><div class='stats'>"
             + "".join(f"<div class='stat'><b>{v}</b><span>{k}</span></div>" for k, v in stats)
             + f"</div><p class='meta' style='margin:12px 0 0'>Model: {e(', '.join(sorted(run['models'])) or 'none')}. "
             + (f"List-price cost of the model call: ${run['cost_usd']:.4f} (from Claude Code's headless result). "
                if run.get("cost_usd") is not None else
                "Cost is not recorded in Claude Code's records, so it is not shown. ")
             + (f"Writer: {e(run['writer'])}." if run.get("writer") else "")
             + "</p></section>")

    answer = (f"<section class='card'><details><summary>"
              f"{'The brief as published' if run.get('kind') == 'workflow' else 'The answer as Claude wrote it'}</summary>"
              f"<p class='answer'>{e(run['answer'] or '(no answer)')}</p></details></section>")

    head = (f"<p class='meta'><a href='index.html'>All runs</a></p><h1>{e(run['question'][:140])}</h1>"
            f"<p class='meta'>{'Workflow' if run.get('kind') == 'workflow' else 'Agent'} run {e(run['id'])}, "
            f"{e(local(run['start']))}</p>")
    foot = (f"<p class='foot'>Revenue Signals scorecard, {GRADER_VERSION}. Source: {e(run['source'])}. "
            f"Generated {e(dt.datetime.now().strftime('%d %b %Y, %H:%M'))}.</p>")
    return page(f"Run {run['id']}", head + verdicts + route + checks + evid + usage + answer + foot)


def index_page(runs: list[dict], skipped: int = 0) -> str:
    e = html.escape
    rows = []
    for r in sorted(runs, key=lambda r: r["start"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True):
        failed = ", ".join(c["name"] for c in r["checks"] if c["ok"] is False) or "none"
        ev = r["evidence"]["pct"]
        rows.append(
            f"<tr><td><a href='{e(r['id'])}.html'>{e(r['question'][:90])}</a><br>"
            f"<span class='meta'>{'Workflow' if r.get('kind') == 'workflow' else 'Agent'} &middot; "
            f"{e(local(r['start']))}</span></td>"
            f"<td>{chip(r['finished'], 'Yes', 'No')}</td><td>{chip(r['goal'], 'Yes', 'No')}</td>"
            f"<td><span class='chip {'pass' if ev >= EVIDENCE_TARGET else 'gap'}'>{ev:g}%</span></td>"
            f"<td>{e(failed)}</td></tr>")
    n = len(runs)
    passed = sum(1 for r in runs if r["goal"])
    finished = sum(1 for r in runs if r["finished"])
    avg_ev = sum(r["evidence"]["pct"] for r in runs) / n
    avg_calls = sum(len(r["steps"]) for r in runs) / n
    times = [t for t in (seconds(r["start"], r["end"]) for r in runs) if t is not None]
    fails: dict[str, int] = {}
    for r in runs:
        for c in r["checks"]:
            if c["ok"] is False:
                fails[c["name"]] = fails.get(c["name"], 0) + 1
    stats = [(f"{passed} of {n}", "met their goal"), (f"{finished} of {n}", "finished"),
             (f"{avg_ev:.0f}%", "average evidence"), (f"{avg_calls:.1f}", "tool calls per run"),
             (f"{sum(times) / len(times):.0f} s" if times else "?", "average run time")]
    common = ", ".join(f"{k} ({v})" for k, v in sorted(fails.items(), key=lambda kv: -kv[1])) or "none"
    body = ("<h1>Agent runs</h1>"
            f"<p class='meta'>Revenue Signals runs, newest first: agent runs are questions asked in Claude Code; "
            "workflow runs are Monday briefs.</p>"
            "<section class='card'><div class='stats'>"
            + "".join(f"<div class='stat'><b>{v}</b><span>{k}</span></div>" for v, k in stats)
            + f"</div><p class='meta' style='margin:12px 0 0'>Checks failed most: {e(common)}.</p></section>"
            "<section class='card'><table><tr><th>Question</th><th>Finished</th><th>Met its goal</th>"
            f"<th>Evidence</th><th>Failed checks</th></tr>{''.join(rows)}</table></section>"
            + (f"<p class='meta'>{skipped} other questions in these records used no Revenue Signals tool "
               "and are not scored.</p>" if skipped else "")
            + f"<p class='foot'>Revenue Signals scorecards, {GRADER_VERSION}. Generated "
            f"{e(dt.datetime.now().strftime('%d %b %Y, %H:%M'))}.</p>")
    return page("Agent runs", body)


# ---------------------------------------------------------------- main

def build(records: Path | None, out: Path, score_all: bool = False) -> tuple[list[dict], int]:
    """Read every run, check it, write the pages and the CSV. Returns (runs, skipped questions)."""
    runs: list[dict] = []
    skipped = 0
    if records is not None and records.is_dir():
        for path in sorted(records.glob("*.jsonl")):
            for run in split_runs(read_jsonl(path), path.stem):
                run["source"] = path.name
                if score_all or any(s["name"].startswith(SCORE_TOOL_PREFIX) for s in run["steps"]):
                    runs.append(run)
                else:
                    skipped += 1
    runs += workflow_runs(out / "records")
    if not runs:
        return runs, skipped

    for r in runs:
        r["evidence"] = evidence(r["answer"], r["steps"])
        r["checks"] = check(r)
        r["finished"] = bool(r["answer"]) and not any(s["error"] for s in r["steps"])
        r["goal"] = r["finished"] and all(c["ok"] is not False for c in r["checks"])

    out.mkdir(parents=True, exist_ok=True)
    for r in runs:
        (out / f"{r['id']}.html").write_text(run_page(r), encoding="utf-8")
    (out / "index.html").write_text(index_page(runs, skipped), encoding="utf-8")
    with (out / "agent_runs.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["run_id", "kind", "session", "started_at", "seconds", "question", "tools", "tool_calls",
                    "model_turns", "input_tokens", "output_tokens", "cost_usd", "finished", "met_goal",
                    "evidence_pct", "failed_checks"])
        for r in runs:
            t_in, _, t_out = tokens(r)
            took = seconds(r["start"], r["end"])
            w.writerow([r["id"], r.get("kind", "agent"), r["session"], r["start"].isoformat() if r["start"] else "",
                        f"{took:.0f}" if took is not None else "", r["question"][:200],
                        " ".join(short_tool(s["name"]) for s in r["steps"]), len(r["steps"]),
                        len(r["usage"]), t_in, t_out,
                        "" if r.get("cost_usd") is None else f"{r['cost_usd']:.4f}",
                        r["finished"], r["goal"], r["evidence"]["pct"],
                        "; ".join(c["name"] for c in r["checks"] if c["ok"] is False)])
    return runs, skipped


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # a console that cannot show a character prints ? instead
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--records", help="folder with Claude Code .jsonl records (default: this project's)")
    ap.add_argument("--out", default="runs", help="output folder (default: runs)")
    ap.add_argument("--all", action="store_true",
                    help="score every question, not only those that used a Revenue Signals tool")
    args = ap.parse_args()

    folder = records_folder(args.records)
    out = Path(args.out)
    runs, skipped = build(folder, out, args.all)
    print(f"Claude Code records: {folder or 'none found for this folder'}")
    if not runs:
        print(f"No Revenue Signals runs found ({skipped} other questions skipped)")
        return 1
    for r in sorted(runs, key=lambda r: r["start"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc)):
        failed = ", ".join(c["name"] for c in r["checks"] if c["ok"] is False) or "-"
        q = r["question"].replace("\n", " ")
        q = q if len(q) <= 48 else q[:45] + "..."
        print(f"{r['id']:<26} {local(r['start']):>19}  finished {'yes' if r['finished'] else 'NO ':3}  "
              f"goal {'yes' if r['goal'] else 'NO ':3}  evidence {r['evidence']['pct']:>5g}%  {q}")
        print(f"{'':27}failed checks: {failed}")
    if skipped:
        print(f"\n{skipped} other questions used no Revenue Signals tool and were not scored (--all scores them).")
    print(f"\nWrote {len(runs)} scorecards, {out / 'index.html'} and {out / 'agent_runs.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
