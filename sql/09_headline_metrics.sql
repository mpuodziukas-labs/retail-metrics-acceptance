-- headline_metrics: one row of whole-period metrics.
-- revenue_gap compares positive-quantity, positive-price revenue in
-- raw_orders with revenue in clean_orders; anything but zero means a join
-- dropped rows.
DROP VIEW IF EXISTS headline_metrics;
CREATE VIEW headline_metrics AS
WITH rev AS (
    SELECT
        SUM(line_revenue) AS revenue_total,
        COUNT(DISTINCT InvoiceNo) AS orders
    FROM clean_orders
),
guest AS (
    SELECT COALESCE(SUM(line_revenue), 0) AS guest_revenue
    FROM clean_orders
    WHERE CustomerID IS NULL
),
cust AS (
    SELECT
        COUNT(*) AS customers,
        COALESCE(SUM(CASE WHEN orders >= 2 THEN 1 ELSE 0 END), 0)
            AS repeat_customers,
        SUM(revenue) AS identified_revenue,
        COALESCE(SUM(CASE WHEN orders >= 2 THEN revenue ELSE 0 END), 0)
            AS repeat_revenue
    FROM customer_summary
),
inv AS (
    SELECT
        COUNT(*) AS rows_loaded,
        COUNT(DISTINCT InvoiceNo) AS invoices,
        COUNT(DISTINCT CASE WHEN InvoiceNo LIKE 'C%' THEN InvoiceNo END)
            AS cancelled_invoices,
        COALESCE(
            SUM(CASE WHEN Quantity > 0 AND UnitPrice > 0
                THEN Quantity * UnitPrice END),
            0
        ) AS raw_positive_revenue
    FROM raw_orders
)
SELECT
    inv.rows_loaded,
    ROUND(rev.revenue_total, 2) AS revenue_total,
    rev.orders,
    ROUND(rev.revenue_total / rev.orders, 2) AS aov,
    cust.customers,
    cust.repeat_customers,
    ROUND(100.0 * cust.repeat_customers / cust.customers, 2)
        AS repeat_purchase_rate_pct,
    ROUND(100.0 * cust.repeat_revenue / cust.identified_revenue, 2)
        AS repeat_revenue_share_pct,
    ROUND(100.0 * guest.guest_revenue / rev.revenue_total, 2)
        AS guest_revenue_share_pct,
    inv.invoices,
    inv.cancelled_invoices,
    ROUND(100.0 * inv.cancelled_invoices / inv.invoices, 2)
        AS cancellation_rate_pct,
    ROUND(
        100.0
        * (SELECT SUM(active_customers) FROM cohort_retention
            WHERE month_index = 1)
        / (SELECT SUM(cohort_size) FROM cohort_retention
            WHERE month_index = 0
                AND cohort_month < (SELECT MAX(month) FROM monthly_revenue)),
        2
    ) AS month1_retention_pct,
    ROUND(inv.raw_positive_revenue, 2) AS raw_positive_revenue,
    ROUND(inv.raw_positive_revenue - rev.revenue_total, 2) AS revenue_gap
FROM rev, guest, cust, inv;
