select customer_id, email
from {{ ref('stg_customers') }}
where email is not null
