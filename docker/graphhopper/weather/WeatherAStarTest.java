package com.graphhopper.routing.weather;

import com.carrotsearch.hppc.IntArrayList;
import com.graphhopper.routing.Path;
import com.graphhopper.routing.ev.DecimalEncodedValue;
import com.graphhopper.routing.ev.DecimalEncodedValueImpl;
import com.graphhopper.routing.util.EncodingManager;
import com.graphhopper.routing.util.TraversalMode;
import com.graphhopper.routing.weighting.BeelineWeightApproximator;
import com.graphhopper.routing.weighting.SpeedWeighting;
import com.graphhopper.routing.weighting.Weighting;
import com.graphhopper.storage.BaseGraph;
import com.graphhopper.storage.NodeAccess;
import com.graphhopper.util.DistanceCalcEarth;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/** Copied into graphhopper-core's tests by the Dockerfile, which runs it before packaging. */
class WeatherAStarTest {
    private static final long DEPARTURE = 1_800_000_000_000L;
    private static final long HOUR = 3_600_000L;
    // The lattice: 3 rows (46.95, 47.00, 47.05) × 5 columns (8.00 … 8.20), 3 hours.
    private static final int ROWS = 3, COLS = 5, HOURS = 3;

    private BaseGraph graph;
    private Weighting weighting;

    //      1            north: longer
    //    /   \
    //   0     3
    //    \   /
    //      2            south: shorter
    @BeforeEach
    void setUp() {
        DecimalEncodedValue speedEnc = new DecimalEncodedValueImpl("speed", 5, 5, true);
        EncodingManager em = EncodingManager.start().add(speedEnc).build();
        graph = new BaseGraph.Builder(em).create();
        NodeAccess na = graph.getNodeAccess();
        na.setNode(0, 47.00, 8.00);
        na.setNode(1, 47.04, 8.10);
        na.setNode(2, 46.98, 8.10);
        na.setNode(3, 47.00, 8.20);
        int[][] edges = {{0, 1}, {1, 3}, {0, 2}, {2, 3}};
        for (int[] e : edges) {
            double distance = DistanceCalcEarth.DIST_EARTH.calcDist(na.getLat(e[0]), na.getLon(e[0]), na.getLat(e[1]), na.getLon(e[1]));
            graph.edge(e[0], e[1]).setDistance(distance).set(speedEnc, 20, 20);
        }
        weighting = new SpeedWeighting(speedEnc);
    }

    private static Map<String, Object> field(Float[] rain, Float[] windU, Float[] windV) {
        Map<String, Object> map = new HashMap<>();
        map.put("departure", DEPARTURE);
        map.put("lat0", 46.95);
        map.put("lon0", 8.0);
        map.put("step", 0.05);
        map.put("rows", ROWS);
        map.put("cols", COLS);
        map.put("t0", DEPARTURE);
        map.put("dt", HOUR);
        map.put("hours", HOURS);
        if (rain != null) map.put("rain", Arrays.asList(rain));
        if (windU != null) map.put("wind_u", Arrays.asList(windU));
        if (windV != null) map.put("wind_v", Arrays.asList(windV));
        map.put("headwind", List.of(List.of(0, 1), List.of(20, 1.5), List.of(40, 3)));
        return map;
    }

    /** Heavy rain on the southern row of the lattice in the given hours, dry everywhere else. */
    private static Float[] southRain(int... hours) {
        Float[] rain = new Float[ROWS * COLS * HOURS];
        Arrays.fill(rain, 1f);
        for (int h : hours)
            for (int c = 0; c < COLS; c++)
                rain[(h * ROWS) * COLS + c] = 8f;
        return rain;
    }

    private Path route(WeatherField field, long legStart) {
        BeelineWeightApproximator approx = new BeelineWeightApproximator(graph.getNodeAccess(), weighting);
        return new WeatherAStar(graph, weighting, TraversalMode.NODE_BASED, field, legStart, approx).calcPath(0, 3);
    }

    @Test
    void dryWeatherTakesTheShorterRoad() {
        Path path = route(WeatherField.parse(field(southRain(), null, null)), DEPARTURE);
        assertEquals(IntArrayList.from(0, 2, 3), path.calcNodes());
    }

    @Test
    void rainWhereTheRiderWouldBeSendsThemRound() {
        Path path = route(WeatherField.parse(field(southRain(0, 1, 2), null, null)), DEPARTURE);
        assertEquals(IntArrayList.from(0, 1, 3), path.calcNodes());
        Path dry = route(WeatherField.parse(field(southRain(), null, null)), DEPARTURE);
        assertTrue(path.getTime() > dry.getTime(), "times stay the profile's riding times");
    }

    @Test
    void rainThatComesLaterDoesNotCount() {
        // The ride takes about 50 minutes; the shower arrives in the third hour.
        WeatherField field = WeatherField.parse(field(southRain(2), null, null));
        assertEquals(IntArrayList.from(0, 2, 3), route(field, DEPARTURE).calcNodes());
        // The same leg started two hours later (a leg after another one) rides into it.
        assertEquals(IntArrayList.from(0, 1, 3), route(field, DEPARTURE + 2 * HOUR).calcNodes());
    }

    @Test
    void headwindCostsAlongTheBearingOnly() {
        Float[] u = new Float[ROWS * COLS * HOURS], v = new Float[ROWS * COLS * HOURS];
        Arrays.fill(u, -40f); // blowing to the west, from the east
        Arrays.fill(v, 0f);
        WeatherField field = WeatherField.parse(field(null, u, v));
        assertEquals(3, field.multiplier(47.0, 8.05, 47.0, 8.10, DEPARTURE), 1e-9, "riding east into it");
        assertEquals(1, field.multiplier(47.0, 8.10, 47.0, 8.05, DEPARTURE), 1e-9, "riding west with it");
        assertEquals(1, field.multiplier(47.0, 8.10, 47.05, 8.10, DEPARTURE), 1e-9, "crosswind only");
        assertEquals(1.5, field.headwindMultiplier(20), 1e-9);
        assertEquals(1.25, field.headwindMultiplier(10), 1e-9);
    }

    @Test
    void cellsWithoutDataAndOutsideTheFieldAreNeutral() {
        Float[] rain = new Float[ROWS * COLS * HOURS]; // all null
        WeatherField field = WeatherField.parse(field(rain, null, null));
        assertEquals(1, field.multiplier(47.0, 8.0, 47.0, 8.05, DEPARTURE), 1e-9);
        WeatherField wet = WeatherField.parse(field(southRain(0, 1, 2), null, null));
        assertEquals(1, wet.multiplier(40.0, 8.0, 40.0, 8.05, DEPARTURE), 1e-9);
        assertEquals(8, wet.multiplier(46.95, 8.0, 46.95, 8.05, DEPARTURE), 1e-9);
    }

    @Test
    void malformedFieldsAreRefused() {
        Map<String, Object> map = field(southRain(), null, null);
        map.put("rain", new ArrayList<>(List.of(1f)));
        assertThrows(IllegalArgumentException.class, () -> WeatherField.parse(map));
        Map<String, Object> wind = field(null, new Float[ROWS * COLS * HOURS], null);
        assertThrows(IllegalArgumentException.class, () -> WeatherField.parse(wind));
        assertThrows(IllegalArgumentException.class, () -> WeatherField.parse("rain"));
    }
}
