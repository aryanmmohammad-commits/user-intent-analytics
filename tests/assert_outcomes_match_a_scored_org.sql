{{ config(severity='warn') }}

-- Every real outcome must point to an organization scored on that run date.
-- A row here was logged against an unknown account or date, and fct_org_outcomes
-- would drop it without a word.

select
    o.outcome_id,
    o.organization_id,
    o.run_date
from {{ ref('stg_sales_ops__pqa_outcomes') }} as o
left join {{ ref('fct_org_intent_score') }} as s
    on s.organization_id = o.organization_id
    and s.run_date = o.run_date
where not o.is_test
  and s.organization_id is null
