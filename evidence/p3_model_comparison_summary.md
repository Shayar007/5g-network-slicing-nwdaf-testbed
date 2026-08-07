# P3: Model Comparison -- Linear Regression vs Holt's Linear Trend (2026-08-07)

## What was compared
The current predictor (ml_predictor.py: linear regression over a fixed
10-min history window) vs Holt's linear trend method (double exponential
smoothing, implemented from scratch in numpy -- no new dependencies,
since this machine has no internet access for `pip install`).

## Data used
Real testbed evidence, not synthetic: the two mMTC accuracy-test CSVs
collected earlier this session (step-change load test and gradual-ramp
load test), converted to a common timestamp/entity/value schema.

## Results (lag-corrected MAE, 60s-ahead forecast, both models given
## identical, CSV-limited history -- apples-to-apples comparison)
| Dataset | Linear Regression MAE | Holt's Method MAE | Winner |
|---------|------------------------|--------------------|--------|
| Step-change load | 4.773 (N=5) | 3.087 (N=5) | Holt |
| Gradual-ramp load | 0.628 (N=5) | 0.370 (N=5) | Holt |
| **Overall (N-weighted)** | **2.700** | **1.729** | **Holt (-36.0%)** |

## Caveat
Sample sizes are small (N=5 per dataset, 10 total evaluated points) --
this is indicative, not a statistically powered result. Absolute MAE
values here differ from the live-testbed evidence collected earlier
(evidence/ml_accuracy_summary.md) because both models here only see the
CSV's limited point count as history (cold start), whereas the earlier
live evidence came from the running service with a fuller Prometheus
history. The LR-vs-Holt *comparison itself* is fair since both models
were given identical history in this test.

## Why Holt's method wins here
Holt's method weights recent observations more heavily via exponential
decay, rather than fitting one line across the whole fixed window
equally. This directly addresses the failure mode documented in
evidence/ml_accuracy_summary.md (fixed-window linear regression dilutes
the fitted slope when old flat data is mixed with a new trend).

## Recommendation
Holt's linear trend method (model_comparison.py, alpha=0.5, beta=0.3)
is a promising drop-in replacement for ml_predictor.py's fit_and_predict().
Given the small sample size, recommend collecting more accuracy-test
runs (varied load patterns, longer duration) before making it the
default -- documented here as a validated, scoped candidate rather than
switched over without further evidence.
