import { powSeed, solveProofOfWork } from "./pow";
import { collectSignals } from "./signals";

/**
 * What a successful proof earns: a receipt cookie (HttpOnly) the server reads on later
 * requests. The assessment itself never leaves the server, so there is nothing to show here.
 */
export interface Receipt {
    expiresIn: number;
}

export interface FingerprintOptions {
    baseUrl: string;
    csrfToken: () => string;
}

function encode(bytes: ArrayBuffer): string {
    return btoa(String.fromCharCode(...new Uint8Array(bytes)));
}

function record(value: unknown): value is Record<string, unknown> {
    return value !== null && typeof value === "object" && !Array.isArray(value);
}

function keyPair(value: unknown): value is CryptoKeyPair {
    return (
        record(value) &&
        value.privateKey instanceof CryptoKey &&
        value.publicKey instanceof CryptoKey &&
        value.privateKey.type === "private" &&
        !value.privateKey.extractable &&
        value.privateKey.algorithm.name === "ECDSA"
    );
}

function isReceipt(value: unknown): value is Receipt {
    return record(value) && typeof value.expiresIn === "number";
}

async function openKeys(): Promise<IDBDatabase> {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open("meteolane-browser-key-v1", 1);
        let expired = false;
        const timer = setTimeout(() => {
            expired = true;
            reject(new Error("Key storage timeout"));
        }, 1500);
        request.onupgradeneeded = () => request.result.createObjectStore("keys");
        request.onsuccess = () => {
            clearTimeout(timer);
            if (expired) request.result.close();
            else resolve(request.result);
        };
        request.onerror = () => {
            clearTimeout(timer);
            reject(new Error("Key storage failed", { cause: request.error }));
        };
    });
}

async function browserKey(): Promise<{ pair: CryptoKeyPair; persistent: boolean }> {
    const candidate = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, [
        "sign",
        "verify",
    ]);
    let db: IDBDatabase | undefined;
    try {
        db = await openKeys();
        const database = db;
        const pair = await new Promise<CryptoKeyPair>((resolve, reject) => {
            // One read/write transaction: concurrent tabs cannot replace each other's first key.
            const tx = database.transaction("keys", "readwrite");
            const timer = setTimeout(() => {
                tx.abort();
                reject(new Error("Key transaction timeout"));
            }, 1500);
            const store = tx.objectStore("keys");
            const read = store.get("signing");
            let selected = candidate;
            read.onsuccess = () => {
                const stored: unknown = read.result;
                if (keyPair(stored)) selected = stored;
                else store.put(candidate, "signing");
            };
            tx.oncomplete = () => {
                clearTimeout(timer);
                resolve(selected);
            };
            tx.onabort = tx.onerror = () => {
                clearTimeout(timer);
                reject(new Error("Key transaction failed", { cause: tx.error }));
            };
        });
        return { pair, persistent: true };
    } catch {
        return { pair: candidate, persistent: false };
    } finally {
        db?.close();
    }
}

class FingerprintRequestError extends Error {
    constructor(readonly status: number) {
        super(`Browser recognition failed (${status})`);
    }
}

/** A client instance deduplicates overlapping requests. Failure never asserts an identity. */
export function createBrowserFingerprint(options: FingerprintOptions) {
    let pending: Promise<Receipt> | undefined;
    async function post(path: string, body?: unknown): Promise<unknown> {
        const response = await fetch(`${options.baseUrl.replace(/\/$/, "")}/api/fingerprint/${path}`, {
            method: "POST",
            credentials: "include",
            cache: "no-store",
            headers: { "Content-Type": "application/json", "X-CSRFToken": options.csrfToken() },
            body: JSON.stringify(body ?? {}),
            signal: AbortSignal.timeout(5000),
        });
        if (!response.ok) throw new FingerprintRequestError(response.status);
        const value: unknown = await response.json();
        return value;
    }
    async function identify(): Promise<Receipt> {
        const { pair, persistent } = await browserKey();
        const payload = JSON.stringify({ version: 2, persistent, signals: await collectSignals() });
        // Concurrent first visits may receive different context cookies. A fresh
        // challenge binds to the cookie that won; never replay the rejected proof.
        for (let attempt = 0; attempt < 3; attempt++) {
            try {
                return await submit(pair, payload);
            } catch (error) {
                if (!(error instanceof FingerprintRequestError) || error.status !== 400 || attempt === 2) throw error;
            }
        }
        throw new Error("Browser recognition failed");
    }
    async function submit(pair: CryptoKeyPair, payload: string): Promise<Receipt> {
        const response = await post("challenge");
        if (
            !record(response) ||
            typeof response.challenge !== "string" ||
            response.challenge.length > 1024 ||
            typeof response.difficulty !== "number" ||
            !Number.isInteger(response.difficulty) ||
            response.difficulty < 0 ||
            response.difficulty > 32
        )
            throw new Error("Invalid browser challenge response");
        const challenge = response.challenge;
        // The cost of each new identity; the server chose it and signed it into the challenge.
        const pow = await solveProofOfWork(powSeed(challenge, payload), response.difficulty);
        const signature = await crypto.subtle.sign(
            { name: "ECDSA", hash: "SHA-256" },
            pair.privateKey,
            new TextEncoder().encode(`${challenge}\n${payload}\n${pow}`),
        );
        const result = await post("verify", {
            challenge,
            payload,
            signature: encode(signature),
            publicKey: encode(await crypto.subtle.exportKey("spki", pair.publicKey)),
            pow,
        });
        if (!isReceipt(result)) throw new Error("Invalid browser receipt response");
        return result;
    }
    return {
        identify(): Promise<Receipt> {
            pending ??= identify().finally(() => {
                pending = undefined;
            });
            return pending;
        },
    };
}
