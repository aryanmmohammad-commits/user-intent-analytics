-- The Salesforce sync must carry exactly the PQAs of the latest run, each with a contact
-- and at least one reason. A row here is a PQA the sync would drop or send half-empty.

with latest as (
    select max(run_date) as run_date from {{ ref('fct_org_intent_score') }}
),

expected as (
    select s.organization_id
    from {{ ref('fct_org_intent_score') }} as s
    inner join latest as l on s.run_date = l.run_date
    where s.is_pqa
),

synced as (
    select organization_id, contact_user_id, reason_1
    from {{ ref('act_salesforce_accounts') }}
    where is_pqa
)

select coalesce(e.organization_id, y.organization_id) as organization_id,
       case when y.organization_id is null then 'missing from sync'
            when e.organization_id is null then 'not a PQA in the scores'
            when y.contact_user_id is null then 'no contact'
            else 'no reason' end as problem
from expected as e
full outer join synced as y on y.organization_id = e.organization_id
where y.organization_id is null
   or e.organization_id is null
   or y.contact_user_id is null
   or y.reason_1 is null
