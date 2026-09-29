import { test, expect } from "@playwright/test";

for (const scenario of [
    { locale: "de-CH", width: 1280, colorScheme: "light" },
    { locale: "de-CH", width: 375, colorScheme: "dark" },
    { locale: "en-GB", width: 375, colorScheme: "light" },
    { locale: "en-GB", width: 1280, colorScheme: "dark" },
] as const) {
    test.describe(`${scenario.locale} ${scenario.width}px ${scenario.colorScheme}`, () => {
        test.use({
            locale: scenario.locale,
            colorScheme: scenario.colorScheme,
            viewport: { width: scenario.width, height: 900 },
        });
        test("weekday tiles support keyboard selection and update the real schedule", async ({ page }, info) => {
            const german = scenario.locale.startsWith("de");
            await page.route(
                url => url.pathname.startsWith("/api/"),
                route => {
                    const path = new URL(route.request().url()).pathname;
                    const json =
                        path === "/api/auth/session"
                            ? {
                                  authenticated: true,
                                  user: {
                                      id: "weekday-demo",
                                      username: "Rider",
                                      email: "rider@example.test",
                                      signup_complete: true,
                                  },
                              }
                            : path.includes("entitlements")
                              ? { plan: "free", max_routes: 2, route_count: 0 }
                              : [];
                    return route.fulfill({ json });
                },
            );
            await page.goto("/routes");
            await page.getByRole("button", { name: german ? "Route erstellen" : "Create route", exact: true }).click();
            const dialog = page.getByRole("dialog");
            const roundTrip = dialog.getByRole("button", { name: german ? "Hin- & Rückfahrt" : "Round trip", exact: true });
            const oneWay = dialog.getByRole("button", { name: german ? "Einfache Fahrt" : "One-way", exact: true });
            const returnTime = dialog.getByLabel(german ? "Abfahrtszeit der Rückfahrt" : "Departure time of the return", { exact: true });
            await expect(roundTrip).toHaveAttribute("aria-pressed", "true");
            await expect(returnTime).toBeVisible();
            if (scenario.width < 600) {
                await expect(roundTrip.locator(".q-icon")).toBeVisible();
                await expect(oneWay.locator(".q-icon")).toBeVisible();
            }
            await oneWay.click();
            await expect(returnTime).toHaveCount(0);
            await roundTrip.focus();
            await roundTrip.press("Space");
            await expect(returnTime).toBeVisible();
            const picker = dialog.getByRole("group", { name: german ? "Tage" : "Days", exact: true });
            const days = picker.getByRole("button");
            await expect(days).toHaveCount(7);
            await expect(picker.locator('[aria-pressed="true"]')).toHaveCount(5);
            const saturday = picker.getByRole("button", { name: german ? "Samstag" : "Saturday", exact: true });
            await saturday.focus();
            await saturday.press("Space");
            await expect(saturday).toHaveAttribute("aria-pressed", "true");
            await expect(
                dialog.getByText(german ? "Mo, Di, Mi, Do, Fr, Sa um 08:00" : "Mon, Tue, Wed, Thu, Fri, Sat at 08:00", {
                    exact: true,
                }),
            ).toBeVisible();
            await saturday.press("Enter");
            await expect(saturday).toHaveAttribute("aria-pressed", "false");
            for (let index = 0; index < 5; index++) await days.nth(index).click();
            await expect(picker.locator('[aria-pressed="true"]')).toHaveCount(0);
            await saturday.click();
            await expect(dialog.getByText(german ? "Sa um 08:00" : "Sat at 08:00", { exact: true })).toBeVisible();
            const fits = await days.evaluateAll(buttons =>
                buttons.every(button => {
                    const box = button.getBoundingClientRect();
                    return (
                        box.left >= 0 &&
                        box.right <= window.innerWidth &&
                        box.height >= 44 &&
                        button.scrollWidth <= button.clientWidth
                    );
                }),
            );
            expect(fits).toBe(true);
            await picker.screenshot({ path: info.outputPath("weekday-selector.png") });
        });
    });
}
