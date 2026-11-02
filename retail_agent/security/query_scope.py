"""Rewrite every original physical source into a brand-scoped relation.

No queries are executed here. QueryService MUST bind allowed_brands
as a BigQuery ARRAY<STRING> parameter from trusted identity/entitlements, never
from model output. This rewrite is not a substitute for database permissions,
cost limits, aggregate protection, or result validation.
"""

from sqlglot import exp, parse_one

from retail_agent.safety.sql_validator import physical_tables, validate_query
from retail_agent.schema.catalog import CATALOG_NAME, DB_NAME, TABLE_POLICIES
from retail_agent.schema.models import DataAccessLevel
from retail_agent.safety.demographic_sql import add_cohort_guard


def _table_path(name: str) -> str:
    # All names here come from application constants, never model output.
    return f"`{CATALOG_NAME}.{DB_NAME}.{name}`"


def _scoped_source(table_name: str, *, demographics: bool = False) -> exp.Select:
    columns = ", ".join(
        f"r.`{name}`"
        for name, policy in TABLE_POLICIES[table_name].items()
        if (policy.access_level == DataAccessLevel.ALLOWED or demographics and policy.access_level == DataAccessLevel.AGGREGATE_ONLY)
        and not (table_name == "orders" and name == "num_of_item")
    )

    if table_name == "products":
        predicate = "r.brand IN UNNEST(@allowed_brands)"
    elif table_name == "order_items":
        predicate = f"""EXISTS (
            SELECT 1 FROM {_table_path('products')} AS p
            WHERE p.id = r.product_id
              AND p.brand IN UNNEST(@allowed_brands)
        )"""
    else:
        relation = "oi.user_id = r.id" if table_name == "users" else "oi.order_id = r.order_id"
        predicate = f"""EXISTS (
            SELECT 1 FROM {_table_path('order_items')} AS oi
            JOIN {_table_path('products')} AS p ON p.id = oi.product_id
            WHERE {relation}
              AND p.brand IN UNNEST(@allowed_brands)
        )"""

    return parse_one(
        f"SELECT {columns} FROM {_table_path(table_name)} AS r WHERE {predicate}",
        read="bigquery",
    )


def apply_brand_scope(sql: str, *, minimum_cohort_size: int = 5) -> str:
    """Validate SQL and scope physical sources, including those inside CTEs."""
    tree = validate_query(sql)
    demographics = bool(tree.meta.get("demographic_subject"))
    add_cohort_guard(tree, minimum_cohort_size)

    # Snapshot BEFORE replacement: never recursively rewrite our own guards.
    original_tables = list(physical_tables(tree))
    for table in original_tables:
        alias = table.args.get("alias")
        replacement = exp.Subquery(
            this=_scoped_source(table.name, demographics=demographics),
            alias=alias.copy() if alias is not None else exp.TableAlias(
                this=exp.to_identifier(table.name, quoted=True)
            ),
        )
        table.replace(replacement)

    return tree.sql(dialect="bigquery", pretty=True)
