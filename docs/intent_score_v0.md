# Intent score v0 - scoring note

Status: **v0, unvalidated.** The weights are expert guesses. No plan history exists, so no weight has been tested against real upgrades.
Run date: 2026-08-31 (dbt var `run_date`). Evidence window: the 90 days ending on the run date (3 Jun - 31 Aug 2026, UTC).

## The question it answers
Each week, Sales decides which active, non-Enterprise organizations to contact about a higher paid plan, and which person to call.

## Models
| Model | Layer | Grain | Read by |
|---|---|---|---|
| intent_signal_weights | seed | one behavior (14 rows) | every score model; Sales and agents as the rulebook |
| int_scored_events | intermediate (view) | one product event in the window, with family and weight | both score marts |
| fct_user_intent_score | mart (table) | one user per run date | Sales, to pick the contact |
| fct_org_intent_score | mart (table) | one eligible organization per run date | Sales (weekly list), Project 03 |

## How the score works
- usage_score = pipelines run in the CI records x 1 + (use_parallelism x 10 + invite_member x 3 + view_docs x 1) / users in the organization
- buying_score = visit_pricing x 2 + start_trial x 5
- intent_score = usage_score + buying_score (used only to sort the list)
- High = top third (percent_rank >= 2/3) among eligible organizations
- Quadrant: pqa (high on both), heavy_user (high usage only), tyre_kicker (high buying only), not_now (neither)
- Contact = the user with the highest user score in the organization (ties: latest event, then user_id)
- Eligible = is_active and plan_type is not enterprise
- Weights live only in seeds/intent_signal_weights.csv. Change them there, never in SQL.

## Decisions and why
| Decision | Why |
|---|---|
| Org usage from CI pipelines, not pipeline events | Event counts follow user count (Project 01: rank correlation 0.98), not real building |
| run_workflow, run_test, successful_pipeline, failed_pipeline count for users only | Same reason; they still help pick the contact |
| pipelines_run counts for organizations only | CI records have no user_id |
| No "x2 when usage is present" multiplier | Almost every org has pipelines, so it would change no ranking; the quadrants keep buying without usage visible as tyre_kicker |
| Event part of usage_score divided by users | Without it, usage_score followed user count (0.67); with it, -0.03, and it follows pipelines (0.92) |
| buying_score kept as a raw count | It follows user count (0.52), but several people checking pricing can be a buying committee. Revisit when outcomes exist |

## Results (run date 2026-08-31)
89 eligible organizations: 13 pqa, 17 heavy_user, 16 tyre_kicker, 43 not_now. The PQA share (15%) passes the "not more than half" check.

## Evidence so far (analyses/)
- route2_plan_crosscheck: behavior rates in paid vs free organizations today. Almost every organization does almost every behavior within 90 days on both plans; per-user rates for parallelism, trial and pricing are equal. Docs are higher in paid orgs, likely because paying customers read more docs. No weight can be confirmed or killed from today's plan.
- usage_headcount_check and score_headcount_check: the before/after numbers above.

## Known limits
- No outcome data: every weight is a hypothesis (H1-H6 in the design brief).
- plan_type is today's snapshot only; no upgrade history or dates.
- The list says who to call, not why. A "top signals" column belongs in Project 03.
- The contact is the most active user, not necessarily the buyer.
- Events and CI records do not reconcile (Project 01); the event-based parts carry that noise.
- Ties at the top-third boundary can move an organization in or out of a quadrant.

## How to re-run
    dbt build --select intent_signal_weights+ --vars "{'run_date': '2026-08-31'}"

## Open questions for GoalEarn
1. Can we get plan history (plan changes with dates) per organization?
2. For the last five customers who upgraded: what did they do in the product first?