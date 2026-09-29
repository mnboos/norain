import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ensureRecognized, prewarmRecognition, resetRecognition } from "../browserRecognition";

const identify = vi.fn<() => Promise<{ expiresIn: number }>>();
vi.mock("@/lib/browser-fingerprint", () => ({ createBrowserFingerprint: () => ({ identify }) }));

beforeEach(() => {
    resetRecognition();
    identify.mockReset();
});
afterEach(() => {
    vi.useRealTimers();
});

describe("browser recognition on demand", () => {
    it("recognises once and reuses the fresh receipt", async () => {
        identify.mockResolvedValue({ expiresIn: 900 });
        prewarmRecognition();
        await ensureRecognized();
        await ensureRecognized();
        expect(identify).toHaveBeenCalledTimes(1);
    });

    it("never rejects: a failed proof leaves the request unrecognised", async () => {
        identify.mockRejectedValue(new Error("blocked"));
        await expect(ensureRecognized()).resolves.toBeUndefined();
        // Not remembered as a success, so the next request tries again.
        identify.mockResolvedValue({ expiresIn: 900 });
        await ensureRecognized();
        expect(identify).toHaveBeenCalledTimes(2);
    });

    it("stops waiting after a few seconds", async () => {
        vi.useFakeTimers();
        identify.mockReturnValue(
            new Promise(() => {
                /* a probe that never finishes */
            }),
        );
        const waited = ensureRecognized();
        await vi.advanceTimersByTimeAsync(6000);
        await expect(waited).resolves.toBeUndefined();
    });
});
