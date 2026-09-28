import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// Read as plain JSON: the vue-i18n plugin turns an imported catalog into compiled messages.
// Vitest runs from the frontend folder (vitest.config.ts `root`).
const catalog = (name: string): unknown =>
    JSON.parse(readFileSync(resolve("src/locales", `${name}.json`), "utf-8"));
const de = catalog("de");
const en = catalog("en");

/** Every leaf of a catalog as "a.b.c" -> message. */
function flatten(node: unknown, prefix = ""): Map<string, string> {
    const out = new Map<string, string>();
    if (typeof node === "string") {
        out.set(prefix, node);
        return out;
    }
    if (typeof node === "object" && node !== null) {
        for (const [key, value] of Object.entries(node)) {
            for (const [k, v] of flatten(value, prefix ? `${prefix}.${key}` : key)) out.set(k, v);
        }
    }
    return out;
}

/** The named placeholders of a message, e.g. {n}, {name}. */
function placeholders(message: string): string[] {
    return [...message.matchAll(/\{(\w+)\}/g)].map(m => m[1] ?? "").sort();
}

describe("message catalogs", () => {
    const german = flatten(de);
    const english = flatten(en);

    it("have exactly the same keys, so nothing falls back to German unseen", () => {
        expect([...english.keys()].sort()).toEqual([...german.keys()].sort());
    });

    it("use the same placeholders and plural forms in both languages", () => {
        for (const [key, message] of german) {
            const other = english.get(key) ?? "";
            expect(placeholders(other), key).toEqual(placeholders(message));
            expect(other.split(" | ").length, key).toEqual(message.split(" | ").length);
        }
    });
});
