import { flushPromises, mount } from "@vue/test-utils";
import { Quasar } from "quasar";
import { VueQueryPlugin, QueryClient } from "@tanstack/vue-query";
import { computed, ref } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PlanPanel from "../PlanPanel.vue";
import { parseEntitlements } from "@/services/billing";

const state = vi.hoisted(() => ({
    trial: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
    preferences: vi.fn<() => Promise<unknown>>(),
}));
interface TestEntitlement {
    plan: string;
    trialEligible: boolean;
    maxRoutes: number;
    routeCount: number;
    billingConfigured: boolean;
    complimentaryUntil: string | null;
    trialEndsAt: string | null;
    paidSubscription: boolean;
}
const entitlement = ref<TestEntitlement>({
    plan: "free",
    trialEligible: true,
    maxRoutes: 2,
    routeCount: 0,
    billingConfigured: false,
    complimentaryUntil: null,
    trialEndsAt: null,
    paidSubscription: false,
});
vi.mock("@/composables/useEntitlements", () => ({
    useEntitlements: () => ({ entitlements: entitlement, isPro: computed(() => entitlement.value.plan === "pro") }),
}));
vi.mock("@/services/billing", async importOriginal => ({
    ...(await importOriginal<typeof import("@/services/billing")>()),
    billingApi: { trial: (...args: unknown[]) => state.trial(...args) },
}));
vi.mock("@/services/briefings", () => ({
    briefingsApi: { preferences: () => state.preferences() },
    pushSupported: () => false,
    enablePush: vi.fn(),
    disablePush: vi.fn(),
}));

beforeEach(() => {
    entitlement.value = {
        plan: "free",
        trialEligible: true,
        maxRoutes: 2,
        routeCount: 0,
        billingConfigured: false,
        complimentaryUntil: null,
        trialEndsAt: null,
        paidSubscription: false,
    };
    state.trial.mockReset();
    state.preferences.mockResolvedValue({
        routes: [],
        recent: [],
        pushPublicKey: "",
        emailConfigured: false,
        pushDeviceCount: 0,
    });
});
function render() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    return mount(PlanPanel, { global: { plugins: [Quasar, [VueQueryPlugin, { queryClient: client }]] } });
}

describe("Plus account", () => {
    it("requires an explicit trial click and keeps checkout disabled during beta", async () => {
        const wrapper = render();
        await flushPromises();
        expect(state.trial).not.toHaveBeenCalled();
        expect(wrapper.text()).toContain("29 € pro Jahr");
        expect(wrapper.text()).toContain("Ohne Kreditkarte");
        expect(wrapper.text()).toContain("noch nicht freigeschaltet");
        state.trial.mockResolvedValue({});
        const button = wrapper.findAll("button").find(b => b.text().includes("14 Tage kostenlos testen"));
        expect(button).toBeDefined();
        if (!button) throw new Error("Trial button missing");
        await button.trigger("click");
        await flushPromises();
        expect(state.trial).toHaveBeenCalledOnce();
        wrapper.unmount();
    });
    it("explains a colleague's complimentary access without implying a payment", async () => {
        entitlement.value = {
            ...entitlement.value,
            plan: "pro",
            trialEligible: false,
            maxRoutes: 20,
            complimentaryUntil: "2099-01-01T00:00:00Z",
        };
        const wrapper = render();
        await flushPromises();
        expect(wrapper.text()).toContain("Plus geschenkt bis");
        expect(wrapper.text()).toContain("Keine automatische Zahlung");
        expect(wrapper.findAll("button").some(b => b.text().includes("14 Tage kostenlos testen"))).toBe(false);
        wrapper.unmount();
    });
    it("fails closed on paid capabilities from an older server", () => {
        const result = parseEntitlements({
            plan: "pro",
            maxRoutes: null,
            routeCount: 3,
            ensembleUncertainty: true,
            status: "active",
            currentPeriodEnd: null,
            cancelAtPeriodEnd: false,
            billingConfigured: false,
        });
        expect(result?.departureComparison).toBe(false);
        expect(result?.trialEligible).toBe(false);
    });
});
