-- Reason rule v0: at most 3 reasons per org, and every reason is in the top third
-- on its signal. Any row returned is a failure.
select
    organization_id,
    run_date,
    count(*) filter (where is_reason) as n_reasons,
    min(pct_rank) filter (where is_reason) as lowest_reason_pct
from {{ ref('fct_org_signal_reasons') }}
group by 1, 2
having count(*) filter (where is_reason) > 3
    or min(pct_rank) filter (where is_reason) < 2.0 / 3