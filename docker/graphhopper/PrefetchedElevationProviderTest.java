package com.graphhopper.reader.dem;

import com.graphhopper.util.Helper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.File;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.ByteBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/** Copied into graphhopper-core's tests by the Dockerfile, which runs it before packaging. */
class PrefetchedElevationProviderTest {

    @Test
    @SuppressWarnings("unchecked")
    void pmtilesCacheEvictsAndReleasesOldMappings() throws Exception {
        PMTilesElevationProvider provider = new PMTilesElevationProvider(
                "unused.pmtiles", PMTilesElevationProvider.TerrainEncoding.TERRARIUM,
                false, 15, "");
        Class<?> tileClass = Class.forName(
                "com.graphhopper.reader.dem.PMTilesElevationProvider$PackedTileData");
        Constructor<?> constructor = tileClass.getDeclaredConstructor(
                ByteBuffer.class, int.class, int.class, int[].class, int.class);
        constructor.setAccessible(true);
        Field data = tileClass.getDeclaredField("data");
        data.setAccessible(true);
        Field buffers = PMTilesElevationProvider.class.getDeclaredField("tileBuffers");
        buffers.setAccessible(true);
        Map<Long, Object> cache = (Map<Long, Object>) buffers.get(provider);

        Object first = null;
        Object last = null;
        for (long i = 0; i <= PMTilesElevationProvider.MAX_CACHED_TILES; i++) {
            Object tile = constructor.newInstance(
                    ByteBuffer.allocateDirect(8), 1, 1, new int[]{0, 0}, 0);
            if (i == 0) first = tile;
            last = tile;
            cache.put(i, tile);
        }

        assertEquals(PMTilesElevationProvider.MAX_CACHED_TILES, cache.size());
        assertNull(data.get(first), "evicted mapping was not released");
        assertNotNull(data.get(last), "newest mapping was released");
    }

    @Test
    void pmtilesProviderDeletesInvalidCachedTileForRegeneration(@TempDir Path dir) throws Exception {
        PMTilesElevationProvider provider = new PMTilesElevationProvider(
                "unused.pmtiles", PMTilesElevationProvider.TerrainEncoding.TERRARIUM,
                false, 15, dir.toString());
        Field tileDir = PMTilesElevationProvider.class.getDeclaredField("tileDir");
        tileDir.setAccessible(true);
        tileDir.set(provider, dir.toFile());
        Path invalid = dir.resolve("123_0.tile");
        Files.write(invalid, new byte[0]);
        Method tryMmap = PMTilesElevationProvider.class.getDeclaredMethod(
                "tryMmapTileFile", long.class);
        tryMmap.setAccessible(true);

        assertNull(tryMmap.invoke(provider, 123L));
        assertFalse(Files.exists(invalid), "invalid tile was not removed for regeneration");
    }

    /** Height from the coordinates, so a wrong key shows up as a wrong height. */
    static class RecordingProvider implements ElevationProvider {
        final List<double[]> calls = new ArrayList<>();
        boolean initialised;

        @Override
        public ElevationProvider init() {
            initialised = true;
            return this;
        }

        @Override
        public double getEle(double lat, double lon) {
            assertTrue(initialised, "delegate used before init()");
            calls.add(new double[]{lat, lon});
            return lat * 1000 + lon;
        }

        @Override
        public boolean canInterpolate() {
            return true;
        }

        @Override
        public void release() {
        }
    }

    // Four nodes whose file order alternates between two tiles far apart; node 5 repeats node 1.
    private static final double[][] NODES = {
            {47.3769123, 8.5416987}, {60.1698557, 24.9383791}, {47.3771234, 8.5421234},
            {60.1701234, 24.9391234}, {47.3769123, 8.5416987}};

    private File osmFile(Path dir) throws Exception {
        StringBuilder xml = new StringBuilder("<?xml version='1.0' encoding='UTF-8'?>\n<osm version=\"0.6\">\n");
        for (int i = 0; i < NODES.length; i++)
            xml.append(String.format(java.util.Locale.ROOT, " <node id=\"%d\" lat=\"%.7f\" lon=\"%.7f\"/>\n", i + 1, NODES[i][0], NODES[i][1]));
        xml.append(" <way id=\"10\"><nd ref=\"1\"/><nd ref=\"2\"/><tag k=\"highway\" v=\"track\"/></way>\n</osm>\n");
        Path file = dir.resolve("tiny.osm");
        Files.writeString(file, xml);
        return file.toFile();
    }

    @Test
    void prefetchesOncePerCoordinateInTileOrderAndServesTheSameHeights(@TempDir Path dir) throws Exception {
        RecordingProvider delegate = new RecordingProvider();
        PrefetchedElevationProvider provider = new PrefetchedElevationProvider(delegate, osmFile(dir), 15, 1, dir.toFile());
        provider.init();

        // One lookup per distinct coordinate, grouped by tile rather than in file order.
        assertEquals(4, delegate.calls.size());
        List<Long> tiles = new ArrayList<>();
        for (double[] call : delegate.calls)
            tiles.add(PrefetchedElevationProvider.tileIndex(call[0], call[1], 1 << 15));
        for (int i = 1; i < tiles.size(); i++)
            assertTrue(tiles.get(i - 1) <= tiles.get(i), "lookups not in tile order: " + tiles);

        // The import passes coordinates rounded to GraphHopper's 1e-7 degree ints.
        int before = delegate.calls.size();
        for (double[] node : NODES) {
            double lat = Helper.intToDegree(Helper.degreeToInt(node[0]));
            double lon = Helper.intToDegree(Helper.degreeToInt(node[1]));
            assertEquals(lat * 1000 + lon, provider.getEle(lat, lon), 0);
        }
        assertEquals(before, delegate.calls.size(), "a prefetched coordinate reached the delegate");

        // Anything else still goes to the delegate.
        assertEquals(46.0 * 1000 + 7.0, provider.getEle(46.0, 7.0), 0);
        assertEquals(before + 1, delegate.calls.size());
    }
}
