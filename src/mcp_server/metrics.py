"""The metrics tool group: tools over the published metric tables."""

from datetime import date
from typing import Annotated, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, StringConstraints

from mcp_server.outputs import METRICS, Name, Run, load_run, parquet, quoted, rows
from mcp_server.server import READ_ONLY, server

DAILY_TOTALS = (
    "The published totals from `start` to `end` (ISO dates, both included) by `grain`: `day`, "
    "`week` (starting Monday) or `month`. It returns columns: `dates` holds the first day of each "
    "period that falls in the range, and `values` holds one list per metric, lined up with "
    "`dates`. `metrics` picks the metrics; leave it out for all of them. At `week` and `month`, "
    "counts and money are summed and `aov`, `return_rate` and `conversion_rate` are recomputed "
    "from the summed parts; a ratio is null when its denominator is 0. The session metrics, named "
    "in `approximate`, are approximate above `day`, because a session that crosses midnight is "
    "counted on both days. A period is `provisional` when any of its days is on or after "
    "`restated_from`, so its numbers may still change. Call `metrics_freshness` for the dates "
    "the data covers and `metrics_list` for the metric names."
)
EXPLAIN = (
    "The full definition of one metric: its `name`, display `label`, `unit` (`usd`, `count` or "
    "`ratio`), whether it is `additive`, a plain-language `description`, its `aggregation` "
    "(`sum`, `count`, `count_distinct` or `ratio`) and its `formula`. Call it before explaining "
    "what a number means. Call `metrics_list` for the valid names."
)
FRESHNESS = (
    "Which pipeline run the metrics come from: its id, source, contract version, data range "
    "(`data_min_date` to `data_max_date`), publish time (`generated_at`, UTC) and quality status. "
    "Call it to learn what dates the data covers and how fresh it is. Rows dated on or after "
    "`restated_from` may still change on later runs."
)
LIST = (
    "Every published metric in contract order: its `name`, display `label`, `unit` (`usd`, "
    "`count` or `ratio`) and whether it is `additive`. A metric is additive when summing it "
    "across categories and distribution centers gives the total. `orders`, the session counts "
    "and every ratio are not additive, because one order or session can span several slices. "
    "Call `metrics_explain` for one metric's full definition."
)

# The columns are in the order of Definition's fields.
DEFINITIONS_SQL = """
select
    metric_name,
    label,
    unit,
    additive,
    description,
    aggregation,
    formula
from read_parquet(?)
"""
# The placeholders take a metric's total per name of METRICS and a value of PERIODS.
TOTALS_SQL = """
select
    min(metric_date) as period_date,
    max(metric_date) >= ? as provisional{totals}
from read_parquet(?)
where metric_date between ? and ?
group by {period}
order by period_date
"""
PERIODS = {
    "day": "metric_date",
    "week": "date_trunc('week', metric_date)",
    "month": "date_trunc('month', metric_date)",
}
# Each ratio's numerator and denominator.
RATIOS = {
    "aov": ("revenue", "orders"),
    "return_rate": ("returned_units", "units"),
    "conversion_rate": ("converted_sessions", "product_view_sessions"),
}
MONEY = ("revenue", "aov")
SESSION = ("product_view_sessions", "converted_sessions", "conversion_rate")
MAX_DAYS = 366
MAX_VALUES = 2000

Grain = Literal["day", "week", "month"]
# These reach Claude as written, so a data file can't put a long string in them.
Label = Annotated[str, StringConstraints(pattern=r"^[\w .-]{1,64}$")]
Text = Annotated[str, StringConstraints(max_length=500)]


class Metric(BaseModel):
    """One metric of the contract."""

    model_config = ConfigDict(frozen=True)

    name: Name
    label: Label
    unit: Literal["usd", "count", "ratio"]
    additive: bool


class Definition(Metric):
    """One metric with its full definition."""

    description: Text
    aggregation: Literal["sum", "count", "count_distinct", "ratio"]
    formula: Text


