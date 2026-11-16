# Output contract v1

This is what data-pipeline promises to the repos that read its output: mcp-server and
task-agent. The machine-readable version is the JSON Schema in [`contract/v1/`](../contract/v1/),
one file per table plus the quality report. Metric definitions come from
[`metrics.yaml`](../metrics.yaml).

Next: demand-forecast will read the same output.

## Versioning

- **Any** change to a table, column, type, grain or metric definition creates a new
  version directory (`contract/v2/`). Published versions are never edited.
- `contract/checksums.json` pins the SHA-256 of each version's schema files, and
  `samples/checksums.json` pins each version's sample files. CI fails if a published version
  changes.
- Every output carries its version: `run_metadata.contract_version` and
  `quality_report.json` → `contract_version`.

## Location

| Item | Path |
|---|---|
| Output directory | `$DATA_OUTPUT_DIR`, default `data/output/` (gitignored) |
| DuckDB database | `<output>/metrics.duckdb`, one table per contract table (schema `main`) |
| Parquet | `<output>/parquet/<table>.parquet`, one file per table |
| Quality report | `<output>/quality_report.json` |
| Sample for tests | `samples/v1/` (committed; built from synthetic fixtures by `make sample`) |

Outputs are replaced as a set at the end of a successful run. A run that fails a
critical quality check publishes nothing; its report goes to `data/runs/<run_id>/`.

## Conventions

- **Time zone:** all dates are UTC calendar dates; `generated_at` is UTC.
- **Complete days only:** outputs cover UTC days before the date of the run's `as_of`. The
  current, partial day is never published, so `data_max_date` is always a complete day.
- **Money:** USD as DOUBLE, rounded to cents.
- **Ratios:** fractions between 0 and 1. They are NULL when the denominator is 0, never 0
  or NaN.
- **Restatement:** any metric for recent days can change on later runs. Statuses update,
  returns arrive up to 11 days after the order, and items and purchase events can be
  stamped up to 4 days after it. Rows with `metric_date >= run_metadata.restated_from`
  (the last 14 days) may change on later runs; older rows are stable unless the pipeline
  runs with `--full-refresh`.
- **Additivity:** `revenue`, `units` and `returned_units` sum across categories and
  distribution centers. `orders`, the session counts and every ratio do not, because one
  order or session can span several slices. For totals, read `daily_totals`.

## Tables

### `daily_metrics`

Grain: one row per `metric_date` × `category` × `distribution_center_id`. Sparse: a row
exists only when the slice had sales or product views that day.

| Column | Type | Null | Description |
|---|---|---|---|
| `metric_date` | DATE | no | UTC date the order was placed (sales metrics) or of the product view (session metrics) |
| `category` | VARCHAR | no | Product category |
| `distribution_center_id` | BIGINT | no | Distribution center that stocks the product |
| `distribution_center_name` | VARCHAR | no | Distribution center name |
| `revenue` | DOUBLE | no | See metric definitions |
| `orders` | BIGINT | no | 〃 |
| `units` | BIGINT | no | 〃 |
| `returned_units` | BIGINT | no | 〃 |
| `aov` | DOUBLE | yes | 〃 |
| `return_rate` | DOUBLE | yes | 〃 |
| `product_view_sessions` | BIGINT | no | 〃 |
| `converted_sessions` | BIGINT | no | 〃 |
| `conversion_rate` | DOUBLE | yes | 〃 |

### `daily_totals`

Grain: one row per `metric_date`. Gap-free from `run_metadata.data_min_date` to
`data_max_date`; days without activity have zeros (and NULL ratios). It has the same
metric columns as `daily_metrics`, computed over all slices (orders and sessions are
de-duplicated, not summed). This is the table to forecast from.

| Column | Type | Null |
|---|---|---|
| `metric_date` | DATE | no |
| `revenue`, `orders`, `units`, `returned_units`, `product_view_sessions`, `converted_sessions` | as above | no |
| `aov`, `return_rate`, `conversion_rate` | DOUBLE | yes |

