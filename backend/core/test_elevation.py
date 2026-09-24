from copy import deepcopy
from unittest.mock import AsyncMock, patch

from django.test import SimpleTestCase, TestCase
from pydantic import ValidationError

from core.elevation import ElevationIn, elevation_profile, profile_samples
from core.models import ElevationProfile


class ElevationSamplingTests(SimpleTestCase):
    def test_preserves_route_order_elevations_and_actual_times(self):
        result = profile_samples([[9, 47, -10], [9.001, 47, 80], [9, 47, 20]], [0, 40, 100], 100)
        self.assertEqual([p["elevation_m"] for p in result], [-10, 80, 20])
        self.assertEqual([p["elapsed_s"] for p in result], [0, 40, 100])
        self.assertGreater(result[-1]["distance_m"], result[1]["distance_m"])

    def test_sparse_path_is_densified_without_changing_it(self):
        points = [[9, 47], [9.01, 47]]
        original = deepcopy(points)
        result = profile_samples(points, None, 100)
        self.assertGreater(len(result), 2)
        self.assertLessEqual(len(result), 2000)
        self.assertAlmostEqual(result[-1]["lon"], 9.01)
        self.assertEqual(result[-1]["elapsed_s"], 100)
        self.assertEqual(points, original)

    def test_invalid_times_rejected(self):
        for times in ([0], [1, 100], [0, float("nan")], [0, 200]):
            with self.assertRaises(ValidationError):
                ElevationIn(coordinates=[[9, 47], [9.01, 47]], total_seconds=100, vertex_times=times)

    def test_stationary_path_has_no_profile(self):
        self.assertEqual(profile_samples([[9, 47], [9, 47]], None, 100), [])


class ElevationCacheTests(TestCase):
    async def test_existing_route_uses_graphhopper_lookup_once_and_keeps_coordinates(self):
        points = [[9, 47], [9.01, 47]]
        original = deepcopy(points)

        async def heights(samples):
            for point in samples:
                point["elevation_m"] = 500

        with patch("core.elevation.terrain_heights", side_effect=heights) as lookup:
            first = await elevation_profile(points, 100)
            second = await elevation_profile(points, 100)
        self.assertEqual(first, second)
        self.assertEqual(points, original)
        self.assertTrue(first["approximate_timing"])
        lookup.assert_awaited_once()
        self.assertEqual(await ElevationProfile.objects.acount(), 1)

    async def test_new_graphhopper_heights_need_no_lookup(self):
        with patch("core.elevation.terrain_heights", new_callable=AsyncMock) as lookup:
            result = await elevation_profile([[9, 47, 500], [9.01, 47, 600]], 100, [0, 100])
        lookup.assert_not_awaited()
        self.assertFalse(result["approximate_timing"])
        self.assertEqual(result["points"][-1]["elevation_m"], 600)

    async def test_missing_heights_remain_retryable(self):
        with patch("core.elevation.terrain_heights", new_callable=AsyncMock) as lookup:
            await elevation_profile([[9, 47], [9.01, 47]], 100)
            await elevation_profile([[9, 47], [9.01, 47]], 100)
        self.assertEqual(lookup.await_count, 2)
        self.assertEqual(await ElevationProfile.objects.acount(), 0)


class ElevationApiTests(TestCase):
    def setUp(self):
        from core.tests import ForecastJobTests

        ForecastJobTests.setUp(self)
        self.client.force_login(self.user)

    def test_saved_route_owner_can_load_profile_without_rerouting(self):
        result = {"points": [], "source": "GraphHopper / Mapterhorn", "approximate_timing": True}
        with patch("core.api.elevation.elevation_profile", AsyncMock(return_value=result)) as profile:
            response = self.client.get(f"/api/routes/{self.route.id}/elevation")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(profile.await_args.args[0], self.route.polyline_coordinates)

    def test_saved_route_is_not_public(self):
        self.client.logout()
        with patch("core.api.elevation.elevation_profile", AsyncMock()) as profile:
            response = self.client.get(f"/api/routes/{self.route.id}/elevation")
        self.assertEqual(response.status_code, 404)
        profile.assert_not_awaited()

    def test_lookup_failure_does_not_return_a_flat_profile(self):
        import httpx

        with patch("core.api.elevation.elevation_profile", AsyncMock(side_effect=httpx.ConnectError("offline"))):
            response = self.client.get(f"/api/routes/{self.route.id}/elevation")
        self.assertEqual(response.status_code, 503)
