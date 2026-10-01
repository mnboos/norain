package com.graphhopper.routing.weather;

import java.util.List;
import java.util.Map;
import java.util.function.Supplier;

/**
 * The weather a request carries in its {@code weather} hint (MeteoLane's backend builds it in
 * core/weather_routing.py): a lattice of cells by hour, each with a rain weight multiplier and
 * the wind as a vector. GraphHopper never fetches weather itself.
 *
 * <p>Layout: {@code rows × cols} cells from {@code lat0}/{@code lon0} (the centre of the first
 * cell) every {@code step} degrees, and {@code hours} slices from {@code t0} every {@code dt}
 * milliseconds. {@code rain}, {@code wind_u} and {@code wind_v} are flat arrays in
 * [hour][row][col] order; a null is a cell without data and counts as no weather. The wind is
 * km/h, the direction it blows <em>to</em> (u east, v north). {@code headwind} is the
 * piecewise-linear weight multiplier over the headwind component in km/h, as
 * {@code [[kmh, multiplier], ...]} in ascending order.
 *
 * <p>{@code sun} (same layout) is the share of the hour the sky lets the sun through, 0 under
 * cloud and 1 when clear, and {@code shade} the multiplier for a road in full shade while the
 * sun is up. A road is sunny when the sky is clear there and neither the terrain nor the trees
 * stand between it and the sun ({@link Shade}); after sunset no road is, and none costs more.
 *
 * <p>Every multiplier is at least 1: weather only ever makes a road more expensive, which keeps
 * the landmark (LM) lower bounds valid.
 */
public final class WeatherField {
    public static final String HINT = "weather";
    /** No road costs more than this, whatever the field says: a detour must stay possible. */
    static final double MAX_MULTIPLIER = 10;

    final long departureMs;
    private final double lat0, lon0, step;
    private final int rows, cols, hours;
    private final long t0Ms, dtMs;
    private final float[] rain, windU, windV, sun;
    private final double[] headwindKmh, headwindMultiplier;
    private final double shadeMultiplier;
    private final Shade shade;
    // The sun moves a quarter of a degree a minute: one position per minute and 0.1° serves
    // every edge there, and spares the trigonometry on every relaxation.
    private final long[] sunKeys = new long[1 << 12];
    private final SunPosition[] sunPositions = new SunPosition[1 << 12];

    WeatherField(long departureMs, double lat0, double lon0, double step, int rows, int cols, long t0Ms, long dtMs,
                 int hours, float[] rain, float[] windU, float[] windV, double[] headwindKmh, double[] headwindMultiplier,
                 float[] sun, double shadeMultiplier, Shade shade) {
        this.departureMs = departureMs;
        this.lat0 = lat0;
        this.lon0 = lon0;
        this.step = step;
        this.rows = rows;
        this.cols = cols;
        this.t0Ms = t0Ms;
        this.dtMs = dtMs;
        this.hours = hours;
        this.rain = rain;
        this.windU = windU;
        this.windV = windV;
        this.headwindKmh = headwindKmh;
        this.headwindMultiplier = headwindMultiplier;
        this.sun = sun;
        this.shadeMultiplier = shadeMultiplier;
        this.shade = shade;
    }

    /** The field in a request hint, as Jackson reads JSON into plain maps and lists. */
    public static WeatherField parse(Object hint) {
        return parse(hint, Shade::get);
    }

    /** As {@link #parse(Object)}, with the terrain and trees from {@code shade} (null: clouds only). */
    static WeatherField parse(Object hint, Shade shade) {
        return parse(hint, () -> shade);
    }

