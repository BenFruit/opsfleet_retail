from sqlglot import parse_one, exp

sql = """
SELECT p.id, p.name
FROM (
    SELECT id, name, brand
    FROM `bigquery-public-data.thelook_ecommerce.products`
    WHERE brand IN UNNEST(@allowed_brands)
) AS p
"""

tree = parse_one(sql, read="bigquery")

for subquery in tree.find_all(exp.Subquery):
    print("Subquery alias:", subquery.alias)

for table in tree.find_all(exp.Table):
    print("Physical table:", table.sql())