# Bike share demand forecasting

The ML module forecasts valid observed station departures and arrivals over the next 30 and 60 minutes. It includes an actually evaluated winter model, two baselines, bounded CPU training, offline inference and a transparent inventory scenario model. It does not reconstruct historical inventory or claim calibrated stockout probabilities.

## Measured model decision

The real-data experiment uses the existing 100-station January–February 2025 import. Its dense export has 566,400 station/time rows; departure coverage is complete and the first 24 hours of arrival coverage are excluded. Ninety-one stations have reference coordinates recovered from valid trips before training. The other locations and all historical capacities remain unknown. No current catalog capacity is supplied to training.

The seasonal baseline won early-validation MAE for all four targets and is the approved inference path. LightGBM improved final-period MAE, but changing the selection rule after observing that period would make the evaluation optimistic. The candidates remain available for reproducibility, not silently substituted for the approved baseline. [The generated report](ml-evaluation/REPORT.md) contains measured MAE, RMSE, Poisson deviance, interval coverage, per-station metrics, demand conditions and charts. [The validation record](DATA_VALIDATION.md) supplies import exclusions and archive checksums.

This is a retrospective event-time evaluation, not a reconstruction of a historical online deployment. Monthly archive publication times are not known. The lag-enhanced comparisons assume an operator/provider can deliver completed-trip counts with the configured delay; Citi Bike public GBFS cannot. Normal forecasts use an already-created artifact, and the API deliberately does not query GBFS inventories as if they were trip counts.

## Features and chronological boundaries

The single feature implementation serves both batch and one-row inference. Features include station category, past-observed reference coordinates, reliable past capacity if supplied, New York weekday/hour/quarter patterns, month, cyclic clock/day-of-year, pre-training seasonal mean/support, prior-day/week counts, comparable-period counts, and explicit missingness. The location reference is an earlier observed coordinate, not a guarantee that a station has never moved. A covered zero means no valid exported rides, not proof that a station was installed or operational.

Seasonal means use only warm-up bins whose end plus the completed-count delay is at or before the warm-up cutoff, with sparse weekday/hour cells shrunk toward station/hour means. The default 24-hour delay excludes the final warm-up day. Training targets never participate in that table. The recent baseline averages fully observed same-local-clock windows one, seven and fourteen days earlier and falls back to seasonal when none are known. Calendar lookbacks preserve New York clock time across DST. The same delay makes the upcoming-window prior-day count unavailable; week lookbacks can remain available. Lower that delay only for a genuinely timely completed-trip provider, not to improve an offline score.

| Period in America/New_York | Purpose |
|---|---|
| January 1–14 | Seasonal warm-up and reference geography |
| January 15–February 7 | LightGBM fitting |
| February 8–12 | Model selection |
| February 13–17 | Interval/dispersion calibration only |
| February 18–28 | Held-out final audit |

All ends are exclusive midnight boundaries. A sample's entire 30/60-minute target must finish within its split. Unknown targets and incomplete lag windows are excluded or remain missing; they are never zero-filled. Models use Poisson loss, 180 trees, 15 leaves, max_bin 63, two CPU threads and fixed seeds. Training samples are capped at 300,000 per target; input is capped at 120 stations, 1.2 million cells and a 512 MB JSONL file. These are conservative defaults even if a larger RAM budget is available.

## Regenerate from real data

Use Python 3.12 and run from `backend/`. The final native Windows verification retrained/evaluated/backtested with Python 3.12 and reproduced the approved artifact. ML-specific versions are pinned in the `ml` extra; install with `-c constraints.txt`. The API image installs the lighter `inference` extra.

```powershell
python -m pip install -e ".[dev,ml]"

# Existing processed data can be reused. To create it, see DATA_INGESTION.md.
python -m app.modules.ingestion.cli --data-dir ../data/real aggregate --start 2025-01-01 --end 2025-03-01

# Reuses verified source discovery, bounded/resumable downloads and streamed ZIP reading.
# Saves pre-training coordinates and source SHA; deletes its owned ZIP after success.
python -m app.modules.ml.cli geography --catalog ../data/real/stations.json --before 2025-01-15 --output ../data/ml/locations.json

python -m app.modules.ml.cli train --data ../data/real/features.jsonl --locations ../data/ml/locations.json --config ml-config.json --output app/modules/ml/artifacts --report ../docs/ml-evaluation

python -m app.modules.ml.cli evaluate --artifact app/modules/ml/artifacts/current.json --data ../data/real/features.jsonl --report ../data/ml/evaluate
python -m app.modules.ml.cli backtest --artifact app/modules/ml/artifacts/current.json --data ../data/real/features.jsonl --report ../data/ml/backtest
```

`evaluate` and `backtest` replay the same sealed final period without retraining. Backtest output includes daily rolling issuance with fixed models and point-in-time features; it is not rolling model refitting. Exact replay requires the original demand-export checksum. Cross-platform floating-point/platform/library differences can change native model bytes; the version therefore identifies actual content and implementation, not just a friendly name. Repeated training with identical model bytes and code/data/config identity reuses the immutable version; timing metadata belongs to each report.

Git attributes preserve artifact bytes and enforce LF for Python feature code, preventing automatic Windows/Linux line-ending conversion from invalidating checksum guards.

For a new range, change all split dates in a copied config, import sufficient warm-up and held-out data, and regenerate the location reference using that warm-up cutoff. Choose these boundaries and thresholds before inspecting final-period results. No hyperparameter sweep or test-period tuning was used for the shipped experiment.