class Metrics(BaseModel):
    """Every metric of the contract, in contract order."""

    model_config = ConfigDict(frozen=True)

    metrics: tuple[Metric, ...]


class Totals(BaseModel):
    """The totals of a date range in columns, one entry per period."""

    model_config = ConfigDict(frozen=True)

    grain: Grain
    dates: tuple[date, ...]
    values: dict[Name, tuple[int | float | None, ...]]
    provisional: tuple[bool, ...]
    approximate: tuple[Name, ...]
    restated_from: date


def definitions(run: Run) -> list[Definition]:
    """Return the run's metric definitions in contract order."""
    order = METRICS[run.contract_version]
    found = [
        Definition(**dict(zip(Definition.model_fields, row, strict=True)))
        for row in rows(DEFINITIONS_SQL, [parquet("metric_definitions")])
    ]
    return sorted(found, key=lambda definition: order.index(definition.name))


def total(name: str, grain: Grain) -> str:
    """Return the SQL that gives one metric's total for a period of the grain."""
    if grain == "day":
        return f"any_value({quoted(name)})"
    if name in RATIOS:
        over, under = (f"sum({quoted(part)})" for part in RATIOS[name])
        ratio = f"{over} / nullif({under}, 0)"
        return f"round({ratio}, 2)" if name in MONEY else ratio
    return f"round(sum({quoted(name)}), 2)" if name in MONEY else f"sum({quoted(name)})"


@server.tool(annotations=READ_ONLY, description=DAILY_TOTALS)
def metrics_daily_totals(
    start: date, end: date, metrics: list[str] | None = None, grain: Grain = "day"
) -> Totals:
    """Return the published totals for a date range by day, week or month."""
    run = load_run()
    order = METRICS[run.contract_version]
    if metrics is not None and not set(metrics) <= set(order):
        raise ToolError(f"That is not a metric. Use any of: {', '.join(order)}.")
    if end < start:
        raise ToolError("`end` is before `start`. Swap them.")
    if start < run.data_min_date or end > run.data_max_date:
        raise ToolError(
            f"The data covers {run.data_min_date} to {run.data_max_date}. Choose a range inside it."
        )
    if grain == "day" and (end - start).days >= MAX_DAYS:
        raise ToolError(
            f"That range is more than {MAX_DAYS} days. Use grain `week` or `month`, or a "
            "shorter range."
        )
    names = [name for name in order if metrics is None or name in metrics]
    sql = TOTALS_SQL.format(
        totals="".join(f",\n    {total(name, grain)} as {quoted(name)}" for name in names),
        period=PERIODS[grain],
    )
    found = rows(sql, [run.restated_from, parquet("daily_totals"), start, end])
    if len(found) * len(names) > MAX_VALUES:
        raise ToolError(
            f"That is {len(found) * len(names)} values, more than {MAX_VALUES}. "
            "Use a coarser grain, fewer metrics or a shorter range."
        )
    dates, provisional, *values = zip(*found, strict=True)
    return Totals(
        grain=grain,
        dates=dates,
        values=dict(zip(names, values, strict=True)),
        provisional=provisional,
        approximate=tuple(name for name in names if grain != "day" and name in SESSION),
        restated_from=run.restated_from,
    )


@server.tool(annotations=READ_ONLY, description=EXPLAIN)
def metrics_explain(name: str) -> Definition:
    """Return the full definition of one metric."""
    found = definitions(load_run())
    for definition in found:
        if definition.name == name:
            return definition
    names = ", ".join(definition.name for definition in found)
    raise ToolError(f"That is not a metric. Use one of: {names}.")


@server.tool(annotations=READ_ONLY, description=FRESHNESS)
def metrics_freshness() -> Run:
    """Return the pipeline run the published metrics come from."""
    return load_run()


@server.tool(annotations=READ_ONLY, description=LIST)
def metrics_list() -> Metrics:
    """Return every metric with its label, unit and whether it is additive."""
    found = definitions(load_run())
    return Metrics(metrics=tuple(Metric(**dict(m)) for m in found))
