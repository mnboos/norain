/** Causal WebIDL checks, not device identifiers or proof that a client is honest.
 * Only fixed relation outcomes leave the page. Privacy tools can legitimately alter them.
 * Each challenge chooses small parameters; every canvas/context is disposable.
 */
import { sha256 } from "./sha256";

export type Outcome = "pass" | "mismatch" | "unavailable";
export type SemanticResults = Record<"font" | "viewport" | "exception" | "serialization", Outcome>;

function argument<T extends string | number>(convert: (hint: string) => T): T {
    // eslint-disable-next-line @typescript-eslint/consistent-type-assertions -- Intentionally exercise WebIDL conversion instead of passing a scalar.
    return { [Symbol.toPrimitive]: convert } as unknown as T;
}

function check(read: () => boolean): Outcome {
    try {
        return read() ? "pass" : "mismatch";
    } catch {
        return "unavailable";
    }
}

export function semanticChecks(challenge: string, doc: Document = document): SemanticResults {
    const seed = sha256(new TextEncoder().encode(challenge));
    const size = 24 + ((seed[0] ?? 0) % 12);
    const text = `mw${seed[1] ?? 0}`;
    return {
        font: check(() => {
            const ctx = doc.createElement("canvas").getContext("2d");
            if (!ctx) throw new Error("2D unavailable");
            ctx.font = "10px monospace";
            let calls = 0;
            const measured = ctx.measureText(
                argument(() => {
                    calls++;
                    ctx.font = `${size}px monospace`;
                    return text;
                }),
            ).width;
            const control = ctx.measureText(text).width;
            return calls === 1 && Number.isFinite(measured) && measured === control;
        }),
        viewport: check(() => {
            const canvas = doc.createElement("canvas");
            const gl = canvas.getContext("webgl");
            if (!gl) throw new Error("WebGL unavailable");
            try {
                const desired = [1, 2, 8 + ((seed[2] ?? 0) % 8), 8 + ((seed[3] ?? 0) % 8)];
                let calls = 0;
                const hints: string[] = [];
                const actual: unknown = gl.getParameter(
                    argument(hint => {
                        hints.push(hint);
                        calls++;
                        gl.viewport(desired[0] ?? 0, desired[1] ?? 0, desired[2] ?? 0, desired[3] ?? 0);
                        return gl.VIEWPORT;
                    }),
                );
                return (
                    calls === 1 &&
                    hints[0] === "number" &&
                    actual instanceof Int32Array &&
                    desired.every((value, index) => actual[index] === value)
                );
            } finally {
                gl.getExtension("WEBGL_lose_context")?.loseContext();
            }
        }),
        exception: check(() => {
            const ctx = doc.createElement("canvas").getContext("2d");
            if (!ctx) throw new Error("2D unavailable");
            const sentinel = Object.freeze({});
            let calls = 0;
            try {
                ctx.measureText(
                    argument<string>(() => {
                        calls++;
                        // eslint-disable-next-line @typescript-eslint/only-throw-error -- A unique non-Error sentinel tests exception identity, not message/stack spoofing.
                        throw sentinel;
                    }),
                );
                return false;
            } catch (error) {
                return error === sentinel && calls === 1;
            }
        }),
        serialization: check(() => {
            const canvas = doc.createElement("canvas");
            canvas.width = 16;
            canvas.height = 16;
            let calls = 0;
            const actual = canvas.toDataURL(
                argument(() => {
                    calls++;
                    canvas.width = 0;
                    return "image/png";
                }),
            );
            return calls === 1 && canvas.width === 0 && actual === "data:,";
        }),
    };
}
