import type { Page } from "@playwright/test";
import fixture from "./fixtures/uncertainty.json" with { type: "json" };

/** Serve the current split forecast API from the existing recorded forecast. */
export async function mockForecast(page: Page, saved: { id: string }, input: object = fixture, pro = true) {
    const forecast = { ...fixture, ...input, job_id: saved.id, version: "2026-09-10T12:00:00Z" };
    const windArrows: unknown = Reflect.get(forecast, "wind_arrows");
    await page.route(
        url => url.pathname.startsWith("/api/"),
        async route => {
            const path = new URL(route.request().url()).pathname;
            const sampleIndex = /\/samples\/(\d+)\/uncertainty$/.exec(path)?.[1];
            const json = path.includes("/auth/session")
                ? { authenticated: true, user: { username: "test", email: "admin@example.com" } }
                : path.includes("entitlements")
                  ? { plan: pro ? "pro" : "free", max_routes: 2, ensemble_uncertainty: pro }
                  : path.endsWith("/map_detail")
                    ? { line: forecast.line, wind_arrows: windArrows ?? [] }
                    : sampleIndex != null
                      ? (forecast.samples[Number(sampleIndex)]?.uncertainty ?? null)
                      : path.includes("/forecast") || path.includes("/route_weather")
                        ? { job_id: saved.id, status: "done", result: forecast }
                        : path === "/api/routes"
                          ? [saved]
                          : saved;
            await route.fulfill({ json });
        },
    );
    // The map's style file, fetched by MapLibre. No query string: the dev server also loads
    // `positron.json?import&url` as a JS module, and that request must pass through.
    await page.route(/\/(positron|dark-matter)[^/?]*\.json$/, route =>
        route.fulfill({
            json: {
                version: 8,
                sources: {},
                layers: [{ id: "background", type: "background", paint: { "background-color": "#eef1f3" } }],
            },
        }),
    );
}
