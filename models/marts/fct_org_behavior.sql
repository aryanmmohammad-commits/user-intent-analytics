{% set behaviors = [
    'signup', 'visit_pricing', 'view_docs', 'connect_repository',
    'create_project', 'first_pipeline', 'run_workflow', 'run_test',
    'successful_pipeline', 'failed_pipeline', 'invite_member',
    'use_parallelism', 'start_trial'
] %}

with orgs as (
    select organization_id from {{ ref('stg_organizations') }}
),

user_counts as (
    select organization_id, count(*) as total_users
    from {{ ref('stg_users') }}
    group by organization_id
),

events as (
    select * from {{ ref('user_intent_events') }}
)

select
    o.organization_id,
    coalesce(uc.total_users, 0) as total_users,
    count(distinct e.user_id) as users_with_events,
    count(e.event_id) as total_events,
    count(distinct e.event_name) as distinct_event_types,
    min(e.event_timestamp) as first_event_at,
    max(e.event_timestamp) as last_event_at,
    {% for b in behaviors %}
    count(*) filter (where e.event_name = '{{ b }}') as {{ b }}_count{{ ',' if not loop.last }}
    {% endfor %}
from orgs o
left join user_counts uc
    on o.organization_id = uc.organization_id
left join events e
    on o.organization_id = e.organization_id
group by o.organization_id, uc.total_users