"""Route section computation: group consecutive weather samples by condition.

Enables the UI to show "Dry: km 0–12, Rain: km 12–18, Dry: km 18–22" style
breakdowns. Computed on the backend so email rendering can use the same sections.
"""

from .api.route_weather import RouteSection, WeatherSample


def _condition(rain_mm: float) -> str:
    if rain_mm < 0.1:
        return "dry"
    elif rain_mm < 2.5:
        return "rain"
    else:
        return "heavy_rain"


CONDITION_LABELS = {
    "dry": "Trocken",
    "rain": "Regen",
    "heavy_rain": "Starker Regen",
}


def _format_time(elapsed_s: int) -> str:
    """Format elapsed seconds as HH:MM."""
    h = elapsed_s // 3600
    m = (elapsed_s % 3600) // 60
    return f"{h}:{m:02d}"


def compute_sections(
    samples: list[WeatherSample],
    total_distance_m: float,
) -> list[RouteSection]:
    """Group consecutive samples by weather condition.

    Returns a list of RouteSection with start/end km, times, and per-section
    max rain/wind and temp range.
    """
    if not samples:
        return []

    total_km = total_distance_m / 1000.0
    total_s = max(samples[-1].elapsed_s if samples else 1, 1)
    sections: list[RouteSection] = []

    current_cond = _condition(samples[0].rain_mm)
    section_samples = [samples[0]]

    for s in samples[1:]:
        cond = _condition(s.rain_mm)
        if cond == current_cond:
            section_samples.append(s)
        else:
            sections.append(_build_section(section_samples, total_km, total_s))
            current_cond = cond
            section_samples = [s]

    # Flush last section
    if section_samples:
        sections.append(_build_section(section_samples, total_km, total_s))

    return sections


def _build_section(
    samples: list[WeatherSample],
    total_km: float,
    total_s: float,
) -> RouteSection:
    first = samples[0]
    last = samples[-1]

    start_km = round((first.elapsed_s / total_s) * total_km, 2)
    end_km = round((last.elapsed_s / total_s) * total_km, 2)
    start_time = _format_time(first.elapsed_s)
    end_time = _format_time(last.elapsed_s)
    condition = _condition(max(s.rain_mm for s in samples))
    max_rain_mm = round(max(s.rain_mm for s in samples), 2)
    winds = [s.headwind for s in samples if s.headwind is not None]
    max_headwind = round(max(winds), 1) if winds else None
    temps = [s.temp for s in samples]
    temp_min = round(min(temps), 1)
    temp_max = round(max(temps), 1)

    return RouteSection(
        start_km=start_km,
        end_km=end_km,
        start_time=start_time,
        end_time=end_time,
        condition=condition,
        max_rain_mm=max_rain_mm,
        max_headwind=max_headwind,
        temp_min=temp_min,
        temp_max=temp_max,
    )
