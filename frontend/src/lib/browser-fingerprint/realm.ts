/**
 * Lie detection. A spoofing extension or a devtools override replaces getters and methods
 * in the page's own realm; a fresh same-origin iframe brings untouched copies of
 * `Function.prototype.toString` and `Object`, which then inspect the page's functions.
 * An extension that also patches every frame can pass; that is the ceiling of any check
 * that runs in the browser it checks.
 */

/** Property names a spoofer overrides; any of them as an own property of the instance is a lie. */
const NAVIGATOR_FIELDS = [
    "userAgent",
    "platform",
    "hardwareConcurrency",
    "languages",
    "language",
    "webdriver",
    "deviceMemory",
    "maxTouchPoints",
];
const SCREEN_FIELDS = ["width", "height", "colorDepth", "availWidth", "availHeight"];

function member(target: unknown, key: string): unknown {
    if ((typeof target !== "object" || target === null) && typeof target !== "function") return undefined;
    const value: unknown = Reflect.get(target, key);
    return value;
}

/** A global constructor's prototype, by path; undefined where this browser lacks it. */
function prototypeOf(path: string[]): object | undefined {
    const ctor = path.reduce<unknown>((target, key) => member(target, key), globalThis);
    const prototype = member(ctor, "prototype");
    return (typeof prototype === "object" && prototype !== null) || typeof prototype === "function"
        ? prototype
        : undefined;
}

/** `[path, getters, methods]`: what the probes read, checked to be the browser's own. */
const TARGETS: [string[], string[], string[]][] = [
    [["Navigator"], NAVIGATOR_FIELDS, []],
    [["Screen"], SCREEN_FIELDS, []],
    [["HTMLCanvasElement"], [], ["toDataURL", "getContext"]],
    [["CanvasRenderingContext2D"], [], ["getImageData", "measureText", "fillText"]],
    [["WebGLRenderingContext"], [], ["getParameter", "getExtension", "getSupportedExtensions"]],
    [["AudioBuffer"], [], ["getChannelData"]],
    [["Date"], [], ["getTimezoneOffset"]],
    [["Intl", "DateTimeFormat"], [], ["resolvedOptions"]],
    [["Function"], [], ["toString"]],
];

type Source = (fn: unknown) => string | null;

/** A `toString` for functions, taken from another realm so the page cannot have patched it. */
export function sourceReader(realm: unknown): Source {
    const toString = member(member(member(realm, "Function"), "prototype"), "toString");
    if (typeof toString !== "function") throw new Error("No Function.prototype.toString in that realm");
    return fn => {
        try {
            return String(Reflect.apply(toString, fn, []));
        } catch {
            return null;
        }
    };
}

/** Native code, carrying its own name. A Proxy prints without one; a JS function prints its source. */
export function isNativeFunction(fn: unknown, name: string, source: Source): boolean {
    if (typeof fn !== "function" || Object.hasOwn(fn, "prototype")) return false;
    const text = source(fn);
    return (
        text !== null &&
        new RegExp(`^function (?:get |set )?${name}\\(\\)\\s*\\{\\s*\\[native code\\]\\s*\\}$`).test(text)
    );
}

/** The watched names an object carries itself instead of inheriting them. */
export function ownOverrides(target: object, names: string[], ownNames = Object.getOwnPropertyNames): string[] {
    return ownNames(target).filter(name => names.includes(name));
}

