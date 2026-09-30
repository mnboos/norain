<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useId, watch } from "vue";

const props = defineProps<{ lang: "de" | "en" }>();
const copy = {
    de: {
        title: "Die richtige Route zur richtigen Zeit.",
        simulation: "Simulation",
        description:
            "Simulation einer Routenplanung mit Fahrtempo und ziehendem Regen: Die kürzere Route trifft auf Regen. Auf der geplanten Route zieht die Regenzelle zuerst über die Strecke und weiter nach Norden. Die Person erreicht die Kreuzung erst danach und bleibt trocken.",
        start: "Start",
        north: "N",
        finish: "Ziel",
        rain: "Regen",
        heavy: "Stark",
        original: "Kürzer, aber nass",
        dry: "Geplante Route",
        scanning: "Regenbewegung und Fahrtempo vergleichen",
        planned: "Die Route ist bei deiner Ankunft trocken",
        riding: "Der Regen kreuzt die Route vor dir",
        crossing: "Jetzt kommst du an. Der Regen ist weiter.",
        arrived: "Trocken am Ziel",
        clears: "Regen vorbei",
        arrival: "Deine Ankunft",
        dryAt: "08:09 · trocken",
        forecastAt: "Stärkster Regen · 08:09",
        speed: "24 km/h",
        pause: "Animation pausieren",
        play: "Animation abspielen",
        timeline: "Simulierter Fahrtverlauf",
    },
    en: {
        title: "The right route. At the right time.",
        simulation: "Simulation",
        description:
            "Route-planning simulation using riding speed and moving rain: the shorter route meets the rain. On the planned route, the rain cell crosses the road first and continues north. The cyclist reaches the crossing later and stays dry.",
        start: "Start",
        north: "N",
        finish: "Finish",
        rain: "Rain",
        heavy: "Heavy",
        original: "Shorter, but wet",
        dry: "Planned route",
        scanning: "Comparing rain movement and riding speed",
        planned: "This route is dry when you get there",
        riding: "The rain crosses your route ahead of you",
        crossing: "You arrive. The rain has moved on.",
        arrived: "Arriving dry",
        clears: "Rain clears",
        arrival: "You arrive",
        dryAt: "08:09 · dry",
        forecastAt: "Heaviest rain · 08:09",
        speed: "24 km/h",
        pause: "Pause animation",
        play: "Play animation",
        timeline: "Simulated ride progress",
    },
};
const t = computed(() => copy[props.lang]);
const id = useId();
const scene = ref<HTMLElement>();
const route = ref<SVGPathElement>();
const elapsed = ref(0);
const paused = ref(false);
const reducedMotion = ref(false);
const isVisible = ref(false);
const tabVisible = ref(true);
const running = computed(() => !paused.value && !reducedMotion.value && isVisible.value && tabVisible.value);
const detour =
    "M 66 326 C 110 326 132 300 184 290 C 227 282 223 352 294 354 C 380 357 416 320 444 264 C 475 205 486 154 538 112";
const original = "M 66 326 C 110 326 132 300 184 290 C 249 277 282 215 352 196 C 425 176 470 146 538 112";
const routeLength = ref(1);
// Four seconds of planning, then 18 simulated minutes at 24 km/h over a 7.2 km route.
// Both weather and rider use this clock. Constant arc-length speed avoids slowing at bends.
const rideMinutes = computed(() => Math.max(0, Math.min(elapsed.value - 4, 18)));
const distance = computed(() => (rideMinutes.value / 18) * routeLength.value);
const clockLabel = computed(() => `08:${String(Math.floor(rideMinutes.value)).padStart(2, "0")}`);
const rider = computed(() => {
    const point = route.value?.getPointAtLength(distance.value);
    return point ? `translate(${point.x} ${point.y})` : "translate(66 326)";
});
const planned = computed(() => elapsed.value >= 2);
const status = computed(() => {
    if (rideMinutes.value >= 18) return t.value.arrived;
    if (rideMinutes.value >= 8.5) return t.value.crossing;
    if (rideMinutes.value >= 1) return t.value.riding;
    if (planned.value) return t.value.planned;
    return t.value.scanning;
});
const reveal = computed(() => Math.max(0, Math.min(1, (elapsed.value - 2) / 1.5)));
// The cell moves north at 25 map units/minute, across BOTH candidate routes.
// Its trailing edge clears the selected crossing by 08:06; the rider arrives at 08:09.
// At that same time it occupies the shorter route, making the timing choice visible.
const rainTransform = computed(() => `translate(350 ${420 - rideMinutes.value * 25})`);

