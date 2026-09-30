-- Route-2 cross-check (weak evidence).
-- For each behavior: how common it is in paid vs free organizations, using today's plan only.
-- Paid orgs may show a behavior because they already pay, so this can weaken a hypothesis
-- more than prove one. Window: the 90 days ending on the run date, 31 Aug 2026.
{% set window_start = '2026-06-03' %}
{% set window_end = '2026-09-01' %}

with orgs as (
    select
        organization_id,
        case when lower(plan_type) = 'free' then 'free' else 'paid' end as plan_group
    from {{ ref('dim_organizations') }}
    where is_active
),

group_sizes as (
    select
        o.plan_group,
        count(distinct o.organization_id) as orgs,
        count(u.user_id) as users
    from orgs as o
    left join {{ ref('dim_users') }} as u
        on u.organization_id = o.organization_id
    group by 1
),

events_90d as (
    select
        e.organization_id,
        e.event_name,
        o.plan_group
    from {{ ref('user_intent_events') }} as e
    inner join orgs as o
        on o.organization_id = e.organization_id
    where e.event_timestamp >= timestamptz '{{ window_start }} 00:00:00+00'
      and e.event_timestamp <  timestamptz '{{ window_end }} 00:00:00+00'
),

by_behavior as (
    select
        event_name,
        plan_group,
        count(*) as events,
        count(distinct organization_id) as orgs_with_behavior
    from events_90d
    group by 1, 2
)

select
    w.behavior,
    w.family,
    w.weight,
    round(100.0 * coalesce(p.orgs_with_behavior, 0) / gp.orgs, 1) as pct_paid_orgs,
    round(100.0 * coalesce(f.orgs_with_behavior, 0) / gf.orgs, 1) as pct_free_orgs,
    round(100.0 * coalesce(p.events, 0) / gp.users, 1) as per_100_users_paid,
    round(100.0 * coalesce(f.events, 0) / gf.users, 1) as per_100_users_free
from {{ ref('intent_signal_weights') }} as w
cross join (select * from group_sizes where plan_group = 'paid') as gp
cross join (select * from group_sizes where plan_group = 'free') as gf
left join by_behavior as p
    on p.event_name = w.behavior and p.plan_group = 'paid'
left join by_behavior as f
    on f.event_name = w.behavior and f.plan_group = 'free'
where w.source = 'event'
order by w.weight desc, w.behavior