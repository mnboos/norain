import { test, expect, type Page } from "@playwright/test";

async function mockSession(page: Page, authenticated = false) {
    await page.route("**/api/auth/session", route =>
        route.fulfill({
            json: {
                authenticated,
                user: authenticated
                    ? { id: "landing-test", email: "rider@example.test", username: "Rider", signup_complete: true }
                    : null,
            },
        }),
    );
}

for (const width of [375, 1280]) {
    for (const locale of ["de-CH", "en-GB"]) {
        for (const colorScheme of ["light", "dark"] as const) {
            test.describe(`${width}px ${locale} ${colorScheme}`, () => {
                test.use({ viewport: { width, height: 900 }, locale, colorScheme });

                test("landing layout and controls", async ({ page }, testInfo) => {
                    const errors: string[] = [];
                    page.on("pageerror", error => errors.push(error.message));
                    await mockSession(page);
                    await page.goto("/");
                    const german = locale.startsWith("de");
                    await expect(page.locator("h1")).toHaveText(
                        german ? "Das Wetter entlang deiner Route." : "The weather along your route.",
                    );
                    await page.evaluate(() => document.fonts.ready);
                    await expect(page.locator(".q-header")).toHaveCount(0);
                    await expect(page.locator(".q-page-container")).toHaveCSS("padding-top", "0px");
                    await expect(page.locator(".welcome")).toHaveCSS("background-color", "rgb(230, 235, 241)");
                    for (const surface of [page.locator(".hero"), page.locator(".plan.bg-brand-gradient")]) {
                        await expect(surface).toHaveCSS(
                            "background-image",
                            "linear-gradient(90deg, rgb(27, 54, 93), rgb(45, 90, 142))",
                        );
                    }
                    await expect(page.locator(".hero .links")).toBeVisible({ visible: width > 1023 });
                    // Quasar's global wrap utility must not stretch these column-flex sections.
                    await expect(page.locator("#features")).toHaveCSS("flex-wrap", "nowrap");
                    await expect(page.locator("#pricing")).toHaveCSS("flex-wrap", "nowrap");
                    expect(
                        await page.locator(".welcome").evaluate(root =>
                            [...root.querySelectorAll("a, button, h1, h2, h3, p, .shot, img")]
                                .filter(element => element.getClientRects().length)
                                .filter(element => {
                                    const rect = element.getBoundingClientRect();
                                    return rect.left < -1 || rect.right > window.innerWidth + 1;
                                })
                                .map(element => element.textContent || element.tagName),
                        ),
                    ).toEqual([]);
                    // The product shots are lazy-loaded, including the wind card below the fold.
                    for (const image of await page.locator(".welcome img").all()) {
                        await image.scrollIntoViewIfNeeded();
                        await expect
                            .poll(() => image.evaluate(el => el instanceof HTMLImageElement && el.naturalWidth > 0))
                            .toBe(true);
                    }
                    await page.locator("h1").scrollIntoViewIfNeeded();
                    expect(
                        await page
                            .locator(".welcome img")
                            .evaluateAll(images =>
                                images.every(image => image instanceof HTMLImageElement && image.naturalWidth > 0),
                            ),
                    ).toBe(true);
                    const headerCta = page.locator(".bar-right > a");
                    await expect(headerCta).toHaveAttribute("href", "/account");
                    await headerCta.hover();
                    await expect(headerCta).toHaveCSS("background-color", "rgb(229, 185, 92)");
                    await page.screenshot({ path: testInfo.outputPath("welcome.png"), fullPage: true });

                    const faqButtons = page.locator(".faq-item").getByRole("button");
                    await expect(faqButtons.nth(0)).toHaveAttribute("aria-expanded", "true");
                    await faqButtons.nth(1).click();
                    await expect(faqButtons.nth(0)).toHaveAttribute("aria-expanded", "false");
                    await expect(faqButtons.nth(1)).toHaveAttribute("aria-expanded", "true");
                    await faqButtons.nth(1).press("Enter");
                    await expect(faqButtons.nth(1)).toHaveAttribute("aria-expanded", "false");
                    await page.getByRole("button", { name: german ? "EN" : "DE", exact: true }).click();
                    await expect(page.locator(".welcome")).toHaveAttribute("lang", german ? "en" : "de");
                    await expect(page.locator("h1")).toHaveText(
                        german ? "The weather along your route." : "Das Wetter entlang deiner Route.",
                    );
                    await expect(page.locator("#features img").first()).toHaveAttribute(
                        "src",
                        `/landing/route-${german ? "en" : "de"}.png`,
                    );
                    if (width > 1023) {
                        await page.locator('a[href="#pricing"]').click();
                        await expect(page).toHaveURL(/#pricing$/);
                        await expect(page.locator("#pricing h2")).toBeInViewport();
                    }
                    expect(errors).toEqual([]);
                    await headerCta.click();
                    await expect(page).toHaveURL(/\/account$/);
                    await expect(page.locator(".q-header")).toBeVisible();
                    await expect(page.locator("body")).toHaveCSS(
                        "--q-primary",
                        colorScheme === "dark" ? "#3b7dc4" : "#2d5a8e",
                    );
                    await page.goBack();
                    await expect(page.locator(".welcome")).toBeVisible();
                    await expect(page.locator(".q-header")).toHaveCount(0);
                });
            });
        }
    }
}

test("legacy landing URL remains available", async ({ page }) => {
    await mockSession(page);
    await page.goto("/welcome");
    await expect(page.locator(".welcome")).toBeVisible();
    await expect(page.locator(".brand")).toHaveAttribute("href", "/");
});

test("route dashboard requires sign-in", async ({ page }) => {
    await mockSession(page);
    await page.goto("/routes");
    await expect(page).toHaveURL(/\/account\?next=\/routes$/);
});

test("signed-in visitor can open the app", async ({ page }) => {
    await mockSession(page, true);
    await page.route("**/api/routes", route => route.fulfill({ json: [] }));
    await page.goto("/");
    await page.getByRole("button", { name: "EN", exact: true }).click();
    const appLink = page.locator(".bar-right > a");
    await expect(appLink).toHaveAttribute("href", "/routes");
    await appLink.click();
    await expect(page).toHaveURL(/\/routes$/);
    await expect(page.locator(".q-header")).toBeVisible();
});
