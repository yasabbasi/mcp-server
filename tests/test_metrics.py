from pathlib import Path

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
