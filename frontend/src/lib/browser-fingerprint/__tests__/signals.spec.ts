import { afterEach, describe, expect, it, vi } from "vitest";
import { webcrypto } from "node:crypto";
import { digest, probe } from "../signals";

afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

describe("first-party fingerprint probes", () => {
    it("uses deterministic SHA-256 rather than a weak integer hash", async () => {
        vi.stubGlobal("crypto", webcrypto);
        expect(await digest("abc")).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    });
    it("keeps blocked and malformed values distinct from real values", async () => {
        expect(
            await probe(() => {
                throw new Error("blocked");
            }),
        ).toEqual({ status: "unavailable" });
        expect(await probe(() => undefined)).toEqual({ status: "unavailable" });
        expect(await probe(() => "x".repeat(4097))).toEqual({ status: "unavailable" });
        expect(await probe(() => false)).toEqual({ status: "ok", value: "false" });
    });
    it("bounds hanging APIs without leaving a timer after successful probes", async () => {
        vi.useFakeTimers();
        const result = probe(
            () =>
                new Promise(() => {
                    /* intentionally unresolved API */
                }),
            100,
        );
        await vi.advanceTimersByTimeAsync(100);
        expect(await result).toEqual({ status: "timeout" });
        await probe(() => "value");
        expect(vi.getTimerCount()).toBe(0);
    });
});
