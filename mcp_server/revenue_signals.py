"""Revenue Signals MCP server (GoalEarn Project 03, steps 4-6).

Read tools over the Cube semantic layer, one append-only write tool and one proposal tool.
The agent orders metrics by name; it never writes SQL and never opens dev.duckdb.

    list_metrics     the menu: every measure and dimension Cube exposes, with descriptions
    list_pqas        this week's product-qualified accounts, with contact and reasons
    explain_account  why one organization scores where it does, with the evidence
    query_metric     any governed metric by name, through Cube
    log_outcome      records what happened after a call (append-only file, never edits)
    review_outcomes  the monthly review: did flagged accounts book meetings, and with which signals
    propose_weight_change  opens a git branch (and pull request) changing one weight; a human merges.
                     Refused unless review_outcomes finds enough evidence. Never touches main.

Outcomes go to outcomes/outcomes.csv (override with REVENUE_SIGNALS_OUTCOMES). They reach
Cube on the weekly refresh: load_outcomes.py -> dbt build -> export_for_cube.py.

Run by the MCP host (Claude Code) over stdio. Over stdio, stdout is the protocol pipe,
so this file never prints: logs go to stderr.

Needs: Python 3.10+, mcp>=2.2,<3, and Cube running (default http://localhost:4000).
Override the address with the CUBE_API_URL environment variable.
"""
from __future__ import annotations

import csv
import datetime as dt
import difflib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

CUBE_API = os.environ.get("CUBE_API_URL", "http://localhost:4000/cubejs-api/v1").rstrip("/")
SCORE_LABEL = "Score v0, unvalidated: expert weights, not yet checked against real outcomes."
OPENERS_RULE = (
    "Openers: open questions about the buyer's goals. Never mention page visits, docs reading, "
    "activity counts or dates, and never assume a need. A trial they started may be named plainly."
)
SCORE_CUBES = ("org_scores", "user_scores", "signal_reasons", "outcomes")
CUBE_NAMES = Literal["org_scores", "user_scores", "events", "signal_reasons", "outcomes"]
OUTCOMES = ("meeting_booked", "not_now", "wrong_person", "already_talking", "no_reply")
OUTCOME_COLUMNS = ["outcome_id", "logged_at_utc", "organization_id", "run_date", "outcome",
                   "note", "is_test", "logged_by"]
