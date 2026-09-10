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
not verify each provider's returned coverage. Ensemble extraction declines timestamps
more than one hour outside its returned range.

## Probability and precipitation amount

For the nearest ensemble hour, each available precipitation series contributes one
value. A member is wet at 0.1 mm or more. `pop` is the fraction of wet members;
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
to a common interval. Adding samples would also double-count repeated time steps.

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
