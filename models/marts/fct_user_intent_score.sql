-- Mart (fact). Grain: one user per run date.
-- Scores each user from their events in the 90-day window (int_scored_events rows with in_user_score)
-- and marks the top user in each organization as the contact (PQL candidate).
{{ config(materialized='table') }}

{% set run_date = var('run_date', '2026-08-31') %}

with users as (
    select user_id, organization_id
    from {{ ref('dim_users') }}
),

user_points as (
    select
        user_id,
        sum(case when family = 'usage' then weight else 0 end) as user_usage_score,
        sum(case when family = 'buying' then weight else 0 end) as user_buying_score,
        max(event_timestamp) as last_event_at
    from {{ ref('int_scored_events') }}
    where in_user_score
    group by 1
),

scored as (
    select
        u.user_id,
        u.organization_id,
        date '{{ run_date }}' as run_date,
        coalesce(p.user_usage_score, 0) as user_usage_score,
        coalesce(p.user_buying_score, 0) as user_buying_score,
        coalesce(p.user_usage_score, 0) + coalesce(p.user_buying_score, 0) as user_intent_score,
        p.last_event_at
    from users as u
    left join user_points as p
        on p.user_id = u.user_id
),

ranked as (
    select
        *,
        row_number() over (
            partition by organization_id
            order by user_intent_score desc, last_event_at desc nulls last, user_id
        ) as rank_in_org
    from scored
)

select
    user_id,
    organization_id,
    run_date,
    user_usage_score,
    user_buying_score,
    user_intent_score,
    last_event_at,
    rank_in_org,
    rank_in_org = 1 and user_intent_score > 0 as is_contact,
    'v0' as score_version
from ranked