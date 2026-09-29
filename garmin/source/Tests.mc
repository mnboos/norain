using Toybox.Test;

(:test)
function testRideResponse(logger) {
    var view = new RideView();
    Test.assert(view.isLocalServer("http://localhost:8000"));
    Test.assert(view.isLocalServer("http://127.0.0.1:8000/"));
    Test.assert(!view.isLocalServer("http://localhost:8000.evil.example"));
    var ride = {
        "name" => "Morning ride", "departureEpoch" => 2000000000,
        "departureLabel" => "03.06. 08:00 CEST", "weatherStatus" => "Weather updated",
        "distanceKm" => 12.5, "durationMinutes" => 40, "tempMin" => 10,
        "tempMax" => 16.5, "rainProbability" => 30, "rainRate" => 0.2, "headwind" => 8
    };
    view.received(200, {"ride" => ride}, 0);
    Test.assertEqual(view.ride["name"], "Morning ride");
    Test.assertEqual(view.number(30, "%.0f", "%"), "30%");
    Test.assertEqual(view.number(null, "%.0f", " C"), "--");
    view.received(-1, null, 0);
    Test.assertEqual(view.ride["name"], "Morning ride");
    view.received(200, {"ride" => {"name" => "Broken"}}, 0);
    Test.assertEqual(view.status, "Invalid server reply");
    view.received(200, {"ride" => null}, 0);
    Test.assert(view.ride == null);
    Test.assertEqual(view.status, "No upcoming rides");
    return true;
}

(:test)
function testRevokedTokenAndSettingsChange(logger) {
    var view = new RideView();
    view.received(401, null, 0);
    Test.assertEqual(view.status, "Check watch token");
    Test.assert(view.ride == null);
    view.received(200, {"ride" => null}, 0);
    Test.assertEqual(view.status, "Check watch token");
    return true;
}
