-- Mart (fact). Grain: one eligible organization (active, not Enterprise) per run date.
-- usage_score  = pipelines run (CI records) x seed weight + event usage points / users.
--                Dividing the event part by users removes the headcount bias (decision B, 30 Sep).
-- buying_score = event buying points (visit_pricing, start_trial).
-- PQA = top third on both scores among eligible organizations. Sales reads this list weekly.
{{ config(materialized='table') }}

{% set run_date = var('run_date', '2026-08-31') %}

with bounds as (
    select
        cast(cast(date '{{ run_date }}' - 89 as varchar) || ' 00:00:00+00' as timestamptz) as window_start,
        cast(cast(date '{{ run_date }}' + 1 as varchar) || ' 00:00:00+00' as timestamptz) as window_end
),

orgs as (
    select organization_id, plan_type
    from {{ ref('dim_organizations') }}
    where is_active and lower(plan_type) <> 'enterprise'
),

users as (
    select organization_id, count(*) as n_users
    from {{ ref('dim_users') }}
    group by 1
),

pipeline_weight as (
    select weight
    from {{ ref('intent_signal_weights') }}
    where behavior = 'pipelines_run' and source = 'ci'
),

pipelines as (
    select p.organization_id, count(*) as pipelines_run
    from {{ ref('stg_pipelines') }} as p
    cross join bounds as b
    where p.created_at >= b.window_start
      and p.created_at <  b.window_end
    group by 1
),

event_points as (
    select
        organization_id,
        sum(case when family = 'usage' then weight else 0 end) as event_usage_points,
        sum(case when family = 'buying' then weight else 0 end) as buying_points
    from {{ ref('int_scored_events') }}
    where in_org_score
    group by 1
),

contacts as (
    select organization_id, user_id as contact_user_id
    from {{ ref('fct_user_intent_score') }}
    where is_contact
),

scored as (
    select
        o.organization_id,
        date '{{ run_date }}' as run_date,
        o.plan_type,
        coalesce(u.n_users, 0) as n_users,
        coalesce(pl.pipelines_run, 0) as pipelines_run,
        coalesce(ep.event_usage_points, 0) as event_usage_points,
        coalesce(pl.pipelines_run, 0) * pw.weight
            + coalesce(ep.event_usage_points / nullif(u.n_users, 0), 0) as usage_score,
        coalesce(ep.buying_points, 0) as buying_score,
        c.contact_user_id
    from orgs as o
    cross join pipeline_weight as pw
    left join users as u on u.organization_id = o.organization_id
    left join pipelines as pl on pl.organization_id = o.organization_id
    left join event_points as ep on ep.organization_id = o.organization_id
    left join contacts as c on c.organization_id = o.organization_id
),

ranked as (
    select
        *,
        usage_score + buying_score as intent_score,
        percent_rank() over (order by usage_score) as usage_pct_rank,
        percent_rank() over (order by buying_score) as buying_pct_rank
    from scored
)

select
    organization_id,
    run_date,
    plan_type,
    n_users,
    pipelines_run,
    event_usage_points,
    round(usage_score, 2) as usage_score,
    buying_score,
    round(intent_score, 2) as intent_score,
    round(usage_pct_rank, 2) as usage_pct_rank,
    round(buying_pct_rank, 2) as buying_pct_rank,
    case
        when usage_pct_rank >= 2.0 / 3 and buying_pct_rank >= 2.0 / 3 then 'pqa'
        when usage_pct_rank >= 2.0 / 3 then 'heavy_user'
        when buying_pct_rank >= 2.0 / 3 then 'tyre_kicker'
        else 'not_now'
    end as quadrant,
    usage_pct_rank >= 2.0 / 3 and buying_pct_rank >= 2.0 / 3 as is_pqa,
    contact_user_id,
    'v0' as score_version
from ranked