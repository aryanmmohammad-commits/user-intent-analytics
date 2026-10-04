# Amplitude activation design

Status: design, built up to the sync-ready table. The table `act_amplitude_users` exists and is tested in dbt;
the Hightouch sync and the cohorts are specified here, not connected (there is no Amplitude project in this
simulation).

## Decision it serves

Product decides **which organizations get an in-product nudge instead of a sales call**. Only 13 of 89
eligible organizations are PQAs; the other boxes still hold revenue, but a call would come too early.
Product teams work in Amplitude, so the boxes have to arrive there as something they can target.

## What moves, and where

```
dbt build (Mon 06:00)
  -> act_amplitude_users                 one row per user of a scored org, latest run date
  -> Hightouch -> Amplitude Identify API  user properties, match on user_id
  -> Amplitude cohorts (defined once, refresh themselves from the properties)
  -> in-app guides, emails, experiments target the cohorts
```

## User properties

| dbt column (`act_amplitude_users`) | Amplitude user property | Note |
|---|---|---|
| user_id | user_id | Match key; the same id the product events carry |
| organization_id | org_id | Also the group key if the Accounts add-on is used |
| revenue_box | revenue_box | pqa, heavy_user, tyre_kicker, not_now: the org's box, set on every user in it |
| org_is_pqa | org_is_pqa | |
| is_pqa_contact | is_pqa_contact | The one person Sales asks for in a PQA org |
| plan_type | plan_type | Snapshot from the organizations table |
| revenue_box_as_of | revenue_box_as_of | Run date, so stale properties are visible |

Properties are overwritten every week (`$set`), so a user who moves box moves cohort on the next sync.
Only these fields are sent: no scores, no event counts, no reasons. Product needs the box, not the math.

## Cohorts

| Cohort | Definition | Owner | Action | Conflict rule |
|---|---|---|---|---|
| PQA contacts | is_pqa_contact = true | Sales | None in product. Suppressed from upgrade nudges | Sales is talking to them: no automated upsell on top |
| PQA teams | revenue_box = pqa and is_pqa_contact = false | Product | Light: highlight features the team already uses | No pricing prompts while Sales is in the account |
| Heavy users, free plan | revenue_box = heavy_user and plan_type = free | Product | In-app upgrade nudge at a usage limit | The biggest self-serve pool (see revenue_opportunity.md) |
| Heavy users, paid | revenue_box = heavy_user and plan_type != free | Product | Expansion prompts (more parallelism, more seats) | |
| Tyre kickers | revenue_box = tyre_kicker | Product / onboarding | Onboarding checklist, "first green pipeline" help | Buying intent without usage: help them reach value first |
| Not now | revenue_box = not_now | Nobody | Nothing extra | Avoid noise |

## Measuring it

Each nudge runs as an Amplitude experiment with a holdout (10% of the cohort sees nothing), so the effect
is measured against a control, not assumed. The outcome that matters is an upgrade or a plan change, which
needs plan history from GoalEarn; until then the read-out is feature adoption.

## Guardrails

- **No tests, no sync**, same as Salesforce: the sync runs only after `dbt build` and the checks pass.
- `tests/assert_one_contact_per_pqa_in_amplitude.sql` keeps the "PQA contacts" cohort at exactly one
  person per PQA organization.
- The PQA contacts cohort is suppressed from automated upsell, so a buyer never gets a pricing pop-up
  in the same week a rep calls them.
- The score is v0 and unvalidated: cohorts target boxes, not score thresholds, so a weight change moves
  people between cohorts without anyone editing cohort definitions.

## Open questions

- Does GoalEarn use the Amplitude Accounts add-on (group analytics)? If so, org-level properties go on the
  `org_id` group instead of copying them onto every user.
- Which usage limit should trigger the upgrade nudge for free heavy users?
