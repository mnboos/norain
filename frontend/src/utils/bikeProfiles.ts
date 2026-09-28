import { symSharpElectricBike, symSharpElectricMoped, symSharpPedalBike } from "@quasar/extras/material-symbols-sharp";

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

/** GraphHopper's bike profiles, as every profile toggle shows them. */
export const BIKE_PROFILE_OPTIONS: { readonly label: string; value: BikeProfile; icon: string }[] = [
    option("bike", symSharpPedalBike),
    option("ebike", symSharpElectricBike),
    option("fast_ebike", symSharpElectricMoped),
];
