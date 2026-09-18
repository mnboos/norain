import { afterEach, describe, expect, it, vi } from "vitest";
import { authApi } from "../auth";

describe("session identity compatibility", () => {
    afterEach(() => {
        vi.unstubAllGlobals();
    });

    it.each([
        { id: "42", email: "rider@example.test", username: "rider" },
        { email: "rider@example.test", username: "rider" },
    ])("accepts current and older user payloads: %j", async user => {
        vi.stubGlobal(
            "fetch",
            vi.fn().mockResolvedValue(
                new Response(JSON.stringify({ authenticated: true, user }), {
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
