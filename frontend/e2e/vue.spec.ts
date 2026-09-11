import { test, expect } from "@playwright/test";

test("shows the empty route dashboard", async ({ page }) => {
    await page.route(
        url => url.pathname === "/api/routes",
        route => route.fulfill({ json: [] }),
    );
    await page.goto("/");
    await expect(page.getByText("NoRain", { exact: true })).toBeVisible();
    await expect(page.getByText("Noch keine Routen — leg los!")).toBeVisible();
});
