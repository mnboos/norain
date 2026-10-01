"""MET Norway (yr.no) as the free forecast fallback: parsing, symbol codes, the fallback order."""

import os
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx
from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from . import grid
from .departures import cell_covers
from .models import ForecastCell

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
DAY = date(2026, 9, 30)
T0 = datetime(2026, 9, 30, 10, tzinfo=UTC)


def _step(at: datetime, *, temp=12.5, period="next_1_hours", rain=0.4, pop=35.0, symbol="lightrain"):
    return {
        "time": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data": {
            "instant": {
                "details": {
                    "air_temperature": temp,
                    "wind_speed": 5.0,
                    "wind_speed_of_gust": 10.0,
                    "wind_from_direction": 370.0,
                }
            },
            period: {
                "summary": {"symbol_code": f"{symbol}_day"},
                "details": {"precipitation_amount": rain, "probability_of_precipitation": pop},
            },
        },
    }


def met_data(**kw) -> dict:
    """Hourly for three steps, then one six-hourly step, as Locationforecast answers."""
    steps = [_step(T0 + timedelta(hours=h)) for h in range(3)]
    steps.append(_step(T0 + timedelta(hours=6), period="next_6_hours", rain=3.0, pop=80.0, symbol="heavysnow"))
    return {"type": "Feature", "properties": {"meta": {}, "timeseries": steps}, **kw}


class SymbolCodeTests(SimpleTestCase):
    def test_symbols_map_to_the_wmo_codes_open_meteo_sends(self):
        cases = {
            "clearsky_day": 0,
            "partlycloudy_polartwilight": 2,
            "fog": 45,
            "lightrainshowers_night": 80,
            "heavyrain": 65,
            "sleet": 71,
            "heavysnowshowers_day": 86,
            "rainandthunder": 95,
            "heavyrainshowersandthunder_night": 95,
            # Snow and sleet in a thunderstorm stay snow, so the frost floor stays under them.
            "snowandthunder": 73,
            "lightssleetshowersandthunder_day": 85,  # MET's own spelling
            "lightssnowshowersandthunder_night": 85,
            "somethingnew": None,
            None: None,
        }
        for symbol, code in cases.items():
            with self.subTest(symbol=symbol):
                self.assertEqual(grid.met_weather_code(symbol), code)


class ParserTests(SimpleTestCase):
    def test_an_hourly_step(self):
        sample = grid._from_met(met_data(), T0 + timedelta(minutes=50))
        self.assertEqual(sample["source"], "met-norway")
        self.assertEqual(sample["temp"], 12.5)
        self.assertEqual(sample["rain_mm"], 0.4)
        self.assertEqual(sample["precipitation_interval_s"], 3600)
        self.assertEqual(sample["wind_speed"], 18.0)  # m/s -> km/h
        self.assertEqual(sample["wind_gust"], 36.0)
        self.assertEqual(sample["wind_dir"], 10.0)
        self.assertEqual(sample["pop"], 0.35)
        self.assertEqual(sample["weather_code"], 61)

    def test_a_six_hourly_step_carries_its_interval(self):
        sample = grid._from_met(met_data(), T0 + timedelta(hours=6))
        self.assertEqual((sample["rain_mm"], sample["precipitation_interval_s"]), (3.0, 21600))
        self.assertEqual(sample["weather_code"], 75)

    def test_a_naive_eta_is_swiss_wall_time(self):
        naive = (T0 + timedelta(hours=2)).astimezone(ZoneInfo("Europe/Zurich")).replace(tzinfo=None)
        data = met_data()
        data["properties"]["timeseries"][2]["data"]["instant"]["details"]["air_temperature"] = 3.0
        self.assertEqual(grid._from_met(data, naive)["temp"], 3.0)

    def test_other_formats_are_rejected(self):
        self.assertIsNone(grid._from_met({"hourly": {"time": ["2026-09-30T12:00"]}}, T0))
        self.assertIsNone(grid._from_met({"hourly": [{"dt": 0}]}, T0))
        self.assertIsNone(grid._from_met({"properties": {"timeseries": []}}, T0))

    def test_extract_sample_routes_by_source(self):
        self.assertEqual(grid.extract_sample(met_data(), T0, "met-norway")["source"], "met-norway")

    def test_coverage_is_the_timeseries_not_the_clamp(self):
        data = met_data()
        self.assertTrue(cell_covers(data, T0 + timedelta(hours=1), "met-norway"))
        self.assertFalse(cell_covers(data, T0 + timedelta(hours=9), "met-norway"))
        del data["properties"]["timeseries"][0]["data"]["next_1_hours"]
        self.assertFalse(cell_covers(data, T0, "met-norway"))


def _transport(handler):
    real = httpx.AsyncClient

    def client(*args, **kwargs):
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)

    return patch("core.grid.httpx.AsyncClient", side_effect=client)


