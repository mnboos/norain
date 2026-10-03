import { test, expect } from "@playwright/test";
import fixture from "./fixtures/uncertainty.json" with { type: "json" };
import { mockForecast } from "./forecastMock";
import { CHART_STYLE } from "../src/utils/chartStyle";

const saved = {
    id: fixture.route_id,
    name: "Forecast indicators",
    start_name: "Start",
    dest_name: "Destination",
    profile: "bike",
    has_geometry: true,
    forecast_available: true,
    next_departure: "2026-09-16T12:00:00+02:00",
};

// Run against vite preview as well as dev: Vue handles invalid numeric v-for ranges
// differently in production, where the former placeholder loop crashed the card.
for (const width of [1400, 390]) {
    for (const colorScheme of ["light", "dark"] as const) {
        test(`indicators and matching charts at ${width}px in ${colorScheme}`, async ({ page }, info) => {
            await page.setViewportSize({ width, height: 950 });
            await page.emulateMedia({ colorScheme });
            await mockForecast(page, saved);
            await page.route(/\/api\/routes\/[^/]+\/elevation$/, route =>
                route.fulfill({
                    json: {
                        points: [
                            { distance_m: 0, elapsed_s: 0, elevation_m: 400 },
                            { distance_m: 1000, elapsed_s: 120, elevation_m: 460 },
                            { distance_m: 2000, elapsed_s: 240, elevation_m: 430 },
                        ],
                        source: "Terrain",
                        approximate_timing: false,
                    },
                }),
            );
            const errors: string[] = [];
            page.on("pageerror", error => errors.push(error.message));
            await page.goto(`/routes/${saved.id}`);

            const indicators = page.getByLabel("Kennzahlen der Fahrt", { exact: true });
            await expect(indicators).toBeVisible();
            await expect(indicators.locator(".text-weight-bold")).toHaveCount(8);
            await expect(indicators).toContainText("Gefühlt Ø");
            await expect(indicators).toContainText("Regen max.");
            await expect(indicators).toContainText("Dauer");

            const charts = page.locator(".js-plotly-plot");
            await expect(charts).toHaveCount(3);
            const titles = charts.locator(".gtitle");
            await expect(titles).toHaveCount(3);
            const styles = await titles.evaluateAll(elements =>
                elements.map(el => ({
                    font: getComputedStyle(el).font,
                    color: getComputedStyle(el).fill,
                })),
            );
            expect(styles[1]).toEqual(styles[0]);
            expect(styles[2]).toEqual(styles[0]);
            if (width === 1400) {
                const boxes = await charts.evaluateAll(elements =>
                    elements.map(el => {
                        const { y, height } = el.getBoundingClientRect();
                        return { y, height };
                    }),
                );
                for (const box of boxes) {
                    expect(Math.abs(box.y - boxes[0].y)).toBeLessThan(2);
                    expect(Math.abs(box.height - boxes[0].height)).toBeLessThan(2);
                }
            }
            for (const chart of await charts.all()) {
                await chart.scrollIntoViewIfNeeded();
                await expect(chart.locator(".main-svg").first()).toBeVisible();
                const box = await chart.boundingBox();
                expect(box?.height).toBeGreaterThanOrEqual(CHART_STYLE.height.compact);
                // Measure the rendered lines, not just the card: trace-dependent autoranging
                // used to leave the elevation endpoints flush against the card's edges.
                const endpoints = await chart.evaluate(el => {
                    const layout: unknown = Reflect.get(el, "_fullLayout");
                    if (!layout || typeof layout !== "object") throw new Error("Chart layout is missing");
                    const axis: unknown = Reflect.get(layout, "xaxis");
                    if (!axis || typeof axis !== "object") throw new Error("Chart x-axis is missing");
                    const width: unknown = Reflect.get(axis, "_length");
                    if (typeof width !== "number" || width <= 0) throw new Error("Chart plot has no width");
                    return Array.from(el.querySelectorAll<SVGPathElement>(".scatterlayer .js-line"))
                        .filter(path => parseFloat(getComputedStyle(path).strokeWidth) > 0)
                        .map(path => ({
                            start: path.getPointAtLength(0).x / width,
                            end: path.getPointAtLength(path.getTotalLength()).x / width,
                        }));
                });
                expect(endpoints.length).toBeGreaterThan(0);
                for (const endpoint of endpoints) {
                    expect(endpoint.start).toBeCloseTo(CHART_STYLE.axis.horizontalInsetFraction, 2);
                    expect(endpoint.end).toBeCloseTo(1 - CHART_STYLE.axis.horizontalInsetFraction, 2);
                }
            }
            expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
            expect(errors).toEqual([]);
            await page.screenshot({ path: info.outputPath("forecast-indicators.png"), fullPage: true });
        });
    }
}
