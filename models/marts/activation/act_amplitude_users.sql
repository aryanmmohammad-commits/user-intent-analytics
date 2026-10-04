{{ config(materialized='table') }}

-- What a reverse-ETL sync (Hightouch) would send to Amplitude as user properties.
-- Design: docs/activation_amplitude.md. Product builds one cohort per revenue_box from these.
-- Grain: one user of a scored organization, latest run date only.

with latest as (

    select max(run_date) as run_date
    from {{ ref('fct_org_intent_score') }}

),

orgs as (

    select o.organization_id, o.run_date, o.quadrant, o.is_pqa, o.plan_type, o.contact_user_id
    from {{ ref('fct_org_intent_score') }} as o
    inner join latest as l
        on o.run_date = l.run_date

)

select
    u.user_id,
    u.organization_id,
    o.quadrant as revenue_box,
    o.is_pqa as org_is_pqa,
    (u.user_id = o.contact_user_id and o.is_pqa) as is_pqa_contact,
    o.plan_type,
    o.run_date as revenue_box_as_of
from {{ ref('fct_user_intent_score') }} as u
inner join orgs as o
    on o.organization_id = u.organization_id
    and o.run_date = u.run_date