class FetchTests(SimpleTestCase):
    def test_identifies_itself_and_keeps_last_modified(self):
        seen = {}

        def handler(request):
            seen.update(request.headers)
            return httpx.Response(200, json=met_data(), headers={"Last-Modified": "Wed, 30 Sep 2026 09:00:00 GMT"})

        with _transport(handler):
            data = async_to_sync(grid._fetch_met)(47.37, 8.54)
        self.assertEqual(seen["user-agent"], grid.MET_USER_AGENT)
        self.assertNotIn("if-modified-since", seen)
        self.assertEqual(data["_met_last_modified"], "Wed, 30 Sep 2026 09:00:00 GMT")

    def test_a_304_is_not_modified(self):
        seen = {}

        def handler(request):
            seen.update(request.headers)
            return httpx.Response(304)

        with _transport(handler):
            data = async_to_sync(grid._fetch_met)(47.37, 8.54, "Wed, 30 Sep 2026 09:00:00 GMT")
        self.assertIs(data, grid.MET_NOT_MODIFIED)
        self.assertEqual(seen["if-modified-since"], "Wed, 30 Sep 2026 09:00:00 GMT")


def _status_error(status):
    response = httpx.Response(status, request=httpx.Request("GET", "https://x.test"))
    return httpx.HTTPStatusError("boom", request=response.request, response=response)


@override_settings(CACHES=LOCMEM_CACHE)
class FallbackOrderTests(TestCase):
    def setUp(self):
        cache.clear()

    def _get(self, **kw):
        return async_to_sync(grid.get_or_fetch_forecast_cell)(47.0, 9.0, DAY, 2, **kw)

    def test_met_comes_before_the_paid_owm(self):
        with (
            patch.dict(os.environ, {"OPENWEATHERMAP_API_KEY": "k"}),
            patch.object(grid, "_fetch_open_meteo", AsyncMock(side_effect=_status_error(503))),
            patch.object(grid, "_fetch_met", AsyncMock(return_value=met_data())),
            patch.object(grid, "_fetch_owm", AsyncMock(side_effect=AssertionError("MET answered"))),
        ):
            cell = self._get()
        self.assertEqual(cell.source, "met-norway")

    def test_owm_only_when_met_fails_too(self):
        with (
            patch.dict(os.environ, {"OPENWEATHERMAP_API_KEY": "k"}),
            patch.object(grid, "_fetch_open_meteo", AsyncMock(side_effect=_status_error(503))),
            patch.object(grid, "_fetch_met", AsyncMock(side_effect=_status_error(500))),
            patch.object(grid, "_fetch_owm", AsyncMock(return_value={"hourly": [{"dt": 0}]})),
        ):
            cell = self._get()
        self.assertEqual(cell.source, "openweathermap")

    def test_a_304_reuses_the_last_response_for_the_place(self):
        previous = met_data(_met_last_modified="Wed, 30 Sep 2026 09:00:00 GMT")
        old = ForecastCell.objects.create(
            lat_r=47.0, lon_r=9.0, day_key=DAY - timedelta(days=1), forecast_days=2, source="met-norway", data=previous
        )
        ForecastCell.objects.filter(pk=old.pk).update(fetched_at=datetime.now(UTC) - timedelta(days=1))
        met = AsyncMock(return_value=grid.MET_NOT_MODIFIED)
        with (
            patch.object(grid, "_fetch_open_meteo", AsyncMock(side_effect=_status_error(503))),
            patch.object(grid, "_fetch_met", met),
        ):
            cell = self._get()
        met.assert_awaited_once_with(47.0, 9.0, "Wed, 30 Sep 2026 09:00:00 GMT")
        self.assertEqual((cell.source, cell.day_key, cell.data), ("met-norway", DAY, previous))

    def test_a_throttled_open_meteo_still_defers_rather_than_fall_back(self):
        from .ratelimit import ProviderThrottled

        with (
            patch.object(grid, "_fetch_open_meteo", AsyncMock(side_effect=_status_error(429))),
            patch.object(grid, "_fetch_met", AsyncMock(side_effect=AssertionError("waits for Open-Meteo"))),
            self.assertRaises(ProviderThrottled),
        ):
            self._get(allow_fallback=False)

    def test_the_cache_prefers_open_meteo_then_met_then_owm(self):
        for source in ("openweathermap", "met-norway"):
            ForecastCell.objects.create(
                lat_r=47.0, lon_r=9.0, day_key=DAY, forecast_days=2, source=source, data=met_data()
            )
        self.assertEqual(
            async_to_sync(grid.get_cached_cells)([(47.0, 9.0)], [(DAY, 2)])[0][(47.0, 9.0, DAY)].source, "met-norway"
        )
        self.assertEqual(grid._find_forecast_cell_sync(47.0, 9.0, DAY, 2).source, "met-norway")
        ForecastCell.objects.create(lat_r=47.0, lon_r=9.0, day_key=DAY, forecast_days=2, source="open-meteo", data={})
        self.assertEqual(
            async_to_sync(grid.get_cached_cells)([(47.0, 9.0)], [(DAY, 2)])[0][(47.0, 9.0, DAY)].source, "open-meteo"
        )
        self.assertEqual(grid._find_forecast_cell_sync(47.0, 9.0, DAY, 2).source, "open-meteo")
