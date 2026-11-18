-- monthly_revenue: revenue, order count and average order value per month,
-- with month-over-month change and a running total.
-- Window function: LAG() for the previous month, SUM() OVER for the total.
DROP VIEW IF EXISTS monthly_revenue;
CREATE VIEW monthly_revenue AS
WITH monthly AS (
    SELECT
        SUBSTR(InvoiceDate, 1, 7) AS month,
        SUM(line_revenue) AS revenue,
        COUNT(DISTINCT InvoiceNo) AS orders
    FROM clean_orders
    GROUP BY SUBSTR(InvoiceDate, 1, 7)
)
SELECT
    month,
    ROUND(revenue, 2) AS revenue,
    orders,
    ROUND(revenue / orders, 2) AS aov,
    ROUND(LAG(revenue) OVER (ORDER BY month), 2) AS prev_revenue,
    ROUND(
        100.0 * (revenue - LAG(revenue) OVER (ORDER BY month))
        / LAG(revenue) OVER (ORDER BY month),
        2
    ) AS mom_change_pct,
    ROUND(
        SUM(revenue) OVER (
            ORDER BY month
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ),
        2
    ) AS running_total
FROM monthly;
