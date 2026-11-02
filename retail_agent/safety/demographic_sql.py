"""Restricted aggregate SQL shapes, not a privacy proof for arbitrary BigQuery."""

from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from retail_agent.schema.catalog import TABLE_POLICIES
from retail_agent.schema.models import DataAccessLevel


COHORT_COLUMN = "__cohort_size"
_AGGREGATES = (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)
_NUMERIC_INPUTS = {("users", "age"), ("order_items", "sale_price"),
                   ("products", "cost"), ("products", "retail_price")}
_METRIC_NODES = {exp.Column, exp.Identifier, exp.Literal, exp.Null, exp.Paren,
                 exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Neg, exp.Round, exp.Abs,
                 exp.Cast, exp.DataType, exp.DataTypeParam, exp.SafeDivide, exp.Nullif}


def validate_demographic_sql(tree) -> str:
    scopes = list(traverse_scope(tree))
    if len(scopes) != 1 or not isinstance(tree, exp.Select) or tree.args.get("with_") or any(tree.find_all(exp.Window)):
        raise ValueError("Demographic SQL requires one flat aggregate SELECT: no CTEs, subqueries, set operations or windows.")
    sources = scopes[0].sources
    if any(not isinstance(s, exp.Table) for s in sources.values()):
        raise ValueError("Demographic SQL requires ordinary physical sources.")
    names = [s.name for s in sources.values()]
    if len(names) != len(set(names)) or set(names) not in ({"users", "order_items"}, {"users", "order_items", "products"}):
        raise ValueError("Demographic SQL must join users to order_items, optionally products, exactly once.")

    def key(column):
        source = sources.get(column.table)
        if source is None:
            raise ValueError("Unresolved demographic column source.")
        return source.name, column.name

    def dimension(column):
        table, name = key(column)
        return (table == "users" and TABLE_POLICIES[table][name].access_level == DataAccessLevel.AGGREGATE_ONLY
                or table == "products"
                or table == "order_items" and name in {"product_id", "status", "sale_price", "created_at", "shipped_at", "delivered_at", "returned_at"})

    links = set()
    for join in tree.args.get("joins") or []:
        on = join.args.get("on")
        if join.side or join.kind not in {"", "INNER"} or not isinstance(on, exp.EQ) or not all(isinstance(c, exp.Column) for c in (on.left, on.right)):
            raise ValueError("Use INNER JOINs with canonical key equality; put filters in WHERE.")
        pair = frozenset((key(on.left), key(on.right)))
        if pair not in {frozenset({("users", "id"), ("order_items", "user_id")}),
                        frozenset({("products", "id"), ("order_items", "product_id")})} or pair in links:
            raise ValueError("Demographic joins must link purchases to their actual buyers/products.")
        links.add(pair)
    if len(links) != len(names) - 1 or tree.args.get("distinct"):
        raise ValueError("Use connected canonical joins and GROUP BY, not SELECT DISTINCT.")

    groups = (tree.args.get("group").expressions if tree.args.get("group") else [])
    if tree.args.get("group") and any(v for k, v in tree.args["group"].args.items() if k != "expressions"):
        raise ValueError("Demographic grouping sets/rollups are not supported.")
    for expression in [*groups, *([tree.args["where"]] if tree.args.get("where") else [])]:
        if any(expression.find_all(exp.AggFunc)) or any(not dimension(c) for c in expression.find_all(exp.Column)):
            raise ValueError("Demographic filters/grouping cannot select individual customer/order/item identifiers.")

    aggregates = []

    def validate_output(expression):
        def replace(node):
            if any(node == group for group in groups):
                return exp.Literal.number(0)
            if isinstance(node, exp.AggFunc):
                if not isinstance(node, _AGGREGATES):
                    raise ValueError("Demographic aggregates support COUNT, SUM, AVG, MIN and MAX.")
                argument = node.this
                if isinstance(argument, exp.Distinct):
                    if not isinstance(node, exp.Count) or len(argument.expressions) != 1:
                        raise ValueError("Only COUNT supports DISTINCT with one direct column.")
                    argument = argument.expressions[0]
                if isinstance(node, exp.Count):
                    if not isinstance(argument, (exp.Star, exp.Column)):
                        raise ValueError("Use COUNT(*) or COUNT([DISTINCT] column), not conditional counts.")
                else:
                    if argument is None or any(type(n) not in _METRIC_NODES for n in argument.walk()):
                        raise ValueError("Use numeric arithmetic inside demographic aggregates; conditional/nested aggregates are not supported.")
                    if any(key(c) not in _NUMERIC_INPUTS for c in argument.find_all(exp.Column)):
                        raise ValueError("Numeric demographic aggregates support age, sale price and product cost/retail price.")
                aggregates.append(node)
                return exp.Literal.number(0)
            return node

        remaining = expression.copy().transform(replace)
        for column in remaining.find_all(exp.Column):
            # ORDER BY may retain an output alias after SQLGlot qualification.
            if column.table or column.name not in tree.named_selects:
                raise ValueError("Demographic outputs must be grouped expressions or aggregates, never individual profiles.")

    for item in tree.expressions:
        validate_output(item.this if isinstance(item, exp.Alias) else item)
    for name in ("having", "order"):
        if tree.args.get(name):
            validate_output(tree.args[name])
    if not aggregates:
        raise ValueError("Demographic SQL must return aggregate statistics.")
    return next(alias for alias, source in sources.items() if source.name == "users")


def add_cohort_guard(tree, minimum: int):
    """Add a service-owned contributing-buyer guard before ORDER/LIMIT."""
    alias = tree.meta.get("demographic_subject")
    if not alias:
        return
    if type(minimum) is not int or minimum < 2:
        raise ValueError("minimum cohort size must be at least 2")
    inputs = {}
    for aggregate in tree.find_all(exp.AggFunc):
        arg = aggregate.this
        if isinstance(arg, exp.Distinct):
            arg = arg.expressions[0]
        if arg is not None and not isinstance(arg, exp.Star):
            for item in [arg, *arg.find_all(exp.Column)]:
                inputs[item.sql(dialect="bigquery")] = item
    subject = exp.column("id", table=alias)
    if inputs:
        checks = [exp.Not(this=exp.Is(this=item.copy(), expression=exp.Null())) for item in inputs.values()]
        subject = exp.If(this=exp.and_(*checks), true=subject, false=exp.Null())
    count = exp.Count(this=exp.Distinct(expressions=[subject]))
    tree.select(exp.alias_(count.copy(), COHORT_COLUMN), append=True, copy=False)
    predicate = exp.GTE(this=count, expression=exp.Literal.number(minimum))
    existing = tree.args.get("having")
    tree.set("having", exp.Having(this=exp.and_(existing.this.copy(), predicate) if existing else predicate))
