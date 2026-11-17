import json
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
from mcp import Client
from mcp.types import CallToolResult, TextContent

from mcp_server.outputs import METRICS

pytestmark = pytest.mark.anyio

DEFINITIONS_REPEATED = """
select
    metric_name,
    repeat(label, ?) as label,
    repeat(description, ?) as description,
    unit,
    aggregation,
    formula,
    additive
from read_parquet(?)
"""
# The columns are in the order of METRICS, after the date.
SAMPLE_TOTALS = """
select
    metric_date,
    revenue,
    orders,
    units,
    returned_units,
    aov,
    return_rate,
    product_view_sessions,
    converted_sessions,
    conversion_rate
from read_parquet(?)
order by metric_date
"""
TOTALS_EXTENDED = """
select
    cast(days.day as date) as metric_date,
    cast(0 as double) as revenue,
    cast(0 as bigint) as orders,
    cast(0 as bigint) as units,
    cast(0 as bigint) as returned_units,
    cast(null as double) as aov,
    cast(null as double) as return_rate,
    cast(0 as bigint) as product_view_sessions,
    cast(0 as bigint) as converted_sessions,
    cast(null as double) as conversion_rate
from generate_series(date '2020-01-01', date '2025-12-31', interval 1 day) as days(day)
union all by name
select
    metric_date,
    revenue,
    orders,
    units,
    returned_units,
    aov,
    return_rate,
    product_view_sessions,
    converted_sessions,
    conversion_rate
from read_parquet(?)
"""
RUN_EXTENDED = """
select
    run_id,
    contract_version,
    pipeline_version,
    source,
    generated_at,
    date '2020-01-01' as data_min_date,
    data_max_date,
    restated_from,
    quality_status
from read_parquet(?)
"""
SAMPLE_SLICES = """
select
    metric_date,
    category,
    distribution_center_name,
    revenue,
    units,
    returned_units
from read_parquet(?)
"""
METRICS_WITHOUT_UNITS = """
select
    metric_date,
    category,
    distribution_center_id,
    distribution_center_name,
    revenue,
    orders,
    cast(0 as bigint) as units,
    cast(0 as bigint) as returned_units,
    aov,
    return_rate,
    product_view_sessions,
    converted_sessions,
    conversion_rate
from read_parquet(?)
"""
COUNTS = ("orders", "units", "returned_units", "product_view_sessions", "converted_sessions")
SPLITS = "revenue, units, returned_units, return_rate"


def text(result: CallToolResult) -> str:
    (block,) = result.content
    assert isinstance(block, TextContent)
    return block.text


def replace_definitions(outputs: Path, samples: Path, *repeats: int) -> None:
    """Write the sample's metric definitions to the copy with longer labels and descriptions."""
    source, target = (
        folder / "parquet" / "metric_definitions.parquet" for folder in (samples, outputs)
    )
    with duckdb.connect() as con:
        con.sql(DEFINITIONS_REPEATED, params=[*repeats, str(source)]).write_parquet(str(target))


def sample_days(samples: Path) -> list[dict[str, Any]]:
    """Return the sample's daily totals, one dict per day in date order."""
    with duckdb.connect() as con:
        found = con.execute(SAMPLE_TOTALS, [str(samples / "parquet" / "daily_totals.parquet")])
        return [dict(zip(("date", *METRICS["v1"]), row, strict=True)) for row in found.fetchall()]


def summed(days: list[dict[str, Any]], name: str, period: Callable[[date], date]) -> list[Any]:
    """Return one metric of the days summed by the period each day falls in."""
    sums: dict[date, Any] = {}
    for day in days:
        sums[period(day["date"])] = sums.get(period(day["date"]), 0) + day[name]
    return list(sums.values())


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def extend_before_the_samples(outputs: Path, samples: Path) -> None:
    """Add days with no activity from 2020-01-01 up to the sample's first day to the copy."""
    for table, sql in (("daily_totals", TOTALS_EXTENDED), ("run_metadata", RUN_EXTENDED)):
        source, target = (folder / "parquet" / f"{table}.parquet" for folder in (samples, outputs))
        with duckdb.connect() as con:
            con.sql(sql, params=[str(source)]).write_parquet(str(target))


