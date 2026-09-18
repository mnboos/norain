import { flushPromises, mount } from "@vue/test-utils";
import { Quasar } from "quasar";
import { describe, expect, it, vi } from "vitest";
import { ref } from "vue";

import type { SessionState } from "@/services/auth";

import CompleteSignupForm from "../CompleteSignupForm.vue";
import { samePassword } from "../passwordRules";

const completeSignup = vi.fn<(username: string, password: string) => Promise<SessionState>>();
vi.mock("@/services/auth", () => ({
    authApi: { completeSignup: (username: string, password: string) => completeSignup(username, password) },
}));

describe("samePassword", () => {
    it("accepts only the same text", () => {
        const rule = samePassword(ref("Correct horse"));
        expect(rule("Correct horse")).toBe(true);
        expect(rule("Correct hose")).toBe("Die Passwörter stimmen nicht überein.");
    });
});

describe("step 2 of sign-up", () => {
    async function submitWith(password: string, repeat: string) {
        completeSignup.mockReset();
        completeSignup.mockResolvedValue({ authenticated: true, user: null });
        const wrapper = mount(CompleteSignupForm, {
            props: { suggestedUsername: "rider" },
            global: { plugins: [Quasar] },
        });
        const inputs = wrapper.findAll("input");
        expect(inputs).toHaveLength(3);
        await inputs[1]?.setValue(password);
        await inputs[2]?.setValue(repeat);
        await wrapper.find("form").trigger("submit");
        await flushPromises();
        return wrapper;
    }

    it("does not submit when the two passwords differ", async () => {
        const wrapper = await submitWith("Correct horse battery", "Correct hose battery");
        expect(completeSignup).not.toHaveBeenCalled();
        expect(wrapper.text()).toContain("Die Passwörter stimmen nicht überein.");
    });

    it("submits when they match", async () => {
        await submitWith("Correct horse battery", "Correct horse battery");
        expect(completeSignup).toHaveBeenCalledWith("rider", "Correct horse battery");
    });
});
