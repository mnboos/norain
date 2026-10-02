import { computed } from "vue";
import { useQuery } from "@tanstack/vue-query";

import { intlLocale } from "@/i18n";
import { billingApi, type PlanOffer } from "@/services/billing";

/**
 * What Free and Plus include and cost, as the server states it (core/entitlements.py). The
 * plan texts take these figures as parameters, so no price, limit or trial length is written
 * into a catalog. The endpoint answers anonymous visitors too, so the landing page reads it.
 */
export function usePlanOffer() {
    const query = useQuery({
        queryKey: ["planOffer"],
        queryFn: async () => (await billingApi.entitlements()).offer,
        staleTime: 60 * 60 * 1000,
    });
    const offer = computed<PlanOffer | null>(() => query.data.value ?? null);
    return { offer };
}

/** An amount in the plan's currency, in the current language: "EUR 29" / "€3.90". */
export function formatPrice(amount: number, currency: string): string {
    return new Intl.NumberFormat(intlLocale(), {
        style: "currency",
        currency,
        minimumFractionDigits: Number.isInteger(amount) ? 0 : 2,
    }).format(amount);
}
