# Project 03 design brief: Revenue Signals Agent

30 Sep 2026, Aryan Mohammaddoost

> **Original design brief, 30 Sep 2026, kept as written.** What changed while building (see the README):
> the Monday brief became a checked HTML page plus a workflow instead of a Slack message; five outcome buttons
> instead of four ("no reply" added); no synthetic outcomes are generated: the monthly review refuses to propose
> weights until 30 real calls are logged; cube_dbt was not used (hand-written cubes with a contract check);
> Cube reads Parquet exports, never dev.duckdb; a run scorecard grades every agent and workflow run.


## Status

Project 03 builds the Revenue Signals Agent: a Slack assistant that tells Sales each Monday which organizations to call, and why. It answers questions from one set of governed metrics and learns from what happens after each call.

- Draft. The target and the semantic layer (Cube) were chosen by Aryan on 30 Sep 2026.
- It builds on Project 02's v0 score: 13 PQAs among 89 eligible orgs, run date 31 Aug 2026.
- GoalEarn's Project 03 Task Definition is not in the project files yet. The deliverables mapping below uses GoalEarn's overview; recheck it when the Task Definition arrives.

## The problem it solves

A score becomes a product only when people use it without us in the room. Today's PQA list fails that on four counts.

| Gap | Today | In the product |
| --- | --- | --- |
| Reach | A table in dev.duckdb on Aryan's laptop | A Slack message every Monday; later, fields on the Salesforce account |
| Reason | Says who to call, not why | The top 2 or 3 signals per org, each with its events and dates |
| Rhythm | Updates only when Aryan runs dbt | Refreshes on a weekly schedule |
| Proof | No outcome data, so the v0 weights are unvalidated | Reps log what happened after each call; those outcomes become the validation set |

The last row matters most: the product creates the outcome data that Project 02 could not get.

## Users and jobs

The agent does three jobs: a Monday brief that comes to Sales, answers on demand, and a monthly review that checks the score against real outcomes.

