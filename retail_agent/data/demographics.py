"""Typed demographic tool: code owns the SQL, joins and privacy counter."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlglot import exp

from retail_agent.schema.catalog import CATALOG_NAME, DB_NAME


COHORT_COLUMN = "__cohort_size"
Dimension = Literal["age", "gender", "state", "city", "country", "traffic_source", "brand", "category", "product_id", "product_name", "month", "status"]
Metric = Literal["customer_count", "units_sold", "revenue", "estimated_gross_profit", "average_sale_price", "average_age"]


class DemographicFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    age_from: int | None = Field(default=None, ge=0, le=120)
    age_to: int | None = Field(default=None, ge=0, le=120)
    country: str | None = Field(default=None, min_length=1, max_length=100)
    state: str | None = Field(default=None, min_length=1, max_length=100)
    city: str | None = Field(default=None, min_length=1, max_length=100)
    gender: str | None = Field(default=None, min_length=1, max_length=100)
    traffic_source: str | None = Field(default=None, min_length=1, max_length=100)
    brand: str | None = Field(default=None, min_length=1, max_length=200)
    category: str | None = Field(default=None, min_length=1, max_length=200)
    product_id: int | None = Field(default=None, ge=1)
    start_date: date | None = None
    end_date: date | None = None
    status: Literal["Complete", "Shipped", "Processing", "Cancelled", "Returned", "all"] = "Complete"

    @model_validator(mode="after")
    def check_ranges(self):
        if self.age_from is not None and self.age_to is not None and self.age_from > self.age_to:
            raise ValueError("age_from must not exceed age_to")
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not exceed end_date")
        return self


class DemographicRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_by: list[Dimension] = Field(default_factory=list, max_length=4)
    filters: DemographicFilters = Field(default_factory=DemographicFilters)
    metrics: list[Metric] = Field(default_factory=lambda: ["customer_count"], min_length=1, max_length=6)
    order_by: str | None = Field(default=None, max_length=40)
    descending: bool = True
    limit: int = Field(default=200, ge=1, le=200)

    @model_validator(mode="after")
    def check_fields(self):
        if len(set(self.group_by)) != len(self.group_by) or len(set(self.metrics)) != len(self.metrics):
            raise ValueError("Dimensions and metrics must be unique")
        if self.order_by is not None and self.order_by not in [*self.group_by, *self.metrics]:
            raise ValueError("order_by must name a selected dimension or metric")
        if not (
            set(self.group_by) & {"age", "gender", "state", "city", "country", "traffic_source"}
            or any(getattr(self.filters, field) is not None for field in ("age_from", "age_to", "country", "state", "city", "gender", "traffic_source"))
            or "average_age" in self.metrics
        ):
            raise ValueError("Use the SQL tool for analysis without demographics")
        return self


_DIMENSIONS = {
    **{name: f"u.{name}" for name in ("age", "gender", "state", "city", "country", "traffic_source")},
    "brand": "p.brand", "category": "p.category", "product_id": "p.id", "product_name": "p.name",
    "month": "DATE_TRUNC(DATE(oi.created_at), MONTH)",
    "status": "oi.status",
}
_METRICS = {
    "customer_count": ("COUNT(*)", ["u.id"]),
    "units_sold": ("SUM(_units)", ["oi.id"]),
    "revenue": ("SUM(_revenue)", ["oi.sale_price"]),
    "estimated_gross_profit": ("SUM(_profit)", ["oi.sale_price", "p.cost"]),
    "average_sale_price": ("SUM(_revenue) / NULLIF(SUM(_priced_units), 0)", ["oi.sale_price"]),
    "average_age": ("AVG(_age)", ["u.age"]),
}


def build_demographic_query(request: DemographicRequest, minimum: int) -> tuple[str, tuple[str, ...], dict]:
    """Return trusted SQL, public column names and the actual filter context.

    No identifiers/expressions come from free-form model text. Filter literals
    are escaped by SQLGlot; brands are still bound by the runner from identity.
    """
    request = DemographicRequest.model_validate(request)
    if type(minimum) is not int or minimum < 2:
        raise ValueError("minimum cohort size must be at least 2")
    groups = list(request.group_by)
    if "product_name" in groups and "product_id" not in groups:
        groups.insert(0, "product_id")  # Different products can share a name.
    dimensions = [f"{_DIMENSIONS[name]} AS {name}" for name in groups]
    select = list(groups)
    select += [f"{_METRICS[name][0]} AS {name}" for name in request.metrics]
    inputs = sorted({column for name in request.metrics for column in _METRICS[name][1]})
    nonnull = " AND ".join(f"{column} IS NOT NULL" for column in inputs)
    cohort = "SUM(_contributes)"
    select.append(f"{cohort} AS {COHORT_COLUMN}")
    filters = request.filters.model_dump(mode="json", exclude_none=True)
    where = ["p.brand IN UNNEST(@allowed_brands)"]
    for name, value in filters.items():
        literal = exp.convert(value).sql(dialect="bigquery")
        if name == "status":
            if value != "all":
                where.append(f"oi.status = {literal}")
        elif name in {"age_from", "age_to"}:
            where.append(f"u.age {'>=' if name == 'age_from' else '<='} {literal}")
        elif name in {"start_date", "end_date"}:
            where.append(f"DATE(oi.created_at) {'>=' if name == 'start_date' else '<='} DATE({literal})")
        else:
            where.append(f"{_DIMENSIONS[name]} = {literal}")
    path = f"{CATALOG_NAME}.{DB_NAME}"
    # One row per group/customer first: repeated purchases must not overweight
    # average customer age or count one buyer multiple times.
    inner = dimensions + [
        "u.id AS _buyer_id", "COUNT(oi.id) AS _units",
        "COUNT(oi.sale_price) AS _priced_units", "SUM(oi.sale_price) AS _revenue",
        "SUM(oi.sale_price - p.cost) AS _profit", "MAX(u.age) AS _age",
        f"MAX(IF({nonnull}, 1, 0)) AS _contributes",
    ]
    sql = (
        f"WITH buyers AS (SELECT {', '.join(inner)} FROM `{path}.order_items` AS oi "
        f"JOIN `{path}.users` AS u ON oi.user_id = u.id "
        f"JOIN `{path}.products` AS p ON oi.product_id = p.id "
        f"WHERE {' AND '.join(where)}"
    )
    sql += " GROUP BY " + ", ".join([*(_DIMENSIONS[name] for name in groups), "u.id"])
    sql += f") SELECT {', '.join(select)} FROM buyers"
    if groups:
        sql += " GROUP BY " + ", ".join(groups)
    sql += f" HAVING {cohort} >= {minimum}"
    if request.order_by:
        sql += f" ORDER BY {request.order_by} {'DESC' if request.descending else 'ASC'}"
    sql += f" LIMIT {request.limit}"
    context = {"tool": "demographic", "group_by": groups, "metrics": request.metrics, "filters": filters}
    if "average_age" in request.metrics:
        context["age_average_basis"] = "Average over distinct buyers within each result group"
    return sql, tuple([*groups, *request.metrics]), context
