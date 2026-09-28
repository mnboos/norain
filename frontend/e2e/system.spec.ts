import { test, expect, type Page } from "@playwright/test";

async function mockSystem(page: Page, { allowed = true, staff = true, empty = false } = {}) {
    const now = new Date().toISOString();
    let fail = false;
    const routeLine = {
        id: "route-one",
        kind: "route",
        name: "Arbeitsweg",
        profile: "bike",
        active: true,
        coordinates: [
            [8, 47],
            [8.04, 47.04],
        ],
    };
    const cell = {
        id: "forecast:1",
        kind: "forecast",
        source: "open-meteo",
        lat: 47.02,
        lon: 8.02,
        fetched_at: now,
        day_key: now.slice(0, 10),
        forecast_days: 3,
        coordinates: [
            [8.015, 47.015],
            [8.025, 47.015],
            [8.025, 47.025],
            [8.015, 47.025],
            [8.015, 47.015],
        ],
    };
    await page.route("**/api/auth/session", route =>
        route.fulfill({
            json: {
                authenticated: true,
                user: { id: "1", email: "admin@example.test", username: "Admin", signup_complete: true },
                ...(staff ? { system: { allowed, login_url: "/admin/login/?next=/system" } } : {}),
            },
        }),
    );
    await page.route(/\/(positron|dark-matter)\.json(?:\?.*)?$/, route => {
        if (route.request().resourceType() === "script") return route.continue();
        return route.fulfill({
            json: {
                version: 8,
                sources: {},
                layers: [
                    {
                        id: "background",
                        type: "background",
                        paint: {
                            "background-color": route.request().url().includes("dark-matter") ? "#202936" : "#edf1f4",
                        },
                    },
                ],
            },
        });
    });
    await page.route("**/api/system/**", route => {
        expect(route.request().method()).toBe("GET");
        const url = new URL(route.request().url());
        if (fail) return route.fulfill({ status: 503, json: { detail: "Temporarily unavailable" } });
        if (url.pathname.endsWith("/summary"))
            return route.fulfill({
                json: {
                    generated_at: now,
                    max_cell_age_seconds: 7200,
                    recurring_routes: empty ? 0 : 1,
                    journeys: 0,
                    stages: 0,
                    missing_geometry: 0,
                    cache_locations: empty ? 0 : 1,
                    caches: empty
                        ? []
                        : [{ kind: "forecast", source: "open-meteo", records: 2, fresh: 1, stale: 1, locations: 1 }],
                    bounds: [8, 47, 8.04, 47.04],
                },
            });
        if (url.pathname.endsWith("/map")) {
            const items = empty
                ? []
                : url.searchParams.get("layer") === "routes"
                  ? [routeLine]
                  : url.searchParams.get("layer") === "cells"
                    ? [cell]
                    : [];
            return route.fulfill({ json: { items, total: items.length, next_offset: null, generated_at: now } });
        }
        if (url.pathname.includes("/coverage/"))
            return route.fulfill({
                json: {
                    id: "route-one",
                    kind: "route",
                    name: "Arbeitsweg",
                    profile: "bike",
                    distance_m: 6000,
                    duration_seconds: 1800,
                    geometry_fetched_at: now,
                    departure: now,
                    unavailable: null,
                    points: [{ lat: 47.02, lon: 8.02, forecast: "usable", ensemble: "missing" }],
                },
            });
        if (url.pathname.endsWith("/cells"))
            return route.fulfill({ json: { items: [cell], total: 1, next_offset: null, generated_at: now } });
        return route.fulfill({
            json: {
                items: empty
                    ? []
                    : [
                          {
                              id: "job",
                              kind: "route",
                              status: "fetching",
                              cells_total: 5,
                              cells_settled: 2,
                              cells_failed: 1,
                              created_at: now,
                              updated_at: now,
                              error: "",
                              possibly_stalled: true,
                          },
                      ],
                total: empty ? 0 : 1,
                next_offset: null,
                stall_timeout_seconds: 300,
            },
        });
    });
    return {
        fail: () => {
            fail = true;
        },
    };
}