// A deterministic radar raster, grouped into six SVG paths rather than thousands of DOM nodes.
// Coarse and fine noise produce connected, patchy cells with the cyan/blue/purple radar palette.
const radarPalette = ["#8bdfdf", "#49cbda", "#20b1d2", "#298cd0", "#6663d1", "#ad42c6"];
function noise(x: number, y: number) {
    const hash = (a: number, b: number) => {
        const value = Math.sin(a * 127.1 + b * 311.7 + 17) * 43758.5453;
        return value - Math.floor(value);
    };
    const ix = Math.floor(x);
    const iy = Math.floor(y);
    const fx = x - ix;
    const fy = y - iy;
    const sx = fx * fx * (3 - 2 * fx);
    const sy = fy * fy * (3 - 2 * fy);
    return (
        (hash(ix, iy) * (1 - sx) + hash(ix + 1, iy) * sx) * (1 - sy) +
        (hash(ix, iy + 1) * (1 - sx) + hash(ix + 1, iy + 1) * sx) * sy
    );
}
function makeRadarBands(minute: number) {
    const bands = radarPalette.map(fill => ({ fill, d: "" }));
    // Build from scattered light rain, peak over the rejected route at 08:09, then dissipate.
    const stormStrength = 0.45 + 0.75 * Math.exp(-(((minute - 9) / 3.8) ** 2));
    for (let y = -108; y < 108; y += 6) {
        for (let x = -144; x < 144; x += 6) {
            // Shear the body and evolve separate cores: rain patches stretch, split, and merge.
            // Keep the broad envelope bounded so the timed crossing stays dry in every frame.
            const warpedX = x + Math.sin(y / 30 + minute * 0.55) * 14;
            const warpedY = y + Math.sin(x / 34 - minute * 0.45) * 12;
            const coreX = 10 + Math.sin(minute * 0.6) * 20;
            const coreY = -8 + Math.cos(minute * 0.45) * 22;
            const width = 55 + Math.sin(minute * 0.5) * 8;
            const height = 59 + Math.sin(minute * 0.4 + 1) * 12;
            const body =
                Math.exp(-(((warpedX + 5) / width) ** 2) - (warpedY / height) ** 2) * 0.52 +
                Math.exp(-(((warpedX - coreX) / 27) ** 2) - ((warpedY - coreY) / 38) ** 2) *
                    (0.48 + Math.sin(minute * 0.7) * 0.18) +
                Math.exp(-(((warpedX + coreX + 8) / 24) ** 2) - ((warpedY + coreY + 28) / 30) ** 2) *
                    (0.3 - Math.sin(minute * 0.7) * 0.16);
            const intensity =
                stormStrength *
                (body +
                    ((noise(warpedX / 23 + minute * 0.18, warpedY / 23 - minute * 0.14) - 0.5) * 0.65 +
                        (noise(x / 8 - minute * 0.09, y / 8 + minute * 0.12) - 0.5) * 0.26) *
                        Math.sqrt(body));
            if (intensity < 0.19) continue;
            const band = bands[Math.min(5, Math.floor((intensity - 0.19) / 0.14))];
            if (band) band.d += `M${x} ${y}h6v6h-6z`;
        }
    }
    return bands;
}
// Precompute radar snapshots once; blend adjacent frames instead of flickering pixels.
// The shared simulation clock freezes both movement and evolution when paused.
const radarFrameMinutes = 0.75;
const radarFrames = Array.from({ length: 25 }, (_, index) => makeRadarBands(index * radarFrameMinutes));
const peakRadarBands = makeRadarBands(9);
const radarFrame = computed(() => rideMinutes.value / radarFrameMinutes);
const radarMix = computed(() => radarFrame.value % 1);
const radarBands = computed(() => radarFrames[Math.floor(radarFrame.value)]);
const nextRadarBands = computed(() => radarFrames[Math.min(24, Math.floor(radarFrame.value) + 1)]);

