# Salesforce activation design

Status: design, built up to the sync-ready table. The table `act_salesforce_accounts` exists and is tested
in dbt; the Hightouch sync itself is specified here, not connected (there is no Salesforce org in this simulation).

## Decision it serves

Every Monday a sales rep decides **which accounts to call, who to ask for, and how to open**. Reps live in
Salesforce, not in our tools, so the list has to arrive there: on the Account they already open, with the
reason and the contact. The outcome of the call must come back the same way, so the score can be checked.

## What moves, and where

```
dbt build (Mon 06:00)
  -> act_salesforce_accounts            one row per scored org, latest run date
  -> Hightouch sync 1 (Mon 06:30)       upsert Account fields, match on Product_Org_Id__c
  -> Hightouch sync 2 (Mon 06:30)       insert one Task per new PQA, owner = account owner
reps call, then set the Task's PQA_Outcome__c (5 values)
  -> ELT (Fivetran/Airbyte) lands Task  -> dbt source -> stg -> fct_org_outcomes
```

In this repo the last line is played by `log_outcome` (append-only CSV) and `scripts/load_outcomes.py`.
Swapping them for the Salesforce Task source changes one staging model; `fct_org_outcomes` stays as it is.

## Object and match key

- **Object: Account.** A PQA is an organization (Project 02 decision: the list is org-level, users only pick
  the contact), so it belongs on the Account, not on a Lead.
- **Match key: `Product_Org_Id__c`**, a custom text field marked External ID and unique, holding our
  `organization_id`. Hightouch upserts on it, so a re-run never creates duplicates.
- **Creating accounts:** only for PQAs that do not exist in Salesforce yet (product-led signups often don't).
  Other boxes update existing Accounts only.

## Field mapping (sync 1)

| dbt column (`act_salesforce_accounts`) | Salesforce field | Type | Note |
|---|---|---|---|
| organization_id | Product_Org_Id__c | Text(20), External ID, unique | Match key |
| pqa_status | PQA_Status__c | Picklist: PQA, Heavy user, Tyre kicker, Not now | Drives list views and reports |
| is_pqa | Is_PQA__c | Checkbox | |
| intent_score | PQA_Intent_Score__c | Number(6,2) | Rounded at this edge only |
| usage_beats_pct | PQA_Usage_Beats_Pct__c | Percent(3,0) | Share of eligible orgs it beats |
| buying_beats_pct | PQA_Buying_Beats_Pct__c | Percent(3,0) | |
| reason_1, reason_2, reason_3 | PQA_Reason_1__c ... PQA_Reason_3__c | Text(255) | The "why", already in sentences |
| contact_user_id | PQA_Contact_User_Id__c | Text(20) | In production: match to a Contact by email |
| plan_type | Product_Plan__c | Picklist: free, scale, performance | Snapshot, not history |
| n_users | Product_Users__c | Number(5,0) | Users in the product, not paid seats |
| run_date | PQA_Run_Date__c | Date | Freshness, visible to the rep |
| score_label | PQA_Score_Version__c | Text(40) | "v0, unvalidated" until the review validates |

Reps get these fields **read-only** (field-level security); only the integration user writes them.

## The Task (sync 2) and the outcome coming back

- One Task per account that is a PQA on this run date and was not a PQA on the previous one. Subject:
  "PQA: call {contact}", due in 7 days, body = the three reasons plus the opener from the Monday brief
  (already checked by code against the openers rule). Dedup key: `organization_id|run_date`.
- Custom picklist `PQA_Outcome__c` on Task with exactly the five brief buttons: meeting_booked, not_now,
  wrong_person, already_talking, no_reply. Same values as `stg_sales_ops__pqa_outcomes`, so the dbt
  accepted_values test guards the contract.

## Guardrails

- **No tests, no sync.** The sync runs only after `dbt build` and both contract checks pass
  (`scripts/weekly_refresh.ps1` stops at the first failure). Hightouch is triggered by the scheduler, not on a timer.
- **Row-count check.** `tests/assert_salesforce_sync_matches_scores.sql` fails if the sync table carries a
  different set of PQAs than the scores, or a PQA without a contact or a reason.
- **Never delete.** When an org leaves the PQA box its status changes; nothing is removed in Salesforce.
- **What is not synced:** raw events, page-visit logs and timestamps. Reps see the reasons as sentences,
  never a click log; the opener rule keeps tracked behavior out of what they say to the buyer.
- **Alerting:** a failed or partial sync (Hightouch run status) pages the analytics engineer, and the
  Monday brief notes when Salesforce is behind.

## Open questions

- Who owns new PQA accounts that are not in Salesforce yet: round-robin queue or territory rules?
- Email is not in this dataset, so contacts cannot be matched to Salesforce Contacts here.
- Should Heavy user and Tyre kicker accounts be visible to Sales at all, or only to Product?
