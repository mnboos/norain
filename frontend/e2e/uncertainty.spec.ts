import { test, expect } from "@playwright/test";
import fixture from "./fixtures/uncertainty.json" with { type: "json" };

const saved = {
    id: fixture.route_id,
    name: "Testfahrt",
    description: "",
    start_name: "Start",
    dest_name: "Ziel",
    start_lat: 47.5,
    start_lon: 9.5,
    dest_lat: 47.55,
    dest_lon: 9.55,
    profile: "bike",
    schedule_cron: "0 12 * * *",
    schedule_description: "Täglich",
    active: true,
    has_geometry: true,
    forecast_available: true,
    next_departure: "2026-09-10T12:00:00+02:00",
    created_at: "2026-09-10T00:00:00Z",
};

test.beforeEach(async ({ page }) => {
    await page.route(
        url => url.pathname.startsWith("/api/"),
        async route => {
            const path = new URL(route.request().url()).pathname;
            await route.fulfill({
                json: path.includes("/forecast") || path.includes("/route_weather") ? fixture : saved,
            });
        },
    );
    await page.route("https://basemaps.cartocdn.com/**", route =>
        route.fulfill({
            json: {
                version: 8,
                sources: {},
                layers: [{ id: "background", type: "background", paint: { "background-color": "#f4f6f7" } }],
            },
        }),
    );
});

test("expands uncertainty, selects chart points, and highlights the map", async ({ page }, testInfo) => {
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`/routes/${saved.id}`);
    await expect(page.getByText("Max. Regenrisiko")).toBeVisible();
    await page.getByText("Vorhersage-Details", { exact: true }).click();
    const details = page.getByTestId("forecast-details");
    await expect(details.getByText("Modellvergleich am ausgewählten Punkt · Bereiche und Median")).toBeVisible();
    const slider = page.getByRole("slider", { name: "Streckenpunkt" });
    await slider.focus();
    await slider.press("ArrowRight");
    await expect(details.getByText("Keine Ensemble-Bereiche für diesen Punkt verfügbar.")).toBeVisible();
    await expect(page.getByTestId("selected-map-sample")).toHaveAttribute("aria-label", /12:05/);
    await slider.press("ArrowRight");
    await expect(page.getByTestId("selected-map-sample")).toHaveAttribute("aria-label", /12:10/);
    // Hover a rendered median marker, exercising Plotly's native event rather than injecting one.
    const point = page.locator(".js-plotly-plot").first().locator(".scatterlayer .point").first();
    await point.scrollIntoViewIfNeeded();
    await point.hover({ force: true });
    await expect(slider).toHaveValue("0");
    await point.click({ force: true });
    await expect(page.getByTestId("selected-map-sample")).toHaveAttribute("aria-label", /12:00/);
    expect(errors).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath("desktop-details.png"), fullPage: true });
});

test("mobile map shares details and supports touch selection without overflow", async ({ browser }, testInfo) => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    const page = await context.newPage();
    await page.route(
        url => url.pathname.startsWith("/api/"),
        route =>
            route.fulfill({
                json: new URL(route.request().url()).pathname.includes("route_weather") ? fixture : saved,
            }),
    );
    await page.route("https://basemaps.cartocdn.com/**", route =>
        route.fulfill({ json: { version: 8, sources: {}, layers: [] } }),
    );
    await page.goto(`/map?route=${saved.id}`);
    await page.getByText("Vorhersage-Details", { exact: true }).tap();
    const slider = page.getByRole("slider", { name: "Streckenpunkt" });
    await slider.scrollIntoViewIfNeeded();
    const box = await slider.boundingBox();
    if (!box) throw new Error("Missing slider");
    await slider.tap({ position: { x: box.width - 8, y: box.height / 2 } });
    await expect(slider).toHaveValue("2");
    await expect(page.getByTestId("selected-map-sample")).toHaveAttribute("aria-label", /12:10/);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath("mobile-details.png") });
    const bounds = await page.getByTestId("forecast-details").boundingBox();
    if (!bounds) throw new Error("Missing details panel");
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(390);
    await context.close();
});