let animationFrame = 0;
let previousTime = 0;
let observer: IntersectionObserver | undefined;
let motionQuery: MediaQueryList | undefined;
function syncMotionPreference() {
    reducedMotion.value = motionQuery?.matches ?? false;
    // A complete, readable scene also works without any movement.
    if (reducedMotion.value) elapsed.value = 13;
}
function syncVisibility() {
    tabVisible.value = !document.hidden;
    previousTime = 0;
}
function animate(now: number) {
    if (running.value && previousTime) {
        elapsed.value = (elapsed.value + Math.max(0, Math.min((now - previousTime) / 1000, 0.1))) % 26;
    }
    previousTime = now;
    animationFrame = requestAnimationFrame(animate);
}
watch(running, active => {
    cancelAnimationFrame(animationFrame);
    previousTime = 0;
    if (active) animationFrame = requestAnimationFrame(animate);
});
onMounted(() => {
    routeLength.value = route.value?.getTotalLength() ?? 1;
    motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    syncMotionPreference();
    motionQuery.addEventListener("change", syncMotionPreference);
    syncVisibility();
    document.addEventListener("visibilitychange", syncVisibility);
    observer = new IntersectionObserver(([entry]) => {
        isVisible.value = entry?.isIntersecting ?? false;
    });
    if (scene.value) observer.observe(scene.value);
});
onBeforeUnmount(() => {
    cancelAnimationFrame(animationFrame);
    observer?.disconnect();
    motionQuery?.removeEventListener("change", syncMotionPreference);
    document.removeEventListener("visibilitychange", syncVisibility);
});
</script>

