/**
 * WMO weather code -> inline SVG glyph, plus the thinning rule that decides how many
 * weather chips the map shows at the current zoom.
 *
 * Kept free of maplibre/vue imports so the pure parts stay unit-testable.
 */

export type RainCondition = "dry" | "rain" | "heavy_rain";

/**
 * Same thresholds as the backend (`core/sections.py::_condition`), so the map, the section
 * list and the emails cut the route at exactly the same points.
 */
export function rainCondition(mm: number): RainCondition {
    if (mm < 0.1) return "dry";
    if (mm < 2.5) return "rain";
    return "heavy_rain";
}

/**
 * Rough day/night flag for the clear-sky glyph. The payload carries no `is_day`, so this is
 * derived from the ETA. Read straight off the ISO string rather than via `new Date()` so an
 * offset-bearing timestamp can't shift it into a different local hour.
 */
export function isNightEta(eta: string): boolean {
    const hh = eta.slice(11, 13);
    const hour = Number(hh);
    if (hh.length !== 2 || !Number.isInteger(hour)) return false;
    return hour < 6 || hour >= 21;
}

// --- glyph pieces (24x24 viewBox) -------------------------------------------------------

const CLOUD = `
    <g fill="#90a4ae">
        <circle cx="9" cy="11" r="4"/>
        <circle cx="14.5" cy="10" r="5"/>
        <rect x="5" y="12" width="14" height="4" rx="2"/>
    </g>`;

const SMALL_CLOUD = `
    <g fill="#90a4ae">
        <circle cx="12" cy="14" r="3.6"/>
        <circle cx="16" cy="13" r="4.4"/>
        <rect x="8.5" y="15" width="12" height="3.6" rx="1.8"/>
    </g>`;

const SUN = `
    <g stroke="#f5a623" stroke-width="2" stroke-linecap="round">
        <path d="M12 1.5v2.5"/><path d="M12 20v2.5"/>
        <path d="M1.5 12h2.5"/><path d="M20 12h2.5"/>
        <path d="M4.6 4.6l1.8 1.8"/><path d="M17.6 17.6l1.8 1.8"/>
        <path d="M19.4 4.6l-1.8 1.8"/><path d="M6.4 17.6l-1.8 1.8"/>
    </g>
    <circle cx="12" cy="12" r="5" fill="#f5a623"/>`;

const MOON = `<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" fill="#f4d03f"/>`;

const SMALL_SUN = `<circle cx="8.5" cy="8" r="3.8" fill="#f5a623"/>`;
const SMALL_MOON = `<path d="M12.4 8.6A5 5 0 1 1 7 3.2a3.9 3.9 0 0 0 5.4 5.4z" fill="#f4d03f"/>`;

const drops = (xs: number[], len: number) =>
    `<g stroke="#3498db" stroke-width="2" stroke-linecap="round">` +
    xs.map(x => `<path d="M${x} 17.5l-1 ${len}"/>`).join("") +
    `</g>`;

const flakes = (xs: number[]) =>
    `<g fill="#5dade2">` + xs.map(x => `<circle cx="${x}" cy="19.5" r="1.3"/>`).join("") + `</g>`;

const BOLT = `<path d="M14 15.5l-4.5 5.5h2.8l-.8 3 4.5-5.6h-2.9z" fill="#f39c12"/>`;

const GLYPHS = {
    clear_day: SUN,
    clear_night: MOON,
    partly_day: SMALL_SUN + SMALL_CLOUD,
    partly_night: SMALL_MOON + SMALL_CLOUD,
    cloudy: CLOUD,
    fog:
        CLOUD +
        `<g stroke="#b0bec5" stroke-width="2" stroke-linecap="round">
            <path d="M6 19h12"/><path d="M8 22h9"/>
        </g>`,
    drizzle: CLOUD + drops([9, 13, 17], 2),
    rain: CLOUD + drops([9, 13, 17], 4),
    sleet: CLOUD + drops([9, 15], 3.5) + flakes([12.5]),
    snow: CLOUD + flakes([9, 13, 17]),
    thunder: CLOUD + BOLT,
} as const;

