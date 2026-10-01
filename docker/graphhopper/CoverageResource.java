package com.graphhopper.application.resources;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.graphhopper.GraphHopperConfig;
import jakarta.inject.Inject;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.ServiceUnavailableException;
import jakarta.ws.rs.core.MediaType;
import java.io.IOException;
import java.nio.file.Files;

/**
 * Internal: the area the running graph and its terrain cover, for the system dashboard.
 * Only the files the build left beside them, as they are: the backend does the geometry.
 */
@Path("coverage")
@Produces(MediaType.APPLICATION_JSON)
public class CoverageResource {
    private static final ObjectMapper JSON = new ObjectMapper();
    private final GraphHopperConfig config;

    @Inject
    public CoverageResource(GraphHopperConfig config) { this.config = config; }

    @GET
    public ObjectNode coverage() {
        // The entrypoint serves <release>/graph and <terrain>/terrain.pmtiles.
        java.nio.file.Path release = java.nio.file.Path.of(config.getString("graph.location", "")).toAbsolutePath().getParent();
        java.nio.file.Path terrain = java.nio.file.Path.of(
            config.getString("graph.elevation.pmtiles.location", "")).toAbsolutePath().getParent();
        if (release == null || terrain == null)
            throw new ServiceUnavailableException("Graph or terrain location is not configured");
        ObjectNode out = JSON.createObjectNode();
        JsonNode artifact = read(release.resolve("artifact.json"));
        if (artifact instanceof ObjectNode node)
            node.remove("terrain_manifest");
        out.put("release", release.getFileName().toString());
        out.set("artifact", artifact);
        out.put("terrain", terrain.getFileName().toString());
        out.set("manifest", read(terrain.resolve("manifest.json")));
        // Releases built before cells.json existed fall back to the terrain's, which may cover more.
        JsonNode cells = read(release.resolve("cells.json"));
        out.put("cells_source", cells.isNull() ? "terrain" : "graph");
        out.set("cells", cells.isNull() ? read(terrain.resolve("cells.json")) : cells);
        out.set("cell_coverage", read(terrain.resolve("cell_coverage.json")));
        return out;
    }

    private static JsonNode read(java.nio.file.Path path) {
        if (!Files.isRegularFile(path))
            return JSON.nullNode();
        try {
            return JSON.readTree(path.toFile());
        } catch (IOException error) {
            throw new ServiceUnavailableException("Cannot read " + path.getFileName(), (Long) null, error);
        }
    }
}