<template>
    <figure ref="scene" class="rain-simulation" :aria-label="t.title">
        <figcaption class="simulation-heading">
            <span class="simulation-title">{{ t.title }}</span>
            <span class="simulation-badge">
                <span />
                {{ t.simulation }}
            </span>
        </figcaption>
        <div class="radar-map">
            <svg class="map-art" viewBox="0 0 600 430" role="img" :aria-labelledby="`${id}-description`">
                <title :id="`${id}-description`">{{ t.description }}</title>
                <defs>
                    <pattern :id="`${id}-grid`" width="40" height="40" patternUnits="userSpaceOnUse">
                        <path d="M 40 0 H 0 V 40" fill="none" stroke="#92b6c6" stroke-opacity=".065" />
                    </pattern>
                    <radialGradient :id="`${id}-map-glow`">
                        <stop stop-color="#234c59" />
                        <stop offset="1" stop-color="#102a3c" />
                    </radialGradient>
                    <g :id="`${id}-rain-raster`" style="isolation: isolate">
                        <g :opacity="1 - radarMix" style="mix-blend-mode: plus-lighter">
                            <path v-for="band in radarBands" :key="band.fill" :d="band.d" :fill="band.fill" />
                        </g>
                        <g :opacity="radarMix" style="mix-blend-mode: plus-lighter">
                            <path v-for="band in nextRadarBands" :key="band.fill" :d="band.d" :fill="band.fill" />
                        </g>
                    </g>
                </defs>
                <rect width="600" height="430" :fill="`url(#${id}-map-glow)`" />
                <!-- Quiet topographic detail keeps the weather and route in the foreground. -->
                <g fill="#24483f" fill-opacity=".5" stroke="#46685b" stroke-opacity=".28">
                    <path d="M -20 62 Q 54 10 116 53 T 192 128 Q 168 187 93 167 T -20 208 Z" />
                    <path d="M 381 -20 Q 352 50 401 88 T 501 87 Q 557 66 622 122 V -20 Z" />
                    <path d="M 442 352 Q 487 306 547 336 T 624 324 V 449 H 429 Z" />
                </g>
                <g fill="none" stroke="#94b0a3" stroke-opacity=".09" stroke-width="1.2">
                    <path
                        v-for="n in 5"
                        :key="n"
                        :d="`M -30 ${30 + n * 13} Q 75 ${n * 5} 141 ${75 + n * 14} T 212 ${165 + n * 14}`"
                    />
                    <path
                        v-for="n in 5"
                        :key="`hill-${n}`"
                        :d="`M ${405 + n * 14} 445 Q ${385 + n * 15} 345 500 ${350 + n * 13} T 640 ${320 + n * 15}`"
                    />
                </g>
                <path
                    d="M -30 232 C 87 171 114 234 158 211 S 204 129 244 126 S 265 43 313 -20"
                    fill="none"
                    stroke="#438195"
                    stroke-opacity=".25"
                    stroke-width="17"
                />
                <path
                    d="M -30 232 C 87 171 114 234 158 211 S 204 129 244 126 S 265 43 313 -20"
                    fill="none"
                    stroke="#73aabd"
                    stroke-opacity=".2"
                    stroke-width="2"
                />
                <rect width="600" height="430" :fill="`url(#${id}-grid)`" />
                <g
                    fill="none"
                    stroke="#69868f"
                    stroke-opacity=".18"
                    stroke-width="6"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                >
                    <path
                        d="M -10 370 L 91 271 L 168 265 L 221 186 L 344 134 L 440 158 L 515 228 L 610 249 M 28 -10 L 73 87 L 152 130 L 221 186 L 271 275 L 361 310 L 413 435 M 73 87 L 173 67 L 255 81 L 344 134 L 389 40 L 492 -10 M 168 265 L 177 373 L 110 441 M 361 310 L 473 337 L 610 299"
                    />
                    <path :d="detour" stroke-width="11" />
                    <path :d="original" stroke-width="11" />
                </g>
                <g fill="none" stroke="#b4c5c8" stroke-opacity=".1" stroke-width="1">
                    <path
                        d="M -10 370 L 91 271 L 168 265 L 221 186 L 344 134 L 440 158 L 515 228 L 610 249 M 28 -10 L 73 87 L 152 130 L 221 186 L 271 275 L 361 310 L 413 435"
                    />
                </g>
                <!-- Northbound rain crosses the road first. Arrows make its direction explicit. -->
                <g fill="none" stroke="#97cee4" stroke-opacity=".35">
                    <path d="M 350 402 V 74" stroke-dasharray="3 9" />
                    <path d="m344 118 6-8 6 8 m-12 120 6-8 6 8 m-12 120 6-8 6 8" />
                </g>
                <g v-if="elapsed < 4" transform="translate(350 195)" opacity=".24">
                    <path v-for="band in peakRadarBands" :key="band.fill" :d="band.d" :fill="band.fill" />
                </g>
                <use class="rain-cell" :href="`#${id}-rain-raster`" :transform="rainTransform" opacity=".85" />
                <g :transform="`translate(95 ${100 - rideMinutes * 25}) scale(.55)`" opacity=".55">
                    <use :href="`#${id}-rain-raster`" />
                </g>
                <path
                    :d="original"
                    fill="none"
                    stroke="#d7b7ac"
                    stroke-opacity=".6"
                    stroke-width="2.5"
                    stroke-dasharray="5 7"
                    stroke-linecap="round"
                />
                <path
                    ref="route"
                    :d="detour"
                    fill="none"
                    stroke="#74e2bf"
                    stroke-width="3.5"
                    pathLength="1"
                    :stroke-dasharray="`${reveal} 1`"
                    stroke-linecap="round"
                    :opacity="reveal"
                />
                <path
                    :d="detour"
                    fill="none"
                    stroke="#a3f2d4"
                    stroke-opacity=".1"
                    stroke-width="13"
                    pathLength="1"
                    :stroke-dasharray="`${reveal} 1`"
                    :opacity="reveal"
                />
                <path
                    :d="detour"
                    fill="none"
                    stroke="#e0fff0"
                    stroke-width="3.5"
                    :stroke-dasharray="`${distance} ${routeLength}`"
                    stroke-linecap="round"
                />
                <g v-if="planned" class="crossing-marker">
                    <circle
                        cx="350"
                        cy="349"
                        r="9"
                        fill="none"
                        stroke="#a3f2d4"
                        stroke-width="1.5"
                        stroke-dasharray="3 3"
                    />
                    <path d="M350 340 V321" stroke="#a3f2d4" stroke-opacity=".6" />
                    <rect
                        x="289"
                        y="295"
                        width="122"
                        height="26"
                        rx="13"
                        fill="#102a3c"
                        stroke="#74e2bf"
                        stroke-opacity=".4"
                    />
                    <text x="350" y="312" fill="#c1f5df" text-anchor="middle" font-size="12">{{ t.dryAt }}</text>
                </g>
                <g v-if="elapsed < 4 || (rideMinutes >= 8 && rideMinutes <= 10)">
                    <circle cx="350" cy="197" r="8" fill="#102a3c" stroke="#efb5a6" stroke-width="1.5" />
                    <path d="m347 194 6 6 m-6 0 6-6" stroke="#efb5a6" stroke-width="1.5" />
                    <rect x="265" y="151" width="170" height="26" rx="13" fill="#102a3c" />
                    <text x="350" y="168" fill="#efd0c7" text-anchor="middle" font-size="12">{{ t.forecastAt }}</text>
                </g>
                <g font-size="12" font-weight="500" fill="#d0e1e4">
                    <circle cx="66" cy="326" r="6" fill="#153849" stroke="#d2f5e5" stroke-width="2" />
                    <text x="66" y="354" text-anchor="middle">{{ t.start }}</text>
                    <circle cx="538" cy="112" r="10" fill="#e5b95c" fill-opacity=".14" />
                    <circle cx="538" cy="112" r="5" fill="#e5b95c" stroke="#ffe6ac" stroke-width="2" />
                    <text x="538" y="91" text-anchor="middle">{{ t.finish }}</text>
                </g>
                <g :transform="rider" class="rider-marker">
                    <circle :r="25 + Math.sin(elapsed * 3) * 3" fill="#9cf4d0" opacity=".09" />
                    <circle r="20" fill="#061e30" opacity=".25" transform="translate(0 3)" />
                    <circle r="17" fill="#e6fff4" stroke="#7de1bd" stroke-width="2" />
                    <g fill="none" stroke="#153b43" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="-7.5" cy="5" r="4" />
                        <circle cx="8" cy="5" r="4" />
                        <path
                            d="M -7.5 5 L -3 -3 L 2 5 H -7.5 M -3 -3 H 5 L 8 5 M 5 -3 L 3 -7 H 6 M -5 -4 H -1 M 0 -8 L -3 -3"
                        />
                        <circle cx="1" cy="-10" r="2" fill="#153b43" stroke="none" />
                    </g>
                </g>
                <g transform="translate(26 30)" fill="#b2c9cc" font-size="10" letter-spacing="2">
                    <path d="M 6 0 L 10 12 L 6 9 L 2 12 Z" fill="#b2c9cc" />
                    <text x="6" y="26" text-anchor="middle">{{ t.north }}</text>
                </g>
            </svg>
            <div class="radar-legend">
                <span>{{ t.rain }}</span>
                <span class="rain-scale" />
                <span>{{ t.heavy }}</span>
            </div>
            <div class="simulation-clock">
                <time>{{ clockLabel }}</time>
                <span>{{ t.speed }}</span>
            </div>
            <div class="route-key">
                <span>
                    <i class="original-line" />
                    {{ t.original }}
                </span>
                <span>
                    <i class="dry-line" />
                    {{ t.dry }}
                </span>
            </div>
        </div>
        <div class="crossing-times">
            <span>
                {{ t.clears }}
                <strong>08:06</strong>
            </span>
            <svg viewBox="0 0 24 16" aria-hidden="true"><path d="M2 8h18m-5-5 5 5-5 5" /></svg>
            <span>
                {{ t.arrival }}
                <strong>08:09</strong>
            </span>
        </div>
        <div class="simulation-footer">
            <div class="ride-status">
                <span class="status-icon" :class="{ 'status-icon--dry': planned }" aria-hidden="true">
                    <svg
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        stroke-width="1.6"
                        stroke-linecap="round"
                        stroke-linejoin="round"
                    >
                        <path v-if="planned" d="m6 12 4 4 8-8" />
                        <path
                            v-else
                            d="M 7 15 H 17 A 4 4 0 0 0 17 7 A 5 5 0 0 0 7 6 A 4.5 4.5 0 0 0 7 15 M 9 18 L 8 20 M 14 18 L 13 20"
                        />
                    </svg>
                </span>
                <span>{{ status }}</span>
            </div>
            <button
                v-if="!reducedMotion"
                type="button"
                class="playback-control"
                :aria-label="paused ? t.play : t.pause"
                @click="paused = !paused"
            >
                <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                    <path v-if="paused" d="M 7 4 L 15 10 L 7 16 Z" />
                    <path v-else d="M 6 5 H 8 V 15 H 6 Z M 12 5 H 14 V 15 H 12 Z" />
                </svg>
            </button>
        </div>
        <div
            class="ride-timeline"
            role="progressbar"
            :aria-label="t.timeline"
            :aria-valuenow="Math.round((distance / routeLength) * 100)"
            :aria-valuemin="0"
            :aria-valuemax="100"
        >
            <span :style="{ transform: `scaleX(${distance / routeLength})` }" />
        </div>
    </figure>