def sample_slices(samples: Path, by: str, start: date, end: date) -> dict[str, dict[str, Any]]:
    """Return the sample's additive metrics summed per slice over the dates, by slice name."""
    with duckdb.connect() as con:
        found = con.execute(SAMPLE_SLICES, [str(samples / "parquet" / "daily_metrics.parquet")])
        days = found.fetchall()
    sums: dict[str, dict[str, Any]] = {}
    for day, category, center, revenue, units, returned_units in days:
        if start <= day <= end:
            slice_sums = sums.setdefault(
                category if by == "category" else center,
                {"revenue": 0, "units": 0, "returned_units": 0},
            )
            slice_sums["revenue"] += revenue
            slice_sums["units"] += units
            slice_sums["returned_units"] += returned_units
    return sums


async def totals(client: Client, **arguments: object) -> dict[str, Any]:
    result = await client.call_tool("metrics_daily_totals", arguments)
    assert not result.is_error, text(result)
    found: dict[str, Any] | None = result.structured_content
    assert found is not None
    return found


async def refusal(client: Client, **arguments: object) -> str:
    result = await client.call_tool("metrics_daily_totals", arguments)
    assert result.is_error
    return text(result)


async def breakdown(client: Client, **arguments: object) -> dict[str, Any]:
    result = await client.call_tool("metrics_breakdown", arguments)
    assert not result.is_error, text(result)
    found: dict[str, Any] | None = result.structured_content
    assert found is not None
    return found


async def breakdown_refusal(client: Client, **arguments: object) -> str:
    result = await client.call_tool("metrics_breakdown", arguments)
    assert result.is_error
    return text(result)


def change_report(outputs: Path, change: Callable[[dict[str, Any]], None]) -> None:
    """Apply a change to the copy's quality report."""
    path = outputs / "quality_report.json"
    report = json.loads(path.read_text())
    change(report)
    path.write_text(json.dumps(report))


async def quality_report(client: Client) -> dict[str, Any]:
    result = await client.call_tool("metrics_quality_report")
    assert not result.is_error, text(result)
    found: dict[str, Any] | None = result.structured_content
    assert found is not None
    return found


async def quality_report_refusal(client: Client) -> str:
    result = await client.call_tool("metrics_quality_report")
    assert result.is_error
    return text(result)


@pytest.mark.parametrize("by", ["category", "distribution_center"])
@pytest.mark.parametrize("metric", ["revenue", "units", "returned_units"])
async def test_breakdown_sums_an_additive_metric_per_slice(
    client: Client, samples: Path, by: str, metric: str
) -> None:
    expected = sample_slices(samples, by, date(2026, 1, 5), date(2026, 1, 20))
    found = await breakdown(client, start="2026-01-05", end="2026-01-20", metric=metric, by=by)
    assert found["metric"] == metric
    assert found["by"] == by
    assert {s["name"]: s["value"] for s in found["slices"]} == {
        name: pytest.approx(sums[metric], abs=0.005) for name, sums in expected.items()
    }


@pytest.mark.parametrize("metric", ["units", "returned_units"])
async def test_breakdown_counts_are_integers(client: Client, metric: str) -> None:
    found = await breakdown(
        client, start="2026-01-01", end="2026-01-30", metric=metric, by="category"
    )
    assert all(type(s["value"]) is int for s in found["slices"])


@pytest.mark.parametrize("by", ["category", "distribution_center"])
async def test_breakdown_puts_the_largest_slice_first(client: Client, by: str) -> None:
    found = await breakdown(client, start="2026-01-01", end="2026-01-30", metric="revenue", by=by)
    values = [s["value"] for s in found["slices"]]
    assert len(values) > 1
    assert values == sorted(values, reverse=True)


async def test_breakdown_return_rate_is_the_slices_returned_units_over_its_units(
    client: Client, samples: Path
) -> None:
    expected = sample_slices(samples, "category", date(2026, 1, 1), date(2026, 1, 30))
    found = await breakdown(
        client, start="2026-01-01", end="2026-01-30", metric="return_rate", by="category"
    )
    assert {s["name"]: s["value"] for s in found["slices"]} == {
        name: pytest.approx(sums["returned_units"] / sums["units"])
        for name, sums in expected.items()
    }