    private static WeatherField parse(Object hint, Supplier<Shade> shadeModel) {
        if (!(hint instanceof Map<?, ?> map))
            throw new IllegalArgumentException("weather must be an object");
        int rows = positive(map, "rows"), cols = positive(map, "cols"), hours = positive(map, "hours");
        double step = number(map, "step");
        long dt = (long) number(map, "dt");
        if (step <= 0 || dt <= 0)
            throw new IllegalArgumentException("weather.step and weather.dt must be positive");
        int size = rows * cols * hours;
        float[] rain = floats(map, "rain", size);
        float[] u = floats(map, "wind_u", size), v = floats(map, "wind_v", size);
        if ((u == null) != (v == null))
            throw new IllegalArgumentException("weather.wind_u and weather.wind_v come together");
        double[][] table = table(map.get("headwind"));
        float[] sun = floats(map, "sun", size);
        double shadeMultiplier = 1;
        Shade shade = null;
        if (sun != null) {
            shadeMultiplier = number(map, "shade");
            if (shadeMultiplier < 1)
                throw new IllegalArgumentException("weather.shade must be at least 1");
            // Only a request that asks for sun opens the archives.
            shade = shadeModel.get();
        }
        return new WeatherField((long) number(map, "departure"), number(map, "lat0"), number(map, "lon0"), step,
                rows, cols, (long) number(map, "t0"), dt, hours, rain, u, v, table[0], table[1], sun, shadeMultiplier, shade);
    }

    /**
     * How much more the edge from (lat1, lon1) to (lat2, lon2) costs when ridden at {@code timeMs}
     * (epoch): rain at its midpoint times the headwind along its bearing times the shade there.
     */
    public double multiplier(double lat1, double lon1, double lat2, double lon2, long timeMs) {
        double lat = (lat1 + lat2) / 2, lon = (lon1 + lon2) / 2;
        double row = (lat - lat0) / step, col = (lon - lon0) / step;
        if (row < -0.5 || col < -0.5 || row > rows - 0.5 || col > cols - 0.5)
            return 1;
        double hour = Math.max(0, Math.min(hours - 1, (timeMs - t0Ms) / (double) dtMs));
        double result = 1;
        if (rain != null) {
            double r = sample(rain, row, col, hour);
            if (!Double.isNaN(r))
                result *= Math.max(1, r);
        }
        if (windU != null && headwindKmh.length > 0) {
            double u = sample(windU, row, col, hour), v = sample(windV, row, col, hour);
            if (!Double.isNaN(u) && !Double.isNaN(v)) {
                // The direction of travel as a unit vector (east, north), on a local flat projection.
                double east = (lon2 - lon1) * Math.cos(Math.toRadians(lat)), north = lat2 - lat1;
                double length = Math.hypot(east, north);
                if (length > 0) {
                    double headwind = -(u * east + v * north) / length;
                    result *= headwindMultiplier(headwind);
                }
            }
        }
        if (sun != null && shadeMultiplier > 1) {
            double clear = sample(sun, row, col, hour);
            if (!Double.isNaN(clear))
                result *= shadeMultiplier(lat, lon, timeMs, Math.max(0, Math.min(1, clear)));
        }
        return Math.min(MAX_MULTIPLIER, result);
    }

    /** 1 in full sun, {@code shade} in full shade; 1 at night, when every road is dark alike. */
    double shadeMultiplier(double lat, double lon, long timeMs, double clear) {
        SunPosition position = sunAt(lat, lon, timeMs);
        if (position.altitude <= 0)
            return 1;
        // Under a closed sky there is nothing to see: the terrain is only looked at for sun.
        double lit = clear > 0 && shade != null && shade.shaded(lat, lon, position) ? 0 : clear;
        return 1 + (shadeMultiplier - 1) * (1 - lit);
    }

    private SunPosition sunAt(double lat, double lon, long timeMs) {
        long latCell = Math.round((lat + 90) * 10), lonCell = Math.round((lon + 180) * 10), minute = Math.floorDiv(timeMs, 60_000);
        long key = ((minute << 12 | latCell) << 12 | lonCell) + 1;
        int slot = (int) ((key * 0x9E3779B97F4A7C15L) >>> 52);
        if (sunKeys[slot] == key)
            return sunPositions[slot];
        SunPosition position = SunPosition.at(latCell / 10.0 - 90, lonCell / 10.0 - 180, minute * 60_000 + 30_000);
        sunKeys[slot] = key;
        sunPositions[slot] = position;
        return position;
    }

