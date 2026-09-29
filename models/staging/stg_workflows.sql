select
    workflow_id,
    pipeline_id,
    project_id,
    organization_id,
    workflow_name,
    cast(created_at as timestamptz) as created_at,
    status,
    cast(duration_seconds as integer) as duration_seconds
from {{ source('circleci_raw', 'workflows') }}
