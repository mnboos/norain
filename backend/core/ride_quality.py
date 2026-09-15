"""Ride quality: one 0..1 score per weather sample, its band and its German label.

This is the only implementation of the scoring. The curves, weights and sensitivity are the
app's own judgement of what makes a ride bad, so they stay on the server: the API sends the
results (``ride_score``, ``ride_label``, ``wind_effort_level``, ``wind_effort``) and the
frontend only turns a score into a colour. Never ship a curve or a breakpoint to the client,
not even indirectly - a threshold in the bundle gives the curve away.

Scores are computed when a response is *served* (``core.jobs.forecast_view``,
``wind_arrows_at_detail``, the route list), never stored with the forecast. A change to
``RIDE_QUALITY`` therefore shows on the next request instead of waiting for every stored job
and thumbnail to be rebuilt, and nothing here spends an API request.

The score is *derived* - it has no physical unit. Anything we can't compute stays ``None``
rather than guessing: a stretch with no usable precipitation or wind data is painted neutral
grey, never a colour from the ramp.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

RideFactor = Literal["rain", "wind", "temp"]
FACTORS: tuple[RideFactor, ...] = ("rain", "wind", "temp")


@dataclass(frozen=True)
class RideQualityConfig:
    """How harsh the ride quality is.

    ``weights``: how much each factor's penalty (0..1) adds to the score. They need not sum
    to 1: the score is clamped, so a weight of 1 lets that factor alone reach the worst
    colour. With the defaults the heaviest rain on its own stops at 0.75.

    ``sensitivity``: how quickly the score climbs the ramp as conditions worsen. The weighted
    sum is bent as ``sum ** (1 / sensitivity)``: 1 is linear, 2 turns 0.25 into 0.5 and 0.55
    into 0.74, and values below 1 hold the colour back. 0 and 1 stay put, so a perfect ride
    stays pale and the worst stays dark red.

    ``rain_risk_aversion``: how much a *chance* of rain counts (see ``rain_impact``). The
    ensemble's amount-if-wet is weighted by ``pop ** (1 / rain_risk_aversion)``: 1 is the plain
    expected impact (a 30% chance counts 30%), 2 makes a 30% chance count like 55% and a 10%
    chance like 32%. 30% of 2 mm/h is 0.21 at 1 and 0.38 at 2, before the rain weight.
    """

    weights: Mapping[RideFactor, float] = field(default_factory=lambda: {"rain": 0.75, "wind": 0.25, "temp": 0.2})
    sensitivity: float = 1.0
    rain_risk_aversion: float = 2.0


# The one place to tune it. The score drives the map line, the list glyph and the
# "sehr gut … sehr schlecht" wording alike, so colour and text stay in agreement.
RIDE_QUALITY = RideQualityConfig()

# Rain dominates - the app is called NoRain. Drizzle is a nuisance, 5 mm/h is the worst it
# gets for scoring purposes.
RAIN_CURVE = ((0, 0), (0.2, 0.15), (1, 0.5), (2.5, 0.8), (5, 1))

# Wind effort in watts: what it costs *this* rider to hold the planned speed, so the same wind
# weighs more on a fast e-bike than on a slow bike. A tailwind is not "better than calm" on
# this scale, it just isn't a penalty, so the curve starts at 0. Calibrated so that at
# ~18 km/h it matches the headwind curve below (10/20/30 km/h ≈ 50/130/230 W).
WIND_POWER_CURVE = ((0, 0), (50, 0.3), (130, 0.65), (230, 1))

# Ground-relative headwind in km/h: the fallback when the effort is unknown - jobs and
# thumbnails from before the metric, or a route without timing.
WIND_CURVE = ((0, 0), (10, 0.3), (20, 0.65), (30, 1))

# Comfortable riding band is 14-22 °C; it gets worse in both directions.
TEMP_CURVE = ((-2, 1), (14, 0), (22, 0), (34, 1))

BAND_LABELS = ("sehr gut", "gut", "mässig", "schlecht", "sehr schlecht")
FACTOR_LABELS: dict[RideFactor, str] = {"rain": "Regen", "wind": "Wind", "temp": "Temperatur"}

# Naming a single cause is only honest when one factor actually dominates. Below this share
# of the total the ride is simply "mixed", and the label stays silent about why.
MIN_WORST_SHARE = 0.5


@dataclass(frozen=True)
class RideScore:
    score: float  # 0 = bestes Wetter, 1 = schlechtestes; derived, unitless
    rain: float  # the three penalties, each 0..1, before weighting
    wind: float
    temp: float
    worst: RideFactor  # largest *weighted* contributor
    worst_share: float  # its share of the weighted total, before sensitivity bends it

    @property
    def band(self) -> int:
        return score_band(self.score)

    @property
    def label(self) -> str:
        """German wording, e.g. "mässig · v. a. Regen"."""
        text = BAND_LABELS[self.band]
        if self.band == 0 or self.worst_share < MIN_WORST_SHARE:
            return text
        return f"{text} · v. a. {FACTOR_LABELS[self.worst]}"


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _piecewise(x: float, points) -> float:
    """Linear interpolation through a sorted (x, y) table, flat outside the ends."""
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            span = x1 - x0
            return y1 if span == 0 else y0 + (x - x0) / span * (y1 - y0)
    return points[-1][1]


def _get(sample, name: str):
    return sample.get(name) if isinstance(sample, Mapping) else getattr(sample, name, None)


def rain_rate_mm_h(sample) -> float | None:
    """Precipitation rate in mm/h, or ``None`` when it cannot be derived.

    ``rain_mm`` is an accumulation over ``precipitation_interval_s``, so without the interval
    it is not a rate - an hourly assumption would silently turn a 15-minute bucket into a
    quarter of the real intensity.
    """
    rate = _get(sample, "rain_rate_mm_h")
    if _finite(rate):
        return float(rate)
    interval = _get(sample, "precipitation_interval_s")
    rain = _get(sample, "rain_mm")
    if _finite(interval) and interval > 0 and _finite(rain):
        return rain * 3600 / interval
    return None


def rain_impact(sample, config: RideQualityConfig = RIDE_QUALITY) -> float | None:
    """The rain penalty (0..1) of a sample: chance of rain combined with how much it would rain.

    Two readings, and the worse one wins:

    - the main run: its rate through ``RAIN_CURVE``, taken at face value. It is the finer,
      15-minute forecast, and rain it shows should not be diluted by a probability;
    - the ensemble: the amount its wet runs predict (``rain_if_wet``, mm/h) through the same
      curve, weighted by the chance (``pop``) bent by ``rain_risk_aversion``. This is what
      catches the shower the main run places a few km off.

    ``pop`` already carries the station correction, and ``rain_if_wet`` the stations' measured
    rate where the ensemble had no wet run. A ``pop`` with no ``rain_if_wet`` (the OWM fallback)
    has no amount of its own, so the main run decides. ``None`` when neither reading exists.
    """
    parts: list[float] = []
    rate = rain_rate_mm_h(sample)
    if rate is not None:
        parts.append(_clamp01(_piecewise(rate, RAIN_CURVE)))
    pop = _get(sample, "pop")
    if_wet = _get(sample, "rain_if_wet")
    if _finite(pop) and _finite(if_wet):
        aversion = config.rain_risk_aversion if config.rain_risk_aversion > 0 else 1.0
        chance = _clamp01(pop) ** (1 / aversion)
        parts.append(_clamp01(_piecewise(if_wet, RAIN_CURVE)) * chance)
    return max(parts) if parts else None


def score_band(score: float) -> int:
    """Which of the five quality bands a score falls in (0 = sehr gut .. 4 = sehr schlecht)."""
    return min(len(BAND_LABELS) - 1, math.floor(_clamp01(score) * len(BAND_LABELS)))


def ride_score(sample, config: RideQualityConfig = RIDE_QUALITY) -> RideScore | None:
    """Combined ride quality of a sample (a ``WeatherSample`` or its stored dict), or ``None``.

    ``None`` when rain or wind is unknown. Rain is ``rain_impact`` - chance and amount together.
    The wind factor reads the wind effort (``wind_power_w``) and falls back to the
    ground-relative headwind when there is none. Never feed apparent wind into either curve:
    it is mostly the rider's own speed and would need a different calibration.
    """
    rain = rain_impact(sample, config)
    if rain is None:
        return None
    power = _get(sample, "wind_power_w")
    headwind = _get(sample, "headwind")
    if _finite(power):
        wind = _clamp01(_piecewise(power, WIND_POWER_CURVE))
    elif _finite(headwind):
        wind = _clamp01(_piecewise(headwind, WIND_CURVE))
    else:
        return None
    temp_value = _get(sample, "temp")
    temp =_clamp01(_piecewise(temp_value, TEMP_CURVE)) if _finite(temp_value) else 0.0

    weighted = {
        "rain": rain * config.weights["rain"],
        "wind": wind * config.weights["wind"],
        "temp": temp * config.weights["temp"],
    }
    worst: RideFactor = "rain"
    for factor in FACTORS:
        if weighted[factor] > weighted[worst]:
            worst = factor

    total = _clamp01(sum(weighted.values()))
    # A non-positive sensitivity has no meaningful curve; treat it as linear.
    score = total ** (1 / config.sensitivity) if config.sensitivity > 0 else total
    share = min(1.0, weighted[worst] / total) if total > 0 else 0.0
    return RideScore(score=score, rain=rain, wind=wind, temp=temp, worst=worst, worst_share=share)


def worst_ride_score(samples, config: RideQualityConfig = RIDE_QUALITY) -> RideScore | None:
    """The worst scoring sample - what spoils a ride. Samples that are ``None`` or unscorable are skipped."""
    scored = [rq for s in samples if s is not None and (rq := ride_score(s, config)) is not None]
    return max(scored, key=lambda rq: rq.score, default=None)


def wind_effort_level(watts) -> str | None:
    """The wind effort as a word instead of watts, which nobody can place and which would claim
    a precision the estimate does not have. The steps are the breakpoints of the wind curve, so
    the word and the map colour agree about the same point.
    """
    if not _finite(watts):
        return None
    rounded = round(watts)
    if rounded < 0:
        return "Wind hilft"
    if rounded == 0:
        return "keiner"
    (_, _), (low, _), (medium, _), (high, _) = WIND_POWER_CURVE
    if rounded < low:
        return "niedrig"
    if rounded < medium:
        return "mittel"
    if rounded < high:
        return "hoch"
    return "sehr hoch"


def wind_effort(watts) -> float:
    """0..1 share of the worst wind effort, for sizing a map arrow: 0 at calm, tailwind or unknown."""
    if not _finite(watts) or watts <= 0:
        return 0.0
    return _clamp01(watts / WIND_POWER_CURVE[-1][0])


def score_sample(sample: dict, config: RideQualityConfig = RIDE_QUALITY) -> dict:
    """A stored sample dict with the served ride-quality fields added (a copy)."""
    rq = ride_score(sample, config)
    return {
        **sample,
        "ride_score": round(rq.score, 4) if rq else None,
        "ride_label": rq.label if rq else None,
        "wind_effort_level": wind_effort_level(sample.get("wind_power_w")),
    }
