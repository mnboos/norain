import { ResponseError } from "@norain/api/runtime";
import { afterEach, describe, expect, it, vi } from "vitest";

import { allauthRequest, ApiError, isQuotaExceeded } from "../http";

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

describe("allauthRequest", () => {
    afterEach(() => {
        vi.unstubAllGlobals();
    });

    function answer(status: number, body: unknown) {
        const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(body), { status }));
        vi.stubGlobal("fetch", fetchMock);
        return fetchMock;
    }

    it("returns a 200 as a reply", async () => {
        answer(200, { status: 200, data: { email: "a@example.test" }, meta: { is_authenticated: true } });
        await expect(allauthRequest("/auth/session")).resolves.toEqual({
            status: 200,
            data: { email: "a@example.test" },
            meta: { is_authenticated: true },
        });
    });

    it("returns a 401 as a reply too: in allauth it means a step is pending", async () => {
        const flows = [{ id: "verify_email", is_pending: true }];
        answer(401, { status: 401, data: { flows }, meta: { is_authenticated: false } });
        const reply = await allauthRequest("/auth/signup", "POST", { email: "a@example.test" });
        expect(reply.status).toBe(401);
        expect(reply.data.flows).toEqual(flows);
    });

    it("throws allauth's first error message", async () => {
        answer(400, { status: 400, errors: [{ message: "Invalid or expired key.", code: "invalid_or_expired_key" }] });
        await expect(allauthRequest("/auth/email/verify", "POST", { key: "x" })).rejects.toMatchObject({
            message: "Invalid or expired key.",
            status: 400,
            code: "invalid_or_expired_key",
        });
    });

    it("throws the axes lockout detail", async () => {
        answer(429, { detail: "Zu viele fehlgeschlagene Anmeldeversuche." });
        await expect(allauthRequest("/auth/login", "POST", {})).rejects.toBeInstanceOf(ApiError);
        await expect(allauthRequest("/auth/login", "POST", {})).rejects.toMatchObject({ status: 429 });
    });

    it("sends the CSRF header on a DELETE without a body", async () => {
        const fetchMock = answer(401, { status: 401, data: {}, meta: { is_authenticated: false } });
        await allauthRequest("/auth/session", "DELETE");
        const init = fetchMock.mock.calls[0]?.[1];
        expect(init?.method).toBe("DELETE");
        expect(init?.body).toBeUndefined();
        expect(new Headers(init?.headers).has("X-CSRFToken")).toBe(true);
    });
});
