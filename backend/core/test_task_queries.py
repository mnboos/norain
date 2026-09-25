"""Query budgets and behavior at the scheduler/planner batching boundaries."""

from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from core import departures
from core.claims import claim_cell
from core.entitlements import allowed_route_ids, briefing_route_ids, briefing_route_ids_by_owner
from core.grid import ENSEMBLE_REQUEST_VERSION, MAX_CELL_AGE, get_cached_cell_keys, get_cached_cells
from core.models import EnsembleCell, ForecastCell, ForecastJob, Plan, RecurringRoute, Subscription, User, route_point
from core.schedule import LOCAL_TZ, local_today
from core.tasks import _plan_forecast_job_async, _prewarm_routes, _scan_route_forecasts_async, _settle_cell


class BulkCellAvailabilityTests(TestCase):
    day = date(2026, 9, 18)

    def forecast(self, lat=47.0, lon=9.0, **values):
        return ForecastCell.objects.create(
            **{
                "lat_r": lat,
                "lon_r": lon,
                "day_key": self.day,
                "forecast_days": 3,
                "source": "open-meteo",
                "data": {"hourly": {"large": []}},
                **values,
            }
        )

    def ensemble(self, lat=47.0, lon=9.0, **values):
        return EnsembleCell.objects.create(
            **{
                "lat_r": lat,
                "lon_r": lon,
                "day_key": self.day,
                "forecast_days": 3,
                "data": {"_norain_request_version": ENSEMBLE_REQUEST_VERSION},
                **values,
            }
        )

    def lookup(self, cells, windows=None):
        return async_to_sync(get_cached_cell_keys)(cells, windows if windows is not None else [(self.day, 3)])

    def test_query_budget_and_projection_are_independent_of_cell_count(self):
        self.forecast()
        self.ensemble()
        for count in (1, 100):
            with self.subTest(count=count), self.assertNumQueries(2) as queries:
                forecasts, ensembles = self.lookup([(47.0, round(9 + i / 100, 2)) for i in range(count)])
                self.assertEqual(forecasts, {(47.0, 9.0, self.day)})
                self.assertEqual(ensembles, forecasts)
            for query in queries.captured_queries:
                projection = query["sql"].split(" FROM ")[0]
                self.assertNotIn('"data"', projection)

    def test_empty_inputs_and_batch_boundary(self):
        with self.assertNumQueries(0):
            self.assertEqual(self.lookup([]), (set(), set()))
            self.assertEqual(self.lookup([(47, 9)], []), (set(), set()))
        self.forecast(lon=14)
        self.ensemble(lon=14)
        with self.assertNumQueries(4):
            forecasts, ensembles = self.lookup([(47.0, round(9 + i / 100, 2)) for i in range(501)])
        self.assertEqual(forecasts, {(47.0, 14.0, self.day)})
        self.assertEqual(ensembles, forecasts)

    def test_freshness_horizon_sources_and_ensemble_version(self):
        self.forecast()
        self.forecast(lon=9.01, source="openweathermap")
        stale = self.forecast(lon=9.02)
        ForecastCell.objects.filter(pk=stale.pk).update(
            fetched_at=datetime.now(UTC) - MAX_CELL_AGE - timedelta(seconds=1)
        )
        self.forecast(lon=9.03, forecast_days=2)
        self.forecast(lon=9.04, source="unrecognized")
        self.ensemble()
        self.ensemble(lon=9.01, data={"_norain_request_version": ENSEMBLE_REQUEST_VERSION - 1})
        self.ensemble(lon=9.02, data={})
        stale_ensemble = self.ensemble(lon=9.03)
        EnsembleCell.objects.filter(pk=stale_ensemble.pk).update(
            fetched_at=datetime.now(UTC) - MAX_CELL_AGE - timedelta(seconds=1)
        )
        self.ensemble(lon=9.04, forecast_days=2)
        forecasts, ensembles = self.lookup([(47, round(9 + i / 100, 2)) for i in range(6)])
        self.assertEqual(forecasts, {(47, 9, self.day), (47, 9.01, self.day)})
        self.assertEqual(ensembles, {(47, 9, self.day)})

    def test_fallback_when_primary_is_inadequate_and_primary_metric_preference(self):
        self.forecast(forecast_days=2)
        self.forecast(source="openweathermap")
        self.forecast(lon=9.01)
        self.forecast(lon=9.01, source="openweathermap")
        with patch("core.grid.emit") as emit:
            forecasts, _ = self.lookup([(47, 9), (47, 9.01)])
        self.assertEqual(forecasts, {(47, 9, self.day), (47, 9.01, self.day)})
        hits = [call.kwargs for call in emit.call_args_list if call.kwargs["kind"] == "forecast"]
        self.assertEqual([hit["source"] for hit in hits], ["openweathermap", "open-meteo"])

    def test_exact_coordinates_dates_and_duplicate_horizons(self):
        tomorrow = self.day + timedelta(days=1)
        self.forecast(lon=9.01)
        self.forecast(lat=47.01)
        self.forecast(day_key=tomorrow, forecast_days=5)
        self.ensemble(day_key=tomorrow, forecast_days=5)
        self.forecast(forecast_days=2)
        self.ensemble(forecast_days=2)
        with self.assertNumQueries(2):
            forecasts, ensembles = self.lookup(
                [(47, 9), (47.01, 9.01), (47, 9)],
                [(self.day.isoformat(), 2), (self.day, 3), (tomorrow, 5)],
            )
        self.assertEqual(forecasts, {(47, 9, tomorrow)})
        self.assertEqual(ensembles, forecasts)

    def test_freshness_cutoff_is_inclusive(self):
        now = datetime.now(UTC)
        self.forecast()
        self.ensemble()
        ForecastCell.objects.update(fetched_at=now - MAX_CELL_AGE)
        EnsembleCell.objects.update(fetched_at=now - MAX_CELL_AGE)
        with patch("core.grid.datetime", wraps=datetime) as clock:
            clock.now.return_value = now
            forecasts, ensembles = self.lookup([(47, 9)])
        self.assertEqual(forecasts, {(47, 9, self.day)})
        self.assertEqual(ensembles, forecasts)