### `metric_definitions`

Grain: one row per metric (`metric_name`), generated from `metrics.yaml`, so consumers
can explain a number without reading this file.

| Column | Type | Description |
|---|---|---|
| `metric_name` | VARCHAR | Column name in `daily_metrics` / `daily_totals` |
| `label` | VARCHAR | Display name |
| `description` | VARCHAR | Plain-language definition |
| `unit` | VARCHAR | `usd`, `count` or `ratio` |
| `aggregation` | VARCHAR | `sum`, `count`, `count_distinct` or `ratio` |
| `formula` | VARCHAR | e.g. `SUM(sale_price) FROM sold_items` |
| `additive` | BOOLEAN | True if summing across slices gives the total |

### `run_metadata`

Exactly one row, describing the run that produced the outputs.

| Column | Type | Description |
|---|---|---|
| `run_id` | VARCHAR | Unique run id, also in the quality report |
| `contract_version` | VARCHAR | Always `v1` here |
| `pipeline_version` | VARCHAR | Package version |
| `source` | VARCHAR | `bigquery` (live thelook) or `fixtures` (synthetic) |
| `generated_at` | TIMESTAMPTZ | Publish time, UTC |
| `data_min_date` | DATE | First `metric_date` |
| `data_max_date` | DATE | Last `metric_date` |
| `restated_from` | DATE | Rows on or after this date may still change |
| `quality_status` | VARCHAR | `pass` or `warn` (failing runs never publish) |

### `quality_report.json`

One JSON document per run: `contract_version`, `run_id`, `generated_at`, `source`,
`status` (`pass`/`warn`/`fail`), `tables` (rows ingested and quarantined per source
table) and `checks` (name, table, check type, severity, status, failed rows, message).
See `contract/v1/quality-report.schema.json` for the exact shape.

## Metric definitions

All metrics are computed over two derived sources:

- **`sold_items`:** order items that were not cancelled, with the product's category and
  distribution center, dated by their order's `created_at` (when the order was placed).
  The item's own `created_at` is not used: in the live source 45% of items fall on a
  different UTC day than their order and some are in the future (see
  [source-profile.md](source-profile.md)). Dating by the order also keeps every order on
  a single day.
- **`product_views`:** `product` events with a `/product/<id>` uri, joined to that
  product. `session_converted` is true if the same session has a `purchase` event.

| Metric | Definition | Unit | Additive |
|---|---|---|---|
| `revenue` | `SUM(sale_price)` over sold_items | usd | yes |
| `orders` | `COUNT(DISTINCT order_id)` over sold_items | count | no |
| `units` | `COUNT(*)` over sold_items | count | yes |
| `returned_units` | `COUNT(*)` over sold_items where `returned_at` is set, counted on the order date | count | yes |
| `aov` | `revenue / orders` | usd | no |
| `return_rate` | `returned_units / units` | ratio | no |
| `product_view_sessions` | `COUNT(DISTINCT session_id)` over product_views | count | no |
| `converted_sessions` | `COUNT(DISTINCT session_id)` over product_views where `session_converted` | count | no |
| `conversion_rate` | `converted_sessions / product_view_sessions` | ratio | no |

**Known approximation:** purchase events don't say which product was bought. Conversion
by category or distribution center therefore means "sessions that viewed a product in
this slice and purchased something". It doesn't mean they bought that product.

**Sessions across midnight:** session metrics are dated by the product view, so a session
that views products on both sides of midnight UTC counts on each of those days. About 11%
of live sessions span two or more dates.

## Reading the outputs

```sql
-- DuckDB
attach 'data/output/metrics.duckdb' as m (read_only);

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
from m.daily_totals
order by metric_date desc
limit 7;
```

```python
import pandas as pd

totals = pd.read_parquet("data/output/parquet/daily_totals.parquet")
```
