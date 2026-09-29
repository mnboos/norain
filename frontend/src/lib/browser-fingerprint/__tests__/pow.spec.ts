import { describe, expect, it } from "vitest";
import { webcrypto } from "node:crypto";
import { leadingZeroBits, powSeed, solveInline, solveRange } from "../pow";
import { sha256, sha256Hex } from "../sha256";
import vector from "./pow-vector.json";

async function subtleHex(text: string): Promise<string> {
    const bytes = await webcrypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, "0")).join("");
}

describe("hand-written SHA-256", () => {
    it("matches WebCrypto across block boundaries and non-ASCII text", async () => {
        const inputs = ["", "abc", "Ω é 🚲", ...[55, 56, 63, 64, 65, 119, 120, 1000].map(n => "x".repeat(n))];
        for (const input of inputs) expect(sha256Hex(input), `length ${input.length}`).toBe(await subtleHex(input));
    });
});

describe("proof-of-work", () => {
    it("matches the server's vector byte for byte", () => {
        // The same file is checked by backend/core/test_fingerprinting.py.
        expect(sha256Hex(vector.payload)).toBe(vector.payloadDigest);
        expect(powSeed(vector.challenge, vector.payload)).toBe(vector.seed);
        expect(sha256Hex(`${vector.seed}:${vector.nonce}`)).toBe(vector.digest);
        // Both sides search upwards from 0, so both find the same smallest nonce.
        expect(solveRange(vector.seed, vector.bits, 0, 100_000)).toBe(vector.nonce);
    });

    it("counts leading zero bits across words", () => {
        expect(leadingZeroBits(Uint32Array.of(0x0000ffff, 0, 0, 0, 0, 0, 0, 0))).toBe(16);
        expect(leadingZeroBits(Uint32Array.of(0, 0x40000000, 0, 0, 0, 0, 0, 0))).toBe(33);
        expect(leadingZeroBits(sha256(new TextEncoder().encode(`${vector.seed}:${vector.nonce}`)))).toBeGreaterThanOrEqual(
            vector.bits,
        );
    });

    it("solves on this thread when there is no worker, and gives up when aborted", async () => {
        const nonce = await solveInline(vector.seed, 8);
        expect(leadingZeroBits(sha256(new TextEncoder().encode(`${vector.seed}:${nonce}`)))).toBeGreaterThanOrEqual(8);
        await expect(solveInline(vector.seed, 64, AbortSignal.abort())).rejects.toThrow();
    });
});
