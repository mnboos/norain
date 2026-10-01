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

import java.time.Instant;
import java.util.Arrays;
import java.util.HashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/** Copied into graphhopper-core's tests by the Dockerfile, which runs it before packaging. */
class ShadeTest {
    private static final long HOUR = 3_600_000L;
    /** 15 January, 11:00 UTC: about noon in the Swiss Plateau, the sun some 20° up in the south. */
    private static final long WINTER_NOON = Instant.parse("2027-01-15T11:00:00Z").toEpochMilli();
    private static final int ROWS = 3, COLS = 5, HOURS = 3;

    /** A wall of rock south of 46.97°N, 1500 m above the plain: the southern road lies in its shadow in winter. */
    static final class Cliff implements Shade.Surface {
        @Override
        public double terrain(double lat, double lon) {
            return lat < 46.97 ? 2000 : 500;
        }

        @Override
        public double farTerrain(double lat, double lon) {
            return terrain(lat, lon);
        }

        @Override
        public double canopy(double lat, double lon) {
            return 0;
        }
    }

    /** Flat land with a wood: 20 m trees between 8.10°E and 8.1005°E, and over everything north of 47.03°N. */
    static final class Wood implements Shade.Surface {
        @Override
        public double terrain(double lat, double lon) {
            return 400;
        }

        @Override
        public double farTerrain(double lat, double lon) {
            return 400;
        }

