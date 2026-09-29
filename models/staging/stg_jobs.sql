with typed as (
    select
        job_id,
        workflow_id,
        project_id,
        organization_id,
        job_name,
        cast(started_at as timestamptz) as started_at,
        cast(stopped_at as timestamptz) as stopped_at,
        status,
        cast(duration_seconds as integer) as duration_seconds_recorded
    from {{ source('circleci_raw', 'jobs') }}
)

select
    *,
    cast(epoch(stopped_at) - epoch(started_at) as integer) as duration_seconds
from typed
