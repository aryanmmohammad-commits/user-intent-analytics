-- Logged call outcomes, typed. Grain: one logged outcome.
-- Test rows stay here, flagged; marts drop them.

with source as (

    select * from {{ source('sales_ops', 'pqa_outcomes') }}

)

select
    trim(outcome_id) as outcome_id,
    cast(trim(logged_at_utc) as timestamp) as logged_at_utc,
    trim(organization_id) as organization_id,
    cast(trim(run_date) as date) as run_date,
    lower(trim(outcome)) as outcome,
    nullif(trim(note), '') as note,
    coalesce(lower(trim(is_test)) = 'true', false) as is_test,
    trim(logged_by) as logged_by
from source