| User | Question they bring | What the agent gives them |
| --- | --- | --- |
| Sales rep | Who do I call this week, who do I ask for, what do I open with? | Monday brief: new PQAs, orgs that moved in or out, top signals, the contact, a suggested opening question, and four outcome buttons |
| RevOps | Can we trust this score? | Ask: any metric by name, with the same number everywhere; the monthly hit-rate review |
| Product | Which orgs need a nudge rather than a call? | Cohorts per box, for example upgrade nudges for heavy\_user and onboarding help for tyre\_kicker (Claude's proposal) |
| Analytics engineer | Are the weights still right? | Monthly review: weight changes proposed as a Git pull request; a human decides |

- Outcome buttons: meeting booked, not now, wrong person, already talking.
- Openers are questions, not pitches. Signals show who is worth talking to, not what they need.

## Architecture

(The architecture diagram is in the README.)

All three agents read through Cube. Only the outcome loop writes, and the monthly review turns those outcomes into proposed weight changes.

## Semantic layer with Cube

Cube fits. The open-source Cube Core reads a local DuckDB file and can build its model from dbt's manifest, so it sits directly on the Project 02 marts.

- Cube Core connects to a local DuckDB database through one setting, `CUBEJS_DB_DUCKDB_DATABASE_PATH` ([Cube docs: DuckDB](https://docs.cube.dev/admin/connect-to-data/data-sources/duckdb)).
- The `cube_dbt` package reads dbt's `manifest.json` and renders selected marts as cubes, which we then enrich with measures and joins ([Cube docs: dbt](https://docs.cube.dev/recipes/data-modeling/dbt)).
- Self-hosted Cube serves the same definitions over REST, GraphQL and a Postgres-compatible SQL API ([Railway: deploy Cube](https://railway.com/deploy/cube--cube)).
- Cube's own MCP server is hosted by Cube Cloud: one endpoint, OAuth sign-in, tools such as `searchDataModel` and `runQuery`, and a listing in Claude's connector directory ([Cube docs: MCP server](https://docs.cube.dev/docs/integrations/mcp-server)). It does not reach a Cube Core install on a laptop.

### Two routes

| Route | Where data and Cube run | How the agent reaches metrics | Effort | Use it for |
| --- | --- | --- | --- | --- |
| Local | dev.duckdb and Cube Core in Docker on the laptop | Our own small MCP server calling Cube's API | Free; needs Docker Desktop | Building and the demo |
| Cloud | Data in MotherDuck or object storage; Cube Cloud | Cube's hosted MCP connector for reads, our tools for writes | Account setup; data leaves the laptop | A real team using it |

Either way, the write tools are ours. Cube's MCP tools query and edit models; they know nothing about outcomes or the weights seed.

### What to model first

| Cube | Built on | Measures | Dimensions |
| --- | --- | --- | --- |
| org\_scores | fct\_org\_intent\_score | pqa\_count, eligible\_orgs, pqa\_share, avg usage\_score, avg buying\_score, pipelines\_run | organization\_id, run\_date, quadrant, plan\_type, is\_pqa, contact\_user\_id |
| user\_scores | fct\_user\_intent\_score | user count, avg user\_intent\_score | user\_id, organization\_id, run\_date |
| events | user\_intent\_events | event count, active users | event\_name, event\_timestamp, user\_id, organization\_id |
| outcomes | new outcomes table | outcomes logged, meetings\_booked, meeting\_rate | organization\_id, run\_date, outcome, quadrant at the time |

### Risks to test in the first spike

- DuckDB lets one process write to a file at a time. If Cube holds dev.duckdb open, `dbt build` may be blocked. Fallbacks: stop Cube during builds, or have dbt write the marts Cube reads to a separate file.
- `cube_dbt` parses `manifest.json`. The dbt on Aryan's PATH reports 2.0.6, so check that its manifest loads.
- Cube Core ships as Docker images, so the Windows laptop needs Docker Desktop.

## Agent tools and guardrails

The agent gets five tools. None of them can change the marts, contact a customer, or change a weight on its own.

| Tool | What it does | Access |
| --- | --- | --- |
| `list_pqas(run_date)` | This week's PQAs with box, contact and top signals | Read |
| `explain_account(org_id)` | Scores, ranks, signals with their events and dates, change since last week | Read |
| `query_metric(measures, dimensions, filters)` | Any governed metric, through Cube | Read |
| `log_outcome(org_id, outcome, note)` | Stores a rep's outcome click | Append-only write to outcomes |
| `propose_weight_change(changes, evidence)` | Opens a Git pull request on the weights seed | Proposal; a human merges |

- Metrics by name only, through Cube. No free-form SQL.
- It drafts outreach and never sends it.
- Every claim shows its evidence: the events, their dates, and the metric name.
- The score is labeled "v0, unvalidated" until the monthly review shows a measured hit rate.

## GoalEarn Project 03 deliverables

Every Project 03 deliverable named in GoalEarn's overview becomes a part of this product, not a separate exercise.

| GoalEarn deliverable | Where it lives in the product |
| --- | --- |
| Revenue-oriented data models | The outcomes table, the reason column, and an account-grain model shaped for the sync |
| Salesforce activation design | A weekly Hightouch sync of is\_pqa, quadrant, scores, contact and top signals to account fields |
| Amplitude activation design | Cohorts per box, so Product can nudge orgs that need no call |
| Revenue opportunity analysis | PQA pipeline size by plan and box; later, meeting rate by box and signal |
| Technical and business documentation | This brief, the Project 02 scoring note, and a runbook for the weekly run |

GoalEarn frames Salesforce and Amplitude as activation designs, so both can ship as written specs before any live connection.

## Build order

Six steps, each shippable and tested on its own. Steps 1 to 5 make a working demo.

1. **Cube spike.** Cube Core in Docker on dev.duckdb, one cube (org\_scores) with pqa\_count. Settle the file-lock and manifest risks above.
2. **Reason column.** The top 2 or 3 signals per org, built and tested in dbt.
3. **Full semantic model.** The four cubes and their measures, with descriptions the agent can read.
4. **MCP server.** A small Python server with the five tools. First test: ask Claude "why is org\_0057 a PQA?"
5. **Monday brief with outcome buttons.** An artifact page for the demo, Slack for real use, writing to the outcomes table.
6. **Monthly review agent.** Hit rates by signal and box, and weight changes proposed as pull requests. In the GoalEarn simulation the outcomes are synthetic and labeled that way.

The Salesforce and Amplitude specs can be written alongside steps 3 to 5, since they only describe fields and cohorts.

## Decisions and open questions

| Decision | Choice | By |
| --- | --- | --- |
| Project 03 target | Revenue Signals Agent | Aryan, 30 Sep |
| Semantic layer | Cube | Aryan, 30 Sep |
| Route | Start local (Cube Core and our own MCP server); move to Cube Cloud when real users exist | Claude's proposal |
| First surface | An artifact page for the demo; Slack for real use | Claude's proposal |

- [ ] Share GoalEarn's Project 03 Task Definition
- [ ] Local or Cloud route for the demo?
- [ ] Are the four outcome buttons the right set?
- [ ] Confirm buying\_score stays a raw count (carried over from Project 02)
- [ ] Ask GoalEarn for plan history, and "what did your last five upgrades do first?"

## Sources

- [Cube docs: DuckDB data source](https://docs.cube.dev/admin/connect-to-data/data-sources/duckdb)
- [Cube docs: dbt integration](https://docs.cube.dev/recipes/data-modeling/dbt)
- [Cube docs: MCP server](https://docs.cube.dev/docs/integrations/mcp-server)
- [Railway: deploy and host Cube](https://railway.com/deploy/cube--cube)
