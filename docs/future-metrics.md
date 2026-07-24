# Future metrics roadmap

These candidates are intentionally outside the 0.1.0 MVP. They should be
introduced only after the underlying data is available and their definitions
are calibrated against reviewed runs.

| Phase | Metric | Purpose | Prerequisite |
| --- | --- | --- | --- |
| 2 | CSI (Code Stability Index) | Measure how often completed code needs follow-up edits | Git history and task/file links |
| 2 | HII v2 (Human Independence Index) | Measure intervention and redirection frequency | Classified intervention events |
| 2 | RRI (Regression Risk Index) | Estimate the chance of regressions | CSI plus rollback history |
| 3 | PV (Progress Velocity) | Weight delivered progress by task difficulty | Calibrated task weights |
| 3 | ACI (Architectural Consistency Index) | Detect changes that violate project invariants | Architecture rules and baseline |
| 4 | Predictive degradation | Forecast MQI drops after model or prompt changes | Several months of historical data |
| 4 | EPI (Engineering Productivity Index) | Combine quality, speed, stability, independence, and cost | All component metrics |

Until those prerequisites are met, the server reports the transparent MVP MQI
(success rate) rather than a synthetic composite score.

See [`metrics.md`](metrics.md) for current definitions.
