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
    "client_hints_grease_mismatch",
    "fetch_metadata_mismatch",
    "graphics_platform_mismatch",
    "realm_tampered",
    "native_stack_tampered",
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

/**
 * Headers the browser's network stack sets itself (Sec-Fetch-*, Sec-CH-UA), as a forger would
 * send them: neither the page nor route.continue may change them, so the fixture server swaps
 * them in (scripts/fingerprint-test-server.py).
 */
function forged(headers: Record<string, string>): Record<string, string> {
    return { "x-test-headers": JSON.stringify(headers) };
}

async function setup(page: Page) {
    await page.route("**/fingerprint-test", route =>
        route.fulfill({
            contentType: "text/html",
            body: "<!doctype html><title>Fingerprint test</title><body></body>",
        }),
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
    test.info().annotations.push({
        type: "assessment",
        description: `${assessment.tier}: ${assessment.indicators.join(", ")}`,
    });
    return assessment;
}

test("an honest browser preserves causal WebIDL relations and detects faulty controls", async ({ page }) => {
    await setup(page);
    const result = await page.evaluate(async () => {
        const path = "/e2e/causal-harness.ts";
        // eslint-disable-next-line @typescript-eslint/consistent-type-assertions -- Vite loads the shared browser harness.
        const module = (await import(/* @vite-ignore */ path)) as typeof import("./causal-harness");
        return module.runCausalHarness();
    });
    expect(result.nativeTrials).toBe(32);
    expect(result.staleFont).toBe("mismatch");
    expect(result.swallowedException).toBe("mismatch");
    expect(result.prematureSnapshot).toBe("mismatch");
});

test("an honest browser shows no lie and every check runs", async ({ page }, testInfo) => {
    await setup(page);
    let result = await identify(page);
    for (const lie of LIES) expect(result.indicators).not.toContain(lie);
    expect(result.indicators).not.toContain("semantic_alteration");
    expect(result.indicators).not.toContain("invalid_semantics");
    // A cold browser (first module transforms, first worker) can run into the probe timeouts;
    // that is degradation, not a lie. The warm second run must complete every check.
    if (result.indicators.some(indicator => indicator.endsWith("_timeout"))) {
        result = await identify(page);
        for (const lie of LIES) expect(result.indicators).not.toContain(lie);
    }
    for (const check of ["iframe", "integrity", "canvasIntegrity", "engine", "worker", "navigator", "automation"]) {
        expect(result.indicators).not.toContain(`${check}_unavailable`);
        expect(result.indicators).not.toContain(`${check}_timeout`);
    }
    // The known-answer canvas and audio checks must pass wherever nothing adds noise on purpose.
    if (!testInfo.project.name.includes("resist")) {
        expect(result.indicators).not.toContain("canvas_noise");
        expect(result.indicators).not.toContain("audio_unstable");
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
    // Called on the wrong object, the replacement answers where the browser's own getter refuses.
    expect(result.indicators).toContain("realm_tampered");
    expect(result.tier).toBe("suspicious");
    expect(result.fingerprintId).toBeNull();
});

test("a wrapper that forwards to the native getter shows in the stack", async ({ page }) => {
    await setup(page);
    await page.evaluate(() => {
        const native = Object.getOwnPropertyDescriptor(Navigator.prototype, "platform");
        const original: unknown = Reflect.get(native ?? {}, "get");
        if (typeof original !== "function") return;
        Object.defineProperty(Navigator.prototype, "platform", {
            get(this: unknown): unknown {
                return Reflect.apply(original, this, []);
            },
            configurable: true,
            enumerable: true,
        });
    });
    const result = await identify(page);
    // Same answer, same refusal: only its own frame in the stack gives the wrapper away.
    expect(result.indicators).not.toContain("realm_tampered");
    expect(result.indicators).toContain("native_stack_tampered");
});

test("a frame that is not the page's own is caught", async ({ page }) => {
    await setup(page);
    await page.evaluate(() => {
        // A hooked contentWindow handing back a realm the spoofer prepared (here: the page itself).
        Object.defineProperty(HTMLIFrameElement.prototype, "contentWindow", {
            get: () => window,
            configurable: true,
        });
    });
    const result = await identify(page);
    // The indicator is the finding: a driven test browser is suspicious for automation anyway.
    expect(result.indicators).toContain("realm_tampered");
});

test("request headers a JSON fetch cannot carry are a lie", async ({ page }) => {
    await setup(page);
    await page.route("**/api/fingerprint/verify", route =>
        route.continue({ headers: { ...route.request().headers(), ...forged({ "sec-fetch-mode": "navigate" }) } }),
    );
    const result = await identify(page);
    expect(result.indicators).toContain("fetch_metadata_mismatch");
});

test("a client-hints brand from another Chromium version is a lie", async ({ page, browserName }) => {
    test.skip(browserName !== "chromium", "Only Chromium sends client hints");
    await setup(page);
    await page.route("**/api/fingerprint/verify", route => {
        const headers = route.request().headers();
        // Chromium derives the made-up brand from its major; an edited template keeps the old one.
        const major = /"Chromium";v="(\d+)"/.exec(headers["sec-ch-ua"] ?? "")?.[1] ?? "130";
        const stale = Number(major) % 3 === 0 ? '"Not?A_Brand";v="99"' : '"Not_A Brand";v="8"';
        return route.continue({
            headers: { ...headers, ...forged({ "sec-ch-ua": `"Chromium";v="${major}", ${stale}` }) },
        });
    });
    const result = await identify(page);
    expect(result.indicators).toContain("client_hints_grease_mismatch");
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