export type GlyphName = keyof typeof GLYPHS;

/**
 * Groups the WMO codes the backend knows about (`core/weather.py::WMO_DE`) into glyphs.
 * `code` is nullable: the OpenWeatherMap fallback path never sets it, so we fall back to
 * the measured rain amount rather than rendering a blank chip.
 */
export function weatherGlyphName(code: number | null | undefined, opts: { rainMm: number; night: boolean }): GlyphName {
    const { rainMm, night } = opts;

    if (code == null) {
        const cond = rainCondition(rainMm);
        return cond === "dry" ? "cloudy" : "rain";
    }

    switch (code) {
        case 0:
        case 1:
            return night ? "clear_night" : "clear_day";
        case 2:
            return night ? "partly_night" : "partly_day";
        case 3:
            return "cloudy";
        case 45:
        case 48:
            return "fog";
        case 51:
        case 53:
        case 55:
            return "drizzle";
        case 56:
        case 57:
        case 66:
        case 67:
            return "sleet";
        case 61:
        case 63:
        case 65:
        case 80:
        case 81:
        case 82:
            return "rain";
        case 71:
        case 73:
        case 75:
        case 77:
        case 85:
        case 86:
            return "snow";
        case 95:
        case 96:
        case 99:
            return "thunder";
        default:
            return rainCondition(rainMm) === "dry" ? "cloudy" : "rain";
    }
}

/** Inner markup of a 24x24 SVG for the given weather code. Never empty. */
export function weatherIconSvg(code: number | null | undefined, opts: { rainMm: number; night: boolean }): string {
    return GLYPHS[weatherGlyphName(code, opts)];
}

// --- zoom-aware thinning ----------------------------------------------------------------

export interface ScreenPoint {
    x: number;
    y: number;
}

export interface ThinnableSample {
    rainMm: number;
}

/**
 * Decide which sample indices get a chip at the current zoom.
 *
 * Keeps the first and last sample, and the first sample of every new weather condition
 * (so a short rain window is never thinned away) - but a condition change only earns a
 * chip when it is at least `transitionMinPx` from the previous one. Without that floor,
 * showery weather flips dry/rain between adjacent samples and thinning stops thinning.
 *
 * `project` maps a sample index to screen pixels (`map.project([lon, lat])`).
 */
export function pickVisibleSamples(
    samples: readonly ThinnableSample[],
    project: (index: number) => ScreenPoint,
    minPx = 64,
    transitionMinPx = 24,
): Set<number> {
    const keep = new Set<number>();
    if (samples.length === 0) return keep;

    const last = samples.length - 1;
    const dist = (a: ScreenPoint, b: ScreenPoint) => Math.hypot(a.x - b.x, a.y - b.y);

    let lastKept: ScreenPoint | undefined;
    let lastKeptIndex = -1;
    let prevCondition: RainCondition | undefined;

    for (const [i, sample] of samples.entries()) {
        const cond = rainCondition(sample.rainMm);
        const isTransition = prevCondition !== undefined && cond !== prevCondition;
        prevCondition = cond;

        const required = i === 0 || i === last ? 0 : isTransition ? transitionMinPx : minPx;
        const point = project(i);
        if (!lastKept || dist(point, lastKept) >= required) {
            keep.add(i);
            lastKept = point;
            lastKeptIndex = i;
        }
    }

    // The endpoint is kept unconditionally; drop the chip before it if they collide.
    if (keep.size > 2 && lastKeptIndex === last) {
        const kept = [...keep];
        const previous = kept[kept.length - 2];
        if (previous !== undefined && previous !== 0 && dist(project(previous), project(last)) < transitionMinPx) {
            keep.delete(previous);
        }
    }

    return keep;
}
