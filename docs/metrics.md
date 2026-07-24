# Metrics

The package exposes an intentionally small MVP metric set over
`metrics_tasks`. Values are calculated for a requested model and rolling period
(`1d`, `7d`, `30d`, or `90d`).

## Current metrics

### Model Quality Index (MQI)

The MVP MQI is the model's success rate:

```text
MQI = successful completed runs / all recorded runs
```

The `get_mqi` MCP tool returns MQI on a `0..1` scale. The standalone
`detect_degradation.py` utility presents the same ratio as a `0..100` score so
that day-over-day drops are easy to read. The quality-weighted formula is
intentionally deferred until enough labeled data exists.

### Success rate

`success_rate` is the proportion of rows whose `status` is `success`. Failed,
interrupted, and human-stopped rows remain in the denominator so the metric
reflects the complete run history.

### Latency

`avg_sec` / `avg_duration_sec` is the average `duration_sec` for rows with an
`ended_at` timestamp. Open runs are excluded from latency aggregates.

### Cost

`avg_cost_usd` and `total_cost_usd` use the optional `cost_usd` value recorded
by the producer. Pricing rows in `metrics_model_pricing` document the rates
used by an ingestion process; this read-only server does not estimate or mutate
costs.

## Planned metrics

The roadmap for stability, independence, regression, velocity, and composite
engineering indexes is documented in [`future-metrics.md`](future-metrics.md).
Those metrics require additional history and calibration and are not silently
substituted into the MVP MQI.

See [`schema.sql`](../schema.sql) for the source columns and
[`query_examples.sql`](../examples/query_examples.sql) for read-only reports.
