{{ config(severity='warn') }}
-- Warns if more than half of eligible organizations are PQAs: then the score separates nobody.
select
    sum(case when is_pqa then 1 else 0 end) as pqas,
    count(*) as orgs
from {{ ref('fct_org_intent_score') }}
having sum(case when is_pqa then 1 else 0 end) > count(*) / 2.0