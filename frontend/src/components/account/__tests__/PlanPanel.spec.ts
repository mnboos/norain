import type { PlanOffer } from "@/services/billing";
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
    offer: PlanOffer;
}
// The figures the texts quote come from the server; these are made up on purpose.
const OFFER: PlanOffer = {
    freeRoutes: 2,
    plusRoutes: 20,
    plusBriefingRoutes: 5,
    freeJourneys: 1,
    plusJourneys: 10,
    plusAlternatives: 3,
    freeRandomRides: 3,
    plusRandomRides: 20,
    trialDays: 21,
    briefingLeadMinutes: 60,
    briefingsPerDay: 10,
    prices: { annual: 31, monthly: 4.5, currency: "EUR" },
};
const entitlement = ref<TestEntitlement>({
    plan: "free",
    trialEligible: true,
    maxRoutes: 2,
    routeCount: 0,
    billingConfigured: false,
    complimentaryUntil: null,
    trialEndsAt: null,
    paidSubscription: false,
    offer: OFFER,
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
    testPush: vi.fn(),
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
        offer: OFFER,
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
        // Intl puts a no-break space between the currency and the amount.
        expect(wrapper.text()).toMatch(/EUR\s31 pro Jahr/);
        expect(wrapper.text()).toMatch(/EUR\s4\.50 pro Monat/);
        expect(wrapper.text()).toContain("Ohne Kreditkarte");
        expect(wrapper.text()).toContain("noch nicht freigeschaltet");
        state.trial.mockResolvedValue({});
        const button = wrapper.findAll("button").find(b => b.text().includes("21 Tage kostenlos testen"));
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
        expect(wrapper.findAll("button").some(b => b.text().includes("21 Tage kostenlos testen"))).toBe(false);
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
        expect(result?.weatherRouting).toBe(false);
        expect(result?.trialEligible).toBe(false);
    });
});