ROOT = Path(__file__).resolve().parent.parent
OUTCOMES_FILE = Path(os.environ.get("REVENUE_SIGNALS_OUTCOMES") or ROOT / "outcomes" / "outcomes.csv")
MIN_CALLED = int(os.environ.get("REVENUE_SIGNALS_MIN_CALLED", "30"))  # evidence gate for any weight change
SEED = Path("seeds") / "intent_signal_weights.csv"  # relative to the repo root
MAX_ROWS = 500
ORG_ID = re.compile(r"^org_\d{4}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
APPEND_ONLY = ToolAnnotations(  # writes, but only adds a row: never edits or deletes
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)
PROPOSES = ToolAnnotations(  # creates a branch and may push it to GitHub; never changes main
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True
)

logging.basicConfig(
    stream=sys.stderr,
    level=os.environ.get("REVENUE_SIGNALS_LOG", "INFO").upper(),
    format="%(asctime)s revenue-signals %(levelname)s %(message)s",
)
log = logging.getLogger("revenue-signals")

INSTRUCTIONS = """\
Revenue Signals for our CI/CD platform (a developer product; the data is simulated).
The product has no brand name in this data. Call it "our CI/CD platform" or "the platform".
GoalEarn is the training program behind this data, not the product: never use it as a product name.
A PQA (product-qualified account) is an organization Sales should call this week. dbt scores
every active, non-Enterprise organization each run date, and Cube serves the numbers.

Rules for answers:
1. Every number comes from one of these tools. Name the metric and the run date it came from.
2. The score is v0 and unvalidated. Say so whenever you present scores.
3. Signals show who is worth talking to, not what they need. When you suggest openers:
   - Ask open questions about the buyer's own goals. Never pitch.
   - Never mention what we observed without them knowing: page visits, docs reading,
     counts of pipelines or events, or dates of activity. That is for the rep, not the script.
   - Never assume a problem or a need ("is anything unclear?", "where does it slow you down?").
   - A step the buyer took with us on purpose, like starting a trial, may be named plainly,
     without guessing why.
   Good: "What are you hoping to get done with your CI/CD setup this quarter?"
   Good: "You started a trial with us. What did you want to find out?"
   Bad: "I saw you visited our pricing page." Bad: "Where is CI/CD slowing your team down?"
4. There is no revenue, price or plan-history data. Say so instead of estimating.
5. Nothing here contacts a customer. One tool writes: log_outcome adds a row to the outcomes
   log. Call it only when the user tells you what happened on a call, with one of the five
   outcomes. Set test=true when the user says it is a test. Never log on your own initiative,
   and never guess an outcome the user did not state.
6. Weights change only through the monthly review: call review_outcomes first. Call
   propose_weight_change only when review_outcomes says enough_evidence is true, for one
   signal, with a modest step and a rationale that cites the numbers. It opens a branch and
   pull request for a human to review and merge; it never changes the live weights.
Start with list_pqas for "who should I call", explain_account for "why this account",
list_metrics to see what can be asked, query_metric for anything else, log_outcome
to record a call result, and review_outcomes for the monthly review.
"""

server = MCPServer(name="revenue-signals", instructions=INSTRUCTIONS)


# ---------------------------------------------------------------- Cube access

def _cube(path: str, query: dict | None = None) -> dict:
    url = f"{CUBE_API}/{path}"
    if query is not None:
        url += "?query=" + urllib.parse.quote(json.dumps(query))
    for _ in range(30):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")[:500]
            raise ToolError(f"Cube rejected the query (HTTP {err.code}): {detail}") from None
        except urllib.error.URLError as err:
            raise ToolError(
                f"Cube is not reachable at {CUBE_API} ({err.reason}). "
                "Start Docker Desktop and the goalearn-cube container, then try again."
            ) from None
        if body.get("error") == "Continue wait":
            time.sleep(2)
            continue
        if "error" in body:
            raise ToolError(f"Cube error: {body['error']}")
        return body
    raise ToolError("Cube kept answering 'Continue wait' for a minute. Try again shortly.")


def _load(query: dict) -> list[dict]:
    log.info("cube load %s", json.dumps(query, sort_keys=True))
    return _cube("load", query).get("data", [])


def _eq(member: str, value: str) -> dict:
    return {"member": member, "operator": "equals", "values": [value]}


def _true(member: str) -> dict:
    return {"member": member, "operator": "equals", "values": ["true"]}


def _on(member: str, day: str) -> dict:
    return {"member": member, "operator": "inDateRange", "values": [day, day]}


def _num(value: Any) -> Any:
    """Cube sends numbers as strings. Convert them; round only here, at the edge."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        number = value
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return value
    if float(number).is_integer():
        return int(number)
    return round(float(number), 4)


def _bool(value: Any) -> bool:
    return value is True or str(value).lower() == "true"


def _pct(value: Any) -> int | None:
    """pct_rank 0..1 -> 'beats N% of eligible organizations'."""
    return None if value is None else int(round(float(value) * 100))


def _day(value: Any) -> str | None:
    return None if value in (None, "") else str(value)[:10]


def _check_date(run_date: str | None) -> None:
    if run_date is not None and not DATE.match(run_date):
        raise ToolError(f"run_date must look like 2026-08-31, got {run_date!r}.")


def _run_dates(org_id: str | None = None, limit: int = 10) -> list[str]:
    """Run dates with scores, newest first (optionally only those that scored org_id)."""
    query: dict[str, Any] = {
        "measures": ["org_scores.eligible_orgs"],
        "timeDimensions": [{"dimension": "org_scores.run_date", "granularity": "day"}],
        "order": {"org_scores.run_date": "desc"},
        "limit": limit,
    }
    if org_id:
        query["filters"] = [_eq("org_scores.organization_id", org_id)]
    rows = _load(query)
    return [_day(r.get("org_scores.run_date.day") or r.get("org_scores.run_date")) for r in rows]


def _latest_run_date() -> str:
    dates = _run_dates(limit=1)
    if not dates:
        raise ToolError("Cube has no scored run dates. Run dbt build and the export first.")
    return dates[0]


def _menu() -> dict[str, dict]:
    members: dict[str, dict] = {}
    for cube in _cube("meta").get("cubes", []):
        for kind in ("measures", "dimensions", "segments"):
            for member in cube.get(kind, []):
                members[member["name"]] = {
                    "cube": cube["name"],
                    "kind": kind[:-1],
                    "type": member.get("type"),
                    "description": (member.get("description") or "").strip(),
                }
    return members


ORG_FIELDS = [
    "organization_id", "plan_type", "quadrant", "is_pqa", "n_users", "pipelines_run",
    "usage_score", "buying_score", "intent_score", "usage_pct_rank", "buying_pct_rank",
    "contact_user_id", "score_version",
]


def _org_row(org_id: str, run_date: str) -> dict | None:
    rows = _load({
        "dimensions": [f"org_scores.{f}" for f in ORG_FIELDS],
        "filters": [_eq("org_scores.organization_id", org_id), _on("org_scores.run_date", run_date)],
        "limit": 2,
    })
    if not rows:
        return None
    return {f: rows[0].get(f"org_scores.{f}") for f in ORG_FIELDS}


# ---------------------------------------------------------------- tools

@server.tool(annotations=READ_ONLY)
def list_metrics(
    cube: Annotated[
        CUBE_NAMES | None,
        Field(description="Only this cube. Leave empty for the whole menu."),
    ] = None,
) -> dict[str, Any]:
    """The menu: every metric (measure) and field (dimension) Cube exposes, with its meaning.

    Call this before query_metric when you are not sure of a metric's exact name. Names are
    cube.member, for example org_scores.pqa_count. If a number someone asks for is not on this
    menu (revenue, for example), it does not exist in this data: say so.
    """
    menu = _menu()
    cubes: dict[str, dict] = {}
    for name, info in sorted(menu.items()):
        if cube and info["cube"] != cube:
            continue
        entry = cubes.setdefault(info["cube"], {"measures": [], "dimensions": []})
        item = {"name": name, "description": info["description"]}
        if info["kind"] == "dimension":
            item["type"] = info["type"]
            entry["dimensions"].append(item)
        elif info["kind"] == "measure":
            entry["measures"].append(item)
    if cube and cube not in cubes:
        raise ToolError(f"Cube has no cube named {cube!r}.")
    return {
        "cubes": cubes,
        "notes": [
            "Score cubes (org_scores, user_scores, signal_reasons, outcomes) hold one snapshot per "
            "run date. query_metric pins them to the latest run date unless you filter on a run_date.",
            "outcomes counts what Sales logged after calls; test outcomes are excluded.",
            "events is not joined to the score cubes; filter it by organization_id instead.",
            SCORE_LABEL,
        ],
    }


@server.tool(annotations=READ_ONLY)
def list_pqas(
    run_date: Annotated[
        str | None, Field(description="Run date as YYYY-MM-DD. Leave empty for the latest run.")
    ] = None,
) -> dict[str, Any]:
    """This week's product-qualified accounts (PQAs): the organizations Sales should call, best first.

    For each account: plan, team size, the contact to ask for, how it ranks on usage and on buying
    (the share of eligible organizations it beats), and up to three reasons, the signals where it
    stands out. Use it for "who should I call this week?". For one account's full detail, use
    explain_account.
    """
    _check_date(run_date)
    day = run_date or _latest_run_date()
    on_day = _on("org_scores.run_date", day)

    totals = _load({
        "measures": ["org_scores.eligible_orgs", "org_scores.pqa_count"],
        "filters": [on_day],
    })
    eligible = _num(totals[0].get("org_scores.eligible_orgs")) if totals else 0
    if not eligible:
        raise ToolError(f"No organizations were scored on {day}. Known run dates: {_run_dates()}")

    fields = ["organization_id", "plan_type", "n_users", "contact_user_id",
              "usage_pct_rank", "buying_pct_rank", "intent_score"]
    orgs = _load({
        "dimensions": [f"org_scores.{f}" for f in fields],
        "filters": [_true("org_scores.is_pqa"), on_day],
        "order": {"org_scores.intent_score": "desc"},
        "limit": MAX_ROWS,
    })
    reasons = _load({
        "dimensions": ["signal_reasons.organization_id", "signal_reasons.reason_rank",
                       "signal_reasons.signal", "signal_reasons.reason_text"],
        "filters": [_true("signal_reasons.is_reason"), _true("org_scores.is_pqa"),
                    _on("signal_reasons.run_date", day)],
        "order": {"signal_reasons.organization_id": "asc", "signal_reasons.reason_rank": "asc"},
        "limit": MAX_ROWS,
    })
    by_org: dict[str, list[dict]] = {}
    for r in reasons:
        by_org.setdefault(r["signal_reasons.organization_id"], []).append({
            "rank": _num(r["signal_reasons.reason_rank"]),
            "signal": r["signal_reasons.signal"],
            "text": r["signal_reasons.reason_text"],
        })

    accounts = []
    for position, o in enumerate(orgs, start=1):
        org_id = o["org_scores.organization_id"]
        accounts.append({
            "position": position,
            "organization_id": org_id,
            "plan": o["org_scores.plan_type"],
            "users": _num(o["org_scores.n_users"]),
            "contact_user_id": o["org_scores.contact_user_id"],
            "usage_beats_pct": _pct(o["org_scores.usage_pct_rank"]),
            "buying_beats_pct": _pct(o["org_scores.buying_pct_rank"]),
            "reasons": sorted(by_org.get(org_id, []), key=lambda x: x["rank"]),
        })

    return {
        "run_date": day,
        "label": SCORE_LABEL,
        "eligible_orgs": eligible,
        "pqa_count": len(accounts),
        "accounts": accounts,
        "openers_rule": OPENERS_RULE,
        "rule": "A PQA is in the top third on both usage_score and buying_score among "
                "active, non-Enterprise organizations. Reasons: signals where the organization "
                "is in the top third, ranked by how much it stands out, at most 3.",
        "evidence": [
            "org_scores.eligible_orgs", "org_scores.pqa_count", "org_scores.is_pqa",
            "org_scores.usage_pct_rank", "org_scores.buying_pct_rank", "org_scores.contact_user_id",
            "signal_reasons.reason_text", "signal_reasons.reason_rank",
        ],
    }


@server.tool(annotations=READ_ONLY)
def explain_account(
    org_id: Annotated[str, Field(description="Organization id, for example org_0057.")],
    run_date: Annotated[
        str | None, Field(description="Run date as YYYY-MM-DD. Leave empty for the latest run.")
    ] = None,
) -> dict[str, Any]:
    """Why one organization scores where it does: its box, scores, ranks, contact, the people
    behind the score, and every signal with its event count, points and last date.

    Also reports what changed since the organization's previous run, when one exists. Use it for
    "why is org_0057 a PQA?" or "tell me about org_0057". Every number names its metric.
    """
    org_id = org_id.strip()
    if not ORG_ID.match(org_id):
        raise ToolError(f"org_id must look like org_0057, got {org_id!r}.")
    _check_date(run_date)

    dates = _run_dates(org_id=org_id, limit=52)
    if run_date:
        day = run_date
        earlier = [d for d in dates if d < run_date]
    else:
        day = dates[0] if dates else _latest_run_date()
        earlier = dates[1:]
    org = _org_row(org_id, day) if (dates and day in dates) else None
    if org is None:
        return {
            "organization_id": org_id,
            "run_date": day,
            "scored": False,
            "message": f"{org_id} has no score on {day}. Only active, non-Enterprise "
                       "organizations are scored, so it is inactive, on Enterprise, or unknown.",
            "evidence": ["org_scores.organization_id", "org_scores.run_date"],
        }

    signals = _load({
        "dimensions": [f"signal_reasons.{f}" for f in (
            "signal", "family", "weight", "event_count", "points", "pct_rank",
            "standout_rank", "is_reason", "reason_rank", "reason_text")],
        "timeDimensions": [{"dimension": "signal_reasons.last_seen_at", "granularity": "day"}],
        "filters": [_eq("signal_reasons.organization_id", org_id), _on("signal_reasons.run_date", day)],
        "order": {"signal_reasons.standout_rank": "asc"},
        "limit": 50,
    })
    people = _load({
        "dimensions": ["user_scores.user_id", "user_scores.user_intent_score", "user_scores.rank_in_org"],
        "filters": [_eq("user_scores.organization_id", org_id), _on("user_scores.run_date", day),
                    {"member": "user_scores.rank_in_org", "operator": "lte", "values": ["3"]}],
        "order": {"user_scores.rank_in_org": "asc"},
        "limit": 3,
    })

    if earlier:
        before = _org_row(org_id, earlier[0]) or {}
        change: Any = {
            "previous_run_date": earlier[0],
            "box": f"{before.get('quadrant')} -> {org['quadrant']}",
            "usage_beats_pct": f"{_pct(before.get('usage_pct_rank'))} -> {_pct(org['usage_pct_rank'])}",
            "buying_beats_pct": f"{_pct(before.get('buying_pct_rank'))} -> {_pct(org['buying_pct_rank'])}",
        }
    else:
        change = (f"No earlier run yet: {day} is the only scored run date for {org_id}, "
                  "so there is no week-on-week change to report.")

    return {
        "organization_id": org_id,
        "run_date": day,
        "scored": True,
        "label": SCORE_LABEL,
        "box": org["quadrant"],
        "is_pqa": _bool(org["is_pqa"]),
        "plan": org["plan_type"],
        "users": _num(org["n_users"]),
        "pipelines_run_90d": _num(org["pipelines_run"]),
        "usage_score": round(float(org["usage_score"]), 2),
        "buying_score": round(float(org["buying_score"]), 2),
        "usage_beats_pct": _pct(org["usage_pct_rank"]),
        "buying_beats_pct": _pct(org["buying_pct_rank"]),
        "contact_user_id": org["contact_user_id"],
        "top_users": [
            {"user_id": p["user_scores.user_id"], "rank_in_org": _num(p["user_scores.rank_in_org"]),
             "user_intent_score": _num(p["user_scores.user_intent_score"])}
            for p in people
        ],
        "signals": [
            {
                "signal": s["signal_reasons.signal"],
                "family": s["signal_reasons.family"],
                "events_90d": _num(s["signal_reasons.event_count"]),
                "weight": _num(s["signal_reasons.weight"]),
                "points": _num(s["signal_reasons.points"]),
                "beats_pct": _pct(s["signal_reasons.pct_rank"]),
                "is_reason": _bool(s["signal_reasons.is_reason"]),
                "reason_text": s["signal_reasons.reason_text"] or None,
                "last_seen": _day(s.get("signal_reasons.last_seen_at.day")
                                  or s.get("signal_reasons.last_seen_at")),
            }
            for s in signals
        ],
        "change_since_previous_run": change,
        "openers_rule": OPENERS_RULE,
        "how_to_read": "usage_beats_pct 95 = more usage than 95% of eligible organizations. "
                       "Points per signal add up to usage_score + buying_score.",
        "evidence": [
            "org_scores.quadrant", "org_scores.usage_score", "org_scores.buying_score",
            "org_scores.usage_pct_rank", "org_scores.buying_pct_rank", "org_scores.contact_user_id",
            "signal_reasons.event_count", "signal_reasons.points", "signal_reasons.pct_rank",
            "signal_reasons.last_seen_at", "user_scores.user_intent_score",
        ],
    }


class MetricFilter(BaseModel):
    member: str = Field(description="A dimension or measure name, for example org_scores.plan_type.")
    operator: Literal[
        "equals", "notEquals", "contains", "gt", "gte", "lt", "lte", "set", "notSet",
        "inDateRange", "beforeDate", "afterDate",
    ] = Field(description="How to compare. Use inDateRange with two YYYY-MM-DD values for dates.")
    values: list[str] = Field(default_factory=list, description='Values as strings, e.g. ["free"] or ["true"].')


@server.tool(annotations=READ_ONLY)
def query_metric(
    measures: Annotated[
        list[str], Field(description="One or more measure names, e.g. [\"org_scores.pqa_count\"].")
    ],
    dimensions: Annotated[
        list[str] | None, Field(description="Fields to group by, e.g. [\"org_scores.plan_type\"].")
    ] = None,
    filters: Annotated[list[MetricFilter] | None, Field(description="Conditions on dimensions or measures.")] = None,
    time_dimension: Annotated[
        str | None, Field(description="A time field to group by, e.g. events.event_timestamp.")
    ] = None,
    granularity: Annotated[
        Literal["day", "week", "month", "quarter", "year"] | None,
        Field(description="Bucket size for time_dimension."),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=MAX_ROWS, description="Maximum rows to return.")] = 100,
) -> dict[str, Any]:
    """Any governed metric by name, through Cube. No SQL: only names on the menu (list_metrics).

    Examples: PQAs by plan = measures ["org_scores.pqa_count"], dimensions ["org_scores.plan_type"].
    Events per month = measures ["events.event_count"], time_dimension "events.event_timestamp",
    granularity "month". Score cubes are pinned to the latest run date unless you filter a run_date.
    An unknown name is refused with the closest real names.
    """
    menu = _menu()
    dimensions = list(dimensions or [])
    filters = list(filters or [])
    problems: list[str] = []

    def check(name: str, want: str) -> None:
        info = menu.get(name)
        if info is None:
            close = difflib.get_close_matches(name, list(menu), n=3, cutoff=0.5)
            hint = f" Closest real names: {', '.join(close)}." if close else ""
            problems.append(f"{name} is not on the menu.{hint}")
        elif want == "measure" and info["kind"] != "measure":
            problems.append(f"{name} is a dimension, not a measure; put it in dimensions.")
        elif want == "dimension" and info["kind"] != "dimension":
            problems.append(f"{name} is a measure, not a dimension; put it in measures.")
        elif want == "dimension" and info["type"] == "time":
            problems.append(f"{name} is a time field; use time_dimension and granularity instead.")
        elif want == "time" and (info["kind"] != "dimension" or info["type"] != "time"):
            problems.append(f"{name} is not a time field.")

    if not measures:
        problems.append("Give at least one measure.")
    for m in measures:
        check(m, "measure")
    for d in dimensions:
        check(d, "dimension")
    for f in filters:
        if f.member not in menu:
            check(f.member, "any")
    if time_dimension:
        check(time_dimension, "time")
    if granularity and not time_dimension:
        problems.append("granularity needs a time_dimension.")
    if problems:
        raise ToolError(
            "Query refused: " + " ".join(problems)
            + " Call list_metrics to see every metric. If what was asked for is not on the menu, "
            "it does not exist in this data."
        )

    used = measures + dimensions + [f.member for f in filters] + ([time_dimension] if time_dimension else [])
    cubes_used = [m.split(".")[0] for m in used]
    query: dict[str, Any] = {
        "measures": measures,
        "dimensions": dimensions,
        "filters": [f.model_dump() for f in filters],
        "limit": limit,
    }
    if time_dimension:
        query["timeDimensions"] = [{"dimension": time_dimension, "granularity": granularity or "month"}]
        query["order"] = {time_dimension: "asc"}

    pinned = None
    score_cubes = [c for c in cubes_used if c in SCORE_CUBES]
    if score_cubes and not any(m.endswith(".run_date") for m in used):
        pinned = _latest_run_date()
        query["filters"].append(_on(f"{score_cubes[0]}.run_date", pinned))

    rows = _load(query)
    out_rows = [{k: (_num(v) if k in measures else v) for k, v in r.items()} for r in rows]
    result: dict[str, Any] = {
        "rows": out_rows,
        "row_count": len(out_rows),
        "truncated": len(out_rows) >= limit,
        "metrics": used,
        "descriptions": {m: menu[m]["description"] for m in measures},
    }
    if pinned:
        result["pinned_run_date"] = pinned
    if score_cubes:
        result["label"] = SCORE_LABEL
    return result


@server.tool(annotations=APPEND_ONLY)
def log_outcome(
    org_id: Annotated[str, Field(description="Organization id, for example org_0057.")],
    outcome: Annotated[
        Literal["meeting_booked", "not_now", "wrong_person", "already_talking", "no_reply"],
        Field(description="What happened on the call, as the user said it."),
    ],
    note: Annotated[str, Field(max_length=300, description="Optional short note from the rep.")] = "",
    test: Annotated[bool, Field(description="True when the user says this is a test, not a real call.")] = False,
) -> dict[str, Any]:
    """Record what happened after Sales called an account. Append-only: adds one row, never edits.

    Use only when the user states the result of a call. The five outcomes: meeting_booked,
    not_now, wrong_person, already_talking, no_reply. A correction is a new row; the latest row
    for an organization and run date wins. Test rows are kept apart and never counted.
    """
    org_id = org_id.strip()
    if not ORG_ID.match(org_id):
        raise ToolError(f"org_id must look like org_0057, got {org_id!r}.")
    if outcome not in OUTCOMES:
        raise ToolError(f"outcome must be one of {', '.join(OUTCOMES)}; got {outcome!r}.")
    note = " ".join(note.split())  # one line, no stray whitespace
    dates = _run_dates(org_id=org_id, limit=1)
    if not dates:
        raise ToolError(f"{org_id} has no score on any run date, so there is no list it was called from.")
    row = {
        "outcome_id": uuid.uuid4().hex,
        "logged_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "organization_id": org_id,
        "run_date": dates[0],
        "outcome": outcome,
        "note": note,
        "is_test": "true" if test else "false",
        "logged_by": "claude-code",
    }
    OUTCOMES_FILE.parent.mkdir(parents=True, exist_ok=True)
    new_file = not OUTCOMES_FILE.exists() or OUTCOMES_FILE.stat().st_size == 0
    with OUTCOMES_FILE.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTCOME_COLUMNS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)
    log.info("logged outcome %s %s %s test=%s", org_id, dates[0], outcome, test)
    return {
        "logged": row,
        "file": str(OUTCOMES_FILE),
        "next": "Outcomes reach Cube on the weekly refresh: load_outcomes.py, dbt build, export_for_cube.py.",
        "note": "Test row: kept apart and never counted." if test else "Counted in outcomes after the refresh.",
    }


# ---------------------------------------------------------------- step 6: monthly review

def _rate(meetings: int, called: int) -> float | None:
    return None if not called else round(meetings / called, 3)


def _review(since: str | None, until: str | None) -> dict[str, Any]:
    _check_date(since)
    _check_date(until)
    until = until or _latest_run_date()
    since = since or (dt.date.fromisoformat(until) - dt.timedelta(days=30)).isoformat()
    window = {"member": "outcomes.run_date", "operator": "inDateRange", "values": [since, until]}
    names = ["scored_orgs", "pqa_orgs", "called_orgs", "called_pqas", "reached_orgs", "meetings_booked"]
    totals_row = (_load({"measures": [f"outcomes.{n}" for n in names], "filters": [window]}) or [{}])[0]
    totals = {n: int(_num(totals_row.get(f"outcomes.{n}")) or 0) for n in names}

    called = _load({
        "dimensions": ["outcomes.organization_id", "outcomes.is_pqa", "outcomes.quadrant",
                       "outcomes.outcome", "outcomes.meeting_booked"],
        "timeDimensions": [{"dimension": "outcomes.run_date", "granularity": "day"}],
        "filters": [window, _true("outcomes.was_called")],
        "limit": MAX_ROWS,
    })
    accounts = [{"org": r["outcomes.organization_id"], "is_pqa": _bool(r["outcomes.is_pqa"]),
                 "box": r["outcomes.quadrant"], "outcome": r["outcomes.outcome"],
                 "meeting": _bool(r["outcomes.meeting_booked"]),
                 "run_date": _day(r.get("outcomes.run_date.day") or r.get("outcomes.run_date"))} for r in called]

    def group(key) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for a in accounts:
            g = out.setdefault(str(key(a)), {"called": 0, "meetings": 0})
            g["called"] += 1
            g["meetings"] += a["meeting"]
        for g in out.values():
            g["meeting_rate"] = _rate(g["meetings"], g["called"])
        return out

    by_signal: dict[str, dict] = {}
    if accounts:
        reasons = _load({
            "dimensions": ["signal_reasons.organization_id", "signal_reasons.signal"],
            "timeDimensions": [{"dimension": "signal_reasons.run_date", "granularity": "day"}],
            "filters": [{"member": "signal_reasons.run_date", "operator": "inDateRange", "values": [since, until]},
                        _true("signal_reasons.is_reason"),
                        {"member": "signal_reasons.organization_id", "operator": "equals",
                         "values": sorted({a["org"] for a in accounts})}],
            "limit": MAX_ROWS,
        })
        has = {(r["signal_reasons.organization_id"],
                _day(r.get("signal_reasons.run_date.day") or r.get("signal_reasons.run_date")),
                r["signal_reasons.signal"]) for r in reasons}
        for signal in sorted({k[2] for k in has}):
            with_it = [a for a in accounts if (a["org"], a["run_date"], signal) in has]
            without = [a for a in accounts if (a["org"], a["run_date"], signal) not in has]
            by_signal[signal] = {
                "called_with_reason": len(with_it), "meetings_with": sum(a["meeting"] for a in with_it),
                "rate_with": _rate(sum(a["meeting"] for a in with_it), len(with_it)),
                "called_without": len(without), "meetings_without": sum(a["meeting"] for a in without),
                "rate_without": _rate(sum(a["meeting"] for a in without), len(without)),
            }

    enough = totals["called_orgs"] >= MIN_CALLED
    return {
        "window": {"since": since, "until": until},
        "label": SCORE_LABEL,
        "totals": totals,
        "meeting_rate_called": _rate(totals["meetings_booked"], totals["called_orgs"]),
        "by_pqa": group(lambda a: "pqa" if a["is_pqa"] else "not_pqa"),
        "by_box": group(lambda a: a["box"]),
        "by_outcome": {k: v["called"] for k, v in group(lambda a: a["outcome"]).items()},
        "by_signal": by_signal,
        "min_called": MIN_CALLED,
        "enough_evidence": enough,
        "verdict": (f"Enough evidence to discuss weights: {totals['called_orgs']} called accounts "
                    f"(minimum {MIN_CALLED})." if enough else
                    f"Not enough evidence to change any weight: {totals['called_orgs']} called accounts "
                    f"in the window, the minimum is {MIN_CALLED}. Keep logging outcomes."),
        "caveats": [
            "Reps choose whom to call, so this is observed evidence, not an experiment.",
            "Signal rates compare called accounts with and without that signal as a reason.",
            "Test outcomes are excluded. Accounts never called are not counted as failures.",
        ],
        "evidence": ["outcomes.scored_orgs", "outcomes.called_orgs", "outcomes.meetings_booked",
                     "outcomes.outcome", "signal_reasons.is_reason"],
    }


@server.tool(annotations=READ_ONLY)
def review_outcomes(
    since: Annotated[str | None, Field(description="First run date, YYYY-MM-DD. Default: 30 days before until.")] = None,
    until: Annotated[str | None, Field(description="Last run date, YYYY-MM-DD. Default: the latest run date.")] = None,
) -> dict[str, Any]:
    """The monthly review: did the accounts we flagged turn into meetings, and which signals went
    with meetings? Totals, meeting rates for PQAs vs other called accounts, by box, by outcome and
    per signal, plus the evidence gate (enough_evidence) that propose_weight_change obeys.
    """
    return _review(since, until)


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")
    if done.returncode != 0:
        raise ToolError(f"git {' '.join(args[:2])} failed: {(done.stderr or done.stdout).strip()[:300]}")
    return done.stdout.strip()


def _seed_change(text: str, signal: str, new_weight: float) -> tuple[str, float]:
    """Change one weight cell in the seed CSV, leaving every other byte as it was."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(newline)
    header = next(csv.reader([lines[0]]))
    if "weight" not in header:
        raise ToolError(f"{SEED} has no weight column (columns: {', '.join(header)}).")
    w = header.index("weight")
    for i, line in enumerate(lines[1:], start=1):
        if not line:
            continue
        cells = next(csv.reader([line]))
        if signal in cells:
            old = float(cells[w])
            cells[w] = f"{new_weight:g}"
            buf = __import__("io").StringIO()
            csv.writer(buf, lineterminator="").writerow(cells)
            lines[i] = buf.getvalue()
            return newline.join(lines), old
    raise ToolError(f"{signal!r} is not a row in {SEED}.")


def _github_compare_url(remote: str, base: str, branch: str) -> str | None:
    m = re.match(r"(?:https://github\.com/|git@github\.com:)([^/]+)/(.+?)(?:\.git)?$", remote)
    return f"https://github.com/{m.group(1)}/{m.group(2)}/compare/{base}...{branch}?expand=1" if m else None


def _open_proposal(repo: Path, signal: str, new_weight: float, rationale: str, evidence_md: str) -> dict:
    """Commit the change on a new branch in a separate worktree, so the working folder and main
    never change. Push it when the repo has a GitHub remote; a human opens and merges the PR."""
    base = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    day = dt.date.today().isoformat()
    branch = f"proposal/weight-{signal}-{day}"
    n = 2
    while _git(repo, "branch", "--list", branch):
        branch, n = f"proposal/weight-{signal}-{day}-{n}", n + 1
    tmp = Path(tempfile.mkdtemp(prefix="weight_proposal_"))
    work = tmp / "work"
    _git(repo, "worktree", "add", "-b", branch, str(work), "HEAD")
    try:
        seed = work / SEED
        if not seed.exists():
            raise ToolError(f"{SEED} is not in the committed repo.")
        text, old = _seed_change(seed.read_bytes().decode("utf-8"), signal, new_weight)
        if abs(old - new_weight) < 1e-9:
            raise ToolError(f"{signal} already has weight {old:g}.")
        seed.write_bytes(text.encode("utf-8"))
        note = work / "proposals" / f"{day}-{signal}.md"
        note.parent.mkdir(exist_ok=True)
        note.write_text(
            f"# Proposal: {signal} weight {old:g} -> {new_weight:g}\n\n"
            f"Opened by the monthly review on {day}. A human reviews and merges; nothing changes until then.\n\n"
            f"## Why\n\n{rationale}\n\n## Evidence\n\n{evidence_md}\n\n"
            f"## After merging\n\nRun scripts/weekly_refresh.ps1 (dbt build, export, checks, brief).\n",
            encoding="utf-8")
        _git(work, "add", str(SEED), str(note.relative_to(work)))
        _git(work, "commit", "-m", f"Propose weight change: {signal} {old:g} -> {new_weight:g}",
             "-m", "Opened by the monthly review (propose_weight_change). Human review required.")
        commit = _git(work, "rev-parse", "--short", "HEAD")
    except Exception:
        _git(repo, "worktree", "remove", "--force", str(work))
        _git(repo, "branch", "-D", branch)
        raise
    _git(repo, "worktree", "remove", "--force", str(work))
    out = {"branch": branch, "commit": commit, "base": base, "old_weight": old, "new_weight": new_weight,
           "files": [str(SEED).replace("\\", "/"), f"proposals/{day}-{signal}.md"], "pushed": False}
    remote = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"],
                            capture_output=True, text=True).stdout.strip()
    if remote:
        pushed = subprocess.run(["git", "-C", str(repo), "push", "-u", "origin", branch],
                                capture_output=True, text=True)
        out["pushed"] = pushed.returncode == 0
        out["pull_request_url"] = _github_compare_url(remote, base, branch) if out["pushed"] else None
        if not out["pushed"]:
            out["push_error"] = (pushed.stderr or "").strip()[:300]
    if not out["pushed"]:
        out["how_to_push"] = f"git push -u origin {branch}  (then open a pull request into {base})"
    return out


