with ranked as (
    select
        *,
        row_number() over (partition by pipeline_id order by created_at) as copy_number
    from {{ source('circleci_raw', 'pipelines') }}
)

select
    pipeline_id,
    project_id,
    organization_id,
    cast(created_at as timestamptz) as created_at,
    cast(updated_at as timestamptz) as updated_at,
    status,
    branch,
    trigger_type
from ranked
where copy_number = 1
