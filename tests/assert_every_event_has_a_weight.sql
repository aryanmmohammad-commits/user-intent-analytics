-- Fails if any event name in user_intent_events has no weight row.
-- Together with the relationships test on the seed, the match is exact both ways.
select distinct e.event_name
from {{ ref('user_intent_events') }} as e
left join {{ ref('intent_signal_weights') }} as w
    on w.behavior = e.event_name
   and w.source = 'event'
where w.behavior is null
