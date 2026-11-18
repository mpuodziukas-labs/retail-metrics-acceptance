-- clean_orders: raw line items enriched with canonical product + customer
-- attributes, cancellations/free-item noise removed.
-- Join #1: raw_orders -> dim_products (canonical description).
-- Join #2: raw_orders -> dim_customers (canonical country, LEFT so guest
-- checkouts with no CustomerID still survive using the raw Country value).
DROP VIEW IF EXISTS clean_orders;
CREATE VIEW clean_orders AS
SELECT
    r.InvoiceNo,
    r.StockCode,
    p.canonical_description AS description,
    r.Quantity,
    r.InvoiceDate,
    r.UnitPrice,
    r.CustomerID,
    COALESCE(c.primary_country, r.Country) AS country,
    r.Quantity * r.UnitPrice AS line_revenue,
    CASE WHEN r.InvoiceNo LIKE 'C%' THEN 1 ELSE 0 END AS is_cancellation
FROM raw_orders r
JOIN dim_products p
    ON r.StockCode = p.StockCode
LEFT JOIN dim_customers c
    ON r.CustomerID = c.CustomerID
WHERE r.Quantity > 0
  AND r.UnitPrice > 0;
