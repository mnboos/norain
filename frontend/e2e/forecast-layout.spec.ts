import { test, expect } from "@playwright/test";
import fixture from "./fixtures/uncertainty.json" with { type: "json" };
import { mockForecast } from "./forecastMock";

const saved = {
    id: fixture.route_id,
    name: "Zihlschlacht-Sitterdorf → Frauenfeld",
    description: "",
    start_name: "Zihlschlacht-Sitterdorf",
    dest_name: "Frauenfeld",
    start_lat: 47.5,
    start_lon: 9.5,
    dest_lat: 47.55,
    dest_lon: 9.55,
    profile: "fast_ebike",
    schedule_description: "Mo, Mi um 08:00",
    schedule_cron: "0 8 * * 1,3",
    active: true,
    has_geometry: true,
    forecast_available: true,
    next_departure: "2026-09-10T12:00:00+02:00",
    created_at: "2026-09-10T00:00:00Z",
};
const forecast = {
    ...fixture,
    sections: [
        {
            condition: "dry",
            start_km: 0,
            end_km: 5,
            start_time: "12:00",
            end_time: "12:10",
            temp_min: 14,
            temp_max: 17,
            max_rain_mm: 0,
            max_headwind: 10,
        },
    ],
    summary: {
        ...fixture.summary,
        max_wind_power_w: 26,
        wind_distribution: {
            headwind_m: 1000,
            crosswind_m: 3000,
            tailwind_m: 1000,
            calm_m: 0,
            unknown_m: 0,
            felt_covered_m: 5000,
            mean_felt_speed: 28,
            timing_source: "routing",
        },
    },
};

for (const width of [1400, 768, 390]) {
    for (const colorScheme of ["light", "dark"] as const) {
        test(`forecast layout ${width}px ${colorScheme}`, async ({ page }, info) => {
            await page.setViewportSize({ width, height: 950 });
            await page.emulateMedia({ colorScheme });
            await mockForecast(page, saved, forecast);
            const errors: string[] = [];
            page.on("pageerror", error => errors.push(error.message));
            await page.goto(`/routes/${saved.id}`);
            await expect(page.locator(".js-plotly-plot")).toHaveCount(3);
            await expect(page.locator(".js-plotly-plot").last().locator(".main-svg").first()).toBeVisible();
            await expect(page.getByTestId("selected-map-sample")).toBeAttached();
            const charts = await page.locator(".js-plotly-plot").evaluateAll(elements =>
                elements.map(el => {
                    const box = el.getBoundingClientRect();
                    return { x: box.x, y: box.y, width: box.width, height: box.height };
                }),
            );
            expect(charts.every(chart => chart.height >= 250)).toBe(true);
            if (width >= 1024) expect(Math.abs(charts[0].y - charts[2].y)).toBeLessThan(2);
            else expect(charts[2].y).toBeGreaterThan(charts[0].y + charts[0].height);
            expect(
                await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth),
            ).toBe(true);
            await expect(page.getByText("NoRain", { exact: true })).toBeVisible();
            expect(await page.locator('.q-toolbar__title').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
            await expect(page.getByRole("tab", { name: "Karte", exact: true })).toBeVisible();
            await page.getByRole("button", { name: "Kennzahlen erklärt" }).click();
            await expect(page.getByRole("dialog")).toContainText("höchsten Wert an einem Streckenpunkt");
            await page.getByRole("button", { name: "Schliessen" }).click();
            await expect(page.getByRole("dialog")).not.toBeVisible();
            await page.evaluate(() => { window.scrollTo(0, 0); });
            await page.screenshot({ path: info.outputPath("forecast.png"), fullPage: true });
            expect(errors).toEqual([]);
            // Map-link navigation is intentionally unavailable for now.
        });
    }
}
