import { computed } from "vue";
import { useEntitlementsQuery } from "@/queries/entitlements";

/**
 * The current account's tier and how much of it is used.
 *
 * The server is the authority — everything here only decides what to *show*. Every limit
 * is enforced again in the API, so a stale cache can never buy anything.
 */
export function useEntitlements() {
    const query = useEntitlementsQuery();

    const entitlements = computed(() => query.data.value ?? null);
    const isPro = computed(() => entitlements.value?.plan === "pro");
    const maxRoutes = computed(() => entitlements.value?.maxRoutes ?? null);
    const atRouteLimit = computed(() => {
        const limit = maxRoutes.value;
        return limit != null && (entitlements.value?.routeCount ?? 0) >= limit;
    });

    return { query, entitlements, isPro, maxRoutes, atRouteLimit };
}
