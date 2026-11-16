from pathlib import Path

import pytest
from mcp import Client
from mcp.types import CallToolResult, TextContent

pytestmark = pytest.mark.anyio


def text(result: CallToolResult) -> str:
    (block,) = result.content
    assert isinstance(block, TextContent)
    return block.text


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
