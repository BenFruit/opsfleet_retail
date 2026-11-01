
from sqlglot import parse, exp
from retail_agent.schema.catalog import TABLE_POLICIES, CATALOG_NAME, DB_NAME
from retail_agent.schema.models import DataAccessLevel
from sqlglot.optimizer.scope import traverse_scope

def validate_sql(sql: str) -> None:
    statements = [
        tree
        for tree in parse(sql, read="bigquery")
        if tree is not None
    ]

    if len(statements) != 1:
        raise ValueError("Expected exactly one SQL statement")

    if not isinstance(statements[0], exp.Select):
        raise ValueError("Only SELECT queries are allowed")

    tree = statements[0]

    if tree.find(exp.Star) is not None:
        raise ValueError("STAR expressions are not allowed")
    

    for table in tree.find_all(exp.Table):
        if table.catalog != CATALOG_NAME:
            raise ValueError(f"Correct catalog name : {CATALOG_NAME}")
        if table.db != DB_NAME:
            raise ValueError(f"Correct db name : {DB_NAME}")
        if table.name not in TABLE_POLICIES:
            raise ValueError("Incorrect table name")

    for scope in traverse_scope(tree):
        for column in scope.columns:
            source = scope.sources.get(column.table)

            if not isinstance(source, exp.Table):
                raise ValueError(
                    f"Unsupported or unresolved source: {column.sql()}"
                )

            table_name = source.name


            policy = TABLE_POLICIES[table_name].get(column.name)

            if policy is None:
                raise ValueError(f"Unknown column: {column.sql()}")

            if policy.access_level == DataAccessLevel.BLOCKED_PII:
                raise ValueError(f"PII column is forbidden: {column.sql()}")


test_cases = [
    ("Allowed column", "SELECT u.id FROM `bigquery-public-data.thelook_ecommerce.users` AS u", True),
    ("Blocked PII", "SELECT u.email FROM `bigquery-public-data.thelook_ecommerce.users` AS u", False),
    ("Unknown column", "SELECT u.fake_column FROM `bigquery-public-data.thelook_ecommerce.users` AS u", False),
    ("DELETE", "DELETE FROM `bigquery-public-data.thelook_ecommerce.users` WHERE id = 1", False),
    ("Multiple statements", "SELECT 1; SELECT 2;", False),
    ("Wildcard", "SELECT * FROM `bigquery-public-data.thelook_ecommerce.products`", False),
    ("Qualified wildcard", "SELECT p.* FROM `bigquery-public-data.thelook_ecommerce.products` AS p", False),
    ("Wrong project", "SELECT p.id FROM `other-project.thelook_ecommerce.products` AS p", False),
    ("Wrong dataset", "SELECT p.id FROM `bigquery-public-data.other_dataset.products` AS p", False),
    ("Unknown table", "SELECT p.id FROM `bigquery-public-data.thelook_ecommerce.payments` AS p", False),
    (
        "Same table, different aliases",
        """
        SELECT u1.id
        FROM `bigquery-public-data.thelook_ecommerce.users` AS u1
        WHERE u1.id IN (
            SELECT u2.id
            FROM `bigquery-public-data.thelook_ecommerce.users` AS u2
        )
        """,
        True,
    ),
    (
        "Different tables, same alias in separate scopes",
        """
        SELECT x.cost
        FROM `bigquery-public-data.thelook_ecommerce.products` AS x
        WHERE x.id IN (
            SELECT x.product_id
            FROM `bigquery-public-data.thelook_ecommerce.order_items` AS x
        )
        """,
        True,
    ),
    (
        "PII inside nested query",
        """
        SELECT p.id
        FROM `bigquery-public-data.thelook_ecommerce.products` AS p
        WHERE EXISTS (
            SELECT u.id
            FROM `bigquery-public-data.thelook_ecommerce.users` AS u
            WHERE u.email = 'example@example.com'
        )
        """,
        False,
    ),
    (
        "Forbidden table inside nested query",
        """
        SELECT p.id
        FROM `bigquery-public-data.thelook_ecommerce.products` AS p
        WHERE EXISTS (
            SELECT x.id
            FROM `bigquery-public-data.thelook_ecommerce.payments` AS x
        )
        """,
        False,
    ),
]

for name, sql, expected_allowed in test_cases:
    try:
        validate_sql(sql)
        allowed = True
        detail = "passed initial checks"
    except ValueError as error:
        allowed = False
        detail = f"rejected: {error}"

    status = "PASS" if allowed == expected_allowed else "FAIL"
    expected = "allow" if expected_allowed else "reject"
    print(f"[{status}] {name} | expected: {expected} | actual: {detail}")