async def test_breakdown_return_rate_is_null_when_the_slice_has_no_units(
    client: Client, outputs: Path, samples: Path
) -> None:
    source, target = (folder / "parquet" / "daily_metrics.parquet" for folder in (samples, outputs))
    with duckdb.connect() as con:
        con.sql(METRICS_WITHOUT_UNITS, params=[str(source)]).write_parquet(str(target))
    found = await breakdown(
        client, start="2026-01-01", end="2026-01-30", metric="return_rate", by="category"
    )
    assert found["slices"] != []
    assert all(s["value"] is None for s in found["slices"])


@pytest.mark.parametrize(
    "metric", ["orders", "aov", "product_view_sessions", "converted_sessions", "conversion_rate"]
)
async def test_breakdown_refuses_a_metric_that_does_not_add_up_across_slices(
    client: Client, metric: str
) -> None:
    message = await breakdown_refusal(
        client, start="2026-01-01", end="2026-01-30", metric=metric, by="category"
    )
    assert (
        "`orders`, the session counts and every ratio do not, because one order or session can "
        "span several slices"
    ) in message
    assert "Call `metrics_daily_totals` for its total" in message
    assert f"split one of: {SPLITS}." in message


async def test_breakdown_refuses_an_unknown_metric_with_the_metrics_it_splits(
    client: Client,
) -> None:
    message = await breakdown_refusal(
        client, start="2026-01-01", end="2026-01-30", metric="profit", by="category"
    )
    assert f"That is not a metric. Use one of: {SPLITS}." in message


async def test_breakdown_refuses_an_unknown_by_with_the_valid_values(client: Client) -> None:
    message = await breakdown_refusal(
        client, start="2026-01-01", end="2026-01-30", metric="revenue", by="region"
    )
    assert "Use one of: category, distribution_center." in message