/** Every getter or method below that is not the browser's own, as `Prototype.name`. */
export function tamperedNatives(realm: Window): string[] {
    const source = sourceReader(realm);
    // The frame's own Object: the page may have patched this realm's getOwnPropertyDescriptor.
    const cleanObject = member(realm, "Object");
    const describe = member(cleanObject, "getOwnPropertyDescriptor");
    const ownNames = member(cleanObject, "getOwnPropertyNames");
    const cleanPrototypeOf = member(cleanObject, "getPrototypeOf");
    if (typeof describe !== "function" || typeof ownNames !== "function" || typeof cleanPrototypeOf !== "function") {
        throw new Error("No clean Object in that realm");
    }
    const descriptor = (target: object, name: string): PropertyDescriptor | undefined => {
        const found: unknown = Reflect.apply(describe, undefined, [target, name]);
        return typeof found === "object" && found !== null ? found : undefined;
    };
    const names = (target: object): string[] => {
        const found: unknown = Reflect.apply(ownNames, undefined, [target]);
        return Array.isArray(found) ? found.filter((item): item is string => typeof item === "string") : [];
    };
    const tampered: string[] = [];
    for (const [path, getters, methods] of TARGETS) {
        const prototype = prototypeOf(path);
        const label = path.join(".");
        if (!prototype) continue;
        for (const name of getters) {
            const found = descriptor(prototype, name);
            if (!found) continue; // not in this browser (deviceMemory outside Chromium)
            const getter: unknown = Reflect.get(found, "get");
            if (!isNativeFunction(getter, name, source) || "value" in found) tampered.push(`${label}.${name}`);
        }
        for (const name of methods) {
            const found = descriptor(prototype, name);
            if (!found) continue;
            if (!isNativeFunction(found.value, name, source)) tampered.push(`${label}.${name}`);
        }
    }
    for (const [label, instance, fields] of [
        ["Navigator", navigator, NAVIGATOR_FIELDS],
        ["Screen", screen, SCREEN_FIELDS],
    ] as const) {
        tampered.push(...ownOverrides(instance, fields, names).map(name => `${label.toLowerCase()}.own.${name}`));
        const prototype = prototypeOf([label]);
        if (prototype && Reflect.apply(cleanPrototypeOf, undefined, [instance]) !== prototype) {
            tampered.push(`${label.toLowerCase()}.prototype`);
        }
    }
    return tampered.sort();
}

/** A hidden same-origin frame, for the duration of `use`. */
export async function withFrame<T>(use: (realm: Window) => T | Promise<T>): Promise<T> {
    const frame = document.createElement("iframe");
    frame.setAttribute("aria-hidden", "true");
    frame.tabIndex = -1;
    frame.style.cssText = "position:absolute;width:0;height:0;border:0;visibility:hidden";
    document.body.append(frame);
    try {
        const realm = frame.contentWindow;
        if (!realm) throw new Error("Frame unavailable");
        return await use(realm);
    } finally {
        frame.remove();
    }
}

/** The frame's time zone, through its own Intl. */
export function frameTimeZone(realm: Window): string | null {
    const format = member(member(realm, "Intl"), "DateTimeFormat");
    if (typeof format !== "function") return null;
    const instance: unknown = Reflect.construct(format, []);
    const resolvedOptions = member(instance, "resolvedOptions");
    if (typeof resolvedOptions !== "function") return null;
    const zone = member(Reflect.apply(resolvedOptions, instance, []), "timeZone");
    return typeof zone === "string" ? zone : null;
}

/**
 * Draw exact colours and read them back. Browsers that add canvas noise (Brave, Safari's
 * advanced fingerprinting protection, Firefox's resistFingerprinting) change some pixels.
 */
export function canvasIntegrity(): string {
    const canvas = document.createElement("canvas");
    canvas.width = 16;
    canvas.height = 16;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) throw new Error("Canvas unavailable");
    ctx.fillStyle = "rgb(17, 34, 51)";
    ctx.fillRect(0, 0, 16, 16);
    ctx.fillStyle = "rgb(204, 102, 0)";
    ctx.fillRect(4, 4, 8, 8);
    const data = ctx.getImageData(0, 0, 16, 16).data;
    let wrong = 0;
    for (let pixel = 0; pixel < 256; pixel++) {
        const x = pixel % 16;
        const y = Math.floor(pixel / 16);
        const inner = x >= 4 && x < 12 && y >= 4 && y < 12;
        const expected = inner ? [204, 102, 0, 255] : [17, 34, 51, 255];
        if (expected.some((value, channel) => data[pixel * 4 + channel] !== value)) wrong++;
    }
    return wrong === 0 ? "clean" : `noise:${wrong}`;
}

function errorMessage(run: () => unknown): string {
    try {
        run();
        return "";
    } catch (error) {
        return error instanceof Error ? error.message : "thrown";
    }
}

/**
 * The engine's own wording for three errors and its stack format. V8, SpiderMonkey and
 * JavaScriptCore each word them differently, and a user-agent override changes none of it.
 */
export function engineTells(): string[] {
    const stack = new Error("probe").stack ?? "";
    return [
        errorMessage(() => new Array<number>(-1).length),
        errorMessage(() => (1).toFixed(101)),
        errorMessage(() => "a".repeat(-1)),
        /\n\s+at /.test(stack) ? "v8" : "other",
    ];
}
