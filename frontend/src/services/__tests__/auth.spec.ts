import { afterEach, describe, expect, it, vi } from "vitest";
import { authApi, pendingFlows, signedIn } from "../auth";
import type { AllauthReply } from "../http";

describe("session identity compatibility", () => {
    afterEach(() => {
        vi.unstubAllGlobals();
    });

    it.each([
        {
            payload: { id: "42", email: "rider@example.test", username: "rider", signup_complete: false },
            user: { id: "42", email: "rider@example.test", username: "rider", signupComplete: false },
        },
        // An older backend has no second sign-up step, so its accounts count as complete.
        {
            payload: { email: "rider@example.test", username: "rider" },
            user: { email: "rider@example.test", username: "rider", signupComplete: true },
        },
    ])("accepts current and older user payloads: %j", async ({ payload, user }) => {
        vi.stubGlobal(
            "fetch",
            vi.fn().mockResolvedValue(
                new Response(JSON.stringify({ authenticated: true, user: payload }), {
                    headers: { "Content-Type": "application/json" },
                }),
            ),
        );
        await expect(authApi.session()).resolves.toEqual({ authenticated: true, user });
    });

    it("accepts a signed-out session", async () => {
        vi.stubGlobal(
            "fetch",
            vi.fn().mockResolvedValue(
                new Response(JSON.stringify({ authenticated: false, user: null }), {
                    headers: { "Content-Type": "application/json" },
                }),
            ),
        );
        await expect(authApi.session()).resolves.toEqual({ authenticated: false, user: null });
    });
});

describe("allauth replies", () => {
    const reply = (
        status: number,
        data: Record<string, unknown> = {},
        meta: Record<string, unknown> = {},
    ): AllauthReply => ({
        status,
        data,
        meta,
    });

    it("lists only the pending flows", () => {
        const flows = [{ id: "login" }, { id: "verify_email", is_pending: true }, { id: "login_by_code" }];
        expect(pendingFlows(reply(401, { flows }))).toEqual(["verify_email"]);
        expect(pendingFlows(reply(401))).toEqual([]);
    });

    it("counts only a 200 with an authenticated session as signed in", () => {
        expect(signedIn(reply(200, {}, { is_authenticated: true }))).toBe(true);
        expect(signedIn(reply(401, {}, { is_authenticated: false }))).toBe(false);
        expect(signedIn(reply(200, {}, {}))).toBe(false);
    });

    it("sends every sign-in identity as `username`", async () => {
        const fetchMock = vi
            .fn()
            .mockResolvedValue(
                new Response(JSON.stringify({ status: 200, data: {}, meta: { is_authenticated: true } }), {
                    status: 200,
                }),
            );
        vi.stubGlobal("fetch", fetchMock);
        await authApi.login("rider@example.test", "secret");
        expect(fetchMock).toHaveBeenCalledWith(
            expect.stringMatching(/\/api\/allauth\/browser\/v1\/auth\/login$/),
            expect.objectContaining({ body: JSON.stringify({ username: "rider@example.test", password: "secret" }) }),
        );
        vi.unstubAllGlobals();
    });
});
