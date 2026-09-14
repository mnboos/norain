# How to interpret route forecasts

NoRain estimates conditions along a timed route. Its summary, charts, and sections
answer related questions using different parts of the provider data. Understanding
those differences helps explain apparently conflicting results.

## Matching the forecast to arrival time

Open-Meteo responses contain dictionary blocks of parallel arrays, such as
`hourly.time` and `hourly.temperature_2m`. OWM responses contain an `hourly` list of
objects. The grid layer dispatches extraction using the stored cell's source and
rejects mismatched block formats. Its OWM parser accepts both numeric rain and a
legacy `{"1h": value}` object.

Open-Meteo extraction selects the nearest 15-minute timestamp if it is within two
hours of the arrival time. Otherwise it uses hourly data. The hourly selection
clamps to the nearest available timestamp even outside the returned range. OWM
also selects its nearest hour. This avoids some empty charts, but a displayed value
can represent a different time from the requested arrival.

Forecast requests are capped at 16 days. The scheduled-route availability indicator
checks whether the departure date falls between today and today + 15 days; it does
not verify each provider's returned coverage. Ensemble uncertainty declines timestamps outside its returned range; unavailable
regional-model values are excluded rather than extrapolated.

## Probability and precipitation amount

For the nearest ensemble hour, each available precipitation series contributes one
value, with control members counted once. A member is wet at 0.1 mm or more
in the preceding forecast hour. At least two valid members are required for an
ensemble probability. `pop` is the fraction of wet members;
`rain_if_wet` is their mean precipitation, or zero when none are wet. Ensemble
probability is preferred; OWM probability is a fallback when available. The current
Open-Meteo deterministic parser does not expose its precipitation-probability field.

If any sample has a probability, the route's rain verdict uses a 25% threshold at
individual samples. Its reported probability is the maximum sample probability,
not the probability of rain anywhere on the entire journey. Samples without
probability do not independently trigger a deterministic verdict in this mode.

If all samples lack probability, the verdict instead checks whether any deterministic
sample has at least 0.1 mm. A missing probability stays null rather than becoming
an asserted 0% chance.

`rain_amount` is the conditional amount at the peak-probability sample, falling back
to its deterministic amount. Without probability it is the maximum deterministic
sample amount. It is **not accumulated rainfall over the ride**. `rain_mm` preserves
the selected provider block's amount: 15-minute and hourly amounts are not normalized
in that legacy field. New samples also include `precipitation_interval_s` and
`rain_rate_mm_h`; charts and point details use the normalized mm/h intensity.
Adding samples would double-count repeated time steps.

## Ensemble ranges and model comparison

The three charts use elapsed riding time, with arrival time in the tooltip. A solid
line shows the ensemble median and shading shows its 10th–90th percentile range;
a dotted line distinguishes the deterministic forecast. Missing ranges remain gaps.
Wind's legend can reveal gust, headwind and crosswind ranges.

Open **Vorhersage-Details** to choose a route sample using the slider, or select a
chart point or map marker. The map highlights that sample. The panel shows metric
ranges, valid member counts, probability source, matched ensemble hour and retrieval
age, plus a model comparison table. On narrow screens the table scrolls horizontally.

Statistics pool all available members with equal weight, so models with more members
contribute more weight. Precipitation ranges include dry members. Temperature and wind
use their available members independently; headwind and crosswind require matching
speed/direction from the same model and member. Metrics with fewer than two valid
members have no reported range. The per-model table exposes differing coverage.

These ranges describe model spread, not calibrated confidence intervals or guaranteed
bounds. The summary labels the peak point probability explicitly and uses **Regen
möglich** rather than claiming that a 25% probability means rain is likely. Zero wet
members and unavailable probability are separate states.

The existing model selection is retained. Ensemble coverage can end before the
16-day deterministic forecast window. Older precipitation-only cache payloads refresh
once; subsequent reuse follows the existing two-hour database expiry. Unsupported
variables do not trigger repeated refetches. Retrieval age is not the model-run age.

## Sections and summary can disagree

Sections group consecutive samples by deterministic precipitation:

| Condition | Sample precipitation |
| --- | --- |
| `dry` | Below 0.1 mm |
| `rain` | At least 0.1 mm and below 2.5 mm |
| `heavy_rain` | At least 2.5 mm |

A section can be dry while the probabilistic summary predicts rain, because the
ensemble expresses uncertainty beyond one deterministic prediction.

Section kilometre positions are estimated from each sample's elapsed-time fraction
of the last available sample time, multiplied by total route distance. They are
not measured distances along the polyline. Section time labels are elapsed durations.
Each group spans its own first and last samples, so neighboring groups can leave
gaps between samples, and a single-sample section has zero displayed length.

## Wind is relative to travel direction

Wind direction describes where wind comes from. For travel bearing `b`, wind
source direction `d`, and speed `v`, NoRain calculates:

```text
headwind  = v × cos(d − b)
crosswind = |v × sin(d − b)|
```

The code converts the angle to radians. Positive headwind is wind against the rider;
negative headwind is tailwind. Crosswind is an absolute magnitude without a left/right
sign. Wind speed and gusts are in km/h; OWM values are converted from m/s.

## Wind effort: the same wind costs more the faster you ride

The map arrows show the real wind: where it blows, over ground. They do not show the felt
(apparent) wind. At riding speed that is mostly your own airflow, so felt-wind arrows would
point at the rider almost everywhere.

How hard a wind hits depends on the rider's speed, so NoRain estimates the **wind effort**:
the extra power needed to hold the routing profile's planned speed `v` against headwind `h`
and crosswind `c`, compared with calm air:

```text
wind_power = ½·ρ·CdA · v · (|v+h, c| · (v+h) − v²)     ρ = 1.2 kg/m³, CdA = 0.5 m²
```

Aerodynamic drag acts along the apparent wind; this is its component along travel, times
ground speed. Rolling resistance and mass cancel out of the difference. A 10 km/h headwind
costs about +64 W at 20 km/h but about +231 W at 40 km/h. So a `fast_ebike` route scores
worse in the same wind than a `bike` route, even though a motor supplies part of the power.
`v` is the average speed over each sample's section, so descents do not inflate it. The
arrow size and the ride score use the wind effort. Where it is unknown (no timing, older
results) the score falls back to the plain headwind. It is an estimate: no shelter, no
real rider position, no motor model.

## Time-zone limitations

Open-Meteo requests use `Europe/Zurich`; the parsers compare its naive local timestamps
with the supplied departure datetime. Supply local timestamps without `Z` or an offset:
aware datetimes can fail comparison with naive provider times.

Django sets `TIME_ZONE=Europe/Zurich`, but scheduling does not consistently use aware
local datetimes: normal next-departure calculation defaults to naive `datetime.now()`,
while pre-warming passes UTC-aware `now` into cron iteration. The UI also combines
UTC date conversion with local time formatting. There is no per-route time-zone
field or complete daylight-saving-time normalization. Keep the local setup aligned
with Zurich and verify boundary dates/times; cross-zone scheduling needs code changes.

## Missing data is not dry weather

Samples whose weather cannot be fetched or extracted are skipped. An empty response
can still include geometry and a summary with default zero values. Plotting explicitly
produces three **Keine Wetterdaten** placeholders, and sections are empty. Interpret
that state as unavailable weather, not as a reliable dry forecast.

[Documentation index](../README.md)
