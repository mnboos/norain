"""Plotly figure generation for route weather forecasts.

Generates Plotly figure JSON dicts that can be rendered client-side via plotly.js
or server-side via kaleido (e.g., for email). Each figure uses distance along the
route as the x-axis so the charts show weather progression along the journey.
"""

from .weather_schemas import RouteWeatherOut

import plotly.graph_objects as go


def generate_forecast_figures(forecast: RouteWeatherOut) -> list[dict]:
    """Return Plotly figure JSONs for a route weather forecast.

    Returns 3 figures:
      1. Temperature along the route (line + fill)
      2. Precipitation: rain_mm (bar) + probability % (line on secondary axis)
      3. Wind: speed, gusts, and headwind (multi-line)

    The route itself is drawn client-side by the MapLibre ``NiceMap`` component,
    not as a Plotly figure.

    When ``forecast.samples`` is empty (no weather data available), returns three
    placeholder figures so the front-end always receives valid Plotly JSON.
    """
    if not forecast.samples:
        return _empty_figures()

    # Compute cumulative distance for each sample
    total_km = forecast.total_distance_m / 1000.0
    total_s = max(forecast.total_seconds, 1)

    distances_km = []
    for s in forecast.samples:
        frac = s.elapsed_s / total_s
        distances_km.append(round(frac * total_km, 2))

    figures = [
        _temperature_figure(distances_km, forecast),
        _precipitation_figure(distances_km, forecast),
        _wind_figure(distances_km, forecast),
    ]
    return figures


def _empty_figures() -> list[dict]:
    """Return three placeholder figures indicating no data is available."""
    import plotly.graph_objects as go

    msg = "Keine Wetterdaten für diese Strecke verfügbar"
    figures: list[dict] = []
    for _ in range(3):
        fig = go.Figure()
        fig.add_annotation(
            text=msg,
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
            font=dict(size=14, color="gray"),
        )
        fig.update_layout(
            template="plotly_white",
            margin=dict(l=12, r=12, t=40, b=12),
            autosize=True,
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
        )
        figures.append(fig.to_plotly_json())
    return figures


def _temperature_figure(distances_km: list[float], forecast: RouteWeatherOut) -> dict:
    import plotly.graph_objects as go

    temps = [s.temp for s in forecast.samples]
    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=distances_km,
            y=temps,
            # mode="lines+markers",
            mode="lines",
            name="Temperatur",
            line=dict(color="#e74c3c", width=2, shape="spline"),
            # marker=dict(size=0),
            # fill="tozeroy",
            # fillcolor="rgba(231, 76, 60, 0.1)",
        )
    )

    # Add reference lines at 5°C and 25°C
    y_min = min(temps) - 2
    y_max = max(temps) + 2
    # fig.add_hline(y=5, line_dash="dash", line_color="lightblue", opacity=0.5, annotation_text="5°C")
    # fig.add_hline(y=25, line_dash="dash", line_color="orange", opacity=0.5, annotation_text="25°C")

    fig.update_layout(
        title="Temperatur entlang der Strecke",
        yaxis_range=[min(y_min, 0), max(y_max, 30)],
        template="plotly_white",
        margin=dict(l=12, r=12, t=40, b=12),
        autosize=True,
        # Axes are hidden: the cards are small squares and the values are shown on hover.
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
    )
    return fig.to_plotly_json()


def _precipitation_figure(distances_km: list[float], forecast: RouteWeatherOut) -> dict:

    fig = go.Figure()

    # Rain bars
    rain_mm = [s.rain_mm for s in forecast.samples]
    fig.add_trace(
        go.Bar(
            x=distances_km,
            y=rain_mm,
            name="Niederschlag",
            marker_color="rgba(52, 152, 219, 0.7)",
            marker_line=dict(width=0),
        )
    )

    # POP line on secondary axis
    pops = [s.pop for s in forecast.samples]
    if any(p is not None for p in pops):
        # Fill None values with 0 for plotting
        pop_values = [(p or 0) * 100 for p in pops]
        fig.add_trace(
            go.Scatter(
                x=distances_km,
                y=pop_values,
                mode="lines",
                name="Regenwahrsch.",
                line=dict(color="#e67e22", width=2, dash="dot"),
                yaxis="y2",
            )
        )

    fig.update_layout(
        title="Niederschlag entlang der Strecke",
        yaxis2=dict(
            overlaying="y",
            side="right",
            range=[0, 100],
            visible=False,
        ),
        template="plotly_white",
        margin=dict(l=12, r=12, t=40, b=12),
        autosize=True,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    return fig.to_plotly_json()


def _wind_figure(distances_km: list[float], forecast: RouteWeatherOut) -> dict:
    import plotly.graph_objects as go

    fig = go.Figure()

    # Wind speed
    wind_speeds = [s.wind_speed for s in forecast.samples]
    fig.add_trace(
        go.Scatter(
            x=distances_km,
            y=wind_speeds,
            mode="lines",
            name="Wind",
            line=dict(color="#2ecc71", width=2),
        )
    )

    # Wind gusts (if available)
    gusts = [s.wind_gust for s in forecast.samples]
    if any(g is not None for g in gusts):
        gust_values = [g or 0 for g in gusts]
        fig.add_trace(
            go.Scatter(
                x=distances_km,
                y=gust_values,
                mode="lines",
                name="Böen",
                line=dict(color="#2ecc71", width=1, dash="dash"),
            )
        )

    # Headwind (only show when > 0)
    headwinds = [s.headwind for s in forecast.samples]
    fig.add_trace(
        go.Scatter(
            x=distances_km,
            y=headwinds,
            mode="lines",
            name="Gegenwind",
            line=dict(color="#e74c3c", width=1.5),
            fill="tozeroy",
            fillcolor="rgba(231, 76, 60, 0.1)",
        )
    )

    fig.add_hline(y=0, line_dash="solid", line_color="gray", opacity=0.3)

    fig.update_layout(
        title="Wind entlang der Strecke",
        template="plotly_white",
        margin=dict(l=12, r=12, t=40, b=12),
        autosize=True,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    return fig.to_plotly_json()
