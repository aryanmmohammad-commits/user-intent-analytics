select
    event_id,
    user_id,
    organization_id,
    organization_was_filled,
    event_name,
    event_timestamp,
    user_event_occurrence
from {{ ref('int_product_events') }}
where is_valid_event_name
  and is_within_data_period
  and not is_repeat_of_one_time_event