    double headwindMultiplier(double kmh) {
        if (kmh <= 0 || headwindKmh.length == 0)
            return 1;
        if (kmh <= headwindKmh[0])
            return Math.max(1, headwindMultiplier[0]);
        for (int i = 1; i < headwindKmh.length; i++) {
            if (kmh <= headwindKmh[i]) {
                double share = (kmh - headwindKmh[i - 1]) / (headwindKmh[i] - headwindKmh[i - 1]);
                return Math.max(1, headwindMultiplier[i - 1] + share * (headwindMultiplier[i] - headwindMultiplier[i - 1]));
            }
        }
        return Math.max(1, headwindMultiplier[headwindMultiplier.length - 1]);
    }

    /** Bilinear in space, linear in time, over the corners that have data; NaN when none has. */
    private double sample(float[] values, double row, double col, double hour) {
        int h0 = (int) Math.floor(hour);
        int h1 = Math.min(hours - 1, h0 + 1);
        double th = hour - h0;
        double a = spatial(values, h0, row, col), b = spatial(values, h1, row, col);
        if (Double.isNaN(a)) return b;
        if (Double.isNaN(b)) return a;
        return a + th * (b - a);
    }

    private double spatial(float[] values, int hour, double row, double col) {
        double r = Math.max(0, Math.min(rows - 1, row)), c = Math.max(0, Math.min(cols - 1, col));
        int r0 = (int) Math.floor(r), c0 = (int) Math.floor(c);
        int r1 = Math.min(rows - 1, r0 + 1), c1 = Math.min(cols - 1, c0 + 1);
        double tr = r - r0, tc = c - c0;
        double sum = 0, weights = 0;
        int[][] corners = {{r0, c0}, {r0, c1}, {r1, c0}, {r1, c1}};
        double[] cornerWeights = {(1 - tr) * (1 - tc), (1 - tr) * tc, tr * (1 - tc), tr * tc};
        for (int k = 0; k < 4; k++) {
            float value = values[(hour * rows + corners[k][0]) * cols + corners[k][1]];
            if (!Float.isNaN(value) && cornerWeights[k] > 0) {
                sum += value * cornerWeights[k];
                weights += cornerWeights[k];
            }
        }
        return weights > 0 ? sum / weights : Double.NaN;
    }

    private static double number(Map<?, ?> map, String key) {
        if (!(map.get(key) instanceof Number n))
            throw new IllegalArgumentException("weather." + key + " must be a number");
        return n.doubleValue();
    }

    private static int positive(Map<?, ?> map, String key) {
        double value = number(map, key);
        if (value < 1 || value != Math.rint(value) || value > 100_000)
            throw new IllegalArgumentException("weather." + key + " must be a positive whole number");
        return (int) value;
    }

    private static float[] floats(Map<?, ?> map, String key, int size) {
        Object value = map.get(key);
        if (value == null)
            return null;
        if (!(value instanceof List<?> list) || list.size() != size)
            throw new IllegalArgumentException("weather." + key + " must be a list of rows × cols × hours values");
        float[] result = new float[size];
        for (int i = 0; i < size; i++) {
            Object item = list.get(i);
            if (item == null) result[i] = Float.NaN;
            else if (item instanceof Number n) result[i] = n.floatValue();
            else throw new IllegalArgumentException("weather." + key + " holds numbers or nulls");
        }
        return result;
    }

    private static double[][] table(Object value) {
        if (value == null)
            return new double[][]{new double[0], new double[0]};
        if (!(value instanceof List<?> list))
            throw new IllegalArgumentException("weather.headwind must be a list of [kmh, multiplier]");
        double[] kmh = new double[list.size()], multiplier = new double[list.size()];
        for (int i = 0; i < list.size(); i++) {
            if (!(list.get(i) instanceof List<?> pair) || pair.size() != 2
                    || !(pair.get(0) instanceof Number k) || !(pair.get(1) instanceof Number m))
                throw new IllegalArgumentException("weather.headwind must be a list of [kmh, multiplier]");
            kmh[i] = k.doubleValue();
            multiplier[i] = m.doubleValue();
            if (i > 0 && kmh[i] <= kmh[i - 1])
                throw new IllegalArgumentException("weather.headwind must ascend");
        }
        return new double[][]{kmh, multiplier};
    }
}
