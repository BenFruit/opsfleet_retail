# Archived design: model-authored demographic SQL (V1)

> Historical reference, not current production guidance. Preserved before the
> switch to two tools: general SQL and structured demographic analysis.
> The code block below is the former `safety/aggregate_policy.py` implementation.
> This is an algorithm/source snapshot, not a runnable snapshot of the repository.

## Why it existed

The model could submit demographic SQL. To keep the cohort count meaningful,
the application had to validate source lineage, join keys, grouping dimensions,
aggregate inputs, individual-ID filters and ordering. This worked for a narrow
SQL subset, but the handwritten validator was difficult to explain and extend.
Rejecting a SQL shape was an implementation limitation, not necessarily a client
privacy rule. This distinction must be retained in the final documentation.

## Former execution path

```text
Model SQL -> sql_validator.validate_sql
          -> demographic_customer_alias (validate the supported aggregate shape)
          -> add_cohort_guard (trusted COUNT DISTINCT + HAVING >= k)
          -> replace physical sources with brand-scoped relations
          -> parameterized dry run / capped execution
          -> verify output columns and trusted cohort size
          -> suppress small groups, remove __cohort_size, normalize/bound rows
          -> LLM
```

`sql_validator.py` additionally required one SELECT, fully qualified known tables,
scope-aware column resolution, no blocked PII anywhere, and an explicit AST-node
allowlist. COUNT(*) was the only permitted star. CTEs, derived-source outputs,
correlated queries and many other otherwise-valid constructs were unsupported.
Unqualified columns were accepted only if exactly one source defined them.
`orders.num_of_item` was prohibited because mixed-brand totals could include
unauthorized items. Reserved `__cohort_size` aliases were rejected.

## Preserved implementation

