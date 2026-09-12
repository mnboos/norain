import { useQuery } from "@tanstack/vue-query";

import { billingApi } from "@/services/billing";
import { useSession } from "@/composables/useSession";

export const entitlementKeys = {
    all: ["entitlements"] as const,
};

export function useEntitlementsQuery() {
    const { isAuthenticated } = useSession();
    return useQuery({
        queryKey: entitlementKeys.all,
        queryFn: () => billingApi.entitlements(),
        enabled: isAuthenticated,
        staleTime: 60_000,
    });
}
