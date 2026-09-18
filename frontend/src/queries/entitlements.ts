import { useQuery } from "@tanstack/vue-query";

import { billingApi } from "@/services/billing";
import { useSession } from "@/composables/useSession";
import { getMetricUserId, identifyPlan } from "@/services/telemetry";

export const entitlementKeys = {
    all: ["entitlements"] as const,
};

export function useEntitlementsQuery() {
    const { isAuthenticated } = useSession();
    return useQuery({
        queryKey: entitlementKeys.all,
        queryFn: async () => {
            const userId = getMetricUserId();
            const result = await billingApi.entitlements();
            identifyPlan(result.plan, userId);
            return result;
        },
        enabled: isAuthenticated,
        staleTime: 60_000,
    });
}