class BulkCellDataTests(BulkCellAvailabilityTests):
    """Apply the availability contract to full payload loading as well."""

    def lookup(self, cells, windows=None):
        forecasts, ensembles = async_to_sync(get_cached_cells)(
            cells, windows if windows is not None else [(self.day, 3)]
        )
        return set(forecasts), set(ensembles)

    def test_query_budget_and_projection_are_independent_of_cell_count(self):
        forecast = self.forecast()
        ensemble = self.ensemble()
        for count in (1, 100):
            with self.subTest(count=count), self.assertNumQueries(2):
                forecasts, ensembles = async_to_sync(get_cached_cells)(
                    [(47, round(9 + i / 100, 2)) for i in range(count)], [(self.day, 3)]
                )
                self.assertEqual(forecasts[(47, 9, self.day)].data, forecast.data)
                self.assertEqual(ensembles[(47, 9, self.day)].data, ensemble.data)

    def test_snapshot_reuses_loaded_cells_and_misses_across_days(self):
        from core.weather import WeatherSnapshot

        self.forecast()
        snapshot = WeatherSnapshot(3)
        points = [{"lat_r": 47, "lon_r": lon} for lon in (9, 9, 9.01)]
        days = [self.day.isoformat(), (self.day + timedelta(days=1)).isoformat()]
        with self.assertNumQueries(2):
            async_to_sync(snapshot.preload)(points, days)
        with self.assertNumQueries(0):
            async_to_sync(snapshot.preload)(points, days)
            for day in days:
                for point in points:
                    for kind in ("forecast", "ensemble"):
                        cell = async_to_sync(snapshot.cell)(kind, point["lat_r"], point["lon_r"], day)
                        self.assertEqual(
                            cell is not None, kind == "forecast" and day == days[0] and point["lon_r"] == 9
                        )

    def test_computation_query_budget_and_output(self):
        from core.weather import compute_route_weather

        self.forecast(
            forecast_days=16,
            data={
                "hourly": {
                    "time": [f"{self.day}T12:00"],
                    "temperature_2m": [15],
                    "precipitation": [0],
                    "wind_speed_10m": [12],
                    "wind_direction_10m": [90],
                }
            },
        )
        point = {"lat": 47, "lon": 9, "lat_r": 47, "lon_r": 9, "elapsed_s": 0}
        baseline = None
        for count in (1, 100):
            with (
                self.subTest(count=count),
                self.assertNumQueries(2),
                patch("core.weather.get_or_fetch_forecast_cell", side_effect=AssertionError("provider fetch")),
                patch("core.weather.get_or_fetch_ensemble_cell", side_effect=AssertionError("provider fetch")),
            ):
                result = async_to_sync(compute_route_weather)(
                    47,
                    9,
                    47,
                    9,
                    "bike",
                    f"{self.day}T12:00",
                    sample_points=[point] * count,
                    polyline=[[9, 47], [9, 47]],
                    total_seconds=0,
                    total_distance_m=0,
                    cache_only=True,
                    include_segments=False,
                )
            self.assertEqual(len(result.samples), count)
            current = result.samples[0].model_dump()
            if baseline is None:
                baseline = current
            self.assertEqual(current, baseline)

    def test_skip_ensemble(self):
        with self.assertNumQueries(1):
            _, ensembles = async_to_sync(get_cached_cells)([(47, 9)], [(self.day, 3)], include_ensemble=False)
        self.assertEqual(ensembles, {})


