"""The metrics tool group: tools over the published metric tables."""

from mcp_server.outputs import Run, load_run
from mcp_server.server import READ_ONLY, server

FRESHNESS = (
    "Which pipeline run the metrics come from: its id, source, contract version, data range "
    "(`data_min_date` to `data_max_date`), publish time (`generated_at`, UTC) and quality status. "
    "Call it to learn what dates the data covers and how fresh it is. Rows dated on or after "
    "`restated_from` may still change on later runs."
)


@server.tool(annotations=READ_ONLY, description=FRESHNESS)
def metrics_freshness() -> Run:
    """Return the pipeline run the published metrics come from."""
    return load_run()