@server.tool(annotations=PROPOSES)
def propose_weight_change(
    signal: Annotated[str, Field(description="The signal to change, for example view_docs.")],
    new_weight: Annotated[float, Field(ge=0, le=20, description="The proposed weight, 0 to 20.")],
    rationale: Annotated[str, Field(min_length=40, max_length=2000,
                                    description="Why, citing the review numbers.")],
) -> dict[str, Any]:
    """Propose one weight change as a git branch and pull request. A human reviews and merges.

    Refused unless review_outcomes finds enough evidence (enough_evidence true). Changes one row
    of the intent_signal_weights seed on a new branch, adds a proposal note, and pushes the branch
    when the repo has a GitHub remote. The live weights and the main branch never change here.
    """
    review = _review(None, None)
    if not review["enough_evidence"]:
        raise ToolError("Refused: " + review["verdict"])
    if not re.match(r"^[a-z_]+$", signal):
        raise ToolError(f"signal must look like view_docs, got {signal!r}.")
    t = review["totals"]
    s = review["by_signal"].get(signal, {})
    evidence_md = (
        f"Window {review['window']['since']} to {review['window']['until']}. "
        f"Called {t['called_orgs']} accounts, {t['meetings_booked']} meetings booked "
        f"(meeting rate {review['meeting_rate_called']}).\n\n"
        f"| {signal} | called | meetings | meeting rate |\n|---|---|---|---|\n"
        f"| reason | {s.get('called_with_reason', 0)} | {s.get('meetings_with', 0)} | {s.get('rate_with')} |\n"
        f"| not a reason | {s.get('called_without', 0)} | {s.get('meetings_without', 0)} | {s.get('rate_without')} |\n\n"
        + "\n".join(f"- {c}" for c in review["caveats"]))
    result = _open_proposal(ROOT, signal, new_weight, rationale.strip(), evidence_md)
    log.info("proposed %s %s -> %s on %s", signal, result["old_weight"], new_weight, result["branch"])
    result["next"] = "A human reviews the pull request. Nothing changes until it is merged and the refresh runs."
    return result


if __name__ == "__main__":
    log.info("starting; Cube at %s; outcomes file %s", CUBE_API, OUTCOMES_FILE)
    server.run()
