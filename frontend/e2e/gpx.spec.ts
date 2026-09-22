import { test, expect } from "@playwright/test";
import { mockForecast } from "./forecastMock";

const points = [[9, 47, 500], [9.01, 47.005, 520], [9.02, 47.01, 510]];
const gpx = '<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1" creator="test"><trk><name>Testfahrt</name><trkseg><trkpt lon="9" lat="47"><ele>500</ele></trkpt><trkpt lon="9.01" lat="47.005"><ele>520</ele></trkpt><trkpt lon="9.02" lat="47.01"><ele>510</ele></trkpt></trkseg></trk></gpx>';

for (const width of [1280, 390]) {
    test('GPX import, cancel and download at width ' + String(width), async ({ page }, info) => {
        await page.setViewportSize({ width, height: 900 });
        const errors: string[] = [];
        page.on("pageerror", e => errors.push(e.message));
        await mockForecast(page, { id: "gpx-test" });
        await page.route("**/api/auth/session", route => route.fulfill({ json: { authenticated: false, user: null } }));
        let uploaded = false;
        await page.route("**/api/gpx/import", route => {
            uploaded = route.request().headers()["content-type"].startsWith("multipart/form-data;");
            return route.fulfill({ json: [{ name: "Testfahrt", coordinates: points, distance_m: 1900, routing_points: points.map(p => p.slice(0, 2)) }] });
        });
        await page.route("**/api/gpx/preview", route => route.fulfill({ json: { coordinates: points, distance_m: 1900, time_s: 342 } }));
        await page.route("**/api/gpx/export", route => {
            expect(route.request().postDataJSON()).toMatchObject({ coordinates: points });
            return route.fulfill({ contentType: "application/gpx+xml", body: gpx });
        });
        await page.goto("/map");
        await page.getByRole("button", { name: "GPX importieren", exact: true }).click();
        await page.locator('input[type="file"]').setInputFiles({ name: "ride.gpx", mimeType: "application/gpx+xml", buffer: Buffer.from(gpx) });
        await expect(page.getByLabel("Name", { exact: true })).toHaveValue("Testfahrt");
        await expect(page.getByLabel("Ø Geschwindigkeit")).toHaveValue("20");
        await page.getByLabel("Fahrzeit", { exact: true }).fill("20");
        await page.screenshot({ path: info.outputPath("gpx-import-" + String(width) + ".png"), fullPage: true });
        await page.getByRole("button", { name: "Übernehmen", exact: true }).click();
        expect(uploaded).toBe(true);
        await expect(page.getByText("Testfahrt", { exact: true })).toBeVisible();
        await expect(page.getByRole("button", { name: "GPX exportieren", exact: true })).toBeEnabled();
        const download = page.waitForEvent("download");
        await page.getByRole("button", { name: "GPX exportieren", exact: true }).click();
        expect((await download).suggestedFilename()).toBe("Testfahrt.gpx");
        await page.getByRole("button", { name: "GPX importieren", exact: true }).click();
        await page.getByRole("button", { name: "Abbrechen", exact: true }).click();
        await expect(page.getByText("Testfahrt", { exact: true })).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
        expect(errors).toEqual([]);
    });
}
