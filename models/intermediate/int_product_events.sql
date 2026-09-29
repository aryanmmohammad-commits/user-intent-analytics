with events as (
    select * from {{ ref('stg_product_events') }}
),

users as (
    select
        user_id,
        organization_id as user_organization_id
    from {{ ref('stg_users') }}
),

filled as (
    select
        e.event_id,
        e.user_id,
        coalesce(e.organization_id, u.user_organization_id) as organization_id,
        e.organization_id is null as organization_was_filled,
        e.event_name,
        e.event_name in (
            'signup', 'visit_pricing', 'view_docs', 'connect_repository',
            'create_project', 'first_pipeline', 'run_workflow', 'run_test',
            'successful_pipeline', 'failed_pipeline', 'invite_member',
            'use_parallelism', 'start_trial'
        ) as is_valid_event_name,
        e.event_timestamp,
        e.event_timestamp < timestamptz '2026-09-01 00:00:00+00' as is_within_data_period,
        e.project_id,
        e.plan_type,
        e.country,
        e.device_type,
        e.source
    from events e
    left join users u
        on e.user_id = u.user_id
),

numbered as (
    select
        *,
        row_number() over (
            partition by user_id, event_name
            order by event_timestamp, event_id
        ) as user_event_occurrence
    from filled
)

select
    *,
    event_name in ('signup', 'first_pipeline', 'start_trial')
        and user_event_occurrence > 1 as is_repeat_of_one_time_event
from numbered