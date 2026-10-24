# Data-quality incident: `unique_orders_order_id`

- **Test:** `unique` on `orders`.`order_id` (fail, 1 failing rows)
- **Exposures downstream:** `revenue_dashboard` (dashboard, Analytics)

## Evidence pack

<details><summary>Failing rows (sample)</summary>

```
unique_field | n_records
--- | ---
12 | 2
(1 row)
```

</details>

**Lineage:** `orders` ← `stg_payments` ← `raw_payments` ← `stg_orders` ← `raw_orders`

**Row counts:** `orders` 5, `stg_payments` 5, `raw_payments` 5, `stg_orders` 4, `raw_orders` 4

**Recent commits on these models:**

```
abc1234 | 2026-10-24 | Vikram Iyer | perf(orders): drop group by in payments CTE
abc1234 | 2026-10-24 | Asha Rao | shop: staging and marts for customers, orders, payments
```
