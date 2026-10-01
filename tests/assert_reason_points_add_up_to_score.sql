-- The receipt must match the total: per org, signal points add up to usage_score and
-- buying_score in fct_org_intent_score. Any row returned is a failure.
-- fct_org_intent_score stores usage_score rounded to 2 decimals, so the usage side is
-- compared at that same rounding. Rounding moves a total by at most 0.005; a missing or
-- extra item moves it by at least 1 / n_users, so a real error is still caught.
with sums as (
    select
        organization_id,
        run_date,
        sum(case when family = 'usage' then points else 0 end) as usage_points,
        sum(case when family = 'buying' then points else 0 end) as buying_points
    from {{ ref('fct_org_signal_reasons') }}
    group by 1, 2
)

select
    o.organization_id,
    o.run_date,
    o.usage_score,
    s.usage_points,
    o.buying_score,
    s.buying_points
from {{ ref('fct_org_intent_score') }} as o
left join sums as s
    on s.organization_id = o.organization_id
   and s.run_date = o.run_date
where s.organization_id is null
   or abs(o.usage_score - round(s.usage_points, 2)) > 0.000001
   or abs(o.buying_score - s.buying_points) > 0.000001