-- Every PQA organization must have exactly one is_pqa_contact user in the Amplitude sync,
-- so the "PQA contacts" cohort holds one person per account.

with latest as (
    select max(run_date) as run_date from {{ ref('fct_org_intent_score') }}
),

pqas as (
    select s.organization_id
    from {{ ref('fct_org_intent_score') }} as s
    inner join latest as l on s.run_date = l.run_date
    where s.is_pqa
),

contacts as (
    select organization_id, count(*) as n_contacts
    from {{ ref('act_amplitude_users') }}
    where is_pqa_contact
    group by organization_id
)

select p.organization_id, coalesce(c.n_contacts, 0) as n_contacts
from pqas as p
left join contacts as c on c.organization_id = p.organization_id
where coalesce(c.n_contacts, 0) <> 1
