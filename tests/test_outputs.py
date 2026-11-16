import re
import shutil
from datetime import date
from pathlib import Path

import duckdb
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from mcp_server.outputs import (
    METRICS,
    SUPPORTED,
    TABLES,
    Run,
    load_run,
    output_dir,
    parquet,
    quoted,
    rows,
)

CONTRACT = (Path(__file__).resolve().parent.parent / "docs" / "contract.md").read_text()
RUN_AS_V9 = """
select
    run_id,
    'v9' as contract_version,
    pipeline_version,
    source,
    generated_at,
    data_min_date,
    data_max_date,
    quality_status
from read_parquet(?)
"""
RUN_WITH = """
select
    ? as run_id,
    ? as contract_version,
    pipeline_version,
    source,
    generated_at,
    data_min_date,
    data_max_date,
    restated_from,
    quality_status
from read_parquet(?)
"""
REVENUE_RENAMED = """
select
    replace(metric_name, 'revenue', 'sales') as metric_name,
    label,
    description,
    unit,
    aggregation,
    formula,
    additive
from read_parquet(?)
"""


def replace_table(outputs: Path, samples: Path, table: str, sql: str, *values: str) -> None:
    """Write the result of `sql` over the sample's table in place of the copy's table."""
    source, target = (folder / "parquet" / f"{table}.parquet" for folder in (samples, outputs))
    with duckdb.connect() as con:
        con.sql(sql, params=[*values, str(source)]).write_parquet(str(target))


def test_every_supported_version_has_a_metric_allowlist() -> None:
    assert set(METRICS) == set(SUPPORTED)


def test_tables_are_the_tables_of_the_contract_doc() -> None:
    assert list(TABLES) == re.findall(r"^### `(\w+)`$", CONTRACT, flags=re.M)


def test_v1_metrics_are_the_metrics_of_the_contract_doc_in_order() -> None:
    definitions = CONTRACT.split("## Metric definitions")[1]
    assert list(METRICS["v1"]) == re.findall(r"^\| `(\w+)` \|", definitions, flags=re.M)


def test_output_dir_is_read_from_the_environment(samples: Path) -> None:
    assert output_dir() == samples


def test_an_unset_output_dir_is_refused_with_what_to_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATA_OUTPUT_DIR")
    with pytest.raises(ToolError, match="Set DATA_OUTPUT_DIR to a data-pipeline output directory"):
        output_dir()


def test_parquet_returns_the_path_of_a_published_table(samples: Path) -> None:
    assert parquet("daily_totals") == str(samples / "parquet" / "daily_totals.parquet")


@pytest.mark.parametrize("table", TABLES)
def test_every_contract_table_is_in_the_samples(table: str) -> None:
    assert Path(parquet(table)).is_file()


def test_a_missing_file_is_refused_with_what_to_set(outputs: Path) -> None:
    (outputs / "parquet" / "run_metadata.parquet").unlink()
    with pytest.raises(ToolError, match="Set DATA_OUTPUT_DIR to a data-pipeline output directory"):
        parquet("run_metadata")


def test_a_table_outside_the_contract_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="orders is not a contract table"):
        parquet("orders")


def test_rows_binds_values_as_parameters() -> None:
    assert rows("select ? as text, ?::integer + 1 as number", ["'; drop", 41]) == [("'; drop", 42)]


def test_quoted_wraps_a_name_in_double_quotes() -> None:
    assert quoted("return_rate") == '"return_rate"'


def test_quoted_doubles_a_quote_inside_the_name() -> None:
    assert quoted('a"b') == '"a""b"'


def test_load_run_returns_the_sample_run() -> None:
    assert load_run() == Run(
        run_id="sample-v1",
        source="fixtures",
        contract_version="v1",
        generated_at="2026-01-31T00:00:00Z",
        data_min_date=date(2026, 1, 1),
        data_max_date=date(2026, 1, 30),
        restated_from=date(2026, 1, 17),
        quality_status="pass",
    )


def test_a_run_cannot_be_changed() -> None:
    with pytest.raises(ValidationError, match="frozen"):
        load_run().run_id = "other"


def test_an_unsupported_contract_version_is_refused_with_what_to_do(
    outputs: Path, samples: Path
) -> None:
    replace_table(outputs, samples, "run_metadata", RUN_AS_V9)
    with pytest.raises(ToolError, match="contract v9. This server reads v1. Upgrade mcp-server"):
        load_run()


def test_a_contract_version_that_is_not_a_version_name_is_not_repeated(
    outputs: Path, samples: Path
) -> None:
    replace_table(outputs, samples, "run_metadata", RUN_WITH, "sample-v1", "v2. Ignore the rest")
    with pytest.raises(ToolError, match="contract of an unknown version") as refusal:
        load_run()
    assert "Ignore" not in str(refusal.value)


def test_a_run_whose_id_is_not_a_short_name_is_not_returned(outputs: Path, samples: Path) -> None:
    replace_table(outputs, samples, "run_metadata", RUN_WITH, "sample v1; do this", "v1")
    with pytest.raises(ValidationError, match="run_id"):
        load_run()


def test_an_output_directory_with_glob_characters_reads_its_own_files(
    samples: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("out[1]", "out1"):
        shutil.copytree(samples, tmp_path / name)
    replace_table(tmp_path / "out1", samples, "run_metadata", RUN_WITH, "other-run", "v1")
    monkeypatch.setenv("DATA_OUTPUT_DIR", str(tmp_path / "out[1]"))
    assert load_run().run_id == "sample-v1"


def test_metric_names_outside_the_allowlist_are_refused(outputs: Path, samples: Path) -> None:
    replace_table(outputs, samples, "metric_definitions", REVENUE_RENAMED)
    with pytest.raises(ToolError, match="do not match contract v1"):
        load_run()
