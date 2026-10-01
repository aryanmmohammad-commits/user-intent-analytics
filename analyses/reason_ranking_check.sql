-- Evidence for P03 step 2 (reason column): two ways to rank the signals behind each PQA.
--   by_points:   points the signal adds to the score (the score formula)
--   by_standout: where the org ranks against other eligible orgs on that signal (percentile)
-- Points follow the score: pipelines x weight; usage events x weight / users; buying events x weight.
-- Run date comes from var run_date (default 2026-08-31).

with orgs as (
    select organization_id, n_users, pipelines_run, is_pqa
    from {{ ref('fct_org_intent_score') }}
),

event_signals as (
    select behavior as signal, family, weight
    from {{ ref('intent_signal_weights') }}
    where source = 'event' and in_org_score and weight > 0
),

pipeline_weight as (
    select weight
    from {{ ref('intent_signal_weights') }}
    where behavior = 'pipelines_run' and source = 'ci'
),

event_counts as (
    select organization_id, event_name as signal, count(*) as n
    from {{ ref('int_scored_events') }}
    where in_org_score and weight > 0
    group by 1, 2
),

points as (
    select
        o.organization_id,
        o.is_pqa,
        s.signal,
        coalesce(c.n, 0) as n,
        case
            when s.family = 'usage'
                then coalesce(coalesce(c.n, 0) * s.weight / nullif(o.n_users, 0), 0)
            else coalesce(c.n, 0) * s.weight
        end as points
    from orgs as o
    cross join event_signals as s
    left join event_counts as c
        on c.organization_id = o.organization_id
       and c.signal = s.signal

    union all

    select
        o.organization_id,
        o.is_pqa,
        'pipelines_run' as signal,
        o.pipelines_run as n,
        o.pipelines_run * pw.weight as points
    from orgs as o
    cross join pipeline_weight as pw
),

ranked as (
    select
        *,
        percent_rank() over (partition by signal order by points) as pct,
        row_number() over (partition by organization_id order by points desc, signal) as rank_by_points
    from points
),

ranked2 as (
    select
        *,
        row_number() over (partition by organization_id order by pct desc, points desc, signal) as rank_by_standout
    from ranked
),

pqa as (
    select * from ranked2 where is_pqa
)

select
    a.organization_id,
    a.rank_by_points as top_n,
    a.signal as by_points,
    round(a.points, 1) as points,
    b.signal as by_standout,
    round(100 * b.pct) as pct
from pqa as a
inner join pqa as b
    on b.organization_id = a.organization_id
   and b.rank_by_standout = a.rank_by_points
where a.rank_by_points <= 3
order by a.organization_id, top_n