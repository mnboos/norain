import { expect, test, type Page } from "@playwright/test";

// Run: npx playwright test --config playwright.fingerprint.config.ts
// Vite serves only the library and proxies /api to a loopback Django fixture, so the tests
// see real same-origin cookies, CSRF, client hints and the real verification endpoint.

/** Indicators that mean a value was forged. An honest browser must never show one. */
const LIES = [
    "user_agent_mismatch",
    "worker_mismatch",
    "iframe_mismatch",
    "native_tampered",
    "engine_mismatch",
    "client_hints_mismatch",
    "invalid_navigator",
    "invalid_worker",
    "invalid_iframe",
    "invalid_integrity",
];

interface Assessment {
    browserId: string;
    persistent: boolean;
    fingerprintId: string | null;
    tier: string;
    continuity: boolean;
    similarity: number | null;
    indicators: string[];
}

function toAssessment(value: unknown): Assessment {
    if (typeof value !== "object" || value === null) throw new Error("No assessment");
    const record = Object.fromEntries(Object.entries(value));
    const indicators: unknown = record.indicators;
    if (
        typeof record.browserId !== "string" ||
        typeof record.persistent !== "boolean" ||
        typeof record.tier !== "string" ||
        typeof record.continuity !== "boolean" ||
        !Array.isArray(indicators)
    )
        throw new Error(`Not an assessment: ${JSON.stringify(value)}`);
    return {
        browserId: record.browserId,
        persistent: record.persistent,
        fingerprintId: typeof record.fingerprintId === "string" ? record.fingerprintId : null,
        tier: record.tier,
        continuity: record.continuity,
        similarity: typeof record.similarity === "number" ? record.similarity : null,
        indicators: indicators.filter((item): item is string => typeof item === "string"),
    };
}

async function setup(page: Page) {
    await page.route("**/fingerprint-test", route =>
        route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Fingerprint test</title><body></body>" }),
    );
    await page.goto("/fingerprint-test");
    await page.evaluate(async () => {
        await fetch("/api/fingerprint/test-csrf");
    });
}

/** Run the real library in the page, then read what the server concluded (fixture endpoint). */
async function identify(page: Page): Promise<Assessment> {
    const receipt = await page.evaluate(async () => {
        // Vite transforms this TypeScript module in the real browser.
        const path = "/src/lib/browser-fingerprint/index.ts";
        // eslint-disable-next-line @typescript-eslint/consistent-type-assertions -- Vite URL loads this exact source module.
        const { createBrowserFingerprint } = (await import(
            /* @vite-ignore */ path
        )) as typeof import("../src/lib/browser-fingerprint");
        return createBrowserFingerprint({
            baseUrl: "",
            csrfToken: () => /csrftoken=([^;]+)/.exec(document.cookie)?.[1] ?? "",
        }).identify();
    });
    // The real reply says nothing about the checks.
    expect(Object.keys(receipt)).toEqual(["expiresIn"]);
    const value: unknown = await page.evaluate(async () => {
        const reply: unknown = await (await fetch("/api/fingerprint/test-assessment")).json();
        return reply;
    });
    const assessment = toAssessment(value);
    test.info().annotations.push({ type: "assessment", description: `${assessment.tier}: ${assessment.indicators.join(", ")}` });
    return assessment;
}

test("an honest browser shows no lie and every check runs", async ({ page }) => {
    await setup(page);
    let result = await identify(page);
    for (const lie of LIES) expect(result.indicators).not.toContain(lie);
    // A cold browser (first module transforms, first worker) can run into the probe timeouts;
    // that is degradation, not a lie. The warm second run must complete every check.
    if (result.indicators.some(indicator => indicator.endsWith("_timeout"))) {
        result = await identify(page);
        for (const lie of LIES) expect(result.indicators).not.toContain(lie);
    }
    for (const check of ["iframe", "integrity", "canvasIntegrity", "engine", "worker", "navigator"]) {
        expect(result.indicators).not.toContain(`${check}_unavailable`);
        expect(result.indicators).not.toContain(`${check}_timeout`);
    }
    // A test browser is driven by a program, which is the one reason it may be suspicious.
    if (result.tier === "suspicious") expect(result.indicators).toContain("automation");
});

test("the key survives a reload; separate contexts have separate keys", async ({ page, browser }) => {
    await setup(page);
    const first = await identify(page);
    expect(first.browserId).toMatch(/^[a-f0-9]{64}$/);
    expect(first.persistent).toBe(true);
    expect(first.continuity).toBe(false);
    await page.reload();
    const second = await identify(page);
    expect(second.browserId).toBe(first.browserId);
    expect(second.continuity).toBe(true);
    if (second.similarity !== null && second.similarity < 0.7) {
        expect(second.indicators).toContain("fingerprint_changed");
    }
    const context = await browser.newContext();
    try {
        const other = await context.newPage();
        await setup(other);
        expect((await identify(other)).browserId).not.toBe(first.browserId);
    } finally {
        await context.close();
    }
});

