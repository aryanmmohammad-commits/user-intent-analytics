-- Grain: one eligible organization per run date per scoring signal (6 signals).
-- The receipt behind the score: points each signal added, and where the org ranks
-- against other eligible orgs on that signal (stand-out).
-- Reason rule v0 (Aryan, 2 Oct 2026): a signal is a reason when the org is in the top
-- third on it (pct_rank >= 2/3, the PQA bar), ranked by stand-out, max 3 per org.
-- Points follow the score: pipelines x weight; usage events x weight / users; buying x weight.
{{ config(materialized='table') }}

{% set run_date = var('run_date', '2026-08-31') %}

with bounds as (
    select
        cast(cast(date '{{ run_date }}' - 89 as varchar) || ' 00:00:00+00' as timestamptz) as window_start,
        cast(cast(date '{{ run_date }}' + 1 as varchar) || ' 00:00:00+00' as timestamptz) as window_end
),

orgs as (
    select organization_id, run_date, n_users, pipelines_run, is_pqa
    from {{ ref('fct_org_intent_score') }}
),

event_signals as (
    select behavior as signal, family, weight
    from {{ ref('intent_signal_weights') }}
    where source = 'event' and in_org_score and weight > 0
),

pipeline_signal as (
    select behavior as signal, family, weight
    from {{ ref('intent_signal_weights') }}
    where behavior = 'pipelines_run' and source = 'ci'
),

event_counts as (
    select
        organization_id,
        run_date,
        event_name as signal,
        count(*) as event_count,
        max(event_timestamp) as last_seen_at
    from {{ ref('int_scored_events') }}
    where in_org_score and weight > 0
    group by 1, 2, 3
),

pipeline_last as (
    select p.organization_id, max(p.created_at) as last_seen_at
    from {{ ref('stg_pipelines') }} as p
    cross join bounds as b
    where p.created_at >= b.window_start
      and p.created_at <  b.window_end
    group by 1
),

points as (
    select
        o.organization_id,
        o.run_date,
        o.is_pqa,
        o.n_users,
        s.signal,
        s.family,
        s.weight,
        coalesce(c.event_count, 0) as event_count,
        cast(case
            when s.family = 'usage'
                then coalesce(coalesce(c.event_count, 0) * s.weight / nullif(o.n_users, 0), 0)
            else coalesce(c.event_count, 0) * s.weight
        end as double) as points,
        c.last_seen_at
    from orgs as o
    cross join event_signals as s
    left join event_counts as c
        on c.organization_id = o.organization_id
       and c.run_date = o.run_date
       and c.signal = s.signal

    union all

    select
        o.organization_id,
        o.run_date,
        o.is_pqa,
        o.n_users,
        s.signal,
        s.family,
        s.weight,
        o.pipelines_run as event_count,
        cast(o.pipelines_run * s.weight as double) as points,
        pl.last_seen_at
    from orgs as o
    cross join pipeline_signal as s
    left join pipeline_last as pl
        on pl.organization_id = o.organization_id
),

ranked as (
    select
        *,
        percent_rank() over (partition by run_date, signal order by points) as pct_rank
    from points
),

standout as (
    select
        *,
        row_number() over (
            partition by organization_id, run_date
            order by pct_rank desc, points desc, signal
        ) as standout_rank
    from ranked
),

flagged as (
    select
        *,
        (points > 0 and pct_rank >= 2.0 / 3 and standout_rank <= 3) as is_reason
    from standout
)

select
    organization_id,
    run_date,
    signal,
    family,
    weight,
    n_users,
    event_count,
    points,
    pct_rank,
    standout_rank,
    is_reason,
    case when is_reason then standout_rank end as reason_rank,
    case when is_reason then
        signal || ': ' || cast(event_count as varchar) || ' in 90 days'
        || case
               when family = 'usage' and signal <> 'pipelines_run' and n_users > 0
                   then ' (' || cast(round(event_count / n_users, 1) as varchar) || ' per user)'
               else ''
           end
        || ', above ' || cast(cast(round(100 * pct_rank) as integer) as varchar) || '% of eligible orgs'
    end as reason_text,
    last_seen_at,
    is_pqa
from flagged