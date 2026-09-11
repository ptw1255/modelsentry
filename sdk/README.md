# ModelSentry

Early warning when your production ML models start degrading.

ModelSentry computes statistical profiles of your model's inputs and outputs
locally — nothing leaves your machine. When feature distributions drift from
baseline, you get an alert before your stakeholders notice.

```
PSI 0.83 ↑  income     CRITICAL
PSI 2.43 ↑  age        CRITICAL
PSI 0.01    tenure     stable
PSI 0.00    country    stable
```

---

## Install

```bash
pip install modelsentry
```

Requires Python 3.11+. No Docker. No cloud account. No infrastructure.

---

## Quickstart

```python
import modelsentry as ms

ms.init(model_id="churn-v3", profile_window=500)

@ms.monitor()
def predict(features_df):
    return model.predict(features_df)
```

Then start the local dashboard:

```bash
modelsentry serve --model churn-v3 --alert-email you@company.com
```

Open [http://localhost:8080](http://localhost:8080) to see live drift scores,
feature distributions vs. baseline, and alert history. The dashboard updates
every 60 seconds and shows an explicit "all systems nominal" state when no
drift is detected — so you always know the system is alive.

---

## How it works

ModelSentry runs as two independent processes — you don't need both running at the
same time.

**In your model (SDK side)**

`@ms.monitor()` captures inputs and outputs on every `predict()` call. Every 500
predictions (configurable via `profile_window`), it computes a statistical profile
on a background thread and saves it to `~/.modelsentry/{model_id}/`. The first
profile is automatically saved as the baseline. Your predict function is not blocked
— monitoring overhead is under 1ms.

**In your browser (dashboard side)**

`modelsentry serve` reads those profile files whenever you open it. You don't need
the dashboard running continuously — just open it when you want to check in, or
after receiving a drift alert email. It auto-refreshes every 60 seconds.

**Storage:** roughly 5–20 KB per profile. At 10,000 predictions/day with
`profile_window=500`, that's ~20 profiles/day — around 200–400 KB/day.

---

## Configuration

### Email alerts via CLI

```bash
modelsentry serve \
  --model churn-v3 \
  --alert-email you@company.com \
  --smtp-host smtp.gmail.com \
  --smtp-port 587 \
  --smtp-user you@gmail.com \
  --smtp-password "your-app-password"
```

### Custom storage location

```python
ms.init(
    model_id="churn-v3",
    profile_window=500,
    storage_path="/data/modelsentry",
)
```

### Integer classification labels

Numeric predictions default to regression. Set the task explicitly when a
classifier returns integer labels such as `0` and `1`:

```python
ms.init(
    model_id="churn-v3",
    prediction_task_type="classification",
)
```

---

## Dashboard

The local dashboard at `localhost:8080` shows:

- Model health overview (green / yellow / red)
- Prediction volume — total monitored since install
- Per-feature distribution charts vs. baseline
- PSI and KS drift scores, color-coded by severity
- Last updated timestamp (auto-refreshes every 60 seconds)
- Alert history with timestamps
- Explicit "all systems nominal" state when no drift is detected

---

## OpenTelemetry (optional)

Install the vendor-neutral instrumentation extra when operational traces and
metrics are useful:

```bash
pip install 'modelsentry[otel]'
```

ModelSentry uses the active OpenTelemetry providers and standard `OTEL_*`
configuration. For a standalone process, configure an OTLP provider explicitly:

```python
from modelsentry.telemetry import configure_telemetry

configure_telemetry()  # reads OTEL_EXPORTER_OTLP_* and OTEL_SERVICE_NAME
```

Application-owned providers are never replaced. If OpenTelemetry is not
installed or is disabled with `MODELSENTRY_OTEL_ENABLED=false`, all telemetry
calls are no-ops and ModelSentry behaves exactly as before. Set
`MODELSENTRY_OTEL_INCLUDE_MODEL_ID=true` (or pass
`telemetry_include_model_id=True` to `ms.init`) only when model IDs are safe to
export; model IDs are never metric attributes.

### Signals and code path

Traces and metrics cover ModelSentry-owned work only:

```text
predict returns → capture/buffer → worker queue → profile + handler
                 → storage → drift comparison → alert delivery
dashboard request → FastAPI HTTP span → storage reads
```

Capture starts after the decorated prediction returns, so customer model
execution is not wrapped or measured. Aggregate profile batches link (up to 16)
capture spans across the worker boundary and report queue delay. Signals include
capture, batch submission, profile/handler duration and outcome, storage
operation duration, buffer size, drift duration/severity, alert
filtering/delivery, and FastAPI request duration/status.

Span names are `modelsentry.capture`, `modelsentry.profile`,
`modelsentry.profile_handler`, `modelsentry.storage`, `modelsentry.drift`, and
`modelsentry.alert`; HTTP spans come from the standard FastAPI instrumentor.
Metric instruments use the corresponding `modelsentry.*.count`,
`*.duration`, `*.errors`, `modelsentry.buffer.size`, and
`modelsentry.batch.submitted` names with bounded `outcome`, `severity`,
`operation`, `object`, `reason`, and `task_type` attributes.

Only bounded operational fields are emitted: counts, durations, task type,
severity, operation, outcome, and HTTP route templates. Raw features,
predictions, feature names, profile/report contents, request/response bodies,
emails, credentials, filesystem paths, and exception messages are never
recorded. Errors contain only a sanitized exception type and fixed stage name.

To verify locally, point `OTEL_EXPORTER_OTLP_ENDPOINT` at an OpenTelemetry
Collector and inspect one prediction window, one drift evaluation, one alert
attempt, and one dashboard request. Confirm spans are connected through the
worker links and contain no customer data.

---

## Links

- **Website:** [getmodelsentry.com](https://getmodelsentry.com)
- **GitHub:** [github.com/treyhamilton/modelsentry](https://github.com/treyhamilton/modelsentry)

---

## License

MIT
