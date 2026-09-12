import { request } from "@/services/http";

export interface Entitlements {
    plan: string;
    /** `null` means unlimited. */
    maxRoutes: number | null;
    ensembleUncertainty: boolean;
    routeCount: number;
    status: string;
    currentPeriodEnd: string | null;
    cancelAtPeriodEnd: boolean;
    /** False when the server has no Stripe keys — hide the upgrade buttons then. */
    billingConfigured: boolean;
}

export const billingApi = {
    entitlements: () => request<Entitlements>("/api/billing/entitlements"),
    /** Both return a Stripe-hosted URL to send the browser to. */
    checkout: () => request<{ url: string }>("/api/billing/checkout", "POST", {}),
    portal: () => request<{ url: string }>("/api/billing/portal", "POST", {}),
};
