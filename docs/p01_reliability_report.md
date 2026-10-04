# Project 01 Reliability Report

29 Sep 2026, Aryan Mohammaddoost

> Exported on 4 Oct 2026 from the working document. Counts are as of Project 01; later projects build on the
> 10,873 clean events in `user_intent_events`.

## Answer

Thirteen product behaviors can be modeled as counts or yes/no flags per user and per organization, using the 10,873 clean events in user\_intent\_events. Their order, timing, project, plan and link to real CI activity cannot be trusted.

- **Use:** view\_docs, visit\_pricing, invite\_member and use\_parallelism as they are. Use signup, first\_pipeline, start\_trial, create\_project, connect\_repository, run\_workflow, run\_test, successful\_pipeline and failed\_pipeline with the limits stated below.
- **Leave out:** the order of events, time since signup, the event's project\_id and plan\_type, and event counts as a stand-in for CI usage.
- **Not claimed:** a clean event is not commercial intent. Scoring intent and choosing which behaviors matter belongs to Project 02, and nothing here tests it.

fct\_user\_behavior (500 users) and fct\_org\_behavior (100 organizations) are built only from user\_intent\_events. 93 of 99 dbt tests pass, and the 6 warnings are the known problems listed below.

## How to read the verdicts

Each field and behavior gets one of three verdicts. A verdict is based on the checks we could run: dbt tests on every model, plus profiling of the raw files.

| Verdict | Meaning |
| --- | --- |
| Usable | Clean, and no check contradicted it. It still cannot be confirmed against a second source. |
| Usable with limits | Clean enough to count or flag, but one specific weakness means it must not be used for some purposes. The limit is stated. |
| Not usable | The data contradicts itself or is noise, and no fix restores the truth. Left out of the intent-ready table. |

A problem that a rule fixes safely (for example a duplicate row) does not lower a verdict. The fix is listed under "Fixed or excluded".

## Fields of product\_events

Six of the ten columns are usable, two only with limits, and two not at all: project\_id and plan\_type.

| Field | Verdict | Evidence | What we did |
| --- | --- | --- | --- |
| event\_id | Usable | 12,035 raw rows carried only 12,000 distinct ids. 33 repeats were exact copies and 2 differed only in project\_id. After removal every id is unique. | Kept one row per id, preferring the copy that has a project. |
| user\_id | Usable | Never empty. Every event's user exists in users. | Nothing needed. |
| organization\_id | Usable | 80 events (0.7%) had none. The other 11,920 match their user's organization in 100% of cases. | Filled the 80 from the user's organization. |
| event\_name | Usable with limits | 15 names appear, 13 are valid behaviors. 282 events (2.4%) are unknown\_event (257) or legacy\_unknown\_action (25) and cannot be interpreted. | Excluded the 282 from the intent-ready table. |
| event\_timestamp | Usable with limits | 10 events are dated September 2026 (all at 23:59:00), after the data ends on 31 Aug. Separately, 49% of events (5,901 of 12,000) are dated before their user's created\_at. Fine for counting inside a period. Not for order or time-since-signup. | Excluded the 10 late events. No fix exists for the rest. |
| project\_id | Not usable | Of 8,474 events that name a project, 8,388 (99%) point to a project owned by a different organization. 3,526 events (29%) have no project. | Left out of the intent-ready table. |
| plan\_type | Not usable | All 99 organizations that have events show all 4 plans across their events, so it is not the plan at the time of the event. | Left out. The organization's current plan is taken from organizations (a snapshot, not a history). |
| country, device\_type, source | Usable, but redundant | Equal to the user's value in 100% of events, so they add nothing new. | Read them from users instead. |

## The 13 behaviors

All 13 valid behaviors can be used as per-user or per-organization counts or yes/no flags: 4 are clean and 9 carry a stated limit. Counts come from the 10,873 usable events in user\_intent\_events.

| Behavior | Verdict | Events | Users (of 500) | Limit |
| --- | --- | --- | --- | --- |
| view\_docs | Usable | 1,054 | 439 (88%) | None found. No second source to confirm it. |
| visit\_pricing | Usable | 624 | 363 (73%) | None found. No second source to confirm it. |
| invite\_member | Usable | 766 | 391 (78%) | None found. No second source to confirm it. |
| use\_parallelism | Usable | 526 | 323 (65%) | None found. No second source to confirm it. |
| signup | Usable with limits | 376 | 376 (75%) | One-time event. 233 users repeated it (up to 7 times) and we kept the earliest. 124 users have none, so a missing signup does not mean the user never signed up. |
| first\_pipeline | Usable with limits | 351 | 351 (70%) | One-time event. 188 users repeated it, earliest kept. Missing for 149 users. Volume does not follow real pipelines (see below). |
| start\_trial | Usable with limits | 319 | 319 (64%) | One-time event. 148 users repeated it, earliest kept. Missing for 181 users. |
| create\_project | Usable with limits | 1,113 | 440 (88%) | Volume does not follow the organization's real projects (see below). |
| connect\_repository | Usable with limits | 1,055 | 442 (88%) | Volume does not follow the organization's real projects (see below). |
| run\_workflow | Usable with limits | 1,274 | 456 (91%) | Volume does not follow real workflows (see below). |
| run\_test | Usable with limits | 1,155 | 454 (91%) | Volume does not follow real test jobs (see below). |
| successful\_pipeline | Usable with limits | 1,253 | 456 (91%) | Volume does not follow real successful pipelines (see below). |
| failed\_pipeline | Usable with limits | 1,007 | 430 (86%) | Volume does not follow real failed pipelines (see below). |

