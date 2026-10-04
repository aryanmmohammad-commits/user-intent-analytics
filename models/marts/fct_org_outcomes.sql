{{ config(materialized='table') }}

-- What happened after each Monday list.
-- Grain: one scored organization per run date, the same grain as fct_org_intent_score.
-- The latest real outcome wins (a correction is a new row). Test rows never count.
-- Every scored org stays in, called or not: hit rates divide by the whole list,
-- not only by the accounts somebody remembered to log.

with scores as (

    select
        organization_id,
        run_date,
        plan_type,
        n_users,
        quadrant,
        is_pqa,
        intent_score,
        contact_user_id
    from {{ ref('fct_org_intent_score') }}

),

real_outcomes as (

    select
        organization_id,
        run_date,
        outcome,
        logged_at_utc,
        row_number() over (
            partition by organization_id, run_date
            order by logged_at_utc desc, outcome_id desc
        ) as newest_first,
        count(*) over (partition by organization_id, run_date) as n_logged
    from {{ ref('stg_sales_ops__pqa_outcomes') }}
    where not is_test

),

latest as (

    select * from real_outcomes where newest_first = 1

)

select
    s.organization_id || '|' || cast(s.run_date as varchar) as org_run_key,
    s.organization_id,
    s.run_date,
    s.plan_type,
    s.n_users,
    s.quadrant,
    s.is_pqa,
    s.intent_score,
    s.contact_user_id,
    coalesce(l.outcome, 'not_called') as outcome,
    l.outcome is not null as was_called,
    coalesce(l.outcome <> 'no_reply', false) as was_reached,
    coalesce(l.outcome = 'meeting_booked', false) as meeting_booked,
    l.logged_at_utc as outcome_logged_at_utc,
    coalesce(l.n_logged, 0) as n_times_logged
from scores as s
left join latest as l
    on l.organization_id = s.organization_id
    and l.run_date = s.run_date
