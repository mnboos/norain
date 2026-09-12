import { isRecord, type Parse, request } from "@/services/http";

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

const parseEntitlements: Parse<Entitlements> = (value) => {
    if (!isRecord(value)) return null;
    const {
        plan, maxRoutes, ensembleUncertainty, routeCount, status, currentPeriodEnd, cancelAtPeriodEnd, billingConfigured,
    } = value;
    if (
        typeof plan !== "string" ||
        (maxRoutes !== null && typeof maxRoutes !== "number") ||
        typeof ensembleUncertainty !== "boolean" ||
        typeof routeCount !== "number" ||
        typeof status !== "string" ||
        (currentPeriodEnd !== null && typeof currentPeriodEnd !== "string") ||
        typeof cancelAtPeriodEnd !== "boolean" ||
        typeof billingConfigured !== "boolean"
    ) {
        return null;
    }
    return {
        plan, maxRoutes, ensembleUncertainty, routeCount, status, currentPeriodEnd, cancelAtPeriodEnd, billingConfigured,
    };
};

const parseRedirect: Parse<{ url: string }> = (value) =>
    isRecord(value) && typeof value.url === "string" ? { url: value.url } : null;

export const billingApi = {
    entitlements: () => request<Entitlements>("/api/billing/entitlements", parseEntitlements),
    /** Both return a Stripe-hosted URL to send the browser to. */
    checkout: () => request<{ url: string }>("/api/billing/checkout", parseRedirect, "POST", {}),
    portal: () => request<{ url: string }>("/api/billing/portal", parseRedirect, "POST", {}),
};