```python
"""Conservative demographic aggregates, not a general SQL privacy proof.

Only flat, canonical purchase/customer/product joins are supported. This keeps
the trusted cohort count aligned with the rows contributing to each metric.
"""

from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from retail_agent.schema.catalog import TABLE_POLICIES
from retail_agent.schema.models import DataAccessLevel


COHORT_COLUMN = "__cohort_size"


class AggregatePolicyError(ValueError):
    pass


def demographic_customer_alias(tree: exp.Select) -> str | None:
    """Validate demographic query shape; return the customer alias if applicable."""
    scopes = list(traverse_scope(tree))
    demographic = False
    for scope in scopes:
        for column in scope.columns:
            sources = [scope.sources.get(column.table)] if column.table else [
                source for source in scope.sources.values()
                if isinstance(source, exp.Table) and column.name in TABLE_POLICIES[source.name]
            ]
            if any(
                isinstance(source, exp.Table)
                and TABLE_POLICIES[source.name].get(column.name) is not None
                and TABLE_POLICIES[source.name][column.name].access_level == DataAccessLevel.AGGREGATE_ONLY
                for source in sources
            ):
                demographic = True
    if not demographic:
        return None
    if len(scopes) != 1 or len(list(tree.find_all(exp.Select))) != 1:
        raise AggregatePolicyError("Demographic analysis requires one flat aggregate SELECT")

    sources = scopes[0].sources
    if any(not isinstance(source, exp.Table) for source in sources.values()):
        raise AggregatePolicyError("Demographic derived sources are not supported")
    tables = [source.name for source in sources.values()]
    if set(tables) not in ({"users", "order_items"}, {"users", "order_items", "products"}) or len(tables) != len(set(tables)):
        raise AggregatePolicyError("Join users to order_items, optionally products, for demographic aggregates")

    def key(column):
        source = sources.get(column.table)
        if not isinstance(source, exp.Table):
            raise AggregatePolicyError("Qualify demographic query columns with table aliases")
        return source.name, column.name

    joins = tree.args.get("joins") or []
    for join in joins:
        condition = join.args.get("on")
        if join.side or join.kind not in {"", "INNER"} or not isinstance(condition, exp.EQ):
            raise AggregatePolicyError("Demographic queries require ordinary INNER JOINs on canonical keys")
        if not isinstance(condition.left, exp.Column) or not isinstance(condition.right, exp.Column):
            raise AggregatePolicyError("Join demographic sources on canonical column keys")
        pair = frozenset((key(condition.left), key(condition.right)))
        if pair not in {
            frozenset({("users", "id"), ("order_items", "user_id")}),
            frozenset({("products", "id"), ("order_items", "product_id")}),
        }:
            raise AggregatePolicyError("Demographic joins must link purchases to their actual customers/products")
    if len(joins) != len(tables) - 1:
        raise AggregatePolicyError("All demographic sources must be joined on canonical keys")
    # Check connectivity: repeating one valid ON predicate must not leave a
    # different source effectively cross joined and inflate the cohort count.
    joined_keys = [frozenset((key(join.args['on'].left)[0], key(join.args['on'].right)[0])) for join in joins]
    if len(set(joined_keys)) != len(joins):
        raise AggregatePolicyError("Demographic join links must not be repeated")

    if tree.args.get("having") is not None or tree.args.get("distinct"):
        raise AggregatePolicyError("Do not use HAVING or SELECT DISTINCT for demographic analysis; cohort suppression is automatic")

    def dimension(column):
        table, name = key(column)
        if table == "users":
            return TABLE_POLICIES[table][name].access_level == DataAccessLevel.AGGREGATE_ONLY
        return table == "products" or (table == "order_items" and name in {"product_id", "status"})

    aliases = {item.alias: item.this for item in tree.expressions if isinstance(item, exp.Alias)}
    grouped = set()
    group = tree.args.get("group")
    for expression in group.expressions if group is not None else []:
        if isinstance(expression, exp.Column) and not expression.table and expression.name in aliases:
            expression = aliases[expression.name]
        if not isinstance(expression, exp.Column) or not dimension(expression):
            raise AggregatePolicyError("Group demographics only by direct demographic/product dimensions, not individual IDs")
        grouped.add(key(expression))

    where = tree.args.get("where")
    if where is not None:
        for column in where.find_all(exp.Column):
            table, name = key(column)
            if (table == "users" and name == "id") or (
                table == "order_items" and name in {"id", "user_id", "order_id", "inventory_item_id"}
            ):
                raise AggregatePolicyError("Individual customer/order filters cannot be combined with demographic analysis")

    aggregate_count = 0
    for projection in tree.expressions:
        value = projection.this if isinstance(projection, exp.Alias) else projection
        if isinstance(value, exp.Column):
            if not dimension(value) or key(value) not in grouped:
                raise AggregatePolicyError("Demographic outputs must be grouped dimensions or approved aggregates, never individual profiles")
            continue
        if not isinstance(projection, exp.Alias) or not isinstance(value, (exp.Count, exp.Sum, exp.Avg)):
            raise AggregatePolicyError("Use named COUNT, SUM or AVG outputs for demographic analysis")
        aggregate_count += 1
        if isinstance(value, exp.Count):
            argument = value.this
            if isinstance(argument, exp.Distinct):
                if len(argument.expressions) != 1:
                    raise AggregatePolicyError("COUNT DISTINCT requires one column")
                argument = argument.expressions[0]
            if not isinstance(argument, (exp.Column, exp.Star)):
                raise AggregatePolicyError("Conditional demographic counts are not supported")
            if isinstance(argument, exp.Column):
                key(argument)
        elif isinstance(value, exp.Avg) and isinstance(value.this, exp.Column) and key(value.this) == ("users", "age"):
            pass  # Purchase-weighted mean age, documented in the observation.
        else:
            for node in value.this.walk():
                if isinstance(node, exp.Column):
                    if key(node) not in {
                        ("order_items", "sale_price"), ("products", "cost"), ("products", "retail_price"),
                    }:
                        raise AggregatePolicyError("Demographic sums/averages support purchase prices and product costs only")
                elif type(node) not in {exp.Identifier, exp.Literal, exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Paren, exp.Neg}:
                    raise AggregatePolicyError("Conditional or nested demographic aggregates are not supported")
    if aggregate_count == 0:
        raise AggregatePolicyError("Demographic analysis must return an aggregate, not customer attributes")

    order = tree.args.get("order")
    for ordered in order.expressions if order is not None else []:
        value = ordered.this
        if isinstance(value, exp.Column) and not value.table and value.name in aliases:
            continue
        if isinstance(value, exp.Column) and key(value) in grouped:
            continue
        if isinstance(value, exp.Literal) and value.is_int and 1 <= int(value.this) <= len(tree.expressions):
            continue
        raise AggregatePolicyError("Order demographic results by an output alias or grouped dimension")
    return next(alias for alias, source in sources.items() if source.name == "users")


def add_cohort_guard(tree: exp.Select, *, minimum_cohort_size: int) -> bool:
    """Add a trusted counter and HAVING BEFORE ORDER/LIMIT. Mutates the tree."""
    alias = demographic_customer_alias(tree)
    if alias is None:
        return False
    # Require at least k distinct buyers contributing non-null inputs to EVERY
    # returned aggregate. A large group with just one non-null age must not
    # publish that person's age as an apparently safe AVG(age). This counter
    # does not change the metrics or remove rows from their calculation.
    inputs = {}
    for projection in tree.expressions:
        value = projection.this if isinstance(projection, exp.Alias) else projection
        if isinstance(value, (exp.Count, exp.Sum, exp.Avg)):
            for column in value.find_all(exp.Column):
                inputs[(column.table, column.name)] = column
    subject = exp.column("id", table=alias)
    if inputs:
        checks = [exp.Not(this=exp.Is(this=column.copy(), expression=exp.Null())) for column in inputs.values()]
        subject = exp.If(this=exp.and_(*checks), true=subject, false=exp.Null())
    count = exp.Count(this=exp.Distinct(expressions=[subject]))
    tree.select(exp.alias_(count.copy(), COHORT_COLUMN), append=True, copy=False)
    tree.set("having", exp.Having(this=exp.GTE(
        this=count, expression=exp.Literal.number(minimum_cohort_size),
    )))
    return True
```