</template>

<style scoped>
.rain-simulation {
    width: 100%;
    margin: 0;
    overflow: hidden;
    border: 1px solid #ffffff26;
    border-radius: 22px;
    background: #102a3c;
    color: #e5f3ef;
    box-shadow:
        0 24px 65px #071c3540,
        0 2px 8px #071c3526;
    isolation: isolate;
}
.simulation-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 10px;
    padding: 20px 22px;
    border-bottom: 1px solid #c1e1df12;
}
.simulation-title {
    font-size: 14px;
    font-weight: 500;
}
.simulation-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: #aac8c7;
    font-size: 10px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
}
.simulation-badge > span {
    width: 5px;
    height: 5px;
    background: #8edabf;
    border-radius: 50%;
}
.radar-map {
    position: relative;
}
.map-art {
    width: 100%;
    height: auto;
    display: block;
}
.radar-legend {
    position: absolute;
    top: 18px;
    right: 20px;
    display: flex;
    align-items: center;
    gap: 7px;
    font-size: 9px;
    color: #b9d0d2;
}
.rain-scale {
    width: 45px;
    height: 4px;
    border-radius: 4px;
    background: linear-gradient(90deg, #8bdfdf, #20b1d2, #298cd0, #ad42c6);
}
.simulation-clock {
    position: absolute;
    top: 42px;
    right: 20px;
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 10px;
    color: #b9d0d2;
    background: #102a3cdd;
    border: 1px solid #c1e1df20;
    border-radius: 8px;
    padding: 5px 9px;
}
.simulation-clock time {
    color: #e5f3ef;
    font-size: 14px;
    font-variant-numeric: tabular-nums;
}
.crossing-times {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 12px;
    padding: 12px 16px;
    border-top: 1px solid #c1e1df12;
    font-size: 10px;
    color: #b9d0d2;
    background: #102a3c;
}
.crossing-times span {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 4px 6px;
}
.crossing-times strong {
    font-size: 12px;
    color: #c1f5df;
    font-weight: 500;
}
.crossing-times svg {
    width: 22px;
    height: 16px;
    flex-shrink: 0;
    fill: none;
    stroke: #7da79f;
    stroke-width: 1.5;
}
.route-key {
    position: absolute;
    bottom: 15px;
    left: 22px;
    right: 22px;
    display: flex;
    justify-content: center;
    flex-wrap: wrap;
    gap: 8px 18px;
    color: #b9cecf;
    font-size: 10px;
}
.route-key > span {
    display: inline-flex;
    gap: 7px;
    align-items: center;
    padding: 3px 7px;
    border-radius: 6px;
    background: #102a3cdd;
}
.route-key i {
    width: 18px;
}
.original-line {
    border-top: 2px dashed #d7b7ac;
}
.dry-line {
    border-top: 2px solid #74e2bf;
}
.simulation-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 12px 18px;
    background: #0d2435;
    border-top: 1px solid #c1e1df12;
}
.ride-status {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 12px;
    font-weight: 500;
}
.status-icon {
    width: 30px;
    height: 30px;
    display: grid;
    place-items: center;
    border-radius: 50%;
    background: #e5b95c14;
    color: #e5b95c;
    flex-shrink: 0;
}
.status-icon--dry {
    background: #74e2bf14;
    color: #9be8cc;
}
.status-icon svg {
    width: 20px;
    height: 20px;
}
.playback-control {
    display: grid;
    place-items: center;
    width: 36px;
    height: 36px;
    flex-shrink: 0;
    border: 1px solid #c1e1df26;
    border-radius: 50%;
    color: #cce4e0;
    background: transparent;
    cursor: pointer;
}
.playback-control:hover {
    background: #c1e1df15;
}
.playback-control:focus-visible {
    outline: 2px solid #e5b95c;
    outline-offset: 3px;
}
.playback-control svg {
    width: 20px;
    height: 20px;
}
.ride-timeline {
    height: 2px;
    background: #c1e1df0d;
}
.ride-timeline > span {
    display: block;
    width: 100%;
    height: 100%;
    transform-origin: left;
    background: #8edabf;
}
@media (max-width: 599px) {
    .simulation-heading {
        padding: 16px;
    }
    .simulation-title {
        font-size: 12px;
    }
    .simulation-badge {
        font-size: 9px;
    }
    .route-key {
        bottom: 10px;
        left: 10px;
        right: 10px;
        gap: 8px 12px;
        font-size: 9px;
    }
    .radar-legend {
        top: 12px;
        right: 14px;
        font-size: 8px;
    }
    .simulation-clock {
        top: 30px;
        right: 12px;
        gap: 6px;
        font-size: 8px;
        padding: 3px 6px;
    }
    .simulation-clock time {
        font-size: 11px;
    }
    .crossing-times {
        font-size: 9px;
        gap: 8px;
        padding: 10px 12px;
    }
    .simulation-footer {
        padding: 10px 12px;
    }
    .ride-status {
        font-size: 11px;
        gap: 8px;
    }
}
</style>
