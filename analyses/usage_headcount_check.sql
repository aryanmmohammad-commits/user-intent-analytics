-- Does the org usage_score measure building or headcount?
-- Per eligible org: the event part of usage_score and the pipelines part, compared with user count.
{% set run_date = var('run_date', '2026-08-31') %}

with orgs as (
    select organization_id
    from {{ ref('dim_organizations') }}
    where is_active and lower(plan_type) <> 'enterprise'
),

users as (
    select organization_id, count(*) as n_users
    from {{ ref('dim_users') }}
    group by 1
),

event_points as (
    select organization_id, sum(weight) as event_usage_points
    from {{ ref('int_scored_events') }}
    where in_org_score and family = 'usage'
    group by 1
),

pipelines as (
    select organization_id, count(*) as pipelines_run
    from {{ ref('stg_pipelines') }}
    where created_at >= cast(cast(date '{{ run_date }}' - 89 as varchar) || ' 00:00:00+00' as timestamptz)
      and created_at <  cast(cast(date '{{ run_date }}' + 1 as varchar) || ' 00:00:00+00' as timestamptz)
    group by 1
),

per_org as (
    select
        o.organization_id,
        coalesce(u.n_users, 0) as n_users,
        coalesce(ep.event_usage_points, 0) as event_points,
        coalesce(p.pipelines_run, 0) as pipeline_points
    from orgs as o
    left join users as u on u.organization_id = o.organization_id
    left join event_points as ep on ep.organization_id = o.organization_id
    left join pipelines as p on p.organization_id = o.organization_id
),

ranked as (
    select
        *,
        rank() over (order by n_users) as r_users,
        rank() over (order by event_points) as r_events,
        rank() over (order by pipeline_points) as r_pipelines,
        rank() over (order by event_points + pipeline_points) as r_usage
    from per_org
)

select
    count(*) as orgs,
    round(sum(event_points) / nullif(sum(event_points + pipeline_points), 0), 2) as share_of_usage_from_events,
    round(corr(r_users, r_events), 2) as users_vs_event_points,
    round(corr(r_users, r_pipelines), 2) as users_vs_pipelines,
    round(corr(r_users, r_usage), 2) as users_vs_usage_score
from ranked