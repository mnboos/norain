package com.graphhopper.application.resources;

import com.graphhopper.GraphHopperConfig;
import com.graphhopper.reader.dem.ElevationProvider;
import com.graphhopper.reader.dem.FallbackElevationProvider;
import com.graphhopper.reader.dem.PMTilesElevationProvider;
import jakarta.inject.Inject;
import jakarta.ws.rs.BadRequestException;
import jakarta.ws.rs.Consumes;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.ServiceUnavailableException;
import jakarta.ws.rs.core.MediaType;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** Internal coordinate lookup: the same native provider as import, without routing or snapping. */
@Path("elevation")
@Produces(MediaType.APPLICATION_JSON)
@Consumes(MediaType.APPLICATION_JSON)
public class ElevationResource {
    private final GraphHopperConfig config;
    @Inject
    public ElevationResource(GraphHopperConfig config) { this.config = config; }

    public record Input(List<List<Double>> points) {}

    @POST
    public Map<String, List<Double>> elevation(Input input) {
        if (input == null || input.points() == null || input.points().isEmpty() || input.points().size() > 2000)
            throw new BadRequestException("Expected 1 to 2000 longitude/latitude pairs");
        for (List<Double> point : input.points()) {
            if (point == null || point.size() != 2 || point.get(0) == null || point.get(1) == null
                || !Double.isFinite(point.get(0)) || !Double.isFinite(point.get(1))
                || Math.abs(point.get(0)) > 180 || Math.abs(point.get(1)) >= 85.05112878)
                throw new BadRequestException("Invalid longitude/latitude pair");
        }
        if (!"pmtiles".equals(config.getString("graph.elevation.provider", "")))
            throw new ServiceUnavailableException("PMTiles elevation is not configured");
        // Bound decoded tile memory even for very long tracks. Each batch uses the
        // native provider on the same local archive, without a shared writable cache.
        List<Double> heights = new ArrayList<>();
        for (int start = 0; start < input.points().size(); start += 64) {
            // The same zoom-12 fallback as the import, or a saved path would read gaps the graph filled.
            ElevationProvider provider = FallbackElevationProvider.withFallback(new PMTilesElevationProvider(
                config.getString("graph.elevation.pmtiles.location", ""),
                PMTilesElevationProvider.TerrainEncoding.TERRARIUM, true,
                config.getInt("graph.elevation.pmtiles.zoom", 15), ""), config, "");
            try {
                provider.init();
                for (List<Double> point : input.points().subList(start, Math.min(start + 64, input.points().size()))) {
                    double value = provider.getEle(point.get(1), point.get(0));
                    heights.add(Double.isFinite(value) ? value : null);
                }
            } catch (RuntimeException error) {
                throw new ServiceUnavailableException("Terrain archive unavailable", (Long) null, error);
            } finally {
                provider.release();
            }
        }
        return Map.of("elevation", heights);
    }
}