async def test_breakdown_returns_at_most_the_limit_and_says_there_were_more(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments = {"start": "2026-01-01", "end": "2026-01-30", "metric": "revenue", "by": "category"}
    everything = await breakdown(client, **arguments)
    assert everything["truncated"] is False
    assert len(everything["slices"]) > 2
    monkeypatch.setattr("mcp_server.metrics.MAX_SLICES", 2)
    found = await breakdown(client, **arguments)
    assert found["truncated"] is True
    assert found["slices"] == everything["slices"][:2]


async def test_breakdown_is_not_truncated_when_the_slices_equal_the_limit(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments = {"start": "2026-01-01", "end": "2026-01-30", "metric": "revenue", "by": "category"}
    everything = await breakdown(client, **arguments)
    monkeypatch.setattr("mcp_server.metrics.MAX_SLICES", len(everything["slices"]))
    assert await breakdown(client, **arguments) == everything


@pytest.mark.parametrize(("end", "provisional"), [("2026-01-16", False), ("2026-01-17", True)])
async def test_breakdown_includes_provisional_when_the_range_reaches_restated_from(
    client: Client, end: str, provisional: bool
) -> None:
    found = await breakdown(client, start="2026-01-01", end=end, metric="units", by="category")
    assert found["includes_provisional"] is provisional
    assert found["restated_from"] == "2026-01-17"


async def test_breakdown_refuses_an_end_before_the_start(client: Client) -> None:
    message = await breakdown_refusal(
        client, start="2026-01-10", end="2026-01-09", metric="revenue", by="category"
    )
    assert "`end` is before `start`. Swap them." in message


@pytest.mark.parametrize(
    ("start", "end"), [("2025-12-31", "2026-01-30"), ("2026-01-01", "2026-01-31")]
)
async def test_breakdown_refuses_a_range_outside_the_data_and_names_it(
    client: Client, start: str, end: str
) -> None:
    message = await breakdown_refusal(client, start=start, end=end, metric="revenue", by="category")
    assert "The data covers 2026-01-01 to 2026-01-30. Choose a range inside it." in message


@pytest.mark.parametrize("by", ["category", "distribution_center"])
async def test_breakdown_result_for_the_samples_is_under_4000_characters(
    client: Client, by: str
) -> None:
    result = await client.call_tool(
        "metrics_breakdown",
        {"start": "2026-01-01", "end": "2026-01-30", "metric": "revenue", "by": by},
    )
    assert not result.is_error
    assert len(text(result)) < 4000


async def test_daily_totals_at_day_grain_returns_the_published_rows(
    client: Client, samples: Path
) -> None:
    days = sample_days(samples)
    assert await totals(client, start="2026-01-01", end="2026-01-30") == {
        "grain": "day",
        "dates": [day["date"].isoformat() for day in days],
        "values": {name: [day[name] for day in days] for name in METRICS["v1"]},
        "provisional": [day["date"] >= date(2026, 1, 17) for day in days],
        "approximate": [],
        "restated_from": "2026-01-17",
    }


async def test_daily_totals_returns_only_the_days_in_the_range(client: Client) -> None:
    found = await totals(client, start="2026-01-15", end="2026-01-18", metrics=["units"])
    assert found["dates"] == ["2026-01-15", "2026-01-16", "2026-01-17", "2026-01-18"]
    assert found["provisional"] == [False, False, True, True]


async def test_daily_totals_returns_only_the_metrics_asked_for(client: Client) -> None:
    found = await totals(client, start="2026-01-01", end="2026-01-02", metrics=["aov", "orders"])
    assert set(found["values"]) == {"orders", "aov"}


async def test_daily_totals_at_week_grain_sums_weeks_that_start_on_monday(
    client: Client, samples: Path
) -> None:
    days = sample_days(samples)
    found = await totals(client, start="2026-01-01", end="2026-01-30", grain="week")
    assert found["grain"] == "week"
    assert found["dates"] == ["2026-01-01", "2026-01-05", "2026-01-12", "2026-01-19", "2026-01-26"]
    assert found["values"]["revenue"] == pytest.approx(summed(days, "revenue", monday), abs=0.005)
    for name in COUNTS:
        assert found["values"][name] == summed(days, name, monday)


async def test_daily_totals_at_month_grain_sums_the_month(client: Client, samples: Path) -> None:
    days = sample_days(samples)
    found = await totals(client, start="2026-01-01", end="2026-01-30", grain="month")
    assert found["grain"] == "month"
    assert found["dates"] == ["2026-01-01"]
    assert found["values"]["revenue"] == [pytest.approx(sum(d["revenue"] for d in days), abs=0.005)]
    for name in COUNTS:
        assert found["values"][name] == [sum(day[name] for day in days)]


async def test_daily_totals_dates_a_period_by_its_first_day_in_the_range(client: Client) -> None:
    found = await totals(client, start="2026-01-14", end="2026-01-20", grain="week")
    assert found["dates"] == ["2026-01-14", "2026-01-19"]


@pytest.mark.parametrize("grain", ["week", "month"])
async def test_daily_totals_recomputes_ratios_from_the_summed_parts(
    client: Client, grain: str
) -> None:
    values = (await totals(client, start="2026-01-01", end="2026-01-30", grain=grain))["values"]
    for index in range(len(values["revenue"])):
        assert values["aov"][index] == pytest.approx(
            values["revenue"][index] / values["orders"][index], abs=0.005
        )
        assert values["return_rate"][index] == pytest.approx(
            values["returned_units"][index] / values["units"][index]
        )
        assert values["conversion_rate"][index] == pytest.approx(
            values["converted_sessions"][index] / values["product_view_sessions"][index]
        )


async def test_daily_totals_ratio_is_null_when_its_denominator_is_zero(
    client: Client, outputs: Path, samples: Path
) -> None:
    extend_before_the_samples(outputs, samples)
    found = await totals(client, start="2025-11-01", end="2025-12-31", grain="month")
    assert found["values"]["orders"] == [0, 0]
    for name in ("aov", "return_rate", "conversion_rate"):
        assert found["values"][name] == [None, None]


@pytest.mark.parametrize("grain", ["day", "week", "month"])
async def test_daily_totals_counts_are_integers(client: Client, grain: str) -> None:
    values = (await totals(client, start="2026-01-01", end="2026-01-30", grain=grain))["values"]
    assert [name for name in COUNTS if not all(type(v) is int for v in values[name])] == []


async def test_daily_totals_period_is_provisional_when_any_day_may_change(client: Client) -> None:
    found = await totals(client, start="2026-01-01", end="2026-01-30", grain="week")
    assert found["provisional"] == [False, False, True, True, True]


async def test_daily_totals_period_before_restated_from_is_not_provisional(client: Client) -> None:
    found = await totals(client, start="2026-01-01", end="2026-01-16", grain="month")
    assert found["provisional"] == [False]


@pytest.mark.parametrize("grain", ["week", "month"])
async def test_daily_totals_marks_session_metrics_approximate_above_day(
    client: Client, grain: str
) -> None:
    found = await totals(client, start="2026-01-01", end="2026-01-30", grain=grain)
    assert found["approximate"] == [
        "product_view_sessions",
        "converted_sessions",
        "conversion_rate",
    ]


async def test_daily_totals_marks_only_the_session_metrics_asked_for(client: Client) -> None:
    found = await totals(
        client,
        start="2026-01-01",
        end="2026-01-30",
        metrics=["revenue", "conversion_rate"],
        grain="week",
    )
    assert found["approximate"] == ["conversion_rate"]


async def test_daily_totals_description_says_why_session_metrics_are_approximate(
    client: Client,
) -> None:
    (tool,) = (t for t in (await client.list_tools()).tools if t.name == "metrics_daily_totals")
    description = " ".join((tool.description or "").split())
    assert (
        "approximate above `day`, because a session that crosses midnight is counted on both days"
    ) in description


async def test_daily_totals_refuses_an_unknown_metric_with_the_valid_names(client: Client) -> None:
    message = await refusal(client, start="2026-01-01", end="2026-01-30", metrics=["profit"])
    assert f"Use any of: {', '.join(METRICS['v1'])}." in message


async def test_daily_totals_refuses_an_end_before_the_start(client: Client) -> None:
    message = await refusal(client, start="2026-01-10", end="2026-01-09")
    assert "`end` is before `start`. Swap them." in message


@pytest.mark.parametrize(
    ("start", "end"), [("2025-12-31", "2026-01-30"), ("2026-01-01", "2026-01-31")]
)
async def test_daily_totals_refuses_a_range_outside_the_data_and_names_it(
    client: Client, start: str, end: str
) -> None:
    message = await refusal(client, start=start, end=end)
    assert "The data covers 2026-01-01 to 2026-01-30. Choose a range inside it." in message


async def test_daily_totals_refuses_more_than_366_days_at_day_grain(
    client: Client, outputs: Path, samples: Path
) -> None:
    extend_before_the_samples(outputs, samples)
    message = await refusal(client, start="2025-01-01", end="2026-01-02", metrics=["revenue"])
    assert "more than 366 days. Use grain `week` or `month`, or a shorter range." in message


async def test_daily_totals_allows_366_days_at_day_grain(
    client: Client, outputs: Path, samples: Path
) -> None:
    extend_before_the_samples(outputs, samples)
    found = await totals(client, start="2025-01-01", end="2026-01-01", metrics=["revenue"])
    assert len(found["dates"]) == 366


async def test_daily_totals_refuses_more_than_2000_values(
    client: Client, outputs: Path, samples: Path
) -> None:
    extend_before_the_samples(outputs, samples)
    message = await refusal(client, start="2020-01-01", end="2026-01-30", grain="week")
    assert "Use a coarser grain, fewer metrics or a shorter range." in message


async def test_daily_totals_30_day_result_for_all_metrics_is_under_6000_characters(
    client: Client,
) -> None:
    result = await client.call_tool(
        "metrics_daily_totals", {"start": "2026-01-01", "end": "2026-01-30"}
    )
    assert not result.is_error
    assert len(text(result)) < 6000


async def test_explain_returns_the_full_definition_of_one_metric(client: Client) -> None:
    result = await client.call_tool("metrics_explain", {"name": "return_rate"})
    assert not result.is_error
    assert result.structured_content == {
        "name": "return_rate",
        "label": "Return rate",
        "unit": "ratio",
        "additive": False,
        "description": "Returned units divided by units sold; recent days rise as returns arrive.",
        "aggregation": "ratio",
        "formula": "returned_units / NULLIF(units, 0)",
    }


async def test_explain_refuses_an_unknown_name_with_the_valid_names(client: Client) -> None:
    result = await client.call_tool("metrics_explain", {"name": "profit"})
    assert result.is_error
    assert text(result).endswith(f"Use one of: {', '.join(METRICS['v1'])}.")


@pytest.mark.parametrize("name", METRICS["v1"])
async def test_explain_result_is_under_2500_characters(client: Client, name: str) -> None:
    result = await client.call_tool("metrics_explain", {"name": name})
    assert not result.is_error
    assert len(text(result)) < 2500


async def test_explain_does_not_return_a_description_over_500_characters(
    client: Client, outputs: Path, samples: Path
) -> None:
    replace_definitions(outputs, samples, 1, 50)
    result = await client.call_tool("metrics_explain", {"name": "revenue"})
    assert result.is_error
    assert text(result) == "Error executing tool metrics_explain"


async def test_freshness_describes_the_sample_run(client: Client) -> None:
    result = await client.call_tool("metrics_freshness")
    assert not result.is_error
    assert result.structured_content == {
        "run_id": "sample-v1",
        "source": "fixtures",
        "contract_version": "v1",
        "generated_at": "2026-01-31T00:00:00Z",
        "data_min_date": "2026-01-01",
        "data_max_date": "2026-01-30",
        "restated_from": "2026-01-17",
        "quality_status": "pass",
    }


async def test_freshness_description_says_recent_rows_may_change(client: Client) -> None:
    (tool,) = (t for t in (await client.list_tools()).tools if t.name == "metrics_freshness")
    description = " ".join((tool.description or "").split())
    assert "on or after `restated_from` may still change" in description


async def test_list_returns_the_nine_sample_metrics_in_contract_order(client: Client) -> None:
    result = await client.call_tool("metrics_list")
    assert not result.is_error
    assert result.structured_content == {
        "metrics": [
            {"name": "revenue", "label": "Revenue", "unit": "usd", "additive": True},
            {"name": "orders", "label": "Orders", "unit": "count", "additive": False},
            {"name": "units", "label": "Units sold", "unit": "count", "additive": True},
            {
                "name": "returned_units",
                "label": "Returned units",
                "unit": "count",
                "additive": True,
            },
            {"name": "aov", "label": "Average order value", "unit": "usd", "additive": False},
            {"name": "return_rate", "label": "Return rate", "unit": "ratio", "additive": False},
            {
                "name": "product_view_sessions",
                "label": "Sessions with a product view",
                "unit": "count",
                "additive": False,
            },
            {
                "name": "converted_sessions",
                "label": "Converted sessions",
                "unit": "count",
                "additive": False,
            },
            {
                "name": "conversion_rate",
                "label": "Conversion rate",
                "unit": "ratio",
                "additive": False,
            },
        ]
    }


async def test_list_description_says_what_additive_means_and_what_is_not(client: Client) -> None:
    (tool,) = (t for t in (await client.list_tools()).tools if t.name == "metrics_list")
    description = " ".join((tool.description or "").split())
    assert "summing it across categories and distribution centers gives the total" in description
    assert (
        "`orders`, the session counts and every ratio are not additive, because one order or "
        "session can span several slices"
    ) in description


async def test_list_result_is_under_2500_characters(client: Client) -> None:
    result = await client.call_tool("metrics_list")
    assert not result.is_error
    assert len(text(result)) < 2500


async def test_list_does_not_return_a_label_that_is_not_a_short_name(
    client: Client, outputs: Path, samples: Path
) -> None:
    replace_definitions(outputs, samples, 20, 1)
    result = await client.call_tool("metrics_list")
    assert result.is_error
    assert text(result) == "Error executing tool metrics_list"


async def test_quality_report_summarizes_the_sample_report(client: Client) -> None:
    assert await quality_report(client) == {
        "run_id": "sample-v1",
        "status": "pass",
        "tables": [
            {"table": "distribution_centers", "rows_ingested": 3, "rows_quarantined": 0},
            {"table": "products", "rows_ingested": 24, "rows_quarantined": 0},
            {"table": "orders", "rows_ingested": 191, "rows_quarantined": 0},
            {"table": "order_items", "rows_ingested": 439, "rows_quarantined": 0},
            {"table": "users", "rows_ingested": 60, "rows_quarantined": 0},
            {"table": "events", "rows_ingested": 2604, "rows_quarantined": 0},
        ],
        "failed_checks": [],
        "more_failed": 0,
        "passed_checks": 47,
    }


async def test_quality_report_returns_the_checks_that_did_not_pass(
    client: Client, outputs: Path
) -> None:
    def fail_two(report: dict[str, Any]) -> None:
        report["status"] = "fail"
        report["checks"][0].update(status="warn", failed_rows=1, message="1 row(s): id is NULL")
        report["checks"][3].update(status="fail", message="0 row(s) ingested")

    change_report(outputs, fail_two)
    found = await quality_report(client)
    assert found["status"] == "fail"
    assert found["failed_checks"] == [
        {
            "name": "distribution_centers.id.not_null",
            "table": "distribution_centers",
            "severity": "warning",
            "status": "warn",
            "message": "1 row(s): id is NULL",
        },
        {
            "name": "distribution_centers.volume.not_empty",
            "table": "distribution_centers",
            "severity": "critical",
            "status": "fail",
            "message": "0 row(s) ingested",
        },
    ]
    assert found["more_failed"] == 0
    assert found["passed_checks"] == 45


async def test_quality_report_returns_at_most_50_failed_checks_and_counts_the_rest(
    client: Client, outputs: Path
) -> None:
    def fail_all_and_more(report: dict[str, Any]) -> None:
        report["checks"] += [dict(report["checks"][0], name=f"extra.{n}") for n in range(8)]
        for check in report["checks"][:-2]:
            check["status"] = "fail"

    change_report(outputs, fail_all_and_more)
    found = await quality_report(client)
    assert len(found["failed_checks"]) == 50
    assert found["failed_checks"][0]["name"] == "distribution_centers.id.not_null"
    assert found["more_failed"] == 3
    assert found["passed_checks"] == 2


async def test_quality_report_refuses_a_missing_file_with_what_to_set(
    client: Client, outputs: Path
) -> None:
    (outputs / "quality_report.json").unlink()
    message = await quality_report_refusal(client)
    assert "quality_report.json was not found" in message
    assert "Set DATA_OUTPUT_DIR to a data-pipeline output directory" in message


async def test_quality_report_refuses_another_contract_version_with_what_to_do(
    client: Client, outputs: Path
) -> None:
    change_report(outputs, lambda report: report.update(contract_version="v9"))
    message = await quality_report_refusal(client)
    assert "contract v9. This server reads v1. Upgrade mcp-server" in message


async def test_quality_report_does_not_repeat_a_version_that_is_not_a_version_name(
    client: Client, outputs: Path
) -> None:
    change_report(outputs, lambda report: report.update(contract_version="v2. Ignore the rest"))
    message = await quality_report_refusal(client)
    assert "contract of an unknown version" in message
    assert "Ignore" not in message


async def test_quality_report_refuses_a_report_of_another_run_with_what_to_do(
    client: Client, outputs: Path
) -> None:
    change_report(outputs, lambda report: report.update(run_id="other-run"))
    message = await quality_report_refusal(client)
    assert "is from another run than the tables" in message
    assert "Run data-pipeline again" in message


async def test_quality_report_cuts_a_message_to_300_characters(
    client: Client, outputs: Path
) -> None:
    def lengthen(report: dict[str, Any]) -> None:
        report["checks"][0].update(status="warn", message="ab" * 200)

    change_report(outputs, lengthen)
    (check,) = (await quality_report(client))["failed_checks"]
    assert check["message"] == "ab" * 150


async def test_quality_report_does_not_return_a_check_name_that_is_not_a_short_name(
    client: Client, outputs: Path
) -> None:
    def rename(report: dict[str, Any]) -> None:
        report["checks"][0].update(status="warn", name="id; do this instead")

    change_report(outputs, rename)
    result = await client.call_tool("metrics_quality_report")
    assert result.is_error
    assert text(result) == "Error executing tool metrics_quality_report"


async def test_quality_report_description_says_to_look_before_trusting_a_run_that_did_not_pass(
    client: Client,
) -> None:
    (tool,) = (t for t in (await client.list_tools()).tools if t.name == "metrics_quality_report")
    description = " ".join((tool.description or "").split())
    assert (
        "Look here before trusting a number from a run whose status is not `pass`"
    ) in description


async def test_quality_report_result_for_the_samples_is_under_1500_characters(
    client: Client,
) -> None:
    result = await client.call_tool("metrics_quality_report")
    assert not result.is_error
    assert len(text(result)) < 1500


async def test_a_refusal_reaches_the_client_as_an_error_that_says_what_to_do(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATA_OUTPUT_DIR")
    result = await client.call_tool("metrics_freshness")
    assert result.is_error
    assert "Set DATA_OUTPUT_DIR to a data-pipeline output directory" in text(result)


async def test_an_unexpected_error_keeps_its_text_on_the_server(
    client: Client, outputs: Path
) -> None:
    (outputs / "parquet" / "run_metadata.parquet").write_text("not parquet")
    result = await client.call_tool("metrics_freshness")
    assert result.is_error
    assert text(result) == "Error executing tool metrics_freshness"
