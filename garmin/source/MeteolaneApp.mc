using Toybox.Application;
using Toybox.Communications;
using Toybox.Graphics;
using Toybox.Lang;
using Toybox.PersistedContent;
using Toybox.Time;
using Toybox.Timer;
using Toybox.WatchUi;

class MeteolaneApp extends Application.AppBase {
    var view;
    function initialize() { AppBase.initialize(); }
    function getInitialView() {
        view = new RideView();
        return [view, new RideDelegate(view)];
    }
    function onSettingsChanged() {
        if (view != null) { view.clear(); view.refresh(); }
    }
}

class RideDelegate extends WatchUi.BehaviorDelegate {
    var view;
    function initialize(v) { BehaviorDelegate.initialize(); view = v; }
    function onSelect() { view.refresh(); return true; }
    function onNextPage() { view.page = (view.page + 1) % 2; WatchUi.requestUpdate(); return true; }
    function onPreviousPage() { return onNextPage(); }
}

class RideView extends WatchUi.View {
    var ride as Lang.Dictionary or Null = null;
    var page = 0;
    var status = "Loading...";
    var busy = false;
    var generation = 0;
    var timer;

    function initialize() { View.initialize(); timer = new Timer.Timer(); }
    function clear() { ride = null; page = 0; generation += 1; busy = false; }
    function onShow() { refresh(); timer.start(method(:tick), 60000, true); }
    function onHide() { timer.stop(); }
    function tick() { refresh(); }
    function refresh() {
        if (busy) { return; }
        var server = Application.Properties.getValue("server");
        var token = Application.Properties.getValue("token");
        if (!(server instanceof Lang.String) || !(token instanceof Lang.String) || token.length() == 0) {
            clear(); status = "Set watch token"; WatchUi.requestUpdate(); return;
        }
        if (server.find("https://") != 0 && !isLocalServer(server)) {
            clear(); status = "Use an HTTPS server"; WatchUi.requestUpdate(); return;
        }
        busy = true;
        status = "Refreshing...";
        while (server.length() > 8 && server.substring(server.length() - 1, server.length()).equals("/")) {
            server = server.substring(0, server.length() - 1);
        }
        try {
            Communications.makeWebRequest(server + "/api/garmin/next-ride", null, {
                :method => Communications.HTTP_REQUEST_METHOD_GET,
                :headers => {"Authorization" => "Bearer " + token},
                :responseType => Communications.HTTP_RESPONSE_CONTENT_TYPE_JSON,
                :context => generation
            }, method(:received));
        } catch (error) {
            busy = false;
            status = "Offline - START retries";
        }
        WatchUi.requestUpdate();
    }
    function isLocalServer(server) {
        return server.equals("http://localhost:8000") || server.equals("http://localhost:8000/") ||
            server.equals("http://127.0.0.1:8000") || server.equals("http://127.0.0.1:8000/");
    }
    function validRide(value) {
        if (!(value instanceof Lang.Dictionary)) { return false; }
        if (!(value["departureEpoch"] instanceof Lang.Number) ||
            !(value["name"] instanceof Lang.String) ||
            !(value["departureLabel"] instanceof Lang.String) ||
            !(value["weatherStatus"] instanceof Lang.String)) { return false; }
        var numbers = ["distanceKm", "durationMinutes", "tempMin", "tempMax", "rainProbability", "rainRate", "headwind"];
        for (var i = 0; i < numbers.size(); i += 1) {
            var numberValue = value[numbers[i]];
            if (numberValue != null && !(numberValue instanceof Lang.Number) && !(numberValue instanceof Lang.Float)) {
                return false;
            }
        }
        return true;
    }
    function received(code as Lang.Number, data as Lang.Dictionary or Lang.String or PersistedContent.Iterator or Null, requestGeneration as Lang.Object) as Void {
        if (requestGeneration != generation) { return; }
        busy = false;
        if (code == 200 && data instanceof Lang.Dictionary) {
            var nextRide = data["ride"];
            if (!data.hasKey("ride") || (nextRide != null && !validRide(nextRide))) {
                status = "Invalid server reply"; WatchUi.requestUpdate(); return;
            }
            ride = nextRide;
            status = ride == null ? "No upcoming rides" : "Updated";
        } else if (code == 401) {
            clear(); status = "Check watch token";
        } else {
            status = "Offline - START retries";
        }
        WatchUi.requestUpdate();
    }
    function line(dc, y, text, font) {
        // Fit route names and server labels within the round screen.
        var limit = dc.getWidth() - 44;
        while (dc.getTextWidthInPixels(text, font) > limit && text.length() > 1) {
            text = text.substring(0, text.length() - 2) + "~";
        }
        dc.drawText(dc.getWidth()/2, y, font, text, Graphics.TEXT_JUSTIFY_CENTER);
    }
    function number(value, format, unit) {
        return value == null ? "--" : value.format(format) + unit;
    }
    function onUpdate(dc) {
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_BLACK);
        dc.clear();
        line(dc, 24, "METEOLANE", Graphics.FONT_XTINY);
        if (ride == null) {
            line(dc, 100, status, Graphics.FONT_SMALL);
            line(dc, 160, "START: refresh", Graphics.FONT_XTINY);
            return;
        }
        if (ride["departureEpoch"] <= Time.now().value()) {
            line(dc, 95, "Ride has departed", Graphics.FONT_SMALL);
            line(dc, 130, "START: next ride", Graphics.FONT_XTINY);
            return;
        }
        line(dc, 55, ride["name"], Graphics.FONT_SMALL);
        line(dc, 85, ride["departureLabel"], Graphics.FONT_XTINY);
        if (page == 0) {
            line(dc, 114, number(ride["distanceKm"], "%.1f", " km") + "  " + number(ride["durationMinutes"], "%d", " min"), Graphics.FONT_SMALL);
            line(dc, 148, number(ride["tempMin"], "%.0f", " C") + " / " + number(ride["tempMax"], "%.0f", " C"), Graphics.FONT_SMALL);
            line(dc, 182, ride["weatherStatus"], Graphics.FONT_XTINY);
        } else {
            line(dc, 114, "Rain " + number(ride["rainProbability"], "%.0f", "%"), Graphics.FONT_SMALL);
            line(dc, 148, number(ride["rainRate"], "%.1f", " mm/h max"), Graphics.FONT_SMALL);
            line(dc, 182, "Headwind " + number(ride["headwind"], "%.0f", " km/h"), Graphics.FONT_XTINY);
        }
        line(dc, 214, status, Graphics.FONT_XTINY);
    }
}
