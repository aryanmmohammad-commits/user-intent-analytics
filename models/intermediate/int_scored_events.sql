-- Grain: one product event inside the 90-day window ending on the run date,
-- with its family, weight and score flags from the intent_signal_weights seed.
-- All organizations are kept; the score marts decide who is eligible.
{{ config(materialized='view') }}

{% set run_date = var('run_date', '2026-08-31') %}

with bounds as (
    select
        date '{{ run_date }}' as run_date,
        cast(cast(date '{{ run_date }}' - 89 as varchar) || ' 00:00:00+00' as timestamptz) as window_start,
        cast(cast(date '{{ run_date }}' + 1 as varchar) || ' 00:00:00+00' as timestamptz) as window_end
),

events as (
    select event_id, user_id, organization_id, event_name, event_timestamp
    from {{ ref('user_intent_events') }}
),

weights as (
    select behavior, family, weight, in_org_score, in_user_score
    from {{ ref('intent_signal_weights') }}
    where source = 'event'
)

select
    e.event_id,
    e.user_id,
    e.organization_id,
    e.event_name,
    e.event_timestamp,
    b.run_date,
    w.family,
    w.weight,
    w.in_org_score,
    w.in_user_score
from events as e
cross join bounds as b
inner join weights as w
    on w.behavior = e.event_name
where e.event_timestamp >= b.window_start
  and e.event_timestamp <  b.window_end