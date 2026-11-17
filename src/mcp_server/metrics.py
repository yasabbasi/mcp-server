"""The metrics tool group: tools over the published metric tables."""

from datetime import date
from typing import Annotated, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, StringConstraints

from mcp_server.outputs import (
    METRICS,
    Check,
    Name,
    Run,
    TableRows,
    load_quality_report,
    load_run,
    parquet,
    quoted,
    rows,
)
from mcp_server.server import READ_ONLY, server

ROUNDED = "Ratios are fractions between 0 and 1, rounded to four places."
BREAKDOWN = (
    "One metric split by `by`, `category` or `distribution_center`, over `start` to `end` (ISO "
    "dates, both included). `slices` holds each slice's `name` and `value`, largest first; at "
    "most 50 are returned, and `truncated` says whether there were more. Only `revenue`, `units`, "
    "`returned_units` and `return_rate` can be split: the first three are summed per slice, and "
    "`return_rate` is the slice's returned units over its units, null when it has no units. "
    f"{ROUNDED} The "
    "other metrics do not add up across slices, so call `metrics_daily_totals` for them. "
    "`includes_provisional` is true when the range reaches `restated_from`, so the numbers may "
    "still change. Call `metrics_freshness` for the dates the data covers."
)
DAILY_TOTALS = (
    "The published totals from `start` to `end` (ISO dates, both included) by `grain`: `day`, "
    "`week` (starting Monday) or `month`. It returns columns: `dates` holds the first day of each "
    "period that falls in the range, and `values` holds one list per metric, lined up with "
    "`dates`. `metrics` picks the metrics; leave it out for all of them. At `week` and `month`, "
    "counts and money are summed and `aov`, `return_rate` and `conversion_rate` are recomputed "
    f"from the summed parts; a ratio is null when its denominator is 0. {ROUNDED} The session "
    "metrics, named "
    "in `approximate`, are approximate above `day`, because a session that crosses midnight is "
    "counted on both days. A period is `provisional` when any of its days is on or after "
    "`restated_from`, so its numbers may still change. Call `metrics_freshness` for the dates "
    "the data covers and `metrics_list` for the metric names."
)
EXPLAIN = (
    "The full definition of one metric: its `name`, display `label`, `unit` (`usd`, `count` or "
    "`ratio`), whether it is `additive`, a plain-language `description`, its `aggregation` "
    f"(`sum`, `count`, `count_distinct` or `ratio`) and its `formula`. {ROUNDED} Call it before "
    "explaining "
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
    f"{ROUNDED} Call `metrics_explain` for one metric's full definition."
)
QUALITY_REPORT = (
    "What the pipeline checked on the run the metrics come from: the run's `status` (`pass`, "
    "`warn` or `fail`), the `rows_ingested` and `rows_quarantined` of each source table in "
    "`tables`, and the checks that did not pass in `failed_checks`, each with its `name`, "
    "`table`, `severity`, `status` and `message`. At most 50 are returned: `more_failed` counts "
    "the ones left out, and `passed_checks` counts the checks that passed. Look here before "
    "trusting a number from a run whose status is not `pass`; `metrics_freshness` gives that "
    "status as `quality_status`."
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
# The placeholders take a column of SLICES and the summed SQL of a metric of SPLITS.
SLICES_SQL = """
select
    {name} as name,
    {value} as value
from read_parquet(?)
where metric_date between ? and ?
group by {name}
order by
    value desc nulls last,
    name
limit ?
"""
SLICES = {"category": "category", "distribution_center": "distribution_center_name"}
# The metrics that can be split by slice: the additive ones and the ratio of two of them.
SPLITS = {"v1": ("revenue", "units", "returned_units", "return_rate")}
# Why the other metrics can't be split, in the contract's words.
NOT_ADDITIVE = {
    "v1": (
        "`revenue`, `units` and `returned_units` sum across categories and distribution centers. "
        "`orders`, the session counts and every ratio do not, because one order or session can "
        "span several slices."
    ),
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
MAX_FAILED = 50
MAX_SLICES = 50
MAX_VALUES = 2000
RATIO_PLACES = 4

Grain = Literal["day", "week", "month"]
# These reach Claude as written, so a data file can't put a long string in them.
Label = Annotated[str, StringConstraints(pattern=r"^[\w .-]{1,64}$")]
SliceName = Annotated[str, StringConstraints(max_length=64)]
Text = Annotated[str, StringConstraints(max_length=500)]


class Slice(BaseModel):
    """One category or distribution center with its value of the metric."""

    model_config = ConfigDict(frozen=True)

    name: SliceName
    value: int | float | None


class Breakdown(BaseModel):
    """One metric of a date range split into slices, largest first."""

    model_config = ConfigDict(frozen=True)

    metric: Name
    by: Name
    slices: tuple[Slice, ...]
    truncated: bool
    includes_provisional: bool
    restated_from: date


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


class QualityReport(BaseModel):
    """The run's quality report, with only the checks that did not pass."""

    model_config = ConfigDict(frozen=True)

    run_id: Name
    status: Name
    tables: tuple[TableRows, ...]
    failed_checks: tuple[Check, ...]
    more_failed: int
    passed_checks: int


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


def summed(name: str) -> str:
    """Return the SQL that gives one metric over a group of rows from the summed parts."""
    if name in RATIOS:
        over, under = (f"sum({quoted(part)})" for part in RATIOS[name])
        return f"round({over} / nullif({under}, 0), {2 if name in MONEY else RATIO_PLACES})"
    return f"round(sum({quoted(name)}), 2)" if name in MONEY else f"sum({quoted(name)})"


def total(name: str, grain: Grain) -> str:
    """Return the SQL that gives one metric's total for a period of the grain."""
    if grain != "day":
        return summed(name)
    published = f"any_value({quoted(name)})"
    if name in RATIOS and name not in MONEY:
        return f"round({published}, {RATIO_PLACES})"
    return published


def check_range(run: Run, start: date, end: date) -> None:
    """Refuse a date range that is backwards or outside the run's data."""
    if end < start:
        raise ToolError("`end` is before `start`. Swap them.")
    if start < run.data_min_date or end > run.data_max_date:
        raise ToolError(
            f"The data covers {run.data_min_date} to {run.data_max_date}. Choose a range inside it."
        )


@server.tool(annotations=READ_ONLY, description=BREAKDOWN)
def metrics_breakdown(start: date, end: date, metric: str, by: str) -> Breakdown:
    """Return one metric per category or distribution center over a date range."""
    run = load_run()
    splits = SPLITS[run.contract_version]
    if metric not in METRICS[run.contract_version]:
        raise ToolError(f"That is not a metric. Use one of: {', '.join(splits)}.")
    if metric not in splits:
        raise ToolError(
            f"`{metric}` can't be split. {NOT_ADDITIVE[run.contract_version]} Call "
            f"`metrics_daily_totals` for its total, or split one of: {', '.join(splits)}."
        )
    if by not in SLICES:
        raise ToolError(f"`by` is not a way to split. Use one of: {', '.join(SLICES)}.")
    check_range(run, start, end)
    sql = SLICES_SQL.format(name=quoted(SLICES[by]), value=summed(metric))
    found = rows(sql, [parquet("daily_metrics"), start, end, MAX_SLICES + 1])
    return Breakdown(
        metric=metric,
        by=by,
        slices=tuple(Slice(name=name, value=value) for name, value in found[:MAX_SLICES]),
        truncated=len(found) > MAX_SLICES,
        includes_provisional=end >= run.restated_from,
        restated_from=run.restated_from,
    )


@server.tool(annotations=READ_ONLY, description=DAILY_TOTALS)
def metrics_daily_totals(
    start: date, end: date, metrics: list[str] | None = None, grain: Grain = "day"
) -> Totals:
    """Return the published totals for a date range by day, week or month."""
    run = load_run()
    order = METRICS[run.contract_version]
    if metrics is not None and not set(metrics) <= set(order):
        raise ToolError(f"That is not a metric. Use any of: {', '.join(order)}.")
    check_range(run, start, end)
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


@server.tool(annotations=READ_ONLY, description=QUALITY_REPORT)
def metrics_quality_report() -> QualityReport:
    """Return the run's quality status, rows per source table and checks that did not pass."""
    report = load_quality_report(load_run())
    failed = [check for check in report.checks if check.status != "pass"]
    return QualityReport(
        run_id=report.run_id,
        status=report.status,
        tables=report.tables,
        failed_checks=tuple(failed[:MAX_FAILED]),
        more_failed=max(len(failed) - MAX_FAILED, 0),
        passed_checks=len(report.checks) - len(failed),
    )
