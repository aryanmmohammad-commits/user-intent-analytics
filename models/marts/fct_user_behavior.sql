{% set behaviors = [
    'signup', 'visit_pricing', 'view_docs', 'connect_repository',
    'create_project', 'first_pipeline', 'run_workflow', 'run_test',
    'successful_pipeline', 'failed_pipeline', 'invite_member',
    'use_parallelism', 'start_trial'
] %}

with users as (
    select user_id, organization_id from {{ ref('stg_users') }}
),

events as (
    select * from {{ ref('user_intent_events') }}
)

select
    u.user_id,
    u.organization_id,
    count(e.event_id) as total_events,
    count(distinct e.event_name) as distinct_event_types,
    min(e.event_timestamp) as first_event_at,
    max(e.event_timestamp) as last_event_at,
    {% for b in behaviors %}
    count(*) filter (where e.event_name = '{{ b }}') as {{ b }}_count{{ ',' if not loop.last }}
    {% endfor %}
from users u
left join events e
    on u.user_id = e.user_id
group by u.user_id, u.organization_id