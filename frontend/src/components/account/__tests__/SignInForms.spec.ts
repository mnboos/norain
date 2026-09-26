import { flushPromises, mount } from "@vue/test-utils";
import { Quasar } from "quasar";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";

import type { AllauthReply } from "@/services/http";

import SignInForms from "../SignInForms.vue";

const pending: AllauthReply = {
    status: 401,
    data: { flows: [{ id: "verify_email", is_pending: true }] },
    meta: { is_authenticated: false },
};
const signedIn: AllauthReply = { status: 200, data: {}, meta: { is_authenticated: true } };

const signup = vi.fn<(email: string) => Promise<AllauthReply>>();
const verifyEmailCode = vi.fn<(code: string) => Promise<AllauthReply>>();
vi.mock("@/services/auth", async importOriginal => ({
    ...(await importOriginal<typeof import("@/services/auth")>()),
    authApi: {
        signup: (email: string) => signup(email),
        verifyEmailCode: (code: string) => verifyEmailCode(code),
    },
}));

async function mountAt(path: string) {
    const router = createRouter({
        history: createMemoryHistory(),
        routes: [{ path: "/account", component: { template: "<div />" } }],
    });
    await router.push(path);
    const wrapper = mount(SignInForms, { global: { plugins: [Quasar, router] } });
    await flushPromises();
    return wrapper;
}

async function submitWith(wrapper: Awaited<ReturnType<typeof mountAt>>, value: string) {
    await wrapper.find("input").setValue(value);
    await wrapper.find("form").trigger("submit");
    await flushPromises();
}

describe("sign-up step 1", () => {
    beforeEach(() => {
        signup.mockReset();
        verifyEmailCode.mockReset();
    });

    it("asks for the mailed code in the same window and signs in with it", async () => {
        signup.mockResolvedValue(pending);
        verifyEmailCode.mockResolvedValue(signedIn);
        const wrapper = await mountAt("/account?mode=signup");

        await submitWith(wrapper, "rider@example.test");
        expect(signup).toHaveBeenCalledWith("rider@example.test");
        expect(wrapper.text()).toContain("E-Mail bestätigen");
        expect(wrapper.text()).toContain("an rider@example.test");
        expect(wrapper.find("input").attributes("autocomplete")).toBe("one-time-code");

        await submitWith(wrapper, " ABCD-EFGH ");
        expect(verifyEmailCode).toHaveBeenCalledWith("ABCD-EFGH");
        expect(wrapper.emitted("signed-in")).toHaveLength(1);
    });

    it("sends the user to sign-in by code once the sign-up code is spent", async () => {
        signup.mockResolvedValue(pending);
        const { ApiError } = await import("@/services/http");
        verifyEmailCode.mockRejectedValue(new ApiError("Conflict", 409));
        const wrapper = await mountAt("/account?mode=signup");

        await submitWith(wrapper, "rider@example.test");
        await submitWith(wrapper, "ABCD-EFGH");
        expect(wrapper.text()).toContain("Mit Code anmelden");
        expect(wrapper.text()).toContain("Fordere einen Anmeldecode an.");
        expect(wrapper.emitted("signed-in")).toBeUndefined();
    });
});
