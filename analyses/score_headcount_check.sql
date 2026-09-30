-- After decision B: does usage_score still follow headcount? Does buying_score?
with ranked as (
    select
        rank() over (order by n_users) as r_users,
        rank() over (order by pipelines_run) as r_pipelines,
        rank() over (order by usage_score) as r_usage,
        rank() over (order by buying_score) as r_buying
    from {{ ref('fct_org_intent_score') }}
)
select
    round(corr(r_users, r_usage), 2) as users_vs_usage_score,
    round(corr(r_pipelines, r_usage), 2) as pipelines_vs_usage_score,
    round(corr(r_users, r_buying), 2) as users_vs_buying_score
from ranked