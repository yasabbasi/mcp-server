"""Read data-pipeline's published outputs: the Parquet tables and the run that made them."""

import glob
import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Annotated, Any

import duckdb
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

SUPPORTED = ("v1",)
MAX_MESSAGE = 300
TABLES = ("daily_metrics", "daily_totals", "metric_definitions", "run_metadata")
METRICS: dict[str, tuple[str, ...]] = {
    "v1": (
        "revenue",
        "orders",
        "units",
        "returned_units",
        "aov",
        "return_rate",
        "product_view_sessions",
        "converted_sessions",
        "conversion_rate",
    ),
}
ENV = "DATA_OUTPUT_DIR"
SET_ENV = f"Set {ENV} to a data-pipeline output directory."

VERSION_SQL = """
select contract_version
from read_parquet(?)
"""
# The columns are in the order of Run's fields. generated_at is formatted here because
# fetching TIMESTAMPTZ into Python needs pytz.
RUN_SQL = """
select
    run_id,
    source,
    contract_version,
    strftime(generated_at at time zone 'UTC', '%Y-%m-%dT%H:%M:%SZ') as generated_at,
    data_min_date,
    data_max_date,
    restated_from,
    quality_status
from read_parquet(?)
"""
METRIC_NAMES_SQL = """
select metric_name
from read_parquet(?)
order by metric_name
"""


# These reach Claude as written, so a data file can't put free text or a long string in them.
Name = Annotated[str, StringConstraints(pattern=r"^[\w.-]{1,64}$")]


class Run(BaseModel):
    """The pipeline run that published the outputs."""

    model_config = ConfigDict(frozen=True)

    run_id: Name
    source: Name
    contract_version: Name
    generated_at: str
    data_min_date: date
    data_max_date: date
    restated_from: date
    quality_status: Name


class TableRows(BaseModel):
    """The rows one source table gave the run."""

    model_config = ConfigDict(frozen=True)

    table: Name
    rows_ingested: int
    rows_quarantined: int


class Check(BaseModel):
    """One quality check of the run."""

    model_config = ConfigDict(frozen=True)

    name: Name
    table: Name
    severity: Name
    status: Name
    message: str

    @field_validator("message")
    @classmethod
    def cut(cls, message: str) -> str:
        """Cut the message to MAX_MESSAGE characters."""
        return message[:MAX_MESSAGE]


class Report(BaseModel):
    """The run's quality report."""

    model_config = ConfigDict(frozen=True)

    run_id: Name
    status: Name
    tables: tuple[TableRows, ...]
    checks: tuple[Check, ...]


def output_dir() -> Path:
    """Return the directory data-pipeline published to, named by DATA_OUTPUT_DIR."""
    value = os.environ.get(ENV)
    if not value:
        raise ToolError(f"{ENV} is not set. {SET_ENV}")
    return Path(value).expanduser()


def parquet(table: str) -> str:
    """Return the path of one contract table's Parquet file, which must exist."""
    if table not in TABLES:
        raise ValueError(f"{table} is not a contract table")
    path = output_dir() / "parquet" / f"{table}.parquet"
    if not path.is_file():
        raise ToolError(f"{path} was not found. {SET_ENV}")
    return glob.escape(str(path))  # read_parquet reads its argument as a glob


def rows(sql: str, params: list[object]) -> list[tuple[Any, ...]]:
    """Run parameterized SQL in a fresh in-memory DuckDB and return every row."""
    with duckdb.connect() as con:
        return con.execute(sql, params).fetchall()


def quoted(name: str) -> str:
    """Return a double-quoted SQL identifier. Give it only names from constants in this repo."""
    return '"' + name.replace('"', '""') + '"'


def unsupported(subject: str, version: object) -> ToolError:
    """Return the refusal of a contract version, naming it only when it is a version name."""
    named = isinstance(version, str) and re.fullmatch(r"v\d{1,3}", version)
    return ToolError(
        f"{subject} contract {version if named else 'of an unknown version'}. "
        f"This server reads {', '.join(SUPPORTED)}. Upgrade mcp-server to read them."
    )


def load_quality_report(run: Run) -> Report:
    """Return the run's quality report, refusing one this server can't read or of another run."""
    path = output_dir() / "quality_report.json"
    if not path.is_file():
        raise ToolError(f"{path} was not found. {SET_ENV}")
    found = json.loads(path.read_text())
    if found.get("contract_version") not in SUPPORTED:
        raise unsupported("The quality report uses", found.get("contract_version"))
    if found.get("run_id") != run.run_id:
        raise ToolError(
            "The quality report is from another run than the tables. Run data-pipeline again, "
            "so it publishes them as a set."
        )
    return Report(**found)


def load_run() -> Run:
    """Return the published run, refusing outputs this server can't read."""
    path = parquet("run_metadata")
    versions = [str(version) for (version,) in rows(VERSION_SQL, [path])]
    if len(versions) != 1 or versions[0] not in SUPPORTED:
        raise unsupported("The outputs use", versions[0] if len(versions) == 1 else None)
    (row,) = rows(RUN_SQL, [path])
    run = Run(**dict(zip(Run.model_fields, row, strict=True)))
    names = [name for (name,) in rows(METRIC_NAMES_SQL, [parquet("metric_definitions")])]
    if names != sorted(METRICS[run.contract_version]):
        raise ToolError(
            f"The outputs do not match contract {run.contract_version}: their metric names "
            f"differ from the contract's. {SET_ENV}"
        )
    return run
