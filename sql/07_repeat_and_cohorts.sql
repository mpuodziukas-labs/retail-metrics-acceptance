-- customer_summary: one row per identified customer (guest checkouts with
-- no CustomerID are excluded by design).
-- cohort_retention: customers grouped by the month of their first order;
-- month_index 0 is the cohort month, retention_pct is the share of the
-- cohort that ordered again in month_index months after it.
DROP VIEW IF EXISTS customer_summary;
CREATE VIEW customer_summary AS
SELECT
    CustomerID,
    COUNT(DISTINCT InvoiceNo) AS orders,
    SUM(line_revenue) AS revenue,
    MIN(SUBSTR(InvoiceDate, 1, 7)) AS first_month
FROM clean_orders
WHERE CustomerID IS NOT NULL
GROUP BY CustomerID;

DROP VIEW IF EXISTS cohort_retention;
CREATE VIEW cohort_retention AS
WITH activity AS (
    SELECT DISTINCT
        CustomerID,
        SUBSTR(InvoiceDate, 1, 7) AS active_month
    FROM clean_orders
    WHERE CustomerID IS NOT NULL
),
sizes AS (
    SELECT first_month AS cohort_month, COUNT(*) AS cohort_size
    FROM customer_summary
    GROUP BY first_month
),
by_index AS (
    SELECT
        s.first_month AS cohort_month,
        (CAST(SUBSTR(a.active_month, 1, 4) AS INTEGER)
            - CAST(SUBSTR(s.first_month, 1, 4) AS INTEGER)) * 12
        + CAST(SUBSTR(a.active_month, 6, 2) AS INTEGER)
        - CAST(SUBSTR(s.first_month, 6, 2) AS INTEGER) AS month_index,
        COUNT(*) AS active_customers
    FROM activity a
    JOIN customer_summary s
        ON a.CustomerID = s.CustomerID
    GROUP BY s.first_month, month_index
)
SELECT
    i.cohort_month,
    i.month_index,
    z.cohort_size,
    i.active_customers,
    ROUND(100.0 * i.active_customers / z.cohort_size, 2) AS retention_pct
FROM by_index i
JOIN sizes z
    ON i.cohort_month = z.cohort_month;
