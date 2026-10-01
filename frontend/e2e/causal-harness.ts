/** Shared by Playwright and the manual installed-Safari page; no provider/DB calls. */
import { semanticChecks } from "../src/lib/browser-fingerprint/semantics";

export function runCausalHarness() {
    const native = Array.from({ length: 32 }, (_, n) => semanticChecks(`native-${n}`));
    if (native.some(result => Object.values(result).includes("mismatch"))) throw new Error("Native semantic mismatch");
    const proto = CanvasRenderingContext2D.prototype;
    // eslint-disable-next-line @typescript-eslint/unbound-method -- Saved native methods are always invoked with Reflect.apply.
    const measure = proto.measureText;
    // eslint-disable-next-line @typescript-eslint/unbound-method -- Saved native methods are always invoked with Reflect.apply.
    const serialize = HTMLCanvasElement.prototype.toDataURL;
    const glProto = WebGLRenderingContext.prototype;
    // eslint-disable-next-line @typescript-eslint/unbound-method -- Saved native methods are always invoked with Reflect.apply.
    const getParameter = glProto.getParameter;
    try {
        proto.measureText = function (text) {
            return Reflect.apply(measure, this, [text]);
        };
        const transparent = semanticChecks("transparent");
        if (transparent.font !== "pass" || transparent.exception !== "pass")
            throw new Error("Transparent wrapper failed");

        proto.measureText = function (text) {
            const before = this.font;
            // eslint-disable-next-line @typescript-eslint/no-unnecessary-type-conversion -- WebIDL receives an object despite its scalar TypeScript signature.
            const scalar = String(text);
            const after = this.font;
            this.font = before;
            try {
                return Reflect.apply(measure, this, [scalar]);
            } finally {
                this.font = after;
            }
        };
        const staleFont = semanticChecks("stale-font").font;
        if (staleFont !== "mismatch") throw new Error("Missed stale font");

        proto.measureText = function (text) {
            try {
                return Reflect.apply(measure, this, [text]);
            } catch {
                return Reflect.apply(measure, this, [""]);
            }
        };
        const swallowedException = semanticChecks("swallowed-exception").exception;
        if (swallowedException !== "mismatch") throw new Error("Missed swallowed exception");
        proto.measureText = measure;

        HTMLCanvasElement.prototype.toDataURL = function (type, quality) {
            const snapshot = document.createElement("canvas");
            snapshot.width = this.width;
            snapshot.height = this.height;
            return Reflect.apply(serialize, snapshot, [type, quality]);
        };
        const prematureSnapshot = semanticChecks("premature-snapshot").serialization;
        if (prematureSnapshot !== "mismatch") throw new Error("Missed premature canvas snapshot");
        HTMLCanvasElement.prototype.toDataURL = serialize;
        glProto.getParameter = function (parameter): unknown {
            // eslint-disable-next-line @typescript-eslint/no-unnecessary-type-conversion -- Faulty proxy converts the object twice.
            Number(parameter);
            return Reflect.apply(getParameter, this, [parameter]);
        };
        const duplicateConversion = semanticChecks("duplicate-conversion").viewport;
        if (native[0]?.viewport === "pass" && duplicateConversion !== "mismatch")
            throw new Error("Missed duplicate WebGL conversion");
        return {
            nativeTrials: native.length,
            native: native[0],
            transparent,
            staleFont,
            swallowedException,
            prematureSnapshot,
            duplicateConversion,
        };
    } finally {
        proto.measureText = measure;
        HTMLCanvasElement.prototype.toDataURL = serialize;
        glProto.getParameter = getParameter;
    }
}