class BulkEligibilityTests(TestCase):
    def owner(self, **subscription):
        name = f"rider-{User.objects.count()}"
        user = User.objects.create(username=name, email=f"{name}@example.test")
        Subscription.objects.create(user=user, plan=Plan.PRO, **subscription)
        return user

    def route(self, owner, **values):
        return RecurringRoute.objects.create(
            **{
                "owner": owner,
                "name": "Commute",
                "start_point": route_point(47, 9),
                "destination_point": route_point(47.01, 9.01),
                "schedule_cron": "0 * * * *",
                "briefing_channel": "email",
                **values,
            }
        )

    def test_eligibility_and_scheduler_queries_do_not_scale_per_owner(self):
        owners = [self.owner() for _ in range(20)]
        routes = [self.route(owner) for owner in owners]
        for count in (1, 20):
            ids = [owner.pk for owner in owners[:count]]
            with self.subTest(count=count), self.assertNumQueries(2):
                self.assertEqual(
                    briefing_route_ids_by_owner(ids),
                    {owner.pk: [route.pk] for owner, route in zip(owners[:count], routes[:count], strict=True)},
                )
        with self.assertNumQueries(3):
            self.assertEqual({route.pk for route in async_to_sync(_prewarm_routes)()}, {route.pk for route in routes})
        with self.assertNumQueries(3):
            self.assertEqual([route.pk for route in async_to_sync(_prewarm_routes)(owners[0].pk)], [routes[0].pk])

    def test_empty_duplicate_and_batched_owners(self):
        with self.assertNumQueries(0):
            self.assertEqual(briefing_route_ids_by_owner([]), {})
        owners = User.objects.bulk_create(
            [User(username=f"batch-{i}", email=f"batch-{i}@example.test") for i in range(501)]
        )
        with self.assertNumQueries(4):
            result = briefing_route_ids_by_owner([owner.pk for owner in owners] + [owners[0].pk])
        self.assertEqual(result, {owner.pk: [] for owner in owners})

    def test_route_quota_includes_roots_without_briefing_and_honors_priority(self):
        owner = self.owner()
        roots = [self.route(owner, briefing_channel="") for _ in range(20)]
        later = self.route(owner)
        self.assertEqual(briefing_route_ids(owner), [])
        self.assertNotIn(later.pk, allowed_route_ids(owner))
        later.free_selected = True
        later.save(update_fields=["free_selected"])
        self.assertEqual(briefing_route_ids(owner), [later.pk])
        self.assertNotIn(roots[-1].pk, allowed_route_ids(owner))

    def test_briefing_order_return_pairs_and_channels(self):
        owner = self.owner()
        roots = [self.route(owner) for _ in range(6)]
        # Priority affects the route quota, not which five briefing roots are oldest.
        RecurringRoute.objects.filter(pk=roots[-1].pk).update(free_selected=True)
        returning = self.route(owner, return_of=roots[0], briefing_channel="push")
        self.route(owner, return_of=roots[1], briefing_channel="")
        self.route(owner, return_of=roots[2], active=False)
        self.route(owner, return_of=roots[-1])
        self.route(owner, briefing_channel="invalid")
        self.route(owner, active=False)
        self.route(None)
        self.route(self.owner(), return_of=roots[3])
        expected = [route.pk for route in roots[:5]] + [returning.pk]
        self.assertEqual(briefing_route_ids(owner), expected)
        self.assertEqual(briefing_route_ids_by_owner([owner.pk])[owner.pk], expected)

    def test_subscription_states(self):
        now = datetime.now(UTC)
        owners = [
            self.owner(),
            self.owner(status="canceled"),
            self.owner(current_period_end=now - timedelta(seconds=1)),
            self.owner(status="canceled", trial_ends_at=now + timedelta(days=1)),
            self.owner(status="canceled", complimentary_until=now + timedelta(days=1)),
            self.owner(status="canceled", trial_ends_at=now - timedelta(seconds=1)),
            self.owner(status="canceled", complimentary_until=now - timedelta(seconds=1)),
            User.objects.create(username="no-subscription", email="free@example.test"),
        ]
        routes = [self.route(owner) for owner in owners]
        selected = briefing_route_ids_by_owner([owner.pk for owner in owners])
        for index, (owner, route) in enumerate(zip(owners, routes, strict=True)):
            self.assertEqual(selected[owner.pk], [route.pk] if index in {0, 3, 4} else [])

    def test_scheduler_departure_boundary_and_missing_departure(self):
        owner = self.owner()
        route = self.route(owner)
        now = datetime.now(UTC)
        for departure, expected in (
            (now + timedelta(hours=4), [route.pk]),
            (now + timedelta(hours=4, seconds=1), []),
            (None, []),
        ):
            with (
                self.subTest(departure=departure),
                patch("core.tasks.datetime", wraps=datetime) as clock,
                patch("core.tasks.next_departure", return_value=departure),
            ):
                clock.now.return_value = now
                self.assertEqual([r.pk for r in async_to_sync(_prewarm_routes)()], expected)


