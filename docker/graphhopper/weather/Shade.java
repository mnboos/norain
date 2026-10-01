package com.graphhopper.routing.weather;

import com.graphhopper.GraphHopperConfig;
import com.graphhopper.reader.dem.ElevationProvider;
import com.graphhopper.reader.dem.FallbackElevationProvider;
import com.graphhopper.reader.dem.PMTilesElevationProvider;
import org.slf4j.LoggerFactory;

import java.util.concurrent.atomic.AtomicLongArray;

/**
 * Whether a road lies in the shadow of the terrain or of trees: the horizon seen from the road
 * towards the sun, against the sun's altitude ({@link SunPosition}). Clouds are the forecast's
 * part ({@link WeatherField}'s {@code sun}); this is only what stands between the road and a
 * clear sky.
 *
 * <p>The horizon is the steepest angle up to the surface along the sun's azimuth, out to
 * {@link #MAX_M}: terrain from the elevation archive the graph was built with, and within
 * {@link #CANOPY_M} the tree heights of the canopy archive on top (prepared from OSM woods by
 * {@code graphhopper-canopy.py}; without one, trees cast no shadow). A road under the canopy
 * is in the shade whatever the sun does.
 *
 * <p>Terrain does not change, so horizons are kept across requests: per cell of {@link #CELL}
 * degrees and sector of {@code 360 / SECTORS} degrees, in a fixed-size table where a newer
 * horizon overwrites an older one on a collision. Each entry is one long (key and angle), so
 * concurrent requests never read half an entry.
 */
public final class Shade {
    /** What the sun's rays pass over on their way to the road. Heights in metres. */
    public interface Surface {
        /** Terrain height near the road, NaN where unknown. */
        double terrain(double lat, double lon);

        /** Terrain height further out, where a coarser archive is good enough. */
        double farTerrain(double lat, double lon);

        /** How high the trees stand above the terrain, 0 in the open. */
        double canopy(double lat, double lon);
    }

    static final double CELL = 0.0002;
    static final int SECTORS = 36;
    /** The rider's eye above the road: a kerb or a hedge row lower than this casts no shadow on them. */
    static final double EYE_M = 1.5;
    /** Trees higher than this over the road itself shade it. */
    static final double UNDER_CANOPY_M = 3;
    static final double FIRST_M = 10, GROWTH = 1.25;
    static final double CANOPY_M = 300, NEAR_M = 1_000, MAX_M = 10_000;
    /** The zoom graphhopper-canopy.py writes. */
    static final int CANOPY_ZOOM = 13;
    private static final double EARTH_RADIUS_M = 6_371_000;
    private static final double ANGLE_SCALE = 300;
    private static final int CACHE_BITS = 22;

    private static volatile GraphHopperConfig config;
    private static volatile Shade configured;
    private static volatile boolean unavailable;

    private final Surface surface;
    private final AtomicLongArray cache = new AtomicLongArray(1 << CACHE_BITS);

    Shade(Surface surface) {
        this.surface = surface;
    }

    /** Called from {@code GraphHopper.init} (patched in by the Dockerfile): where the archives are. */
    public static void configure(GraphHopperConfig ghConfig) {
        config = ghConfig;
    }

    /**
     * The shade model for serving, opened on first use; null when there is no terrain archive
     * or it cannot be opened. Then only the clouds count: a broken archive must not fail every
     * request that asks for sun.
     */
    public static Shade get() {
        Shade shade = configured;
        if (shade != null || config == null || unavailable)
            return shade;
        synchronized (Shade.class) {
            if (configured != null || unavailable)
                return configured;
            if (!"pmtiles".equals(config.getString("graph.elevation.provider", ""))
                    || config.getString("graph.elevation.pmtiles.location", "").isEmpty()) {
                unavailable = true;
                return null;
            }
            try {
                configured = new Shade(new ArchiveSurface(config));
            } catch (RuntimeException error) {
                unavailable = true;
                LoggerFactory.getLogger(Shade.class).error("Shade: cannot open the terrain or canopy archive; clouds only", error);
            }
            return configured;
        }
    }

    /** True when the terrain or the trees stand between the road at (lat, lon) and the sun. */
    public boolean shaded(double lat, double lon, SunPosition sun) {
        if (sun.altitude <= 0)
            return true;
        int sector = (int) Math.floor(sun.azimuth / (360.0 / SECTORS)) % SECTORS;
        return horizon(lat, lon, sector) >= sun.altitude;
    }

