"""Compact Plotly forecasts with ensemble spread and elapsed ride time."""

import plotly.graph_objects as go

from .api.route_weather import RouteWeatherOut

# Visual-only smoothing: the curve still passes through every real sample (the markers), but a
# spline can slightly over/undershoot between two points at abrupt changes. Moderate smoothing
# keeps that small; use {"shape": "linear"} for straight segments only.
SMOOTH = {"shape": "spline", "smoothing": 0.7}

# Cool, orange-free series hues at mid lightness, so one hex reads on both the light (#ffffff) and
# the dark (#1c2533) card. Validated for color-blind separation in the wind chart's order
# (teal, violet, rose, blue): blue next to violet, or rose next to teal, would not pass. The map
# markers and the light theme (frontend/src/utils/theme.ts) reuse these hues.
TEAL = "#1a9e8f"
VIOLET = "#7b5fd6"
ROSE = "#d24d78"
BLUE = "#2f7fd8"


def _fill(color: str, alpha: float) -> str:
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha})"


def _ensemble_traces(fig, forecast, metric, name, label, color, unit, visible=True) -> bool:
    """Band + median for one metric; False when there is no ensemble data to draw."""
    ranges = [s.uncertainty.metrics.get(metric) if s.uncertainty else None for s in forecast.samples]
    if not any(r and r.median is not None for r in ranges):
        return False
    x = [s.elapsed_s / 60 for s in forecast.samples]
    custom = [[i, s.eta[11:16]] for i, s in enumerate(forecast.samples)]
    common = {"x": x, "customdata": custom, "legendgroup": metric, "visible": visible, "connectgaps": False}
    # Separate each contiguous run: Plotly's filled polygons must not bridge missing data.
    run = []
    runs = []
    for i, value in enumerate(ranges):
        if value and value.median is not None:
            run.append(i)
        elif run:
            runs.append(run)
            run = []
    if run:
        runs.append(run)
    for indices in runs:
        for bound, fill_mode in (("p10", None), ("p90", "tonexty")):
            fig.add_trace(
                go.Scatter(
                    x=[x[i] for i in indices],
                    y=[getattr(ranges[i], bound) for i in indices],
                    mode="lines",
                    line={"width": 0, **SMOOTH},
                    fill=fill_mode,
                    fillcolor=_fill(color, 0.16),
                    legendgroup=metric,
                    visible=visible,
                    showlegend=False,
                    hoverinfo="skip",
                )
            )
    # The legend names only the metric: the caption above the charts explains band/median/dotted,
    # and one entry per metric toggles its whole group (band, median and single forecast).
    fig.add_trace(
        go.Scatter(
            **common,
            y=[r.median if r else None for r in ranges],
            mode="lines+markers",
            marker={"size": 4},
            line={"color": color, "width": 2, **SMOOTH},
            name=name,
            hovertemplate=f"%{{customdata[1]}} Uhr · %{{y:.1f}} {unit}<extra>{label}: Median</extra>",
        )
    )
    return True


