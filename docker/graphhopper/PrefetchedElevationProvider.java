package com.graphhopper.reader.dem;

import com.carrotsearch.hppc.LongArrayList;
import com.carrotsearch.hppc.LongDoubleHashMap;
import com.graphhopper.GraphHopperConfig;
import com.graphhopper.reader.ReaderElement;
import com.graphhopper.reader.ReaderNode;
import com.graphhopper.reader.osm.OSMInput;
import com.graphhopper.reader.osm.SkipOptions;
import com.graphhopper.util.Helper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.File;
import java.util.Arrays;

/**
 * Looks up the height of every node of the import file once, in tile order, before the import
 * reads its ways, and answers the import from that table.
 * <p>
 * OSMReader asks for heights way by way, and way IDs follow no geography. Over a continent the
 * decoded zoom-15 tiles are far larger than RAM (Europe: 187 GB), so nearly every lookup was a
 * random disk read and "pass2 - start reading OSM ways" took four hours. In tile order each tile
 * is read once. The heights are the wrapped provider's own, at the same coordinates the import
 * passes (GraphHopper stores them as 1e-7 degree ints), so the graph does not change. A
 * coordinate missing from the table goes to the wrapped provider.
 */
public class PrefetchedElevationProvider implements ElevationProvider {
    private static final Logger logger = LoggerFactory.getLogger(PrefetchedElevationProvider.class);
    private static final long ORDINAL_MASK = (1L << 33) - 1;

    private final ElevationProvider delegate;
    private final File osmFile;
    private final int zoom;
    private final int workerThreads;
    private LongDoubleHashMap heights;

    public PrefetchedElevationProvider(ElevationProvider delegate, File osmFile, int zoom, int workerThreads) {
        this.delegate = delegate;
        this.osmFile = osmFile;
        this.zoom = zoom;
        this.workerThreads = workerThreads;
    }

    /**
     * Wraps the provider for an import, which is when datareader.file is set (the entrypoint passes
     * it only to the build). The server and its /elevation endpoint get the provider as it is.
     */
    public static ElevationProvider forImport(ElevationProvider provider, GraphHopperConfig config) {
        String file = config.getString("datareader.file", "");
        if (file.isEmpty() || provider == ElevationProvider.NOOP)
            return provider;
        int zoom = config.getInt("graph.elevation.pmtiles.zoom", -1);
        return new PrefetchedElevationProvider(provider, new File(file), zoom > 0 ? zoom : 15,
                config.getInt("datareader.worker_threads", 2));
    }

    @Override
    public ElevationProvider init() {
        delegate.init();
        prefetch();
        return this;
    }

    private void prefetch() {
        long start = System.nanoTime();
        LongArrayList coords = new LongArrayList();
        try (OSMInput input = OSMInput.open(osmFile, workerThreads, new SkipOptions(false, true, true))) {
            ReaderElement element;
            while ((element = input.getNext()) != null) {
                if (element.getType() == ReaderElement.Type.NODE) {
                    ReaderNode node = (ReaderNode) element;
                    coords.add(key(Helper.degreeToInt(node.getLat()), Helper.degreeToInt(node.getLon())));
                }
            }
        } catch (Exception e) {
            throw new RuntimeException("Could not read the nodes of " + osmFile, e);
        }
        int count = coords.size();
        logger.info("Elevation prefetch: read {} nodes in {}s", count, seconds(start));

        // Tile row-major, then file order: (tile << 33) | ordinal fits a long at zoom 15.
        int tiles = 1 << zoom;
        long[] order = new long[count];
        for (int i = 0; i < count; i++) {
            long c = coords.get(i);
            order[i] = (tileIndex(lat(c), lon(c), tiles) << 33) | i;
        }
        Arrays.parallelSort(order);

        heights = new LongDoubleHashMap(count);
        for (int i = 0; i < count; i++) {
            long c = coords.get((int) (order[i] & ORDINAL_MASK));
            if (!heights.containsKey(c))
                heights.put(c, delegate.getEle(lat(c), lon(c)));
            if ((i + 1) % 10_000_000 == 0)
                logger.info("Elevation prefetch: {} of {} nodes, {}s", i + 1, count, seconds(start));
        }
        logger.info("Elevation prefetch: {} heights in {}s", heights.size(), seconds(start));
    }

    @Override
    public double getEle(double lat, double lon) {
        if (heights != null) {
            int slot = heights.indexOf(key(Helper.degreeToInt(lat), Helper.degreeToInt(lon)));
            if (heights.indexExists(slot))
                return heights.indexGet(slot);
        }
        return delegate.getEle(lat, lon);
    }

    @Override
    public boolean canInterpolate() {
        return delegate.canInterpolate();
    }

    @Override
    public void release() {
        heights = null;
        delegate.release();
    }

    static long key(int lat, int lon) {
        return ((long) lat << 32) | (lon & 0xFFFFFFFFL);
    }

    private static double lat(long key) {
        return Helper.intToDegree((int) (key >> 32));
    }

    private static double lon(long key) {
        return Helper.intToDegree((int) key);
    }

    /** The same tile arithmetic as PMTilesElevationProvider, as y * tiles + x. */
    static long tileIndex(double lat, double lon, int tiles) {
        double latRad = Math.toRadians(lat);
        int x = (int) Math.floor((lon + 180.0) / 360.0 * tiles);
        int y = (int) Math.floor((1.0 - Math.log(Math.tan(latRad) + 1.0 / Math.cos(latRad)) / Math.PI) / 2.0 * tiles);
        x = Math.max(0, Math.min(tiles - 1, x));
        y = Math.max(0, Math.min(tiles - 1, y));
        return (long) y * tiles + x;
    }

    private static long seconds(long start) {
        return (System.nanoTime() - start) / 1_000_000_000L;
    }
}
