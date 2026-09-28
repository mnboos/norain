/**
 * Errors a worker stores are codes (`ForecastJob.error`, `Journey.plan_error`), because no
 * request language exists there; this words them. Rows written before the codes hold German
 * prose, so an unknown value is shown as it is, and a missing one as the generic fallback.
 */
import { t, te } from "@/i18n";

function codeText(prefix: string, code: string | null | undefined, fallback: string): string {
    if (!code) return t(fallback);
    const key = `${prefix}.${code}`;
    return te(key) ? t(key) : code;
}

/** Why a forecast job failed. */
export function jobErrorText(code: string | null | undefined): string {
    return codeText("errors.job", code, "errors.job.fallback");
}

/** Why a journey or random ride could not be planned. */
export function planErrorText(code: string | null | undefined): string {
    return codeText("errors.plan", code, "errors.plan.fallback");
}