def generate_forecast_figures(forecast: RouteWeatherOut) -> list[dict]:
    """Three figures; customdata carries the sample index for map/detail selection."""
    figures = []
    x = [s.elapsed_s / 60 for s in forecast.samples]
    custom = [[i, s.eta[11:16]] for i, s in enumerate(forecast.samples)]
    # (metric, legend name, hover label, color): legend names stay short so the wind legend fits
    # one row; the hover label can be more precise.
    definitions = [
        ("Temperatur", "°C", [("temperature", "Temperatur", "Temperatur", ROSE)]),
        ("Niederschlag", "mm/h", [("precipitation", "Niederschlag", "Niederschlag", BLUE)]),
        (
            "Wind",
            "km/h",
            [
                ("windSpeed", "Wind", "Wind", TEAL),
                ("windGust", "Böen", "Böen", VIOLET),
                ("headwind", "Gegenwind", "Gegen-(+)/Rückenwind(−)", ROSE),
                ("crosswind", "Seitenwind", "Seitenwind", BLUE),
            ],
        ),
    ]
    for number, (title, unit, metrics) in enumerate(definitions):
        fig = go.Figure()
        show_legend = False
        if not forecast.samples:
            fig.add_annotation(
                text="Keine Wetterdaten für diese Strecke verfügbar",
                xref="paper",
                yref="paper",
                x=0.5,
                y=0.5,
                showarrow=False,
            )
        else:
            with_ensemble = {
                metric
                for index, (metric, name, label, color) in enumerate(metrics)
                if _ensemble_traces(
                    fig, forecast, metric, name, label, color, unit, True if index == 0 else "legendonly"
                )
            }
            if number == 0:
                series = [("temperature", [s.temp for s in forecast.samples])]
            elif number == 1:
                series = [("precipitation", [s.rain_rate_mm_h for s in forecast.samples])]
            else:
                series = [
                    ("windSpeed", [s.wind_speed for s in forecast.samples]),
                    ("windGust", [s.wind_gust for s in forecast.samples]),
                    ("headwind", [s.headwind for s in forecast.samples]),
                ]
            # The dotted single forecast joins its metric's legend group and color; it only gets
            # its own legend entry when there is no ensemble median to carry the group.
            lookup = {metric: (index, name, label, color) for index, (metric, name, label, color) in enumerate(metrics)}
            for metric, values in series:
                index, name, label, color = lookup[metric]
                fig.add_trace(
                    go.Scatter(
                        x=x,
                        y=values,
                        customdata=custom,
                        mode="lines+markers",
                        marker={"size": 3},
                        line={"dash": "dot", "width": 1.5, "color": color, **SMOOTH},
                        connectgaps=False,
                        name=name,
                        legendgroup=metric,
                        showlegend=metric not in with_ensemble,
                        visible=True if index == 0 else "legendonly",
                        hovertemplate=f"%{{customdata[1]}} Uhr · %{{y:.1f}} {unit}<extra>{label}: Einzelprognose</extra>",
                    )
                )
            # A lone series needs no legend - the chart title already names it.
            show_legend = len(metrics) > 1
            if number == 1 and any(s.pop is not None for s in forecast.samples):
                show_legend = True
                fig.add_trace(
                    go.Scatter(
                        x=x,
                        y=[s.pop * 100 if s.pop is not None else None for s in forecast.samples],
                        customdata=custom,
                        name="Regenrisiko (%)",
                        legendgroup="pop",
                        mode="lines+markers",
                        marker={"size": 3},
                        connectgaps=False,
                        line={"color": TEAL, "dash": "dot", "width": 1.5, **SMOOTH},
                        yaxis="y2",
                        hovertemplate="%{customdata[1]} Uhr · %{y:.0f}%<extra>Regenrisiko am Punkt</extra>",
                    )
                )
                # Plotly otherwise syncs an overlaying axis's ticks to the primary axis, giving
                # labels like 23.7 / 71.3 %; round percentage steps read better without gridlines.
                # zeroline off: this axis starts at 0 just like the primary one (which keeps its
                # zeroline as the rain baseline), so both would be drawn on the same pixel row and
                # the single faint baseline the frontend theme intends would read twice as heavy.
                fig.update_layout(
                    yaxis2={
                        "overlaying": "y",
                        "side": "right",
                        "range": [0, 100],
                        "title": "%",
                        "tickmode": "linear",
                        "dtick": 25,
                        "zeroline": False,
                    }
                )
        fig.update_layout(
            # Title pinned top-left; the legend (if any) sits as one compact row between title and
            # plot, so it never competes with the x axis for space. Both align with the plot's left
            # edge (a container-anchored horizontal legend makes Plotly stack it vertically).
            title={
                "text": title,
                "font": {"size": 14},
                "x": 0,
                "xref": "paper",
                "xanchor": "left",
                "y": 1,
                "yref": "container",
                "yanchor": "top",
                "pad": {"t": 10},
            },
            template="plotly_white",
            autosize=True,
            hovermode="closest",
            showlegend=show_legend,
            # The legend is anchored to the plot's top and grows upward: leave room for two rows (the
            # wind grid always, precipitation on narrow tiles) so it never runs into the title.
            margin={"l": 45, "r": 40 if number == 1 else 12, "t": 82 if show_legend else 38, "b": 42},
            xaxis={"title": {"text": "Fahrzeit (min)", "standoff": 4}, "zeroline": False},
            # Rain rate can't be negative: without this, an all-dry route autoranges to -1..1 mm/h
            # (and a spline dipping below 0 near a rain onset would be drawn as negative rain).
            # The rain chart gets no zeroline: precipitation cannot go negative and rangemode pins
            # the bottom of the range to 0, so the line only ever redraws the plot's bottom border.
            yaxis={"title": unit, "zeroline": number != 1, **({"rangemode": "nonnegative"} if number == 1 else {})},
            legend={
                "orientation": "h",
                "x": 0,
                "xanchor": "left",
                "y": 1.02,
                "yanchor": "bottom",
                "font": {"size": 11},
                "itemwidth": 30,
                "tracegroupgap": 0,
                "bgcolor": "rgba(0,0,0,0)",
                # Plotly wraps a horizontal legend at the plot width, which left the wind chart's
                # fourth entry alone on a second row; a fixed 2x2 grid stays tidy at any width
                # (0.5 overflows the row and stacks all four, so leave some slack).
                **({"entrywidth": 0.4, "entrywidthmode": "fraction"} if len(metrics) > 2 else {}),
            },
        )
        figures.append(fig.to_plotly_json())
    return figures
