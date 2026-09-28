import { test, expect } from "@playwright/test";

test("shows the empty route dashboard", async ({ page }) => {
    await page.route("**/api/auth/session", route =>
        route.fulfill({
            json: {
                authenticated: true,
                user: { id: "dashboard-test", email: "rider@example.test", username: "Rider", signup_complete: true },
            },
        }),
    );
    await page.route(
        url => url.pathname === "/api/routes",
        route => route.fulfill({ json: [] }),
    );
    await page.goto("/routes");
    await expect(page.getByText("Meteolane", { exact: true })).toBeVisible();
    await expect(page.getByText("Noch keine Routen — leg los!")).toBeVisible();
});
