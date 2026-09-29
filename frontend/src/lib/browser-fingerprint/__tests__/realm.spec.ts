import { describe, expect, it } from "vitest";
import { engineTells, isNativeFunction, ownOverrides, sourceReader } from "../realm";

// Node's own built-ins are real native code; jsdom's DOM getters are JavaScript, so the
// checks are exercised on the language built-ins here and on the DOM in Playwright.
const source = sourceReader(globalThis);

describe("native function checks", () => {
    it("accepts native code that carries its own name", () => {
        expect(isNativeFunction(Math.max, "max", source)).toBe(true);
        const getter: unknown = Reflect.get(Object.getOwnPropertyDescriptor(Map.prototype, "size") ?? {}, "get");
        expect(isNativeFunction(getter, "size", source)).toBe(true);
    });

    it("refuses replacements, proxies and wrong names", () => {
        expect(isNativeFunction(() => 8, "max", source)).toBe(false);
        expect(
            isNativeFunction(
                function max() {
                    return 8;
                },
                "max",
                source,
            ),
        ).toBe(false);
        // A Proxy prints as native code, but without a name.
        expect(isNativeFunction(new Proxy(Math.max, {}), "max", source)).toBe(false);
        expect(isNativeFunction(Math.min, "max", source)).toBe(false);
        expect(isNativeFunction(undefined, "max", source)).toBe(false);
    });

    it("sees a patched toString through a clean one", () => {
        const patched = () => 8;
        patched.toString = () => "function max() { [native code] }";
        expect(isNativeFunction(patched, "max", source)).toBe(false);
    });

    it("finds instance overrides of inherited fields", () => {
        class Fake {
            get userAgent(): string {
                return navigator.userAgent;
            }
        }
        const instance = new Fake();
        expect(ownOverrides(instance, ["userAgent"])).toEqual([]);
        Object.defineProperty(instance, "userAgent", { value: "spoofed" });
        expect(ownOverrides(instance, ["userAgent", "platform"])).toEqual(["userAgent"]);
    });
});

describe("engine tells", () => {
    it("reads V8's own wording, which the server maps to Blink", () => {
        // The same strings are backend/core/test_fingerprinting.py's V8 fixture.
        expect(engineTells()).toEqual([
            "Invalid array length",
            "toFixed() digits argument must be between 0 and 100",
            "Invalid count value: -1",
            "v8",
        ]);
    });
});