test("blocked storage degrades to an ephemeral key, not to a lie", async ({ page }) => {
    await setup(page);
    await page.evaluate(() => {
        Object.defineProperty(window, "indexedDB", {
            get() {
                throw new Error("blocked");
            },
        });
    });
    const result = await identify(page);
    expect(result.indicators).toContain("ephemeral_key");
    expect(result.persistent).toBe(false);
    for (const lie of LIES) expect(result.indicators).not.toContain(lie);
    expect(result.fingerprintId).toBeNull();
});

test("a patched native method is a lie, even with a patched toString", async ({ page }) => {
    await setup(page);
    await page.evaluate(() => {
        const getter = () => 2;
        Object.defineProperty(Navigator.prototype, "hardwareConcurrency", {
            get: getter,
            configurable: true,
            enumerable: true,
        });
        // What spoofing extensions do to hide the patch from this realm's toString.
        const nativeToString: unknown = Reflect.get(Function.prototype, "toString");
        if (typeof nativeToString !== "function") return;
        Function.prototype.toString = function (this: unknown) {
            return this === getter
                ? "function get hardwareConcurrency() { [native code] }"
                : String(Reflect.apply(nativeToString, this, []));
        };
    });
    const result = await identify(page);
    expect(result.indicators).toContain("native_tampered");
    expect(result.indicators).toContain("worker_mismatch");
    expect(result.indicators).toContain("iframe_mismatch");
    expect(result.tier).toBe("suspicious");
    expect(result.fingerprintId).toBeNull();
});

test("a navigator override is caught three ways", async ({ page }) => {
    await setup(page);
    await page.evaluate(() => Object.defineProperty(navigator, "userAgent", { value: "Spoofed browser" }));
    const result = await identify(page);
    expect(result.indicators).toContain("user_agent_mismatch");
    expect(result.indicators).toContain("worker_mismatch");
    expect(result.indicators).toContain("iframe_mismatch");
    expect(result.indicators).toContain("native_tampered");
    expect(result.tier).toBe("suspicious");
});

test("copied or altered proofs are refused", async ({ page }) => {
    await setup(page);
    let captured = "";
    page.on("request", request => {
        if (request.url().endsWith("/api/fingerprint/verify")) captured = request.postData() ?? "";
    });
    await identify(page);
    expect(captured).not.toBe("");
    const parsed: unknown = JSON.parse(captured);
    const altered = JSON.stringify({ ...(typeof parsed === "object" && parsed !== null ? parsed : {}), pow: "1" });
    const statuses = await page.evaluate(
        async bodies =>
            Promise.all(
                bodies.map(
                    async body =>
                        (
                            await fetch("/api/fingerprint/verify", {
                                method: "POST",
                                headers: {
                                    "Content-Type": "application/json",
                                    "X-CSRFToken": /csrftoken=([^;]+)/.exec(document.cookie)?.[1] ?? "",
                                },
                                body,
                            })
                        ).status,
                ),
            ),
        [captured, altered],
    );
    expect(statuses).toEqual([400, 400]);
});

test("concurrent tabs keep one non-exportable key", async ({ page, context }) => {
    const other = await context.newPage();
    await setup(page);
    await setup(other);
    const [first, second] = await Promise.all([identify(page), identify(other)]);
    expect(first.browserId).toBe(second.browserId);
    expect(first.indicators).not.toContain("ephemeral_key");
    const nonExportable = await page.evaluate(async () => {
        const db = await new Promise<IDBDatabase>((resolve, reject) => {
            const request = indexedDB.open("meteolane-browser-key-v1", 1);
            request.onsuccess = () => {
                resolve(request.result);
            };
            request.onerror = () => {
                reject(new Error("Cannot open test keys"));
            };
        });
        try {
            const stored: unknown = await new Promise(resolve => {
                const read = db.transaction("keys").objectStore("keys").get("signing");
                read.onsuccess = () => {
                    resolve(read.result);
                };
            });
            if (
                !stored ||
                typeof stored !== "object" ||
                !("privateKey" in stored) ||
                !(stored.privateKey instanceof CryptoKey)
            )
                return false;
            try {
                await crypto.subtle.exportKey("pkcs8", stored.privateKey);
                return false;
            } catch {
                return !stored.privateKey.extractable;
            }
        } finally {
            db.close();
        }
    });
    expect(nonExportable).toBe(true);
});

test("headless Chromium reports automation", async ({ page, browserName }) => {
    test.skip(browserName !== "chromium", "Only headless Chromium says so in its user agent");
    await setup(page);
    const result = await identify(page);
    expect(result.indicators).toContain("automation");
    expect(result.tier).toBe("suspicious");
});
