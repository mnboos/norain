import { ResponseError } from "@norain/api/runtime";
import { describe, expect, it } from "vitest";

import { ApiError, isQuotaExceeded } from "../http";

describe("isQuotaExceeded", () => {
    it("recognises a 402 from the generated client", () => {
        // The route-creation dialog hangs off this: the generated client is what throws
        // when POST /api/routes is refused, so its error shape has to be handled.
        expect(isQuotaExceeded(new ResponseError(new Response(null, { status: 402 })))).toBe(true);
    });

    it("recognises a 402 from the hand-written endpoints", () => {
        expect(isQuotaExceeded(new ApiError("Tarifgrenze", 402))).toBe(true);
    });

    it("ignores other failures, so they still surface as real errors", () => {
        expect(isQuotaExceeded(new ResponseError(new Response(null, { status: 500 })))).toBe(false);
        expect(isQuotaExceeded(new ApiError("nope", 401))).toBe(false);
        expect(isQuotaExceeded(new Error("network down"))).toBe(false);
        expect(isQuotaExceeded(undefined)).toBe(false);
    });
});
