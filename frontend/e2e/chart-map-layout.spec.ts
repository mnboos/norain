import { test, expect, type Locator, type Page } from "@playwright/test";
import fixture from "./fixtures/uncertainty.json" with { type: "json" };
import { mockForecast } from "./forecastMock";

const saved = {
    id: fixture.route_id,
    name: "Chart layout regression",
    start_name: "Start",
    dest_name: "Destination",
    profile: "bike",
    has_geometry: true,
    forecast_available: true,
    next_departure: "2026-09-16T12:00:00+02:00",
};
const wideLine = [[9.2, 47.5], [9.8, 47.51]];
const tallLine = [[9.2, 47.2], [9.21, 47.8]];

async function expectSeparate(page: Page, tiles: Locator, beside = false) {
    await expect(tiles).toHaveCount(3);
    await expect.poll(async () => {
        const map = await page.locator("#map").boundingBox();
        const boxes = await tiles.evaluateAll(elements => elements.map(el => {
            const { x, y, width, height } = el.getBoundingClientRect();
            return { x, y, width, height };
        }));
        return !!map && map.width > 0 && map.height >= 299 && boxes.every(box =>
            box.width > 0 && box.height > 0 &&
            (beside ? box.x >= map.x + map.width - 1 : box.y + box.height <= map.y + 1),
        );
    }).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    for (const tile of await tiles.all()) {
        await tile.scrollIntoViewIfNeeded();
        await expect.poll(() => tile.evaluate(el => {
            const box = el.getBoundingClientRect();
            const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
            return hit !== null && el.contains(hit);
        })).toBe(true);
    }
}

for (const viewport of [
    { width: 1400, height: 950 },
    { width: 768, height: 950 },
    { width: 390, height: 950 },
    { width: 390, height: 500 },
]) {
    test(`charts reserve space above map at ${viewport.width}x${viewport.height}`, async ({ page }) => {
        await page.setViewportSize(viewport);
        await mockForecast(page, saved, { ...fixture, line: wideLine });
        let releaseFigures!: () => void;
        const figuresReady = new Promise<void>(resolve => { releaseFigures = resolve; });
        await page.route("**/figures", async route => {
            await figuresReady;
            await route.fulfill({ json: fixture.figures });
        });
        await page.goto(`/routes/${saved.id}`);
        try {
            await expectSeparate(page, page.getByLabel("Diagramm wird geladen"));
        } finally {
            releaseFigures();
        }
        await expect(page.locator(".js-plotly-plot .main-svg").first()).toBeVisible();
        await expectSeparate(page, page.locator(".js-plotly-plot"));
    });
}

test("tall route charts stay beside the map and move above it on mobile", async ({ page }) => {
    await page.setViewportSize({ width: 1400, height: 950 });
    await mockForecast(page, saved, { ...fixture, line: tallLine });
    await page.goto(`/routes/${saved.id}`);
    await expect(page.locator(".js-plotly-plot .main-svg").first()).toBeVisible();
    await expectSeparate(page, page.locator(".js-plotly-plot"), true);
    await page.setViewportSize({ width: 390, height: 700 });
    await expectSeparate(page, page.locator(".js-plotly-plot"));
    await page.setViewportSize({ width: 1400, height: 950 });
    await expectSeparate(page, page.locator(".js-plotly-plot"), true);
});
