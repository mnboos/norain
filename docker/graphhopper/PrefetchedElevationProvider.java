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
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.file.NoSuchFileException;
import java.nio.file.StandardOpenOption;
import java.util.Arrays;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Looks up the height of every node of the import file once, in tile order, before the import
 * reads its ways, and answers the import from that table.
 * <p>
 * OSMReader asks for heights way by way, and way IDs follow no geography. Over a continent the
 * decoded zoom-15 tiles are far larger than RAM (Europe: 187 GB), so nearly every lookup was a
 * random disk read and "pass2 - start reading OSM ways" took four hours. In tile order each tile
 * is read once, and a few threads read the next tiles' cache files ahead of the lookups, because
 * one thread waiting on each tile in turn leaves the disk idle (~45 min for Europe). The heights are the wrapped provider's own, at the same coordinates the import
 * passes (GraphHopper stores them as 1e-7 degree ints), so the graph does not change. A
 * coordinate missing from the table goes to the wrapped provider.
 */
public class PrefetchedElevationProvider implements ElevationProvider {
    private static final Logger logger = LoggerFactory.getLogger(PrefetchedElevationProvider.class);
    private static final long ORDINAL_MASK = (1L << 33) - 1;
    // Tiles read ahead of the lookups (~200 KB each), and by how many threads.
    private static final int READ_AHEAD_TILES = 512;
    private static final int READ_AHEAD_THREADS = 16;

    private final ElevationProvider delegate;
    private final File osmFile;
    private final int zoom;
    private final int workerThreads;
    private final File tileDir;
    private LongDoubleHashMap heights;

    /** @param tileDir the zoom-15 provider's decoded tile cache, read ahead of the lookups; null for none */
    public PrefetchedElevationProvider(ElevationProvider delegate, File osmFile, int zoom, int workerThreads, File tileDir) {
        this.delegate = delegate;
        this.osmFile = osmFile;
        this.zoom = zoom;
        this.workerThreads = workerThreads;
        this.tileDir = tileDir;
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
        String tileDir = config.getString("graph.elevation.cache_dir", "");
        return new PrefetchedElevationProvider(provider, new File(file), zoom > 0 ? zoom : 15,
                config.getInt("datareader.worker_threads", 2), tileDir.isEmpty() ? null : new File(tileDir));
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

        ExecutorService readAhead = tileDir == null ? null : Executors.newFixedThreadPool(READ_AHEAD_THREADS, r -> {
            Thread t = new Thread(r, "elevation-read-ahead");
            t.setDaemon(true);
            return t;
        });
        heights = new LongDoubleHashMap(count);
        try {
            // Index into order[] of the first node of the next tile not yet handed to read-ahead.
            int ahead = 0, tilesAhead = 0;
            long lastTile = -1;
            for (int i = 0; i < count; i++) {
                long tile = order[i] >>> 33;
                if (readAhead != null && tile != lastTile) {
                    lastTile = tile;
                    tilesAhead--;
                    while (ahead < count && tilesAhead < READ_AHEAD_TILES) {
                        long next = order[ahead] >>> 33;
                        File f = tileFile(next, tiles);
                        readAhead.execute(() -> readQuietly(f));
                        tilesAhead++;
                        while (ahead < count && order[ahead] >>> 33 == next)
                            ahead++;
                    }
                }
                long c = coords.get((int) (order[i] & ORDINAL_MASK));
                if (!heights.containsKey(c))
                    heights.put(c, delegate.getEle(lat(c), lon(c)));
                if ((i + 1) % 10_000_000 == 0)
                    logger.info("Elevation prefetch: {} of {} nodes, {}s", i + 1, count, seconds(start));
            }
        } finally {
            if (readAhead != null)
                readAhead.shutdownNow();
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

    /** The provider's cache file for a tile: its PMTiles (Hilbert) ID and the zoom. */
    private File tileFile(long tile, int tiles) {
        long id = PMTilesReader.hilbertBase(zoom) + PMTilesReader.xyToHilbertD(zoom, tile % tiles, tile / tiles);
        return new File(tileDir, id + "_" + zoom + ".tile");
    }

    /** Pulls a file into the page cache. A tile not decoded yet has no file: the provider makes it. */
    private static void readQuietly(File f) {
        ByteBuffer buf = READ_BUFFER.get();
        try (FileChannel ch = FileChannel.open(f.toPath(), StandardOpenOption.READ)) {
            while (ch.read(buf.clear()) > 0) ;
        } catch (NoSuchFileException ignored) {
        } catch (IOException e) {
            logger.debug("Elevation read-ahead of {} failed: {}", f, e.getMessage());
        }
    }

    private static final ThreadLocal<ByteBuffer> READ_BUFFER = ThreadLocal.withInitial(() -> ByteBuffer.allocateDirect(1 << 20));

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
