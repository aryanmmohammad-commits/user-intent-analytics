{{ config(materialized='table') }}

-- What a reverse-ETL sync (Hightouch) would write to Salesforce Account fields every Monday.
-- Design: docs/activation_salesforce.md. Read by a tool outside dbt, so it is a mart.
-- Grain: one scored organization, latest run date only (Salesforce holds the current state).
-- Rounding happens here because this is the edge: the marts upstream keep exact values.

with latest as (

    select max(run_date) as run_date
    from {{ ref('fct_org_intent_score') }}

),

scores as (

    select s.*
    from {{ ref('fct_org_intent_score') }} as s
    inner join latest as l
        on s.run_date = l.run_date

),

reasons as (

    select
        r.organization_id,
        max(case when r.reason_rank = 1 then r.reason_text end) as reason_1,
        max(case when r.reason_rank = 2 then r.reason_text end) as reason_2,
        max(case when r.reason_rank = 3 then r.reason_text end) as reason_3
    from {{ ref('fct_org_signal_reasons') }} as r
    inner join latest as l
        on r.run_date = l.run_date
    where r.is_reason
    group by r.organization_id

)

select
    s.organization_id,
    case s.quadrant
        when 'pqa' then 'PQA'
        when 'heavy_user' then 'Heavy user'
        when 'tyre_kicker' then 'Tyre kicker'
        else 'Not now'
    end as pqa_status,
    s.is_pqa,
    round(s.intent_score, 2) as intent_score,
    cast(round(s.usage_pct_rank * 100) as integer) as usage_beats_pct,
    cast(round(s.buying_pct_rank * 100) as integer) as buying_beats_pct,
    r.reason_1,
    r.reason_2,
    r.reason_3,
    s.contact_user_id,
    s.plan_type,
    s.n_users,
    s.run_date,
    s.score_version || ', unvalidated' as score_label
from scores as s
left join reasons as r
    on r.organization_id = s.organization_id
