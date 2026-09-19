import { test, expect } from "@playwright/test";
import fixture from "./fixtures/uncertainty.json" with { type: "json" };
import { mockForecast } from "./forecastMock";

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
    await mockForecast(page, saved);
});

test("expands uncertainty, selects chart points, and highlights the map", async ({ page }, testInfo) => {
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`/routes/${saved.id}`);
    await expect(page.getByText("Regenrisiko", { exact: true })).toBeVisible();
    const windChart = page.locator(".js-plotly-plot").nth(0);
    await expect(windChart.locator(".gtitle")).toHaveText("Gegenwind");
    await expect(windChart.locator(".legend")).toContainText("Gegenwind (+) / Rückenwind (−)");
    await page.getByText("Vorhersage-Details", { exact: true }).click();
    const details = page.getByTestId("forecast-details");
    await expect(
        details.getByText(
            "Die Fläche zeigt, wie stark das Wetter schwanken könnte. Auch Werte ausserhalb sind möglich.",
        ),
    ).toBeVisible();
    const slider = page.getByRole("slider", { name: "Streckenpunkt" });
    await slider.focus();
    await slider.press("ArrowRight");
    await expect(details.getByText("Für diesen Punkt ist kein Wetterbereich verfügbar.")).toBeVisible();
    await expect(page.getByTestId("selected-map-sample")).toHaveAttribute("aria-label", /12:05/);
    await slider.press("ArrowRight");
    await expect(page.getByTestId("selected-map-sample")).toHaveAttribute("aria-label", /12:10/);
    // Hover the actual line endpoint; permanent point markers are no longer shown.
    const plot = page.locator(".js-plotly-plot").first();
    // Temperature lines keep one solid series colour; no chart-relative gradient paints them.
    await expect(plot.locator("linearGradient stop")).toHaveCount(0);
    await expect(plot.locator(".scatterlayer .js-line").last()).not.toHaveCSS("stroke", /url\(/);
    // The two isolated values on either side of the missing sample remain visible.
    await expect(plot.locator(".scatterlayer .point")).toHaveCount(2);
    const line = plot.locator(".scatterlayer .js-line").last();
    await plot.scrollIntoViewIfNeeded();
    const position = await line.evaluate(element => {
        if (!(element instanceof SVGPathElement)) throw new Error("Expected a chart line");
        const path = element;
        const point = path.getPointAtLength(0);
        const matrix = path.getScreenCTM();
        if (!matrix) throw new Error("Chart line is not on screen");
        const screen = new DOMPoint(point.x, point.y).matrixTransform(matrix);
        return { x: screen.x + 1, y: screen.y };
    });
    await page.mouse.move(position.x, position.y);
    await expect(page.getByRole("tooltip")).toBeVisible();
    await expect(page.getByRole("tooltip")).toContainText("°C");
    await expect(page.getByRole("tooltip")).not.toContainText(/Median|Einzelprognose/);
    await expect(slider).toHaveValue("0");
    await page.mouse.click(position.x, position.y);
    await expect(page.getByTestId("selected-map-sample")).toHaveAttribute("aria-label", /12:00/);
    expect(errors).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath("desktop-details.png"), fullPage: true });
    await page.mouse.move(0, 0);
    await expect(page.getByRole("tooltip")).toHaveCount(0);
});

test("mobile map shares details and supports touch selection without overflow", async ({ browser }, testInfo) => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    const page = await context.newPage();
    await mockForecast(page, saved);
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
