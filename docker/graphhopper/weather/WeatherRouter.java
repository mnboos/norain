package com.graphhopper.routing.weather;

import com.graphhopper.GHRequest;
import com.graphhopper.config.Profile;
import com.graphhopper.routing.AlgorithmOptions;
import com.graphhopper.routing.EdgeRestrictions;
import com.graphhopper.routing.FlexiblePathCalculator;
import com.graphhopper.routing.Path;
import com.graphhopper.routing.RoutingAlgorithm;
import com.graphhopper.routing.RoutingAlgorithmFactory;
import com.graphhopper.routing.Router;
import com.graphhopper.routing.RouterConfig;
import com.graphhopper.routing.WeightingFactory;
import com.graphhopper.routing.lm.LMApproximator;
import com.graphhopper.routing.lm.LandmarkStorage;
import com.graphhopper.routing.querygraph.QueryGraph;
import com.graphhopper.routing.util.EncodingManager;
import com.graphhopper.routing.weighting.BeelineWeightApproximator;
import com.graphhopper.routing.weighting.WeightApproximator;
import com.graphhopper.routing.weighting.Weighting;
import com.graphhopper.routing.weighting.custom.FindMinMax;
import com.graphhopper.storage.BaseGraph;
import com.graphhopper.storage.Graph;
import com.graphhopper.storage.RoutingCHGraph;
import com.graphhopper.storage.index.LocationIndex;
import com.graphhopper.util.DistancePlaneProjection;
import com.graphhopper.util.Parameters;
import com.graphhopper.util.TranslationMap;
import com.graphhopper.util.details.PathDetailsBuilderFactory;

import java.util.List;
import java.util.Map;

import static com.graphhopper.util.Parameters.Algorithms.ALT_ROUTE;

/**
 * GraphHopper's router, plus routing around weather: a request whose hints carry
 * {@link WeatherField#HINT} is solved with {@link WeatherAStar} (time-dependent, forward), every
 * other request exactly as before. Installed by the Dockerfile in {@code GraphHopper.doCreateRouter}.
 *
 * <p>Weather changes by the hour, so it can never use CH; landmarks still give the A* its
 * lower bound when the profile has them, because weather only ever adds cost. Via points and
 * round trips route leg by leg, and each leg starts at the departure plus the riding time of
 * the legs before it. {@code alternative_route} is refused: its bidirectional search cannot know
 * the time.
 */
public class WeatherRouter extends Router {

    public WeatherRouter(BaseGraph graph, EncodingManager encodingManager, LocationIndex locationIndex,
                         Map<String, Profile> profilesByName, PathDetailsBuilderFactory pathDetailsBuilderFactory,
                         TranslationMap translationMap, RouterConfig routerConfig, WeightingFactory weightingFactory,
                         Map<String, RoutingCHGraph> chGraphs, Map<String, LandmarkStorage> landmarks) {
        super(graph, encodingManager, locationIndex, profilesByName, pathDetailsBuilderFactory, translationMap,
                routerConfig, weightingFactory, chGraphs, landmarks);
    }

    @Override
    protected Solver createSolver(GHRequest request) {
        if (!request.getHints().has(WeatherField.HINT))
            return super.createSolver(request);
        WeatherField field = WeatherField.parse(request.getHints().getObject(WeatherField.HINT, null));
        return new WeatherSolver(request, profilesByName, routerConfig, encodingManager, weightingFactory, graph,
                locationIndex, landmarks, field);
    }

    static class WeatherSolver extends FlexSolver {
        private final Map<String, LandmarkStorage> landmarks;
        private final WeatherField field;

        WeatherSolver(GHRequest request, Map<String, Profile> profilesByName, RouterConfig routerConfig,
                      EncodingManager encodingManager, WeightingFactory weightingFactory, BaseGraph graph,
                      LocationIndex locationIndex, Map<String, LandmarkStorage> landmarks, WeatherField field) {
            super(request, profilesByName, routerConfig, encodingManager, weightingFactory, graph, locationIndex);
            this.landmarks = landmarks;
            this.field = field;
        }

        @Override
        protected void checkRequest() {
            super.checkRequest();
            if (ALT_ROUTE.equalsIgnoreCase(request.getAlgorithm()))
                throw new IllegalArgumentException("weather cannot be combined with algorithm=" + ALT_ROUTE
                        + ": a bidirectional search does not know when the rider is where");
        }

        @Override
        protected FlexiblePathCalculator createPathCalculator(QueryGraph queryGraph) {
            LandmarkStorage lms = request.getHints().getBool(Parameters.Landmark.DISABLE, false)
                    ? null : landmarks.get(profile.getName());
            if (lms != null && request.getCustomModel() != null)
                FindMinMax.checkLMConstraints(profile.getCustomModel(), request.getCustomModel(), lookup);
            int activeLandmarks = Math.max(1, request.getHints().getInt(Parameters.Landmark.ACTIVE_COUNT,
                    routerConfig.getActiveLandmarkCount()));
            return new LegClockPathCalculator(queryGraph, new Factory(field, lms, activeLandmarks), weighting, getAlgoOpts());
        }
    }

    /** Creates one {@link WeatherAStar} per leg, starting at the clock the calculator keeps. */
    static final class Factory implements RoutingAlgorithmFactory {
        private final WeatherField field;
        private final LandmarkStorage lms;
        private final int activeLandmarks;
        long legStartMs;

        Factory(WeatherField field, LandmarkStorage lms, int activeLandmarks) {
            this.field = field;
            this.lms = lms;
            this.activeLandmarks = activeLandmarks;
            this.legStartMs = field.departureMs;
        }

        @Override
        public RoutingAlgorithm createAlgo(Graph g, Weighting w, AlgorithmOptions opts) {
            Weighting weighting = g.wrapWeighting(w);
            WeightApproximator approximator;
            if (lms != null) {
                approximator = new LMApproximator(g, g.wrapWeighting(lms.getWeighting()), weighting, lms, activeLandmarks, false);
            } else {
                BeelineWeightApproximator beeline = new BeelineWeightApproximator(g.getNodeAccess(), weighting);
                beeline.setDistanceCalc(DistancePlaneProjection.DIST_PLANE);
                approximator = beeline;
            }
            WeatherAStar algo = new WeatherAStar(g, weighting, opts.getTraversalMode(), field, legStartMs, approximator);
            algo.setMaxVisitedNodes(opts.getMaxVisitedNodes());
            algo.setTimeoutMillis(opts.getTimeoutMillis());
            return algo;
        }
    }

    /**
     * A flexible path calculator that moves the clock on after every leg by that leg's riding
     * time. {@code ViaRouting} and {@code RoundTripRouting} both ask for the legs in riding order.
     */
    static final class LegClockPathCalculator extends FlexiblePathCalculator {
        private final Factory factory;

        LegClockPathCalculator(QueryGraph queryGraph, Factory factory, Weighting weighting, AlgorithmOptions algoOpts) {
            super(queryGraph, factory, weighting, algoOpts);
            this.factory = factory;
        }

        @Override
        public List<Path> calcPaths(int from, int to, EdgeRestrictions edgeRestrictions) {
            List<Path> paths = super.calcPaths(from, to, edgeRestrictions);
            if (!paths.isEmpty() && paths.get(0).isFound())
                factory.legStartMs += paths.get(0).getTime();
            return paths;
        }
    }
}
