-- product_country_revenue: revenue per product per country, ranked inside
-- each country. row_num breaks ties by StockCode so a top-N cut is stable;
-- revenue_rank (RANK) shows ties honestly.
-- Window functions: ROW_NUMBER() and RANK() with PARTITION BY country.
DROP VIEW IF EXISTS product_country_revenue;
CREATE VIEW product_country_revenue AS
WITH per_product AS (
    SELECT
        country,
        StockCode AS stock_code,
        description,
        SUM(line_revenue) AS revenue,
        SUM(Quantity) AS units
    FROM clean_orders
    GROUP BY country, StockCode, description
)
SELECT
    country,
    stock_code,
    description,
    ROUND(revenue, 2) AS revenue,
    units,
    RANK() OVER (
        PARTITION BY country ORDER BY revenue DESC
    ) AS revenue_rank,
    ROW_NUMBER() OVER (
        PARTITION BY country ORDER BY revenue DESC, stock_code
    ) AS row_num
FROM per_product;
