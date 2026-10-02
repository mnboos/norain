import { describe, expect, it } from "vitest";
import { semanticChecks } from "../semantics";

describe("causal checks", () => {
    it("reports unavailable APIs without throwing or exporting parameters", () => {
        const doc = {
            createElement: () => {
                throw new Error("blocked");
            },
        };
        // eslint-disable-next-line @typescript-eslint/consistent-type-assertions -- Minimal blocked document fixture.
        const result = semanticChecks("nonce", doc as unknown as Document);
        expect(result).toEqual({
            font: "unavailable",
            viewport: "unavailable",
            exception: "unavailable",
            serialization: "unavailable",
        });
    });
});
