-- monthly_cancellation: share of invoices that are cancellations
-- (InvoiceNo starts with C). Read from raw_orders because clean_orders
-- removes cancellations by design.
DROP VIEW IF EXISTS monthly_cancellation;
CREATE VIEW monthly_cancellation AS
SELECT
    SUBSTR(InvoiceDate, 1, 7) AS month,
    COUNT(DISTINCT InvoiceNo) AS invoices,
    COUNT(DISTINCT CASE WHEN InvoiceNo LIKE 'C%' THEN InvoiceNo END)
        AS cancelled_invoices,
    ROUND(
        100.0 * COUNT(DISTINCT CASE WHEN InvoiceNo LIKE 'C%' THEN InvoiceNo END)
        / COUNT(DISTINCT InvoiceNo),
        2
    ) AS cancellation_rate_pct
FROM raw_orders
GROUP BY SUBSTR(InvoiceDate, 1, 7);
