select
    u.user_id,
    u.organization_id,
    u.country,
    u.device_type,
    u.source,
    u.created_at,
    o.plan_type as organization_plan_type
from {{ ref('stg_users') }} u
left join {{ ref('stg_organizations') }} o
    on u.organization_id = o.organization_id