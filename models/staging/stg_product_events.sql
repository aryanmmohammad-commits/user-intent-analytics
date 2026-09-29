with raw_events as (
    select * from {{ source('source', 'product_events') }}
),

ranked as (
    select
        *,
        row_number() over (
            partition by event_id
            order by project_id is null  
        ) as copy_number
    from raw_events
)

select
    event_id,
    user_id,
    organization_id,
    event_name,
    cast(event_timestamp as timestamptz) as event_timestamp,
    project_id,
    plan_type,
    country,
    device_type,
    source
from ranked
where copy_number = 1