for (const width of [1440, 390]) {
    test(`system map and diagnostics at ${width}px`, async ({ page }, info) => {
        await page.setViewportSize({ width, height: 1000 });
        const errors: string[] = [];
        page.on("pageerror", error => errors.push(error.message));
        const mock = await mockSystem(page);
        await page.goto("/system");
        await expect(page.getByRole("heading", { name: "Systemübersicht" })).toBeVisible();
        await expect(page.getByText("2 / 2 Objekte geladen", { exact: false })).toBeVisible();
        await expect(page.getByText("Möglicherweise stehen geblieben")).toBeVisible();
        const canvas = page.locator(".system-map canvas");
        await expect(canvas).toBeVisible();
        // The saved line crosses the map centre; click the rendered geometry.
        await canvas.click();
        await expect(page.getByRole("heading", { name: "Arbeitsweg" })).toBeVisible();
        await expect(page.getByRole("cell", { name: "Nutzbar", exact: true })).toBeVisible();
        await page.evaluate(() => { window.scrollTo(0, 0); });
        await page.screenshot({
            path: info.outputPath(`system-light-${width}.png`),
            fullPage: true,
            animations: "disabled",
        });
        await page.getByRole("button", { name: "Dunkles Design" }).click();
        await expect(page.locator("body")).toHaveClass(/body--dark/);
        await expect(page.locator("body")).toHaveCSS("background-color", "rgb(18, 24, 33)");
        await page.mouse.move(0, 0);
        await page.evaluate(() => { window.scrollTo(0, 0); });
        await page.screenshot({
            path: info.outputPath(`system-dark-${width}.png`),
            fullPage: true,
            animations: "disabled",
        });
        await page.getByRole("checkbox", { name: "Wiederkehrende Routen", exact: true }).click();
        await expect(page.getByText("1 / 1 Objekte geladen", { exact: false })).toBeVisible();
        await canvas.click();
        await expect(page.getByRole("heading", { name: "Wetterzelle" })).toBeVisible();
        await expect(page.getByText("Angeforderter Horizont: 3 Tage.", { exact: false })).toBeVisible();
        mock.fail();
        await page.getByRole("button", { name: "Aktualisieren", exact: true }).click();
        await expect(page.getByRole("alert")).toContainText("Bereits geladene Daten bleiben sichtbar");
        await expect(canvas).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
        expect(errors).toEqual([]);
    });
}

test("staff without OTP sees verification, and makes no system requests", async ({ page }) => {
    await mockSystem(page, { allowed: false });
    const requests: string[] = [];
    page.on("request", request => {
        if (request.url().includes("/api/system/")) requests.push(request.url());
    });
    await page.goto("/system");
    await expect(page.getByRole("link", { name: "Administrator-Anmeldung" })).toBeVisible();
    expect(requests).toEqual([]);
});

test("empty system has explicit empty states", async ({ page }) => {
    await mockSystem(page, { empty: true });
    await page.goto("/system");
    await expect(page.getByText("Noch keine Wetterzellen gespeichert.")).toBeVisible();
    await expect(page.getByText("Keine Aufträge in diesem Zeitraum.")).toBeVisible();
    await expect(page.getByText("Keine passenden Daten.")).toBeVisible();
});

test("map follows all pages without losing earlier features", async ({ page }) => {
    await mockSystem(page);
    await page.route("**/api/system/map?**", route => {
        const url = new URL(route.request().url());
        if (url.searchParams.get("layer") !== "routes") return route.fallback();
        const second = url.searchParams.get("offset") === "1";
        return route.fulfill({
            json: {
                items: [
                    {
                        id: second ? "second" : "first",
                        kind: "route",
                        name: "Route",
                        coordinates: [
                            [8, 47],
                            [8.04, 47.04],
                        ],
                    },
                ],
                total: 2,
                next_offset: second ? null : 1,
                generated_at: new Date().toISOString(),
            },
        });
    });
    await page.goto("/system");
    await expect(page.getByText("3 / 3 Objekte geladen", { exact: false })).toBeVisible();
});