## Separate Docker training

On an 8 GB host, stop the serving stack while training. From the repository root:

```powershell
docker compose down
docker compose -f compose.ml.yml run --build --rm ml
# Equivalent: npm run ml:train
docker compose up --build
```

The standalone trainer has no PostgreSQL dependency, two CPUs and a 4 GB hard memory ceiling. It uses `data/real` and `data/ml/locations.json`, writes artifacts into the repository and charts into `docs/ml-evaluation`. The final native run took 48.6 seconds with sampled peak RSS 177.6 MiB. Docker execution still requires a Docker-capable host; actual native PostgreSQL/PostGIS and model workflows passed. See [final verification](FINAL_VALIDATION.md).

## Predict offline or serve in the application

The packaged `app/modules/ml/artifacts/current.json` points to a working local artifact. No internet or raw archives are needed for archive-only inference. Get a station ID from its `seasonal.json` or the saved station catalog; IDs remain strings.

```powershell
python -m app.modules.ml.cli predict --artifact app/modules/ml/artifacts/current.json --station YOUR_STATION_ID --at 2025-02-25T08:00:00-05:00 --replay
# Optional genuinely available completed-count input:
python -m app.modules.ml.cli predict --artifact app/modules/ml/artifacts/current.json --station YOUR_STATION_ID --at 2025-02-25T08:00:00-05:00 --history ../data/real/features.jsonl --replay
```

`--replay` explicitly marks retrospective predictions before artifact creation. Predictions before model fitting, model selection and interval calibration have all completed are refused even in replay mode. For present-day inference omit `--replay` and supply a current 15-minute-aligned timestamp. A two-month winter artifact older than 90 days, a season outside its training months, an unknown station or absent prior ride evidence returns a low-confidence seasonal fallback, marks intervals unvalidated in that context and suppresses inventory risk. This is why the 2025 artifact does not claim reliable October 2026 availability.

`GET /api/v1/forecasts/model` returns source, model/configuration and measured evaluation metadata. `POST /api/v1/forecasts/predict` accepts `{"station_id":"...","as_of":"..."}`; `as_of` is optional and defaults to the latest 15-minute issuance boundary. Only recent boundaries are accepted; CLI handles historical replay. The repository reads only non-demo Citi Bike snapshots whose reported/update/fetch times were known at issuance. Missing inventory remains missing. Database failure is a structured 503. The first issuance after building an artifact is its next 15-minute boundary. Existing `/stations/nearby` continues returning measured inventory unchanged.

The API uses archive-only inference and does not accept client-invented bike/dock values. `ML_ARTIFACT_PATH` may point to a deployment-approved version or pointer. Artifact loading is cached per process; restart API workers after activation or rollback to a compatible version. Never load model artifacts uploaded by untrusted clients. SHA-256 checks detect corruption, not authenticity: trust comes from deployment approval and local access control. Native text avoids Python pickle execution but still uses a native parser. Paths, sizes, exact library version, feature order and data/feature implementation checksums are checked before inference; changed feature code requires regeneration.

## Uncertainty and inventory risk

Separate late-validation residuals fit a nominal 90% interval using `|y−mean|/sqrt(1+mean)`, with nonnegative integer endpoints. Final-period coverage and interval width are measured in the report. Overlapping windows and time dependence mean these intervals have no exchangeability-based guarantee. Low-confidence contexts retain historical interval numbers only as explicitly unvalidated references.

LightGBM exposes global gain importance and top local feature contributions to log mean; baseline drivers show the station/weekday/hour mean and historical support. Count dispersion comes from excess validation squared residuals, bounded to a Gamma-Poisson mixture parameter. It is not calibrated against stockout labels.

Optional CLI `--inventory path.json` accepts one normalized snapshot with `station_id`, nonnegative integer `bikes` and `docks`, timezone-aware `reported_at`, `feed_updated_at`, `fetched_at`, and explicit boolean `is_installed`, `is_renting`, `is_returning`, `is_demo`. All timestamps must be at or before issuance and within five minutes. Synthetic, stale, absent, closed or low-confidence contexts do not produce a real risk estimate.

The 2,048-path, 60-step simulation starts with measured bikes and usable slots `bikes+docks`, not an assumed historical capacity. Thirty-minute and second-half-hour count means determine rates. Independent Gamma rate multipliers model overdispersion; each minute draws arrivals/departures and randomizes their order, clipping served flows to usable inventory bounds. It reports empty/full occupancy at each horizon, any-time boundary hits and ending-bike quantiles. Incoherent horizon means suppress risk rather than inventing a correction. Minute ordering, constant slots, capped extreme rates, no rebalancing, no weather, censored rides and independent flows are explicit approximations. Scenario probabilities always carry `calibrated_stockout_probability: false`.

## Next validation work

Collect sustained inventory history and independently evaluate stockout calibration/Brier scores by horizon/station. Add multiple seasons, publication-aware historical inputs and operating/relocation history. Validate a timely completed-trip provider before using online lags. Forecast presentation, recommendations and saved commutes now exist; persistent drift/outcome monitoring remains future work.

References: [LightGBM CPU and Poisson parameters](https://lightgbm.readthedocs.io/en/v4.6.0/Parameters.html), [native models and prediction contributions](https://lightgbm.readthedocs.io/en/v4.6.0/pythonapi/lightgbm.Booster.html), [Poisson deviance](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.mean_poisson_deviance.html), [official Citi Bike data](https://citibikenyc.com/system-data).