    /** The horizon angle in degrees at the cell around (lat, lon), towards the middle of {@code sector}. */
    double horizon(double lat, double lon, int sector) {
        long latIndex = Math.round((lat + 90) / CELL), lonIndex = Math.round((lon + 180) / CELL);
        long key = ((latIndex << 21 | lonIndex) << 6 | sector) + 1;
        int slot = (int) ((key * 0x9E3779B97F4A7C15L) >>> (64 - CACHE_BITS));
        long entry = cache.get(slot);
        if (entry >>> 16 == key)
            return (entry & 0xFFFF) / ANGLE_SCALE - 90;
        double angle = march(latIndex * CELL - 90, lonIndex * CELL - 180, (sector + 0.5) * 360.0 / SECTORS);
        long stored = Math.round((Math.max(-90, Math.min(90, angle)) + 90) * ANGLE_SCALE);
        cache.set(slot, key << 16 | stored);
        return stored / ANGLE_SCALE - 90;
    }

    /** The steepest angle up to the surface from the rider's eye, along {@code azimuth}. */
    double march(double lat, double lon, double azimuth) {
        double ground = surface.terrain(lat, lon);
        if (Double.isNaN(ground))
            return -90;
        if (surface.canopy(lat, lon) > UNDER_CANOPY_M)
            return 90;
        double eye = ground + EYE_M;
        double north = Math.cos(Math.toRadians(azimuth)), east = Math.sin(Math.toRadians(azimuth));
        double metresPerDegreeLat = Math.PI * EARTH_RADIUS_M / 180;
        double metresPerDegreeLon = metresPerDegreeLat * Math.cos(Math.toRadians(lat));
        double steepest = -90;
        for (double d = FIRST_M; d <= MAX_M; d *= GROWTH) {
            double pLat = lat + d * north / metresPerDegreeLat, pLon = lon + d * east / metresPerDegreeLon;
            double height = d <= NEAR_M ? surface.terrain(pLat, pLon) : surface.farTerrain(pLat, pLon);
            if (Double.isNaN(height))
                continue;
            if (d <= CANOPY_M)
                height += surface.canopy(pLat, pLon);
            // The earth curves away from a level line of sight: at 10 km by about 8 m.
            double rise = height - eye - d * d / (2 * EARTH_RADIUS_M);
            steepest = Math.max(steepest, Math.toDegrees(Math.atan2(rise, d)));
        }
        return steepest;
    }

    /**
     * The archives on disk: the terrain the graph was built with (zoom 15 with its zoom-12
     * fallback, like the import), the fallback alone for far terrain, and the canopy archive
     * when there is one. One instance serves every request, so the providers' tile caches stay
     * warm; they are not thread-safe, hence the lock.
     */
    static final class ArchiveSurface implements Surface {
        private final ElevationProvider near, far, canopy;

        ArchiveSurface(GraphHopperConfig config) {
            String location = config.getString("graph.elevation.pmtiles.location", "");
            near = FallbackElevationProvider.withFallback(new PMTilesElevationProvider(location,
                    PMTilesElevationProvider.TerrainEncoding.TERRARIUM, true,
                    config.getInt("graph.elevation.pmtiles.zoom", 15), ""), config, "").init();
            String fallback = config.getString("graph.elevation.pmtiles.fallback.location", "");
            far = fallback.isEmpty() ? near : new PMTilesElevationProvider(fallback,
                    PMTilesElevationProvider.TerrainEncoding.TERRARIUM, true,
                    config.getInt("graph.elevation.pmtiles.fallback.zoom", 12), "").init();
            // Beside the terrain (the entrypoint's `canopy` command puts it there), not a config
            // key: a served graph keeps the config it was built with, and Dropwizard's -Ddw.
            // nests a key that config does not declare, where GraphHopper never reads it.
            java.io.File trees = new java.io.File(location).toPath().resolveSibling("canopy.pmtiles").toFile();
            canopy = !trees.isFile() ? null : new PMTilesElevationProvider(trees.getPath(),
                    PMTilesElevationProvider.TerrainEncoding.TERRARIUM, false, CANOPY_ZOOM, "").init();
        }

        @Override
        public synchronized double terrain(double lat, double lon) {
            return near.getEle(lat, lon);
        }

        @Override
        public synchronized double farTerrain(double lat, double lon) {
            return far.getEle(lat, lon);
        }

        @Override
        public synchronized double canopy(double lat, double lon) {
            if (canopy == null)
                return 0;
            double height = canopy.getEle(lat, lon);
            // A missing tile is open land: the archive holds only tiles with trees in them.
            return Double.isNaN(height) || height < 0 ? 0 : height;
        }
    }
}