        @Override
        public double canopy(double lat, double lon) {
            return lat > 47.03 || (lon > 8.10 && lon < 8.1005) ? 20 : 0;
        }
    }

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
        na.setNode(1, 47.02, 8.10);
        na.setNode(2, 46.99, 8.10);
        na.setNode(3, 47.00, 8.20);
        int[][] edges = {{0, 1}, {1, 3}, {0, 2}, {2, 3}};
        for (int[] e : edges) {
            double distance = DistanceCalcEarth.DIST_EARTH.calcDist(na.getLat(e[0]), na.getLon(e[0]), na.getLat(e[1]), na.getLon(e[1]));
            graph.edge(e[0], e[1]).setDistance(distance).set(speedEnc, 20, 20);
        }
        weighting = new SpeedWeighting(speedEnc);
    }

    private static Map<String, Object> field(long t0, float sun) {
        Float[] values = new Float[ROWS * COLS * HOURS];
        Arrays.fill(values, sun);
        Map<String, Object> map = new HashMap<>();
        map.put("departure", t0);
        map.put("lat0", 46.95);
        map.put("lon0", 8.0);
        map.put("step", 0.05);
        map.put("rows", ROWS);
        map.put("cols", COLS);
        map.put("t0", t0);
        map.put("dt", HOUR);
        map.put("hours", HOURS);
        map.put("sun", Arrays.asList(values));
        map.put("shade", 3);
        return map;
    }

    private Path route(WeatherField field, long start) {
        BeelineWeightApproximator approx = new BeelineWeightApproximator(graph.getNodeAccess(), weighting);
        return new WeatherAStar(graph, weighting, TraversalMode.NODE_BASED, field, start, approx).calcPath(0, 3);
    }

    @Test
    void theSunStandsWhereTheAlmanacSays() {
        // Zurich at the June solstice, solar noon: 90° − 47.37° + 23.44°, due south.
        SunPosition noon = SunPosition.at(47.37, 8.54, Instant.parse("2026-06-21T11:26:00Z").toEpochMilli());
        assertEquals(66.07, noon.altitude, 0.3);
        assertEquals(180, noon.azimuth, 2);
        SunPosition morning = SunPosition.at(47.37, 8.54, Instant.parse("2026-06-21T05:00:00Z").toEpochMilli());
        assertTrue(morning.altitude > 0 && morning.azimuth > 50 && morning.azimuth < 90, "rises in the north-east");
        assertTrue(SunPosition.at(47.37, 8.54, Instant.parse("2026-06-21T23:00:00Z").toEpochMilli()).altitude < 0);
        SunPosition winter = SunPosition.at(47.0, 8.1, WINTER_NOON);
        assertEquals(21, winter.altitude, 2);
    }

    @Test
    void terrainBetweenTheRoadAndTheSunShadesIt() {
        Shade shade = new Shade(new Cliff());
        SunPosition sun = SunPosition.at(47.0, 8.1, WINTER_NOON);
        assertTrue(shade.shaded(46.99, 8.05, sun), "2.2 km from a 1500 m wall");
        assertFalse(shade.shaded(47.04, 8.05, sun), "far enough north to see over it");
        assertTrue(shade.horizon(47.04, 8.05, 0) < 0, "nothing to the north");
        assertEquals(shade.horizon(46.99, 8.05, 17), shade.horizon(46.99, 8.05, 17), "kept, not recomputed differently");
    }

    @Test
    void treesShadeTheRoadUnderThemAndBesideThem() {
        Shade shade = new Shade(new Wood());
        SunPosition sun = SunPosition.at(47.0, 8.1, WINTER_NOON);
        assertTrue(shade.shaded(47.04, 8.0, sun), "under the canopy");
        assertFalse(shade.shaded(47.0, 8.0, sun), "in the open");
        // A row of 20 m trees 15 m to the east, the low winter sun behind it in the morning.
        SunPosition morning = SunPosition.at(47.0, 8.1, Instant.parse("2027-01-15T08:30:00Z").toEpochMilli());
        assertTrue(morning.altitude > 0 && morning.azimuth > 120 && morning.azimuth < 150);
        assertTrue(shade.march(47.0, 8.0998, 135) > 0);
    }

    @Test
    void theRiderAvoidsTheShadowOnASunnyDay() {
        Shade shade = new Shade(new Cliff());
        assertEquals(IntArrayList.from(0, 1, 3), route(WeatherField.parse(field(WINTER_NOON, 1), shade), WINTER_NOON).calcNodes());
        assertEquals(IntArrayList.from(0, 2, 3), route(WeatherField.parse(field(WINTER_NOON, 1), null), WINTER_NOON).calcNodes(),
                "without terrain, a clear sky shines on both roads alike");
    }

    @Test
    void underCloudOrAtNightTheShorterRoadStays() {
        Shade shade = new Shade(new Cliff());
        assertEquals(IntArrayList.from(0, 2, 3), route(WeatherField.parse(field(WINTER_NOON, 0), shade), WINTER_NOON).calcNodes());
        long night = WINTER_NOON - 11 * HOUR;
        WeatherField dark = WeatherField.parse(field(night, 1), shade);
        assertEquals(IntArrayList.from(0, 2, 3), route(dark, night).calcNodes());
        assertEquals(1, dark.multiplier(46.99, 8.0, 46.99, 8.05, night), 1e-9);
    }

    @Test
    void shadeCostsBetweenOneAndTheMultiplier() {
        WeatherField clear = WeatherField.parse(field(WINTER_NOON, 1), new Shade(new Cliff()));
        assertEquals(3, clear.multiplier(46.99, 8.0, 46.99, 8.05, WINTER_NOON), 1e-9, "in the cliff's shadow");
        assertEquals(1, clear.multiplier(47.04, 8.0, 47.04, 8.05, WINTER_NOON), 1e-9, "in the sun");
        WeatherField hazy = WeatherField.parse(field(WINTER_NOON, 0.5f), null);
        assertEquals(2, hazy.multiplier(47.04, 8.0, 47.04, 8.05, WINTER_NOON), 1e-9, "half the hour behind cloud");
        Map<String, Object> cheap = field(WINTER_NOON, 1);
        cheap.put("shade", 0.5);
        assertThrows(IllegalArgumentException.class, () -> WeatherField.parse(cheap, null));
    }
}
