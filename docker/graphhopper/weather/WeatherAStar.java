package com.graphhopper.routing.weather;

import com.graphhopper.coll.GHIntObjectHashMap;
import com.graphhopper.routing.AbstractRoutingAlgorithm;
import com.graphhopper.routing.EdgeToEdgeRoutingAlgorithm;
import com.graphhopper.routing.Path;
import com.graphhopper.routing.PathExtractor;
import com.graphhopper.routing.SPTEntry;
import com.graphhopper.routing.util.TraversalMode;
import com.graphhopper.routing.weighting.WeightApproximator;
import com.graphhopper.routing.weighting.Weighting;
import com.graphhopper.storage.Graph;
import com.graphhopper.util.EdgeIterator;
import com.graphhopper.util.GHUtility;

import java.util.PriorityQueue;

import static com.graphhopper.util.EdgeIterator.ANY_EDGE;
import static com.graphhopper.util.EdgeIterator.NO_EDGE;

/**
 * Time-dependent A*: GraphHopper's forward {@link com.graphhopper.routing.AStar} with one difference. Every label also
 * carries the riding time from the start of the leg, and an edge costs its weight times the
 * weather multiplier at the moment the rider is on it ({@link WeatherField#multiplier}). The
 * search has to run forward: a backward search does not know the time it arrives at.
 *
 * <p>The riding time itself is the profile's ({@link Weighting#calcEdgeMillis}), untouched by
 * the weather, so the path's times and every eta stay GraphHopper's. The multipliers are at
 * least 1, so the approximator's lower bound (beeline or landmarks) stays valid.
 */
public class WeatherAStar extends AbstractRoutingAlgorithm implements EdgeToEdgeRoutingAlgorithm {
    private final WeatherField field;
    /** Epoch milliseconds at which this leg starts: the departure plus the legs before it. */
    private final long legStartMs;
    private GHIntObjectHashMap<TimedEntry> fromMap;
    private PriorityQueue<TimedEntry> fromHeap;
    private TimedEntry currEdge;
    private int visitedNodes;
    private int to = -1;
    private WeightApproximator weightApprox;
    private int fromOutEdge;
    private int toInEdge;

    public WeatherAStar(Graph graph, Weighting weighting, TraversalMode tMode, WeatherField field, long legStartMs,
                        WeightApproximator approximator) {
        super(graph, weighting, tMode);
        this.field = field;
        this.legStartMs = legStartMs;
        this.weightApprox = approximator;
        int size = Math.min(Math.max(200, graph.getNodes() / 10), 2000);
        fromMap = new GHIntObjectHashMap<>(size);
        fromHeap = new PriorityQueue<>(size);
    }

    @Override
    public Path calcPath(int from, int to) {
        return calcPath(from, to, EdgeIterator.ANY_EDGE, EdgeIterator.ANY_EDGE);
    }

    @Override
    public Path calcPath(int from, int to, int fromOutEdge, int toInEdge) {
        if ((fromOutEdge != ANY_EDGE || toInEdge != ANY_EDGE) && !traversalMode.isEdgeBased())
            throw new IllegalArgumentException("Restricting the start/target edges is only possible for edge-based graph traversal");
        this.fromOutEdge = fromOutEdge;
        this.toInEdge = toInEdge;
        checkAlreadyRun();
        setupFinishTime();
        this.to = to;
        if (fromOutEdge == NO_EDGE || toInEdge == NO_EDGE)
            return extractPath();
        weightApprox.setTo(to);
        double weightToGoal = weightApprox.approximate(from);
        if (Double.isInfinite(weightToGoal))
            return extractPath();
        fromHeap.add(new TimedEntry(EdgeIterator.NO_EDGE, from, weightToGoal, 0, 0, null));
        runAlgo();
        return extractPath();
    }

    private void runAlgo() {
        while (!fromHeap.isEmpty()) {
            currEdge = fromHeap.poll();
            if (currEdge.isDeleted())
                continue;
            visitedNodes++;
            if (isMaxVisitedNodesExceeded() || finished() || isTimeoutExceeded())
                break;

            int currNode = currEdge.adjNode;
            double lat = nodeAccess.getLat(currNode), lon = nodeAccess.getLon(currNode);
            EdgeIterator iter = edgeExplorer.setBaseNode(currNode);
            while (iter.next()) {
                if (!accept(iter, currEdge.edge) || (currEdge.edge == NO_EDGE && fromOutEdge != ANY_EDGE && iter.getEdge() != fromOutEdge))
                    continue;
                double edgeWeight = GHUtility.calcWeightWithTurnWeight(weighting, iter, false, currEdge.edge);
                if (Double.isInfinite(edgeWeight))
                    continue;
                long edgeMillis = weighting.calcEdgeMillis(iter, false);
                int neighborNode = iter.getAdjNode();
                // The weather where and when the rider is halfway along the edge.
                double multiplier = field.multiplier(lat, lon, nodeAccess.getLat(neighborNode), nodeAccess.getLon(neighborNode),
                        legStartMs + currEdge.millis + edgeMillis / 2);
                double tmpWeight = currEdge.weightOfVisitedPath + edgeWeight * multiplier;
                int traversalId = traversalMode.createTraversalId(iter, false);
                TimedEntry entry = fromMap.get(traversalId);
                if (entry == null || entry.weightOfVisitedPath > tmpWeight) {
                    double weightToGoal = weightApprox.approximate(neighborNode);
                    if (Double.isInfinite(weightToGoal))
                        continue;
                    if (entry != null)
                        entry.setDeleted();
                    entry = new TimedEntry(iter.getEdge(), neighborNode, tmpWeight + weightToGoal, tmpWeight,
                            currEdge.millis + edgeMillis, currEdge);
                    fromMap.put(traversalId, entry);
                    fromHeap.add(entry);
                }
            }
        }
    }

    private boolean finished() {
        return currEdge.adjNode == to && (toInEdge == ANY_EDGE || currEdge.edge == toInEdge) && (fromOutEdge == ANY_EDGE || currEdge.edge != NO_EDGE);
    }

    protected Path extractPath() {
        if (currEdge == null || !finished())
            return createEmptyPath();
        // The extractor recomputes time and distance from the weighting, so both stay the
        // profile's; the weight is the weather-weighted one the search minimised.
        return PathExtractor.extractPath(graph, weighting, currEdge).setWeight(currEdge.weightOfVisitedPath);
    }

    @Override
    public int getVisitedNodes() {
        return visitedNodes;
    }

    @Override
    public String getName() {
        return "weather_astar|" + weightApprox;
    }

    /** An A* label ({@code weight} is the heap key, visited weight plus estimate) with its riding time. */
    static final class TimedEntry extends SPTEntry {
        final double weightOfVisitedPath;
        /** Riding time from the start of the leg to this label's node. */
        final long millis;

        TimedEntry(int edgeId, int adjNode, double weightForHeap, double weightOfVisitedPath, long millis, SPTEntry parent) {
            super(edgeId, adjNode, weightForHeap, parent);
            this.weightOfVisitedPath = weightOfVisitedPath;
            this.millis = millis;
        }

        @Override
        public double getWeightOfVisitedPath() {
            return weightOfVisitedPath;
        }
    }
}