## Former general SQL validator (source snapshot)

<details>
<summary>Expand the old sql_validator.py implementation</summary>

```python
"""A deliberately restricted SQL subset, not a general BigQuery security parser.

Based on the exercises in inspect_sql.py. Derived-table columns, CTEs,
correlated references and demographic aggregates need additional validation
before they can be supported. This module does not authorize rows or execute SQL.
"""

from sqlglot import exp, parse
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.scope import traverse_scope

from retail_agent.schema.catalog import CATALOG_NAME, DB_NAME, TABLE_POLICIES
from retail_agent.schema.models import DataAccessLevel
from retail_agent.safety.aggregate_policy import AggregatePolicyError, COHORT_COLUMN, demographic_customer_alias


# Fail closed for SQL constructs/functions we have not explicitly reviewed.
_ALLOWED_NODES = frozenset({
    exp.Select, exp.From, exp.Join, exp.Table, exp.Identifier, exp.Column,
    exp.TableAlias, exp.Alias, exp.Literal, exp.Null, exp.Boolean,
    exp.And, exp.Or, exp.Not, exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT,
    exp.LTE, exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Neg, exp.Paren,
    exp.Where, exp.Group, exp.Having, exp.Order, exp.Ordered, exp.Limit,
    exp.Offset, exp.Distinct, exp.Count, exp.Sum, exp.Avg, exp.Min,
    exp.Max, exp.In, exp.Between, exp.Is, exp.Like, exp.Case, exp.If,
    exp.Coalesce, exp.Nullif, exp.Subquery, exp.Exists,
    exp.Star, exp.Date, exp.DateTrunc, exp.TimestampTrunc, exp.Extract,
    exp.CurrentDate, exp.CurrentTimestamp, exp.DateSub, exp.DateAdd,
    exp.TimestampSub, exp.TimestampAdd, exp.Interval, exp.Var,
    exp.Cast, exp.DataType, exp.DataTypeParam, exp.SafeDivide,
})

_TIME_UNITS = frozenset({
    "YEAR", "QUARTER", "MONTH", "WEEK", "DAY", "DAYOFWEEK", "DAYOFYEAR",
    "HOUR", "MINUTE", "SECOND", "MILLISECOND", "MICROSECOND", "ISOYEAR", "ISOWEEK",
})


class SQLValidationError(ValueError):
    """Only controlled messages; safe to expose as query-repair feedback."""


def validate_sql(sql: str) -> None:
    """Reject unsupported/unsafe SQL; success is not execution permission.

    Requires unambiguous columns and fully qualified physical tables.
    AGGREGATE_ONLY requires the restricted demographic aggregate policy.
    COUNT(*) is allowed, but wildcard column projection is not.
    """
    try:
        statements = [tree for tree in parse(sql, read="bigquery") if tree is not None]
    except ParseError as error:
        raise SQLValidationError("Invalid BigQuery SQL") from error

    if len(statements) != 1:
        raise SQLValidationError("Expected exactly one SQL statement")

    tree = statements[0]
    if not isinstance(tree, exp.Select):
        raise SQLValidationError("Only SELECT queries are allowed")
    for star in tree.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count) or star.parent.this is not star or any(star.args.values()):
            raise SQLValidationError("Wildcard expressions are not allowed outside COUNT(*)")

    for node in tree.walk():
        if type(node) not in _ALLOWED_NODES:
            raise SQLValidationError(f"Unsupported SQL construct: {type(node).__name__}")
        if isinstance(node, exp.Var) and node.name.upper() not in _TIME_UNITS:
            raise SQLValidationError("Unsupported date/time unit")

    for table in tree.find_all(exp.Table):
        if table.catalog != CATALOG_NAME or table.db != DB_NAME:
            raise SQLValidationError("Table is outside the allowed project/dataset")
        if table.name not in TABLE_POLICIES:
            raise SQLValidationError("Table is not allowed")
        if not all(isinstance(table.args.get(key), exp.Identifier) for key in ("this", "db", "catalog")):
            raise SQLValidationError("Only ordinary physical table names are supported")
        if any(value for key, value in table.args.items() if key not in {"this", "db", "catalog", "alias"}):
            raise SQLValidationError("Table modifiers are not supported")

    for alias in tree.find_all(exp.TableAlias):
        if alias.args.get("columns"):
            raise SQLValidationError("Table column aliases are not supported")
    if any(item.alias_or_name.lower() == COHORT_COLUMN for item in tree.expressions):
        raise SQLValidationError("The cohort output name is reserved for trusted code")

    try:
        for scope in traverse_scope(tree):
            # Also detect duplicate source aliases, even if no columns use them.
            scope.selected_sources
            for column in scope.columns:
                if column.db or column.catalog:
                    raise SQLValidationError("Use a table alias to qualify columns")
                output_aliases = {
                    item.alias for item in scope.expression.expressions
                    if isinstance(item, exp.Alias)
                }
                if (
                    not column.table and column.name in output_aliases
                    and column.find_ancestor(exp.Select) is scope.expression
                    and column.find_ancestor(exp.Group, exp.Order, exp.Having) is not None
                    and not any(
                        column.name in TABLE_POLICIES[source.name]
                        for source in scope.sources.values() if isinstance(source, exp.Table)
                    )
                ):
                    continue  # Unambiguous reference to a validated output expression.
                source = scope.sources.get(column.table)
                if not column.table:
                    sources = list(scope.sources.values())
                    if any(not isinstance(item, exp.Table) for item in sources):
                        raise SQLValidationError("Qualify columns; derived sources are not supported")
                    candidates = [
                        item for item in sources
                        if column.name in TABLE_POLICIES[item.name]
                    ]
                    if len(candidates) != 1:
                        raise SQLValidationError("Column is unknown or ambiguous; use an explicit table alias")
                    source = candidates[0]
                if not isinstance(source, exp.Table):
                    raise SQLValidationError("Unsupported or unresolved column source; use a local table alias")

                policy = TABLE_POLICIES[source.name].get(column.name)
                if policy is None:
                    raise SQLValidationError("Unknown column")
                if policy.access_level == DataAccessLevel.BLOCKED_PII:
                    raise SQLValidationError("PII column is forbidden")
                if source.name == "orders" and column.name == "num_of_item":
                    raise SQLValidationError("Count authorized order_items instead of whole-order num_of_item")
    except OptimizeError as error:
        raise SQLValidationError("Unsupported or ambiguous query scope") from error
    try:
        demographic_customer_alias(tree)
    except AggregatePolicyError as error:
        raise SQLValidationError(str(error)) from None
```

