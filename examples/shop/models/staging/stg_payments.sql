select
    id as payment_id,
    order_id,
    method as payment_method,
    amount_paise / 100.0 as amount
from {{ ref('raw_payments') }}
