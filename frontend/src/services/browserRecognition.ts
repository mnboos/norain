import { createBrowserFingerprint } from "@/lib/browser-fingerprint";
import { getCookie, useBackendHost } from "@/utils";

/**
 * Browser recognition on demand: only where an anonymous vote or a sign-up may follow, never
 * on every page. A successful proof leaves an HttpOnly receipt the server reads on the next
 * request; nothing about it is visible here (core/fingerprinting.py).
 */

// The receipt lives 15 minutes; renew it before it runs out under a pending request.
const FRESH_FOR_MS = 12 * 60 * 1000;
// A vote or sign-up waits at most this long; without a receipt it counts as unrecognised. The
// prewarm usually finishes first; this covers a phone at the capped proof-of-work (~4 s).
const WAIT_MS = 6000;

let client: ReturnType<typeof createBrowserFingerprint> | undefined;
let recognisedAt = 0;
let running: Promise<void> | undefined;

function wanted(): boolean {
    return (
        import.meta.env.VITE_BROWSER_FINGERPRINT_ENABLED !== "false" && Date.now() - recognisedAt >= FRESH_FOR_MS
    );
}

function run(): Promise<void> {
    client ??= createBrowserFingerprint({
        baseUrl: useBackendHost(),
        csrfToken: () => getCookie("csrftoken") ?? "",
    });
    running ??= client
        .identify()
        .then(
            () => {
                recognisedAt = Date.now();
            },
            () => {
                // Unsupported browsers, blocked storage and offline use stay fully usable.
            },
        )
        .finally(() => {
            running = undefined;
        });
    return running;
}

/** Start in the background where a vote or a sign-up may follow, so the click need not wait. */
export function prewarmRecognition(): void {
    if (wanted()) void run();
}

/** Resolves once a fresh receipt is in place, or after `WAIT_MS`. It never rejects. */
export async function ensureRecognized(): Promise<void> {
    if (!wanted()) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    await Promise.race([
        run(),
        new Promise<void>(resolve => {
            timer = setTimeout(resolve, WAIT_MS);
        }),
    ]);
    clearTimeout(timer);
}

/** For tests: forget the last receipt. */
export function resetRecognition(): void {
    client = undefined;
    recognisedAt = 0;
    running = undefined;
}
