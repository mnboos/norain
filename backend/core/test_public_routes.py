"""Public routes: privacy zones, photos without metadata, comments, likes, a visitor's forecast."""

import shutil
import tempfile
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase, override_settings
from PIL import Image

from . import tests as fixtures
from .geo import haversine_m
from .models import ForecastJob, RecurringRoute, RouteComment, RoutePhoto, User, route_line, route_point
from .photos import PhotoError, process_photo
from .public_routes import MIN_PUBLIC_DISTANCE_M, public_geometry, visible_range
from .tasks import _job_geometry, _plan_forecast_job_async

# Along the 47th parallel, one vertex every 0.001° (about 76 m), 7.6 km in all, at 5 m/s.
LINE = [[round(9.0 + i * 0.001, 3), 47.0] for i in range(101)]
STEP_M = haversine_m(9.0, 47.0, 9.001, 47.0)
TIMES = [i * STEP_M / 5 for i in range(101)]
ELEVATIONS = [400 + (i % 10) * 3 for i in range(101)]


def _jpeg_with_gps() -> bytes:
    image = Image.new("RGB", (64, 48), "red")
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # Make
    gps = exif.get_ifd(0x8825)
    gps[2] = (47.0, 0.0, 0.0)  # GPSLatitude
    gps[4] = (9.0, 0.0, 0.0)  # GPSLongitude
    out = BytesIO()
    image.save(out, "JPEG", exif=exif)
    return out.getvalue()


class VisibleRangeTests(SimpleTestCase):
    def test_trims_a_circle_round_each_end(self):
        first, last = visible_range(LINE, ((9.0, 47.0), (9.1, 47.0)), 500)
        self.assertGreaterEqual(haversine_m(*LINE[first], 9.0, 47.0), 500)
        self.assertLess(haversine_m(*LINE[first - 1], 9.0, 47.0), 500)
        self.assertGreaterEqual(haversine_m(*LINE[last], 9.1, 47.0), 500)
        self.assertLess(haversine_m(*LINE[last + 1], 9.1, 47.0), 500)

    def test_a_round_trip_hides_home_at_both_ends(self):
        loop = [*LINE, *reversed(LINE[:-1])]
        first, last = visible_range(loop, ((9.0, 47.0), (9.0, 47.0)), 500)
        for point in (loop[first], loop[last]):
            self.assertGreaterEqual(haversine_m(*point, 9.0, 47.0), 500)

    def test_a_route_that_doubles_back_past_home_is_trimmed_past_the_last_pass(self):
        # Out 300 m, back through the doorstep, then away the other way.
        wiggle = [[9.0 + i * 0.001, 47.0] for i in range(4)]
        line = [*wiggle, *reversed(wiggle[:-1]), *[[9.0 - i * 0.001, 47.0] for i in range(1, 60)]]
        first, _ = visible_range(line, ((9.0, 47.0), tuple(line[-1])), 500)
        self.assertTrue(all(haversine_m(*p, 9.0, 47.0) >= 500 for p in line[first:40]))

    def test_too_little_left_is_none(self):
        short = LINE[: int(2 * 500 / STEP_M) + int(MIN_PUBLIC_DISTANCE_M / STEP_M) - 2]
        self.assertIsNone(visible_range(short, (tuple(short[0]), tuple(short[-1])), 500))
        self.assertIsNotNone(visible_range(short, (tuple(short[0]), tuple(short[-1])), 0))


class PhotoProcessingTests(SimpleTestCase):
    def test_metadata_is_gone(self):
        original = _jpeg_with_gps()
        with Image.open(BytesIO(original)) as image:
            self.assertTrue(image.getexif().get_ifd(0x8825))
        processed = process_photo(original)
        for data in (processed.image, processed.thumbnail):
            with Image.open(BytesIO(data)) as image:
                self.assertEqual(dict(image.getexif()), {})
                self.assertNotIn("exif", image.info)
        self.assertEqual((processed.width, processed.height), (64, 48))

    def test_large_photos_are_bounded(self):
        out = BytesIO()
        Image.new("RGB", (4000, 3000)).save(out, "PNG")
        processed = process_photo(out.getvalue())
        self.assertEqual(max(processed.width, processed.height), 2048)

    def test_not_a_photo(self):
        with self.assertRaises(PhotoError):
            process_photo(b"<svg xmlns='http://www.w3.org/2000/svg'/>")
        out = BytesIO()
        Image.new("RGB", (8, 8)).save(out, "GIF")
        with self.assertRaises(PhotoError):
            process_photo(out.getvalue())


