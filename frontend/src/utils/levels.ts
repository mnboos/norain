/**
 * The server's ride-quality results are codes (`core/ride_quality.py`: band, cause, impact and
 * wind-effort levels); this words them. Never a threshold here: the curves stay on the server.
 * Unknown codes (a newer server) fall back to the code itself rather than a blank.
 */
import { t, te } from "@/i18n";

function word(prefix: string, code: string): string {
    const key = `${prefix}.${code}`;
    return te(key) ? t(key) : code;
}

/** "mässig", or "mässig · v. a. Regen" when one factor clearly spoils the ride. */
export function rideLabelText(band: string | null | undefined, cause?: string | null): string {
    if (!band) return "";
    const bandText = word("levels.band", band);
    return cause ? t("levels.rideLabel", { band: bandText, cause: word("levels.cause", cause) }) : bandText;
}

/** A rain or frost level: "leicht", "mässig", "stark". */
export function impactText(level: string | null | undefined): string {
    return level ? word("levels.impact", level) : "";
}

export function windEffortText(level: string | null | undefined): string {
    return level ? word("levels.windEffort", level) : "";
}

/** A WMO weather code (Open-Meteo) as a short description; "" when unknown. */
export function weatherCodeText(code: number | null | undefined): string {
    if (code == null) return "";
    const key = `weather.wmo.${code}`;
    return te(key) ? t(key) : "";
}
