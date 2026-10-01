import { isRecord, type Parse, request } from "@/services/http";

export interface Entitlements {
    plan: string;
    maxRoutes: number | null;
    ensembleUncertainty: boolean;
    departureComparison: boolean;
    weatherRouting: boolean;
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
    /** What Free and Plus include and cost; null from an API older than it. */
    offer: PlanOffer | null;
}

/** The figures the plan texts quote. They come from the server (core/entitlements.py) only. */
export interface PlanOffer {
    freeRoutes: number;
    plusRoutes: number;
    plusBriefingRoutes: number;
    freeJourneys: number;
    plusJourneys: number;
    plusAlternatives: number;
    freeRandomRides: number;
    plusRandomRides: number;
    trialDays: number;
    briefingLeadMinutes: number;
    briefingsPerDay: number;
    prices: { annual: number; monthly: number; currency: string };
}

const number = (value: unknown) => (typeof value === "number" ? value : null);

function parseOffer(offer: unknown, prices: unknown): PlanOffer | null {
    if (!isRecord(offer) || !isRecord(prices)) return null;
    const parsed = {
        freeRoutes: number(offer.freeRoutes),
        plusRoutes: number(offer.plusRoutes),
        plusBriefingRoutes: number(offer.plusBriefingRoutes),
        freeJourneys: number(offer.freeJourneys),
        plusJourneys: number(offer.plusJourneys),
        plusAlternatives: number(offer.plusAlternatives),
        freeRandomRides: number(offer.freeRandomRides),
        plusRandomRides: number(offer.plusRandomRides),
        trialDays: number(offer.trialDays),
        briefingLeadMinutes: number(offer.briefingLeadMinutes),
        briefingsPerDay: number(offer.briefingsPerDay),
    };
    const { annual, monthly, currency } = prices;
    if (typeof annual !== "number" || typeof monthly !== "number" || typeof currency !== "string") return null;
    if (!isComplete(parsed)) return null;
    return { ...parsed, prices: { annual, monthly, currency } };
}

function isComplete<T extends Record<string, number | null>>(
    value: T,
): value is { [K in keyof T]: NonNullable<T[K]> } {
    return Object.values(value).every(v => v !== null);
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
        weatherRouting: value.weatherRouting === true,
        maxBriefingRoutes: typeof value.maxBriefingRoutes === "number" ? value.maxBriefingRoutes : 0,
        trialEligible: value.trialEligible === true,
        trialEndsAt: typeof value.trialEndsAt === "string" ? value.trialEndsAt : null,
        complimentaryUntil: typeof value.complimentaryUntil === "string" ? value.complimentaryUntil : null,
        paidSubscription: value.paidSubscription === true,
        offer: parseOffer(value.offer, value.prices),
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
