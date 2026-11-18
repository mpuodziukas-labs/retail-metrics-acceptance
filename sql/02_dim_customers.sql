-- dim_customers: one canonical Country per CustomerID. A handful of
-- customers in the raw data appear against more than one Country value
-- (address changes / data entry); take the country recorded on their
-- earliest invoice as the primary one; ties on date break by InvoiceNo, then Country.
-- Window function #2: ROW_NUMBER() ordered by InvoiceDate, InvoiceNo, Country.
DROP VIEW IF EXISTS dim_customers;
CREATE VIEW dim_customers AS
WITH ranked AS (
    SELECT
        CustomerID,
        Country,
        InvoiceDate,
        ROW_NUMBER() OVER (
            PARTITION BY CustomerID
            ORDER BY InvoiceDate ASC, InvoiceNo, Country
        ) AS rn
    FROM raw_orders
    WHERE CustomerID IS NOT NULL
)
SELECT
    CustomerID,
    Country AS primary_country
FROM ranked
WHERE rn = 1;
