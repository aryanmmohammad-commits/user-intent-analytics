select
    user_id,
    organization_id,
    country,
    device_type,
    source,
    cast(created_at as timestamptz) as created_at
from {{ source('source', 'users') }}