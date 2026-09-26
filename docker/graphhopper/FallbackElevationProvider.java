package com.graphhopper.reader.dem;

import com.graphhopper.GraphHopperConfig;

/**
 * Reads the zoom-15 archive and, where it has no value (a missing tile or Terrarium nodata),
 * a coarser archive of the same area. Without it GraphHopper stores 0 m for such a node, and
 * its slopes against the real heights around it become cliffs.
 */
public class FallbackElevationProvider implements ElevationProvider {
    private final ElevationProvider primary;
    private final ElevationProvider fallback;

    public FallbackElevationProvider(ElevationProvider primary, ElevationProvider fallback) {
        this.primary = primary;
        this.fallback = fallback;
    }

    /**
     * Wraps the configured provider when graph.elevation.pmtiles.fallback.location is set.
     * The import (GraphHopper.init) and the /elevation endpoint both come through here, so a
     * saved path reads the same heights the graph was built with.
     */
    public static ElevationProvider withFallback(ElevationProvider primary, GraphHopperConfig config, String cacheDir) {
        String location = config.getString("graph.elevation.pmtiles.fallback.location", "");
        if (location.isEmpty() || primary == ElevationProvider.NOOP)
            return primary;
        // An explicit zoom: the provider would otherwise pick min(max zoom, 11).
        PMTilesElevationProvider fallback = new PMTilesElevationProvider(
                location, PMTilesElevationProvider.TerrainEncoding.TERRARIUM, primary.canInterpolate(),
                config.getInt("graph.elevation.pmtiles.fallback.zoom", 12), cacheDir);
        fallback.setAutoRemoveTemporaryFiles(config.getBool("graph.elevation.clear", false));
        return new FallbackElevationProvider(primary, fallback);
    }

    @Override
    public ElevationProvider init() {
        primary.init();
        fallback.init();
        return this;
    }

    @Override
    public double getEle(double lat, double lon) {
        double value = primary.getEle(lat, lon);
        return Double.isNaN(value) ? fallback.getEle(lat, lon) : value;
    }

    @Override
    public boolean canInterpolate() {
        return primary.canInterpolate();
    }

    @Override
    public void release() {
        primary.release();
        fallback.release();
    }
}