**Cross-check that failed.** Across the 100 organizations, event volume follows the number of users (rank correlation 0.98 for all events) but not the organization's CI records. For each pipeline-related behavior compared with its matching CI count, the rank correlation lies between −0.14 and 0.08, which is close to zero. So these counts describe what users did in the product log. They must not be read as how much CI activity an organization really has. The dataset is synthetic, so this may be by construction, but the tables give no way to reconcile them.

**Signal strength is a separate question.** The typical user did 11 of the 13 behaviors, and seven behaviors are done by more than 85% of users. A behavior that almost everyone does will separate few users from the rest. Deciding which behaviors carry intent is Project 02's work.

## Fixed or excluded, and why it is safe

10,873 of the 12,035 raw event rows (90%) reach the intent-ready table. Five rules remove or repair the rest. Duplicates are removed in staging. The other rules are applied as flags in int\_product\_events, and only the final table drops the flagged rows, so every count below can be reproduced.

| Rule | Rows affected | Rows left | Why it is safe |
| --- | --- | --- | --- |
| Keep one row per event\_id, preferring the copy that has a project | 35 removed | 12,000 | 33 were exact copies and 2 differed only in project\_id. The unique test passes on all 12,000. |
| Fill missing organization\_id from the user | 80 filled, none removed | 12,000 | The other 11,920 events match their user's organization in 100% of cases. No empties remain and every organization exists. |
| Keep only the 13 accepted event names | 282 excluded | 11,718 | unknown\_event (257) and legacy\_unknown\_action (25) cannot be interpreted. |
| Keep only events before 1 Sep 2026 | 10 excluded | 11,708 | The data ends on 31 Aug. All 10 are dated at exactly 23:59:00, an unusual pattern. |
| Keep only the earliest signup, first\_pipeline and start\_trial per user | 835 excluded | 10,873 | This is an assumption, not a proven fact (see open questions). |

Two repairs in the CI tables: 12 duplicate pipelines were removed (3,012 to 3,000), and 10 negative job durations were recomputed from stopped\_at minus started\_at. That recomputation agrees with the recorded duration for the other 19,990 jobs, so it is a safe rule.

## Unfixable problems and open questions

Five problems cannot be fixed from the data we have, and six questions need an answer from GoalEarn before Project 02 relies on these fields.

| Problem | Evidence | Effect on Project 02 |
| --- | --- | --- |
| Timestamps contradict other records | 49% of events (5,901 of 12,000) are dated before their user's created\_at. 244 of 500 users were created before their own organization. 1,421 of 2,980 pipelines (48%) are dated before their project. | No reliable signup date and no time-since-signup features. |
| Event order is not a user journey | 124 users have no signup event. Only 26 users' earliest event is a signup. Of 266 users with both signup and first\_pipeline, 120 did first\_pipeline first. | Funnels and step-by-step sequences cannot be built. Use counts and flags. |
| Broken references in the CI tables | 20 pipelines point to a project that does not exist (project\_missing\_999). 18 workflows point to a missing pipeline. 40 workflows belong to a different project than their pipeline. 12 pipelines have status unknown\_status. | Exclude these rows or report them as unknown in any CI-based rate. |
| Status is inconsistent with timestamps | All 388 jobs with status running also have a stop time. 211 workflows are running. | Do not trust running as a live state. |
| Event counts do not match CI activity | Rank correlation with real pipelines, workflows, jobs and projects is between −0.14 and 0.08 (see the 13 behaviors). | Event counts must not stand in for CI usage. |

**Open questions**

1. Is the earliest occurrence the real one for signup, first\_pipeline and start\_trial? We assumed yes and excluded 835 later rows. If a later one is correct, counts and timing change.
2. Should first\_pipeline happen once per user, per organization or per project? We treated it as once per user.
3. Why do 124 users have no signup event: tracking gaps, invited users or older accounts? The answer decides whether a missing signup means anything.
4. When an event is dated before its user's created\_at, which clock is right: the event log or the user record?
5. Can legacy\_unknown\_action (25 events) be mapped to a valid behavior? Until then it stays excluded.
6. Is there a key that links an event to a pipeline or workflow? project\_id is the only candidate and it is unreliable, so today event counts cannot be checked against CI records.
