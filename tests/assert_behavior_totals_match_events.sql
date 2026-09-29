select 'user level' as level, a.total as behavior_total, b.total as event_total
from (select sum(total_events) as total from {{ ref('fct_user_behavior') }}) a
cross join (select count(*) as total from {{ ref('user_intent_events') }}) b
where a.total <> b.total

union all

select 'org level' as level, a.total as behavior_total, b.total as event_total
from (select sum(total_events) as total from {{ ref('fct_org_behavior') }}) a
cross join (select count(*) as total from {{ ref('user_intent_events') }}) b
where a.total <> b.total