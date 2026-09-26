import { symSharpElectricBike, symSharpElectricMoped, symSharpPedalBike } from "@quasar/extras/material-symbols-sharp";

import type { BikeProfile } from "@/services/auth";

/** GraphHopper's bike profiles, as every profile toggle shows them. */
export const BIKE_PROFILE_OPTIONS: { label: string; value: BikeProfile; icon: string }[] = [
    { label: "Velo", value: "bike", icon: symSharpPedalBike },
    { label: "E-Bike", value: "ebike", icon: symSharpElectricBike },
    { label: "S-Pedelec", value: "fast_ebike", icon: symSharpElectricMoped },
];