@override_settings(CACHES=fixtures.LOCMEM_CACHE, CHANNEL_LAYERS=fixtures.INMEM_CHANNELS)
class PublicRouteApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        media = override_settings(MEDIA_ROOT=self.media)
        media.enable()
        self.addCleanup(media.disable)

        self.owner = User.objects.create_user(username="owner", email="owner@example.com", password="pw")
        self.visitor = User.objects.create_user(username="visitor", email="visitor@example.com", password="pw")
        self.route = RecurringRoute.objects.create(
            owner=self.owner,
            name="Along the lake",
            description="Flat and pretty",
            start_point=route_point(47.0, 9.0),
            start_name="Home Street 5",
            destination_point=route_point(47.0, 9.1),
            dest_name="Office Tower",
            via_points=[[9.05, 47.0]],
            schedule_cron="15 7 * * 1-5",
            schedule_description="Weekdays at 07:15",
            polyline=route_line(LINE),
            vertex_times=TIMES,
            vertex_elevations=ELEVATIONS,
            sample_points=[{"lat": 47.0, "lon": 9.0, "lat_r": 47.0, "lon_r": 9.0, "elapsed_s": 0, "idx": 0}],
            total_seconds=int(TIMES[-1]),
            total_distance_m=STEP_M * 100,
        )
        self.owner_client = Client()
        self.owner_client.force_login(self.owner)
        self.visitor_client = Client()
        self.visitor_client.force_login(self.visitor)
        self.anonymous = Client()

    def _publish(self, zone=500):
        response = self.owner_client.put(
            f"/api/routes/{self.route.id}/sharing",
            {"visibility": "public", "privacyZoneM": zone},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.route.refresh_from_db()
        return response.json()

    def _upload(self, client=None, **fields):
        data = {"file": _upload_file(), **fields}
        return (client or self.owner_client).post(f"/api/routes/{self.route.id}/photos", data)

    # -- sharing ---------------------------------------------------------------

    def test_publishing_gives_a_link_and_the_public_page_hides_where_and_when(self):
        sharing = self._publish()
        slug = sharing["public_slug"]
        self.assertTrue(slug)

        response = self.anonymous.get(f"/api/public/routes/{slug}")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        text = response.content.decode()
        for secret in ("Home Street", "Office Tower", "15 7", "Weekdays", str(self.route.id)):
            self.assertNotIn(secret, text)
        for key in ("via_points", "start_lat", "dest_lat", "schedule_cron", "start_name", "dest_name"):
            self.assertNotIn(key, body)
        for point in body["line"]:
            self.assertGreaterEqual(haversine_m(*point, 9.0, 47.0), 500)
            self.assertGreaterEqual(haversine_m(*point, 9.1, 47.0), 500)
        self.assertEqual(body["author"], "owner")
        self.assertLess(body["distance_m"], STEP_M * 100 - 1000)
        self.assertGreater(body["ascent_m"], 0)
        self.assertFalse(body["is_owner"])

        listed = self.anonymous.get("/api/public/routes").json()
        self.assertEqual([r["slug"] for r in listed], [slug])
        self.assertNotIn("Home Street", str(listed))

    def test_a_zone_that_leaves_too_little_is_refused(self):
        response = self.owner_client.put(
            f"/api/routes/{self.route.id}/sharing",
            {"visibility": "public", "privacyZoneM": 2000},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)  # 7.6 km minus 2 x 2 km still leaves enough
        self.route.polyline = route_line(LINE[:40])
        self.route.vertex_times = TIMES[:40]
        self.route.save()
        response = self.owner_client.put(
            f"/api/routes/{self.route.id}/sharing",
            {"visibility": "public", "privacyZoneM": 2000},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 422)

    def test_private_routes_and_unpublished_links_are_404(self):
        self.assertEqual(self.anonymous.get("/api/public/routes/nothing").status_code, 404)
        slug = self._publish()["public_slug"]
        self.owner_client.put(
            f"/api/routes/{self.route.id}/sharing", {"visibility": "private"}, content_type="application/json"
        )
        self.assertEqual(self.anonymous.get(f"/api/public/routes/{slug}").status_code, 404)
        self.assertEqual(self.anonymous.get("/api/public/routes").json(), [])
        # Re-publishing keeps the link.
        self.assertEqual(self._publish()["public_slug"], slug)

    def test_only_the_owner_changes_sharing(self):
        response = self.visitor_client.put(
            f"/api/routes/{self.route.id}/sharing", {"visibility": "public"}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 404)

    def test_the_bbox_filter_ignores_the_hidden_ends(self):
        slug = self._publish()["public_slug"]
        near_home = "8.99,46.99,9.003,47.01"  # holds only the first 200 m
        self.assertEqual(self.anonymous.get(f"/api/public/routes?bbox={near_home}").json(), [])
        middle = self.anonymous.get("/api/public/routes?bbox=9.04,46.99,9.06,47.01").json()
        self.assertEqual([r["slug"] for r in middle], [slug])

    # -- photos ----------------------------------------------------------------

    def test_uploaded_photos_lose_their_metadata(self):
        response = self._upload(caption="Sunset")
        self.assertEqual(response.status_code, 200, response.content)
        photo = RoutePhoto.objects.get()
        with photo.image.open("rb") as handle, Image.open(handle) as image:
            self.assertEqual(dict(image.getexif()), {})
        self.assertIsNone(photo.location)  # never read from the file

    def test_private_photos_are_the_owners_alone(self):
        photo_id = self._upload().json()["id"]
        self.assertEqual(self.anonymous.get(f"/api/photos/{photo_id}/thumb").status_code, 404)
        self.assertEqual(self.visitor_client.get(f"/api/photos/{photo_id}/full").status_code, 404)
        response = self.owner_client.get(f"/api/photos/{photo_id}/full")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertIn("private", response["Cache-Control"])

        self._publish()
        response = self.anonymous.get(f"/api/photos/{photo_id}/thumb")
        self.assertEqual(response.status_code, 200)
        self.assertIn("public", response["Cache-Control"])

    def test_a_photo_inside_a_privacy_zone_loses_its_position_publicly(self):
        self._upload(lat="47.0", lon="9.001")
        self._upload(lat="47.0", lon="9.05")
        slug = self._publish()["public_slug"]
        photos = self.anonymous.get(f"/api/public/routes/{slug}").json()["photos"]
        self.assertEqual([(p["lat"], p["lon"]) for p in photos], [(None, None), (47.0, 9.05)])
        own = self.owner_client.get(f"/api/routes/{self.route.id}/photos").json()
        self.assertEqual(own[0]["lon"], 9.001)

    def test_photo_quota(self):
        with patch("core.api.community.entitlements_for_sync", return_value=SimpleNamespace(max_route_photos=1)):
            self.assertEqual(self._upload().status_code, 200)
            self.assertEqual(self._upload().status_code, 402)
        self.assertEqual(RoutePhoto.objects.count(), 1)

    def test_only_the_owner_adds_photos(self):
        self._publish()
        self.assertEqual(self._upload(self.visitor_client).status_code, 404)

    def test_deleting_a_photo_removes_its_files(self):
        photo_id = self._upload().json()["id"]
        photo = RoutePhoto.objects.get()
        storage, name = photo.image.storage, photo.image.name
        self.assertTrue(storage.exists(name))
        with self.captureOnCommitCallbacks(execute=True):
            response = self.owner_client.delete(f"/api/routes/{self.route.id}/photos/{photo_id}")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(storage.exists(name))

    # -- comments and likes --------------------------------------------------------

    def test_comments(self):
        slug = self._publish()["public_slug"]
        url = f"/api/public/routes/{slug}/comments"
        self.assertEqual(self.anonymous.post(url, {"body": "hi"}, content_type="application/json").status_code, 401)
        response = self.visitor_client.post(url, {"body": "  Lovely ride!  "}, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        comment = response.json()
        self.assertEqual(comment["body"], "Lovely ride!")
        self.assertTrue(comment["can_edit"])

        third = User.objects.create_user(username="third", email="third@example.com", password="pw")
        other = Client()
        other.force_login(third)
        listed = other.get(url).json()
        self.assertEqual([(c["author"], c["can_delete"]) for c in listed], [("visitor", False)])
        self.assertEqual(other.delete(f"/api/public/comments/{comment['id']}").status_code, 404)
        self.assertEqual(
            other.patch(
                f"/api/public/comments/{comment['id']}", {"body": "x"}, content_type="application/json"
            ).status_code,
            404,
        )

        # The route's owner moderates their own page.
        self.assertTrue(self.owner_client.get(url).json()[0]["can_delete"])
        self.assertEqual(self.owner_client.delete(f"/api/public/comments/{comment['id']}").status_code, 204)
        self.assertFalse(RouteComment.objects.exists())

    def test_comments_are_rate_limited(self):
        slug = self._publish()["public_slug"]
        url = f"/api/public/routes/{slug}/comments"
        codes = [
            self.visitor_client.post(url, {"body": f"#{i}"}, content_type="application/json").status_code
            for i in range(8)
        ]
        self.assertEqual(codes[-1], 429)

    def test_likes(self):
        slug = self._publish()["public_slug"]
        url = f"/api/public/routes/{slug}/like"
        self.assertEqual(self.anonymous.put(url).status_code, 401)
        self.assertEqual(self.visitor_client.put(url).json(), {"liked": True, "like_count": 1})
        self.assertEqual(self.visitor_client.put(url).json(), {"liked": True, "like_count": 1})
        self.assertTrue(self.visitor_client.get(f"/api/public/routes/{slug}").json()["liked"])
        self.assertEqual(self.visitor_client.delete(url).json(), {"liked": False, "like_count": 0})

    # -- a visitor's forecast and copy ------------------------------------------------

    def _forecast(self, client, slug):
        with patch("core.tasks.plan_forecast_job", SimpleNamespace(aenqueue=AsyncMock())) as plan:
            response = client.get(f"/api/public/routes/{slug}/forecast?date=2030-06-01&time=09:00")
        return response, plan.aenqueue

    def test_a_visitor_forecast_runs_on_the_public_line_as_the_visitors_job(self):
        slug = self._publish()["public_slug"]
        response, enqueue = self._forecast(self.visitor_client, slug)
        self.assertEqual(response.status_code, 202, response.content)
        enqueue.assert_awaited_once()
        job = ForecastJob.objects.get()
        self.assertEqual((job.kind, job.owner), (ForecastJob.Kind.PUBLIC_ROUTE, self.visitor))
        self.assertEqual(job.params["privacy_zone_m"], 500)

        geometry = async_to_sync(_job_geometry)(job)
        self.assertEqual(geometry, {**public_geometry(self.route), "profile": self.route.profile})
        self.assertEqual(geometry["sample_points"][0]["elapsed_s"], 0)
        for sample in geometry["sample_points"]:
            self.assertGreaterEqual(haversine_m(sample["lon"], sample["lat"], 9.0, 47.0), 500)

        # Anyone may read the page, but planning a ride needs an account.
        response, enqueue = self._forecast(self.anonymous, slug)
        self.assertEqual(response.status_code, 401)
        enqueue.assert_not_awaited()
        self.assertEqual(ForecastJob.objects.count(), 1)
        self.assertEqual(self.anonymous.get(f"/api/public/routes/{slug}").status_code, 200)

    def test_a_forecast_for_a_route_made_private_fails(self):
        slug = self._publish()["public_slug"]
        self._forecast(self.visitor_client, slug)
        RecurringRoute.objects.filter(id=self.route.id).update(visibility="private")
        job = ForecastJob.objects.get()
        with patch("core.tasks.publish", AsyncMock()):
            async_to_sync(_plan_forecast_job_async)(str(job.id))
        job.refresh_from_db()
        self.assertEqual(job.status, ForecastJob.Status.FAILED)

    def test_copying_saves_the_public_line_as_the_visitors_route(self):
        slug = self._publish()["public_slug"]
        with patch("core.api.recurring_route.refresh_route_geometry", SimpleNamespace(aenqueue=AsyncMock())):
            response = self.visitor_client.post(f"/api/public/routes/{slug}/copy", {}, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        copy = RecurringRoute.objects.get(owner=self.visitor)
        self.assertEqual(copy.geometry_source, "imported")
        self.assertEqual(copy.imported_coordinates, public_geometry(self.route)["polyline"])
        self.assertEqual(copy.visibility, "private")
        self.assertEqual(
            self.anonymous.post(f"/api/public/routes/{slug}/copy", {}, content_type="application/json").status_code, 401
        )


def _upload_file():
    return SimpleUploadedFile("photo.jpg", _jpeg_with_gps(), content_type="image/jpeg")
