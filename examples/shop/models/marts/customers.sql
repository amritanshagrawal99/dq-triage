with order_stats as (
    select
        customer_id,
        min(order_date) as first_order_date,
        count(*) as order_count,
        sum(case when status = 'completed' then amount else 0 end) as lifetime_value
    from {{ ref('orders') }}
    group by customer_id
)
select
    c.customer_id,
    c.first_name || ' ' || c.last_name as full_name,
    c.signup_date,
    s.first_order_date,
    coalesce(s.order_count, 0) as order_count,
    coalesce(s.lifetime_value, 0) as lifetime_value
from {{ ref('stg_customers') }} c
left join order_stats s on s.customer_id = c.customer_id
