import { flushPromises, mount } from "@vue/test-utils";
import { Quasar } from "quasar";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";

import type { BikeProfile, SessionState, UsernameCheck } from "@/services/auth";

import CompleteSignupForm from "../CompleteSignupForm.vue";
import { samePassword } from "../passwordRules";

const completeSignup = vi.fn<(username: string, password: string, profile: BikeProfile) => Promise<SessionState>>();
const usernameAvailable = vi.fn<(username: string) => Promise<UsernameCheck>>();
vi.mock("@/services/auth", () => ({
    authApi: {
        completeSignup: (username: string, password: string, profile: BikeProfile) =>
            completeSignup(username, password, profile),
        usernameAvailable: (username: string) => usernameAvailable(username),
    },
}));

describe("samePassword", () => {
    it("accepts only the same text", () => {
        const rule = samePassword(ref("Correct horse"));
        expect(rule("Correct horse")).toBe(true);
        expect(rule("Correct hose")).toBe("Die Passwörter stimmen nicht überein.");
    });
});

describe("step 2 of sign-up", () => {
    beforeEach(() => {
        vi.useFakeTimers();
        completeSignup.mockReset();
        completeSignup.mockResolvedValue({ authenticated: true, user: null });
        usernameAvailable.mockReset();
        usernameAvailable.mockResolvedValue({ available: true, detail: "" });
    });
    afterEach(() => vi.useRealTimers());

    function mountForm() {
        return mount(CompleteSignupForm, {
            props: { email: "rider.one@example.test", defaultProfile: "ebike" },
            global: { plugins: [Quasar] },
        });
    }

    async function submit(wrapper: ReturnType<typeof mountForm>) {
        await wrapper.find("form").trigger("submit");
        await flushPromises();
    }

    it("suggests the email's local part and checks it once typing pauses", async () => {
        const wrapper = mountForm();
        expect(wrapper.find("input").element.value).toBe("rider.one");
        expect(usernameAvailable).not.toHaveBeenCalled();

        await vi.runAllTimersAsync();
        expect(usernameAvailable).toHaveBeenCalledTimes(1);
        expect(usernameAvailable).toHaveBeenCalledWith("rider.one");
    });

    it("shows a taken username and does not submit it", async () => {
        usernameAvailable.mockResolvedValue({ available: false, detail: "This username is already taken." });
        const wrapper = mountForm();
        await vi.runAllTimersAsync();
        await flushPromises();

        expect(wrapper.text()).toContain("This username is already taken.");
        expect(wrapper.find("button[type=submit]").attributes("disabled")).toBeDefined();
    });

    it("submits without a password", async () => {
        const wrapper = mountForm();
        // Only username and password until a password is typed: no repeat field yet.
        expect(wrapper.findAll("input")).toHaveLength(2);

        await submit(wrapper);
        expect(completeSignup).toHaveBeenCalledWith("rider.one", "", "ebike");
    });

    it("does not submit when the two passwords differ", async () => {
        const wrapper = mountForm();
        await wrapper.findAll("input")[1]?.setValue("Correct horse battery");
        const inputs = wrapper.findAll("input");
        expect(inputs).toHaveLength(3);
        await inputs[2]?.setValue("Correct hose battery");

        await submit(wrapper);
        expect(completeSignup).not.toHaveBeenCalled();
        expect(wrapper.text()).toContain("Die Passwörter stimmen nicht überein.");
    });

    it("submits a matching password with the chosen profile", async () => {
        const wrapper = mountForm();
        await wrapper.findAll("input")[1]?.setValue("Correct horse battery");
        await wrapper.findAll("input")[2]?.setValue("Correct horse battery");
        const bike = wrapper.findAll("button").find(button => button.text().includes("Velo"));
        await bike?.trigger("click");

        await submit(wrapper);
        expect(completeSignup).toHaveBeenCalledWith("rider.one", "Correct horse battery", "bike");
    });
});
