import { solveRange } from "./pow";

// Runs until it finds the nonce; the page terminates it on a timeout.
self.onmessage = (event: MessageEvent<unknown>) => {
    const task: unknown = event.data;
    if (typeof task !== "object" || task === null || !("seed" in task) || !("bits" in task)) return;
    const { seed, bits } = task;
    if (typeof seed !== "string" || typeof bits !== "number") return;
    for (let start = 0; start < Number.MAX_SAFE_INTEGER; start += 100_000) {
        const found = solveRange(seed, bits, start, 100_000);
        if (found !== null) {
            self.postMessage(found);
            return;
        }
    }
};
