# Current prototype: two analysis tools

## Decision

Use general SQL for non-demographic analysis and a typed request for demographic
analysis. Do not maintain a handwritten grammar/lineage engine for arbitrary
model-authored demographic SQL. Historical source and rationale are preserved in
[archive/sql-demographic-validator-v1.md](archive/sql-demographic-validator-v1.md).
That archive is not a production-ready implementation or a full repository backup.

## 1. General SQL tool

`safety/sql_validator.py` uses SQLGlot to parse/qualify one read-only query.
CTEs, derived tables, correlated subqueries, window functions, set operations and
recognized built-in functions can be used; there is no long AST-node allowlist.

Checks remain:
- known fully qualified physical tables in the permitted dataset;
- deterministic column policies, including references inside CTEs/subqueries;
- no direct PII and no demographic fields, even in WHERE: use tool 2 instead;
- no DML/DDL, wildcard projections, custom parameters, external/UDF/unknown
  functions, identity functions, or unreviewed physical-source modifiers;
- no orders.num_of_item, which may include unauthorized brands.

SQLGlot resolves names; application code walks physical sources rather than
mistaking CTE names for database tables. QueryScope replaces EVERY physical
source with an authorized-brand relation. Source projections contain ALLOWED
columns only, an additional barrier against PII/demographic access by this tool.
Customers/orders are visible through authorized purchases; all item sources
remain independently restricted. Whole-order status/timestamps are not redefined
as brand-specific facts: prefer order_items for those metrics.

This is broader SELECT support, NOT a promise that arbitrary BigQuery syntax or
functions are supported or production-safe. Results must be scalar tabular data.
Join grain, business correctness and natural-language intent still need evaluation.

## 2. Structured demographic tool

`data/demographics.py` owns both the Pydantic contract and SQL generation:

```json
{
  "group_by": ["state"],
  "filters": {"age_from": 20, "age_to": 30, "brand": "Calvin Klein", "status": "Complete"},
  "metrics": ["revenue", "customer_count"],
  "order_by": "revenue",
  "limit": 10
}
```

Only named dimensions/metrics/filter fields are accepted. There is no raw SQL,
user_id filter/output, arbitrary expression or model-controlled cohort counter.
Literal values are escaped through SQLGlot. Brand entitlements are bound as
BigQuery parameters from trusted startup identity, not from this request.

The query joins purchase -> customer/product on fixed keys and applies brand,
demographic, date and status filters. It first groups purchases by requested
dimensions AND customer, then aggregates those per-customer rows. Consequently:
- customer_count counts each buyer once per group;
- average_age is over DISTINCT buyers, not weighted by repeated purchases;
- revenue/profit and average sale price retain their purchase-level meaning;
- product-name grouping also includes product_id to avoid merging namesakes.

The private __cohort_size counts buyers with non-null inputs contributing to all
requested metrics. A large otherwise-null group cannot publish one known age as
an apparently safe average. HAVING applies the threshold BEFORE ranking/LIMIT.
ResultGuard checks the counter again and removes it before LLM delivery.

Default threshold: 5, configurable with --min-cohort-size (minimum 2). The number
is our documented prototype choice, not a number mandated by Harry/Vlad. It
applies to demographic results only; ordinary product revenue has no threshold.
Empty demographic results mean no publishable rows, not necessarily zero buyers.
Rankings cover publishable groups only. Group customer counts can overlap.

## Shared execution and result boundary

QueryService is the only agent execution path. Both tools use the same trusted
brand mapping, parameter binding, dry run, maximum billed bytes, row/response
bounds, timeout/cancellation and safe errors. Limits apply per query plus bounded
per-turn attempts/model calls/scan accounting in the graph. The raw runner and
its legacy unrestricted execute_query method are not model tools.
Failures carry their execution stage: authorization/validation/dry-run failures
consume attempts but no scan allowance. SQL repair observations include a bounded
copy of the model's previous SQL plus controlled feedback, never raw backend
exception bodies. In particular, calendar-month comparisons use DATE arithmetic,
not unsupported TIMESTAMP_SUB(... MONTH).

Prepared output names and tool context belong to QueryService, not to model-
provided assertions of safety. ResultGuard checks actual columns, scalar types,
finite numbers, cohort metadata and observation size before any rows return to
the model. General SQL observations include normalized submitted SQL (executed
with brand scoping); demographic observations include actual structured filters.
This replaces the former handwritten interpretation of SQL status predicates.

The CLI renders checked tables, not model-generated copies. Database-derived
PII values cannot be authorized merely by renaming an output column. Supplemental
email/phone text checks are NOT comprehensive detection of names/addresses in
arbitrary user input. This limitation remains explicit.

## Observability and status

Local JSON logs contain allowlisted IDs, counts, timings, token usage, stages and
safe error codes. No SQL, prompts, raw rows, brand lists or exception bodies are
logged. SQL in model observations is not exported to tracing. Implicit LangSmith
tracing is disabled; Langfuse is not integrated yet.

Offline tests cover both tools, the graph, row isolation, CTE/window/UNION paths,
PII laundering attempts and small-group/NULL cases. Live smoke tests cover a
CTE + ROUND revenue/profit query and a typed exact-age buyer count. They do not
constitute a general correctness/security proof.

## Production HLD (not implemented)

Use actual identity, database-enforced brand permissions/safe views, least-
privilege service accounts, a governed/versioned metric layer, external safe
observability, regression evaluations and operational budgets. Keep or extend
the two-tool boundary when it makes enforcement simpler. Do not assume arbitrary
demographic SQL is a production requirement. Document cross-question inference
and mitigation in production; Vlad explicitly excluded its implementation from
the prototype. k-suppression is not a formal privacy guarantee.
