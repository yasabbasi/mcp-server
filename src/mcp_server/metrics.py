"""The metrics tool group: tools over the published metric tables."""

from typing import Annotated, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, StringConstraints

from mcp_server.outputs import METRICS, Name, Run, load_run, parquet, rows
from mcp_server.server import READ_ONLY, server

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


def definitions(run: Run) -> list[Definition]:
    """Return the run's metric definitions in contract order."""
    order = METRICS[run.contract_version]
    found = [
        Definition(**dict(zip(Definition.model_fields, row, strict=True)))
        for row in rows(DEFINITIONS_SQL, [parquet("metric_definitions")])
    ]
    return sorted(found, key=lambda definition: order.index(definition.name))


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
