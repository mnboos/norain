import { compress, INITIAL_STATE, sha256Hex } from "./sha256";

/**
 * The proof-of-work: the smallest decimal `n` for which SHA-256(`${seed}:${n}`) starts with
 * `bits` zero bits. The seed is 64 hex characters, exactly one block, so it is hashed once
 * and every attempt costs a single compression of the nonce block.
 * Must match `core.fingerprinting.pow_seed` / `pow_valid` (see `__tests__/pow-vector.json`).
 */
export function powSeed(challenge: string, payload: string): string {
    return sha256Hex(`${challenge}\n${sha256Hex(payload)}`);
}

export function leadingZeroBits(state: Uint32Array): number {
    let bits = 0;
    for (const word of state) {
        if (word === 0) {
            bits += 32;
            continue;
        }
        return bits + Math.clz32(word);
    }
    return bits;
}

/** Search `count` nonces from `start`; the nonce as a string, or null when none met `bits`. */
export function solveRange(seed: string, bits: number, start: number, count: number): string | null {
    const seedBytes = new TextEncoder().encode(seed);
    if (seedBytes.length !== 64) throw new Error("The seed must be one 64-byte block");
    const w = new Uint32Array(64);
    const midstate = Uint32Array.from(INITIAL_STATE);
    compress(midstate, seedBytes, 0, w);
    const state = new Uint32Array(8);
    // The second (last) block: ":" + digits, the 0x80 marker, zeros, the message length in bits.
    const block = new Uint8Array(64);
    block[0] = 0x3a;
    for (let n = start; n < start + count; n++) {
        const digits = String(n);
        const length = digits.length + 1;
        for (let i = 0; i < digits.length; i++) block[i + 1] = digits.charCodeAt(i);
        block[length] = 0x80;
        block.fill(0, length + 1, 60);
        const total = (64 + length) * 8;
        block[60] = total >>> 24;
        block[61] = (total >>> 16) & 0xff;
        block[62] = (total >>> 8) & 0xff;
        block[63] = total & 0xff;
        state.set(midstate);
        compress(state, block, 0, w);
        if (leadingZeroBits(state) >= bits) return digits;
    }
    return null;
}

const CHUNK = 20_000;

/** Solve on this thread, yielding between chunks so the page stays responsive. */
export async function solveInline(seed: string, bits: number, signal?: AbortSignal): Promise<string> {
    for (let start = 0; start < Number.MAX_SAFE_INTEGER; start += CHUNK) {
        signal?.throwIfAborted();
        const found = solveRange(seed, bits, start, CHUNK);
        if (found !== null) return found;
        await new Promise(resolve => setTimeout(resolve, 0));
    }
    throw new Error("No proof-of-work found");
}

/** Solve in a worker when the browser has them, else on this thread. */
export function solveProofOfWork(seed: string, bits: number, timeout = 30_000): Promise<string> {
    const signal = AbortSignal.timeout(timeout);
    if (typeof Worker === "undefined") return solveInline(seed, bits, signal);
    return new Promise((resolve, reject) => {
        let worker: Worker | undefined;
        const finish = () => {
            worker?.terminate();
            signal.removeEventListener("abort", abort);
        };
        const abort = () => {
            finish();
            reject(new Error("Proof-of-work timed out"));
        };
        const fallBack = () => {
            finish();
            solveInline(seed, bits, signal).then(resolve, reject);
        };
        signal.addEventListener("abort", abort);
        try {
            worker = new Worker(new URL("./pow.worker.ts", import.meta.url), { type: "module" });
        } catch {
            fallBack();
            return;
        }
        worker.onmessage = (event: MessageEvent<unknown>) => {
            finish();
            if (typeof event.data === "string") resolve(event.data);
            else reject(new Error("Invalid proof-of-work"));
        };
        // A worker the page may not start (CSP, an old browser) still leaves the inline solver.
        worker.onerror = fallBack;
        worker.postMessage({ seed, bits });
    });
}
