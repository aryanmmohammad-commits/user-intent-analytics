select
    organization_id,
    organization_name,
    country,
    plan_type,
    cast(created_at as timestamptz) as created_at,
    cast(is_active as boolean) as is_active
from {{ source('source', 'organizations') }}
