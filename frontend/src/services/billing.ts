import { isRecord, type Parse, request } from "@/services/http";

export interface Entitlements {
    plan: string;
    maxRoutes: number | null;
    ensembleUncertainty: boolean;
    departureComparison: boolean;
    maxBriefingRoutes: number;
    routeCount: number;
    status: string;
    currentPeriodEnd: string | null;
    cancelAtPeriodEnd: boolean;
    billingConfigured: boolean;
    trialEligible: boolean;
    trialEndsAt: string | null;
    complimentaryUntil: string | null;
    paidSubscription: boolean;
}

export const parseEntitlements: Parse<Entitlements> = value => {
    if (!isRecord(value)) return null;
    if (
        typeof value.plan !== "string" ||
        (value.maxRoutes !== null && typeof value.maxRoutes !== "number") ||
        typeof value.ensembleUncertainty !== "boolean" ||
        typeof value.routeCount !== "number" ||
        typeof value.status !== "string" ||
        typeof value.cancelAtPeriodEnd !== "boolean" ||
        typeof value.billingConfigured !== "boolean" ||
        (value.currentPeriodEnd !== null && typeof value.currentPeriodEnd !== "string")
    )
        return null;
    // Defaults permit a rolling deployment against an older API.
    return {
        plan: value.plan,
        maxRoutes: value.maxRoutes,
        ensembleUncertainty: value.ensembleUncertainty,
        routeCount: value.routeCount,
        status: value.status,
        currentPeriodEnd: value.currentPeriodEnd,
        cancelAtPeriodEnd: value.cancelAtPeriodEnd,
        billingConfigured: value.billingConfigured,
        departureComparison: value.departureComparison === true,
        maxBriefingRoutes: typeof value.maxBriefingRoutes === "number" ? value.maxBriefingRoutes : 0,
        trialEligible: value.trialEligible === true,
        trialEndsAt: typeof value.trialEndsAt === "string" ? value.trialEndsAt : null,
        complimentaryUntil: typeof value.complimentaryUntil === "string" ? value.complimentaryUntil : null,
        paidSubscription: value.paidSubscription === true,
    };
};

const parseRedirect: Parse<{ url: string }> = value =>
    isRecord(value) && typeof value.url === "string" ? { url: value.url } : null;

export const billingApi = {
    entitlements: () => request("/api/billing/entitlements", parseEntitlements),
    trial: () => request("/api/billing/trial", parseEntitlements, "POST", {}),
    checkout: (interval: "annual" | "monthly" = "annual") =>
        request("/api/billing/checkout", parseRedirect, "POST", { interval }),
    portal: () => request("/api/billing/portal", parseRedirect, "POST", {}),
    selectFreeRoutes: (routeIds: string[]) =>
        request(
            "/api/billing/free-routes",
            value => (isRecord(value) && Array.isArray(value.routeIds) ? value.routeIds : null),
            "POST",
            { routeIds },
        ),
};
