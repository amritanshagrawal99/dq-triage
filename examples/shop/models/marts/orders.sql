with payments as (
    select order_id, sum(amount) as amount
    from {{ ref('stg_payments') }}
    group by order_id
)
select o.*, p.amount
from {{ ref('stg_orders') }} o
left join payments p on p.order_id = o.order_id
