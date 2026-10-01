import { createBrowserFingerprint } from "../src/lib/browser-fingerprint";
import { runCausalHarness } from "./causal-harness";

async function run() {
    const causal = runCausalHarness();
    await fetch("/api/fingerprint/test-csrf");
    const client = () =>
        createBrowserFingerprint({
            baseUrl: "",
            csrfToken: () => /csrftoken=([^;]+)/.exec(document.cookie)?.[1] ?? "",
        });
    const receipt = await client().identify();
    if (Object.keys(receipt).join() !== "expiresIn") throw new Error("Receipt leaked assessment");
    const first = await assessment();
    await client().identify();
    const second = await assessment();
    if (!first.browserId || first.browserId !== second.browserId || second.continuity !== true)
        throw new Error("Browser key continuity failed");
    if (
        !Array.isArray(second.indicators) ||
        second.indicators.includes("semantic_alteration") ||
        second.indicators.includes("invalid_semantics")
    )
        throw new Error("Native semantic diagnostic failed");
    return {
        status: "PASS",
        userAgent: navigator.userAgent,
        causal,
        signedReceipt: true,
        keyContinuity: true,
        persistent: second.persistent,
        tier: second.tier,
        indicators: second.indicators,
    };
}

const output = document.getElementById("result");
async function assessment(): Promise<Record<string, unknown>> {
    const value: unknown = await (await fetch("/api/fingerprint/test-assessment")).json();
    if (typeof value !== "object" || value === null) throw new Error("Missing assessment");
    return Object.fromEntries(Object.entries(value));
}
run()
    .then(result => {
        if (output) output.textContent = JSON.stringify(result, null, 2);
    })
    .catch((error: unknown) => {
        if (output) output.textContent = `FAIL: ${String(error)}`;
    });
