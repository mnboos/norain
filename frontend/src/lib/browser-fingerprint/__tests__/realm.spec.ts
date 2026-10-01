import { describe, expect, it, vi } from "vitest";
import {
    engineTells,
    isNativeFunction,
    isOwnFrame,
    nanBits,
    ownOverrides,
    sourceReader,
    wrappedInStack,
    wrongReceiver,
} from "../realm";

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

describe("wrong receivers", () => {
    const size: unknown = Reflect.get(Object.getOwnPropertyDescriptor(Map.prototype, "size") ?? {}, "get");

    it("a native getter refuses another object, with nothing of ours between throw and marker", () => {
        const native = wrongReceiver(size, Reflect.apply);
        expect(native.outcome).toBe("threw TypeError");
        expect(wrappedInStack(native.stack)).toBe(false);
    });

    it("a replacement answers instead, and a wrapper shows its own frame", () => {
        expect(wrongReceiver(() => 8, Reflect.apply).outcome).toBe("returned");
        const wrapper = function (this: unknown): unknown {
            return typeof size === "function" ? Reflect.apply(size, this, []) : undefined;
        };
        const wrapped = wrongReceiver(wrapper, Reflect.apply);
        expect(wrapped.outcome).toBe(wrongReceiver(size, Reflect.apply).outcome);
        expect(wrappedInStack(wrapped.stack)).toBe(true);
    });

    it("reads each engine's stack format", () => {
        // As JavaScriptCore and SpiderMonkey print them (Playwright's WebKit and Firefox).
        const jsc = "platform@[native code]\n__meteolaneRealmProbe@http://x/realm.ts:128:87\nwrongReceiver@http://x:1:2";
        expect(wrappedInStack(jsc)).toBe(false);
        expect(wrappedInStack(`platform@[native code]\nget@\n${jsc.split("\n").slice(1).join("\n")}`)).toBe(true);
        const gecko = "__meteolaneRealmProbe@http://x/realm.ts:128:87\nwrongReceiver@http://x/realm.ts:146:15";
        expect(wrappedInStack(gecko)).toBe(false);
        expect(wrappedInStack(`get@moz-extension://abc/inject.js:9:57\n${gecko}`)).toBe(true);
        const v8 = "TypeError: Illegal invocation\n    at get platform (<anonymous>)\n    at __meteolaneRealmProbe (x:1:2)";
        expect(wrappedInStack(v8)).toBe(false);
        expect(wrappedInStack(v8.replace("(<anonymous>)", "(chrome-extension://abc/inject.js:5:10)"))).toBe(true);
    });

    it("handles a promise-returning method's rejection instead of leaving it unhandled", async () => {
        const unhandled = vi.fn();
        process.on("unhandledRejection", unhandled);
        try {
            const rejecting = () => Promise.reject(new TypeError("Illegal invocation"));
            expect(wrongReceiver(rejecting, Reflect.apply).outcome).toBe("promise");
            await new Promise(resolve => setTimeout(resolve, 20));
            expect(unhandled).not.toHaveBeenCalled();
        } finally {
            process.off("unhandledRejection", unhandled);
        }
    });

    it("cannot judge a stack without the marker", () => {
        expect(wrappedInStack(null)).toBeNull();
        expect(wrappedInStack("TypeError: x\n    at somewhere (file.js:1:2)")).toBeNull();
    });
});

describe("frame checks", () => {
    // jsdom does not index its frames as window[i]; that a real frame passes is Playwright's to show.
    it("takes nothing for a frame that is not one of the window's own", () => {
        expect(isOwnFrame(window)).toBe(false);
        expect(isOwnFrame({})).toBe(false);
        expect(isOwnFrame(null)).toBe(false);
    });

    it("reads the NaN bits through any realm's WebAssembly", () => {
        expect(nanBits(globalThis)).toEqual(nanBits());
        expect(nanBits({})).toBeNull();
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