</details>

## Result-boundary contract

The former QueryService detected demographics with the same validator and
attached `minimum_cohort_size` to its internal QueryResult. ResultGuard required
the original projection columns plus `__cohort_size`, verified an integer count,
withheld groups below the threshold, then removed the internal column before
LLM delivery. Empty output never distinguished zero buyers from a small group.
Ordinary non-demographic product revenue had no cohort threshold.

The private counter used a common set of buyers with non-null inputs to every
returned aggregate. This was conservative: it did not alter metric calculations,
but could suppress more groups than a per-metric contributor policy would.

## Production HLD considerations (proposals, NOT implemented)

- Prefer database-enforced, identity-aware authorized views/row access policies
  and least-privilege credentials over relying only on application SQL rewriting.
- A semantic/metric layer can define revenue, status policies, grain and currency.
- If arbitrary demographic SQL is necessary, use robust lineage/contributor
  analysis and adversarial evaluation rather than extending handwritten cases.
- Version policies/personas, keep PII-safe audit trails, and review policy changes.
- Describe differencing/intersection attacks across queries and production
  mitigations. The client explicitly did not require that implementation in MVP.
- Neither the old validator nor configurable k-suppression constitutes a formal
  privacy guarantee. Generic sensitive free-text handling also remains separate.

## Decision

Use general validated SQL for non-demographic analysis, and a typed demographic
request whose SQL is built by trusted code. This removes the need to prove the
safety of arbitrary model-authored demographic SQL while retaining dynamic
dimensions/filters/metrics. It is a prototype boundary, not a claim that every
BigQuery feature is supported or safe.
