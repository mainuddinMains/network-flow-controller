Each cell is `mean ± standard deviation [min-max]` across runs (one run per seed). RTT is the per-run mean over successful probes, measured at the source with a monotonic clock; it includes both directions.

| Delay (ms) | Loss | Runs | Success fraction | RTT of successful probes (ms) | Timeouts per run | Predicted success | Predicted RTT (ms) |
|---|---|---|---|---|---|---|---|
| 10 | 0% | 3 | 1.000 ± 0.000 [1.000-1.000] | 155.8 ± 3.0 [153.7-159.2] | 0.0 ± 0.0 [0.0-0.0] | 1.000 | 155.8 |
| 10 | 5% | 3 | 0.900 ± 0.035 [0.860-0.920] | 154.7 ± 1.7 [153.6-156.7] | 10.0 ± 3.5 [8.0-14.0] | 0.902 | 155.8 |
| 100 | 0% | 3 | 1.000 ± 0.000 [1.000-1.000] | 346.6 ± 3.4 [344.5-350.6] | 0.0 ± 0.0 [0.0-0.0] | 1.000 | 335.8 |
| 100 | 5% | 3 | 0.903 ± 0.038 [0.860-0.930] | 344.4 ± 0.8 [343.7-345.3] | 9.7 ± 3.8 [7.0-14.0] | 0.902 | 335.8 |
