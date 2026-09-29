import { afterEach, describe, expect, it, vi } from "vitest";
import { webcrypto } from "node:crypto";
import { createBrowserFingerprint } from "../index";
import { leadingZeroBits, powSeed } from "../pow";
import { sha256 } from "../sha256";

vi.mock("../signals", () => ({ collectSignals: () => Promise.resolve({}) }));
afterEach(() => {
    vi.unstubAllGlobals();
});

function stubBrowser(fetcher: typeof fetch) {
    vi.stubGlobal("crypto", webcrypto);
    vi.stubGlobal("indexedDB", {
        open() {
            throw new Error("blocked");
        },
    });
    vi.stubGlobal("fetch", fetcher);
}

function sentBody(fetcher: ReturnType<typeof vi.fn<typeof fetch>>, call: number): Record<string, string> {
    const body = fetcher.mock.calls[call]?.[1]?.body;
    if (typeof body !== "string") throw new Error("No JSON body");
    const parsed: unknown = JSON.parse(body);
    if (typeof parsed !== "object" || parsed === null) throw new Error("Not an object");
    return Object.fromEntries(Object.entries(parsed).map(([key, value]) => [key, String(value)]));
}

describe("browser proof transport", () => {
    it("deduplicates calls and signs a fresh challenge after a context race", async () => {
        const fetcher = vi
            .fn<typeof fetch>()
            .mockResolvedValueOnce(Response.json({ challenge: "first", difficulty: 0 }))
            .mockResolvedValueOnce(Response.json({ detail: "context changed" }, { status: 400 }))
            .mockResolvedValueOnce(Response.json({ challenge: "second", difficulty: 0 }))
            .mockResolvedValueOnce(Response.json({ expiresIn: 900 }));
        stubBrowser(fetcher);
        const client = createBrowserFingerprint({ baseUrl: "", csrfToken: () => "csrf" });
        const first = client.identify();
        expect(client.identify()).toBe(first);
        expect(await first).toEqual({ expiresIn: 900 });
        expect(fetcher).toHaveBeenCalledTimes(4);
        expect(sentBody(fetcher, 1).challenge).toBe("first");
        expect(fetcher.mock.calls[1]?.[1]?.credentials).toBe("include");
        expect(sentBody(fetcher, 3).challenge).toBe("second");
    });

    it("solves the server's difficulty and signs the nonce with the payload", async () => {
        const fetcher = vi
            .fn<typeof fetch>()
            .mockResolvedValueOnce(Response.json({ challenge: "challenge", difficulty: 10 }))
            .mockResolvedValueOnce(Response.json({ expiresIn: 900 }));
        stubBrowser(fetcher);
        await createBrowserFingerprint({ baseUrl: "", csrfToken: () => "csrf" }).identify();
        const { challenge, payload, pow, publicKey, signature } = sentBody(fetcher, 1);
        const seed = powSeed(challenge ?? "", payload ?? "");
        const digest = sha256(new TextEncoder().encode(`${seed}:${pow ?? ""}`));
        expect(leadingZeroBits(digest)).toBeGreaterThanOrEqual(10);
        expect(JSON.parse(payload ?? "")).toMatchObject({ version: 2, persistent: false });
        const key = await webcrypto.subtle.importKey(
            "spki",
            Uint8Array.from(atob(publicKey ?? ""), char => char.charCodeAt(0)),
            { name: "ECDSA", namedCurve: "P-256" },
            false,
            ["verify"],
        );
        const valid = await webcrypto.subtle.verify(
            { name: "ECDSA", hash: "SHA-256" },
            key,
            Uint8Array.from(atob(signature ?? ""), char => char.charCodeAt(0)),
            new TextEncoder().encode(`${challenge ?? ""}\n${payload ?? ""}\n${pow ?? ""}`),
        );
        expect(valid).toBe(true);
    });

    it("does not retry rate limits or accept a malformed challenge or receipt", async () => {
        const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({}, { status: 429 }));
        stubBrowser(fetcher);
        const client = createBrowserFingerprint({ baseUrl: "", csrfToken: () => "csrf" });
        await expect(client.identify()).rejects.toThrow("429");
        expect(fetcher).toHaveBeenCalledTimes(1);
        fetcher.mockReset().mockResolvedValueOnce(Response.json({ challenge: "valid", difficulty: 99 }));
        await expect(client.identify()).rejects.toThrow("Invalid browser challenge");
        fetcher
            .mockReset()
            .mockResolvedValueOnce(Response.json({ challenge: "valid", difficulty: 0 }))
            .mockResolvedValueOnce(Response.json({ tier: "high" }));
        await expect(client.identify()).rejects.toThrow("Invalid browser receipt");
        expect(fetcher).toHaveBeenCalledTimes(2);
    });
});
