import {
    symSharpElectricBike,
    symSharpElectricMoped,
    symSharpHiking,
    symSharpPedalBike,
} from "@quasar/extras/material-symbols-sharp";

import { watch, type Ref } from "vue";

import { t } from "@/i18n";
import type { BikeProfile } from "@/services/auth";

function option(value: BikeProfile, icon: string) {
    return {
        value,
        icon,
        // A getter, so every toggle words it in the current language when it renders.
        get label() {
            return t(`profiles.${value}`);
        },
    };
}

/** GraphHopper's routing profiles (the bike ones and hiking), as every profile toggle shows them. */
export const BIKE_PROFILE_OPTIONS: { readonly label: string; value: BikeProfile; icon: string }[] = [
    option("bike", symSharpPedalBike),
    option("ebike", symSharpElectricBike),
    option("fast_ebike", symSharpElectricMoped),
    option("hike", symSharpHiking),
];

/** A form's default distance, given for a bike: a hike covers about a quarter of it in the same time. */
export function defaultKm(profile: string, bikeKm: number): number {
    return profile === "hike" ? Math.round(bikeKm / 4) : bikeKm;
}

/**
 * Keeps distance fields at the profile's default while the rider has not changed them: switching
 * to hiking turns an untouched 80 km day into 20 km. Synchronous, so loading a saved journey
 * (profile first, then its own values) overwrites what this set.
 */
export function followProfileDefaults(profile: Ref<string>, fields: [Ref<number>, number][]): void {
    watch(
        profile,
        (next, previous) => {
            for (const [field, bikeKm] of fields) {
                if (field.value === defaultKm(previous, bikeKm)) field.value = defaultKm(next, bikeKm);
            }
        },
        { flush: "sync" },
    );
}

/** Hiking has no wind effort: a headwind barely slows a walker (backend core/ride_quality.py). */
export function hasWindEffort(profile: string | null | undefined): boolean {
    return profile !== "hike";
}
