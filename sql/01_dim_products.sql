-- dim_products: one canonical row per StockCode. Real UCI data has the same
-- StockCode recorded with slightly different/missing Description across
-- invoices; pick the first non-null description deterministically.
-- Window function #1: ROW_NUMBER() to de-duplicate.
DROP VIEW IF EXISTS dim_products;
CREATE VIEW dim_products AS
WITH ranked AS (
    SELECT
        StockCode,
        Description,
        ROW_NUMBER() OVER (
            PARTITION BY StockCode
            ORDER BY (Description IS NULL), Description
        ) AS rn
    FROM raw_orders
    WHERE StockCode IS NOT NULL
)
SELECT
    StockCode,
    Description AS canonical_description
FROM ranked
WHERE rn = 1;
