select
    project_id,
    organization_id,
    project_slug,
    repo_provider,
    repo_name,
    default_branch,
    cast(created_at as timestamptz) as created_at
from {{ source('circleci_raw', 'projects') }}
