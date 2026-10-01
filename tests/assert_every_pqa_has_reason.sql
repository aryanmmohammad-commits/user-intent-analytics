{{ config(severity='warn') }}
-- Expectation, not a law: a PQA can reach the top third on a summed score without any
-- single signal in the top third. If this fires, Sales gets a PQA with no stated reason.
select organization_id, run_date
from {{ ref('fct_org_signal_reasons') }}
where is_pqa
group by 1, 2
having count(*) filter (where is_reason) = 0