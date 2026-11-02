"""One read-only SQL tool with column policies and restricted demographic shapes.

SQLGlot resolves CTE/subquery/window scopes; we do not maintain a grammar
allowlist. Unknown/UDF/external functions and source modifiers still fail closed.
Demographic queries are checked structurally, not by a catalog of business tasks.
"""

from sqlglot import exp, parse
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from retail_agent.schema.catalog import CATALOG_NAME, DB_NAME, TABLE_POLICIES
from retail_agent.schema.models import DataAccessLevel
from retail_agent.safety.demographic_sql import COHORT_COLUMN, validate_demographic_sql


class SQLValidationError(ValueError):
    """Controlled messages only; do not echo SQL or library exception text."""


def physical_tables(tree):
    """CTE names are Scope references, not physical BigQuery sources."""
    seen = set()
    for scope in traverse_scope(tree):
        for _, source in scope.selected_sources.values():
            if isinstance(source, exp.Table) and id(source) not in seen:
                seen.add(id(source))
                yield source


def validate_query(sql: str):
    """Return a qualified AST ready for brand rewriting, or reject it."""
    try:
        statements = [node for node in parse(sql, read="bigquery") if node is not None]
        if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.SetOperation)):
            raise SQLValidationError("Expected one read-only SELECT query")
        tree = statements[0]
        for node in tree.walk():
            if isinstance(node, (exp.DML, exp.DDL, exp.Command, exp.Into, exp.Parameter, exp.Placeholder)):
                raise SQLValidationError("Only read-only SQL without custom parameters is allowed")
            if isinstance(node, (exp.Anonymous, exp.Dot)):
                raise SQLValidationError("Unknown, remote or user-defined functions are not allowed")
            if isinstance(node, exp.Func) and node.sql_name() in {"SESSION_USER", "CURRENT_USER", "USER", "SYSTEM_USER", "EXTERNAL_QUERY"}:
                raise SQLValidationError("Identity and external-data functions are not allowed")
            if isinstance(node, exp.Star) and not (isinstance(node.parent, exp.Count) and node.parent.this is node and not any(node.args.values())):
                raise SQLValidationError("Use explicit columns, not wildcard projections")
        for table in physical_tables(tree):
            if table.catalog != CATALOG_NAME or table.db != DB_NAME or table.name not in TABLE_POLICIES:
                raise SQLValidationError("Table is outside the allowed sources")
            if not all(isinstance(table.args.get(key), exp.Identifier) for key in ("this", "db", "catalog")):
                raise SQLValidationError("Only ordinary physical table sources are allowed")
            if any(value for key, value in table.args.items() if key not in {"this", "db", "catalog", "alias"}):
                raise SQLValidationError("Table source modifiers are not supported")
            if table.args.get("alias") and table.args["alias"].args.get("columns"):
                raise SQLValidationError("Physical table column-renaming aliases are not supported")
        schema = {CATALOG_NAME: {DB_NAME: {
            table: {column: "UNKNOWN" for column in policies}
            for table, policies in TABLE_POLICIES.items()
        }}}
        tree = qualify(tree, dialect="bigquery", schema=schema, expand_stars=False)
        if any(item.alias_or_name.lower() == COHORT_COLUMN for select in tree.find_all(exp.Select) for item in select.expressions):
            raise SQLValidationError("The cohort output name is reserved for trusted code")
        if len(tree.named_selects) != len(set(tree.named_selects)):
            raise SQLValidationError("Use unique output column aliases")
        demographic = False
        for scope in traverse_scope(tree):
            for column in scope.columns:
                owner = scope
                while owner is not None and column.table not in owner.sources:
                    owner = owner.parent  # Correlated references resolve outside.
                source = owner.sources.get(column.table) if owner is not None else None
                if source is None:
                    raise SQLValidationError("Column source is unresolved or ambiguous")
                if isinstance(source, exp.Table):
                    policy = TABLE_POLICIES[source.name].get(column.name)
                    if policy is None:
                        raise SQLValidationError("Unknown column")
                    if policy.access_level == DataAccessLevel.BLOCKED_PII:
                        raise SQLValidationError("PII column is forbidden")
                    if policy.access_level == DataAccessLevel.AGGREGATE_ONLY:
                        demographic = True
                    if source.name == "orders" and column.name == "num_of_item":
                        raise SQLValidationError("Count authorized order_items instead of whole-order num_of_item")
        if demographic:
            try:
                tree.meta["demographic_subject"] = validate_demographic_sql(tree)
            except ValueError as error:
                raise SQLValidationError(str(error)) from None
        return tree
    except (ParseError, OptimizeError):
        raise SQLValidationError("SQL cannot be parsed or its columns are unknown/ambiguous") from None


def validate_sql(sql: str) -> None:
    validate_query(sql)
