import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import fixture from "./fixtures/uncertainty.json" with { type: "json" };
import { mockForecast } from "./forecastMock";

const feltTrace = {
    x: [0, 5, 10], y: [20, 34, 22], customdata: [[null, "12:00"], [null, "12:05"], [null, "12:10"]],
    name: "Gefühlt (geschätzt)", legendgroup: "felt", visible: "legendonly", mode: "lines", type: "scatter",
    line: { color: "#977521", width: 2 }, connectgaps: false,
};
const fallback = {
    ...fixture,
    // The wind chart gets the felt-wind trace and room for its legend.
    figures: fixture.figures.map((figure, i) => i !== 2 ? figure : {
        ...figure,
        data: [...figure.data, feltTrace],
        layout: { ...figure.layout, margin: { ...figure.layout.margin, t: 106 } },
    }),
    wind_segments: fixture.samples.map((s, i) => ({
        start_m: i * 100, end_m: (i + 1) * 100, lat: s.lat, lon: s.lon,
        elapsed_s: s.elapsed_s, bearing: 30, rider_speed: 20, wind_speed: 15, wind_dir: 60,
        headwind: 13, crosswind: 7.5, felt_speed: 34, felt_angle: 13, wind_power_w: 90, wind_coverage: 1,
        felt_coverage: 1,
    })),
    wind_arrows: fixture.samples.map(s => ({
        lat: s.lat, lon: s.lon, bearing: 30, wind_speed: 15, wind_dir: 60, wind_power_w: 90,
    })),
    summary: { ...fixture.summary, max_wind_power_w: 90, wind_distribution: {
        headwind_m: 2000, crosswind_m: 1500, tailwind_m: 1000, calm_m: 300, unknown_m: 200,
        mean_felt_speed: 28, max_felt_speed: 34, felt_covered_m: 4800, mean_wind_power_w: 60, max_wind_power_w: 90,
        timing_source: "routing",
    } },
};

/** A fixture from WIND_SMOKE_FIXTURE only needs a route line; the page reads the rest itself. */
function hasLine(value: unknown): value is { line: number[][] } {
    return typeof value === "object" && value !== null && "line" in value && Array.isArray(value.line)
        && value.line.length > 0;
}

function loadForecast(): { line: number[][] } {
    if (!process.env.WIND_SMOKE_FIXTURE) return fallback;
    const parsed: unknown = JSON.parse(readFileSync(process.env.WIND_SMOKE_FIXTURE, "utf8"));
    if (!hasLine(parsed)) throw new Error("WIND_SMOKE_FIXTURE has no route line");
    return parsed;
}

const forecast = loadForecast();
const [first] = forecast.line;
const last = forecast.line.at(-1);
const id = "00000000-0000-4000-8000-000000000001";
const saved = { id, name: "Wind-Testfahrt", start_name: "Start", dest_name: "Ziel", description: "",
    start_lat: first[1], start_lon: first[0],
    dest_lat: last?.[1], dest_lon: last?.[0], profile: "bike",
    schedule_cron: "0 12 * * *", schedule_description: "Täglich", active: true, has_geometry: true,
    forecast_available: true, next_departure: "2026-09-13T12:00:00+02:00", created_at: "2026-09-12T00:00:00Z" };

test("wind profile renders on desktop and mobile; felt chart does not select a weather sample", async ({ page }, info) => {
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await mockForecast(page, saved, forecast, false);
    await page.goto(`/routes/${id}`);
    await expect(page.getByTestId("wind-distribution")).toBeVisible();
    await expect(page.getByText("Gegenwind max.", { exact: true })).toBeVisible();
    await expect(page.getByText("Windaufwand max.", { exact: true })).toBeVisible();
    await expect(page.locator(".wx-wind-arrow").first()).toBeVisible();
    await expect(page.locator(".wx-wind-arrow").first()).toHaveAttribute("aria-label", /^Wind: 15 km\/h aus NO, von vorne rechts · Windaufwand mittel/);
    await page.getByText("Vorhersage-Details", { exact: true }).click();
    const slider = page.getByRole("slider", { name: "Streckenpunkt" });
    await slider.focus();
    await slider.press("ArrowRight");
    const selected = await slider.inputValue();
    const legend = page.locator(".legend .traces").filter({ hasText: "Gefühlt (geschätzt)" }).locator(".legendtoggle");
    await legend.scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath("wind-before-legend.png"), fullPage: true });
    await legend.click();
    // Hover/click the actual newly visible Plotly line. Null customdata must not reset selection.
    const line = page.locator(".js-plotly-plot").nth(2).locator(".scatterlayer .trace").last().locator("path.js-line");
    await expect(line).toBeVisible();
    await line.hover({ force: true });
    await line.click({ force: true });
    await expect(slider).toHaveValue(selected);
    await page.screenshot({ path: info.outputPath("wind-desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByTestId("wind-distribution").scrollIntoViewIfNeeded();
    await expect(page.getByTestId("wind-distribution")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath("wind-mobile.png"), fullPage: true });
    expect(errors).toEqual([]);
});