@override_settings(
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
)
class TaskQueryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create(username="rider", email="rider@example.test")
        Subscription.objects.create(user=self.owner, plan=Plan.PRO)
        self.departure = datetime.now(UTC).astimezone(LOCAL_TZ) + timedelta(minutes=30)

    def points(self, count):
        return [
            {
                "lat": 47.0,
                "lon": round(9 + i / 100, 2),
                "lat_r": 47.0,
                "lon_r": round(9 + i / 100, 2),
                "elapsed_s": i * 60,
                "idx": i,
            }
            for i in range(count)
        ]

    def job(self, count, **params):
        return ForecastJob.objects.create(
            kind=ForecastJob.Kind.ADHOC,
            owner=self.owner,
            key=f"query-{ForecastJob.objects.count()}",
            params={"departure_time": self.departure.isoformat(), **params},
            geometry={"sample_points": self.points(count), "polyline": [], "total_seconds": count * 60},
        )

    def warm(self, points, windows=None, *, ensembles=True):
        for day, _ in windows or [(self.departure.date(), 16)]:
            for point in points:
                fields = {"lat_r": point["lat_r"], "lon_r": point["lon_r"], "day_key": day, "forecast_days": 16}
                ForecastCell.objects.get_or_create(**fields, source="open-meteo", defaults={"data": {}})
                if ensembles:
                    EnsembleCell.objects.get_or_create(
                        **fields, defaults={"data": {"_norain_request_version": ENSEMBLE_REQUEST_VERSION}}
                    )

    @contextmanager
    def queues(self, *, stations=False):
        forecast, ensemble, station, compute = AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock()
        with (
            patch("core.tasks.refresh_forecast_cell", SimpleNamespace(aenqueue=forecast)),
            patch("core.tasks.refresh_ensemble_cell", SimpleNamespace(aenqueue=ensemble)),
            patch("core.tasks.refresh_station_observations", SimpleNamespace(aenqueue=station)),
            patch("core.tasks.compute_route_weather_job", SimpleNamespace(aenqueue=compute)),
            patch("core.tasks._wants_stations", AsyncMock(return_value=stations)),
            patch("core.tasks.publish", AsyncMock()),
            patch("core.jobs.publish", AsyncMock()),
            patch("core.tasks.telemetry.bind_job", AsyncMock()),
        ):
            yield forecast, ensemble, station, compute

    def test_warm_planning_queries_and_writes_are_constant(self):
        budgets = []
        for count in (1, 50):
            job = self.job(count)
            self.warm(self.points(count))
            with self.queues() as (forecast, ensemble, _, compute), CaptureQueriesContext(connection) as queries:
                async_to_sync(_plan_forecast_job_async)(str(job.pk))
            budgets.append(len(queries))
            updates = [q for q in queries.captured_queries if q["sql"].startswith("UPDATE")]
            self.assertEqual(len(updates), 3)  # planning, initialized fetching, guarded handoff
            self.assertEqual(len([q for q in queries if 'FROM "core_forecastcell"' in q["sql"]]), 1)
            self.assertEqual(len([q for q in queries if 'FROM "core_ensemblecell"' in q["sql"]]), 1)
            forecast.assert_not_awaited()
            ensemble.assert_not_awaited()
            compute.assert_awaited_once_with(str(job.pk))
            job.refresh_from_db()
            self.assertEqual(job.cells_settled, count * 2)
            self.assertEqual(job.status, ForecastJob.Status.ASSEMBLING)
        self.assertEqual(budgets[0], budgets[1])

    def test_workers_finishing_during_enqueue_see_warm_progress_and_wait_for_stations(self):
        job = self.job(2)
        self.warm(self.points(1))
        progress = []
        with self.queues(stations=True) as (forecast, ensemble, station, compute):

            async def finish(*args):
                current = await ForecastJob.objects.aget(pk=job.pk)
                progress.append((current.cells_settled, current.cells_total, current.status))
                compute.assert_not_awaited()
                await _settle_cell(str(job.pk), failed=len(progress) == 1)

            forecast.side_effect = finish
            ensemble.side_effect = finish
            station.side_effect = finish
            async_to_sync(_plan_forecast_job_async)(str(job.pk))
            async_to_sync(_settle_cell)(str(job.pk))  # late completion cannot hand off twice
            compute.assert_awaited_once_with(str(job.pk))
        self.assertEqual(progress, [(2, 5, "fetching"), (3, 5, "fetching"), (4, 5, "fetching")])
        job.refresh_from_db()
        self.assertEqual((job.cells_settled, job.cells_failed, job.status), (5, 1, "assembling"))

    def test_fully_warm_cells_still_wait_for_station_work(self):
        job = self.job(1)
        self.warm(self.points(1))
        with self.queues(stations=True) as (forecast, ensemble, station, compute):
            async_to_sync(_plan_forecast_job_async)(str(job.pk))
            forecast.assert_not_awaited()
            ensemble.assert_not_awaited()
            station.assert_awaited_once_with(str(job.pk))
            compute.assert_not_awaited()
            job.refresh_from_db()
            self.assertEqual((job.cells_settled, job.cells_total), (2, 3))
            async_to_sync(_settle_cell)(str(job.pk))
            compute.assert_awaited_once_with(str(job.pk))

    def test_mixed_cache_across_midnight_enqueues_only_missing_date(self):
        self.departure = self.departure.replace(hour=23, minute=45)
        job = self.job(2, departure_flex_after_minutes=30)
        self.warm(self.points(2))
        with self.queues() as (forecast, ensemble, _, compute):
            async_to_sync(_plan_forecast_job_async)(str(job.pk))
            compute.assert_not_awaited()
        tomorrow = (self.departure + timedelta(days=1)).date().isoformat()
        for enqueue in (forecast, ensemble):
            self.assertEqual(enqueue.await_count, 2)
            self.assertEqual({call.args[2] for call in enqueue.await_args_list}, {tomorrow})
        job.refresh_from_db()
        self.assertEqual((job.cells_settled, job.cells_total), (4, 8))

    def scan_route(self, count):
        return RecurringRoute.objects.create(
            owner=self.owner,
            name="Commute",
            start_point=route_point(47, 9),
            destination_point=route_point(47.01, 9.01),
            schedule_cron="0 * * * *",
            briefing_channel="email",
            sample_points=self.points(count),
        )

    @contextmanager
    def scan_queues(self):
        with (
            self.queues() as queues,
            patch("core.tasks.upcoming_departures", return_value=[self.departure]),
            patch("core.tasks.refresh_route_thumbnail") as thumbnail,
        ):
            thumbnail.using.return_value.aenqueue = AsyncMock()
            yield queues

    def test_warm_scan_uses_two_cache_queries_for_small_and_large_routes(self):
        counts = []
        for count in (1, 50):
            route = self.scan_route(count)
            self.warm(self.points(count))
            with self.scan_queues() as (forecast, ensemble, _, _), CaptureQueriesContext(connection) as queries:
                result = async_to_sync(_scan_route_forecasts_async)(str(route.pk))
            counts.append(len(queries))
            self.assertEqual(result, {"cells_enqueued": 0, "ensembles_enqueued": 0})
            forecast.assert_not_awaited()
            ensemble.assert_not_awaited()
            self.assertEqual(len([q for q in queries if 'FROM "core_forecastcell"' in q["sql"]]), 1)
            self.assertEqual(len([q for q in queries if 'FROM "core_ensemblecell"' in q["sql"]]), 1)
        self.assertEqual(counts[0], counts[1])

    def test_scan_skips_claims_but_planner_enqueues_them_with_job_id(self):
        route = self.scan_route(2)
        job = self.job(2)
        self.warm(self.points(2), ensembles=False)
        windows = departures.fetch_windows(job.params, self.points(2), local_today())
        day, days = windows[0]
        self.assertTrue(claim_cell("ensemble", 47.0, 9.0, day, days))
        with self.scan_queues() as (forecast, ensemble, _, _):
            result = async_to_sync(_scan_route_forecasts_async)(str(route.pk))
            forecast.assert_not_awaited()
            ensemble.assert_awaited_once_with(47, 9.01, day, days)
        self.assertEqual(result, {"cells_enqueued": 0, "ensembles_enqueued": 1})
        with self.queues() as (forecast, ensemble, _, compute):
            async_to_sync(_plan_forecast_job_async)(str(job.pk))
            forecast.assert_not_awaited()
            self.assertEqual(ensemble.await_count, 2)
            self.assertEqual({call.args[-1] for call in ensemble.await_args_list}, {str(job.pk)})
            compute.assert_not_awaited()
