-- customer_order_windows: per-customer order sequencing and cumulative
-- spend, computed over clean_orders. A per-customer sequencing view exposed
-- for analysis and covered by tests; no other view or pipeline step reads it.
-- Window function #3: ROW_NUMBER() for order sequence.
-- Window function #4: SUM() OVER a running frame for cumulative revenue.
DROP VIEW IF EXISTS customer_order_windows;
CREATE VIEW customer_order_windows AS
SELECT
    CustomerID,
    InvoiceNo,
    InvoiceDate,
    line_revenue,
    ROW_NUMBER() OVER (
        PARTITION BY CustomerID
        ORDER BY InvoiceDate, InvoiceNo
    ) AS order_seq,
    SUM(line_revenue) OVER (
        PARTITION BY CustomerID
        ORDER BY InvoiceDate, InvoiceNo
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS running_customer_revenue
FROM clean_orders
WHERE CustomerID IS NOT NULL;
