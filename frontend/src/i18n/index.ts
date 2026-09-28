/**
 * vue-i18n, set up once. German is the source language (`src/locales/de.json`) and the
 * fallback; `en.json` must have exactly the same keys (a unit test checks).
 *
 * `.ts` modules use `t` from here, and only inside functions: a label built at import time
 * would keep the language the page loaded with. This module imports nothing of the app's, so
 * services (http.ts) can read the locale without an import cycle; switching and saving the
 * language lives in `composables/useLocale.ts`.
 */
import { createI18n } from "vue-i18n";
import { de as dateFnsDe, enGB as dateFnsEnGB, type Locale as DateFnsLocale } from "date-fns/locale";

import de from "@/locales/de.json";
import en from "@/locales/en.json";

export type MessageSchema = typeof de;
export type AppLocale = "de" | "en";

export const LOCALES: readonly AppLocale[] = ["de", "en"];
export const DEFAULT_LOCALE: AppLocale = "de";

declare module "vue-i18n" {
    // eslint-disable-next-line @typescript-eslint/no-empty-interface, @typescript-eslint/no-empty-object-type
    export interface DefineLocaleMessage extends MessageSchema {}
}

export function isAppLocale(value: unknown): value is AppLocale {
    return value === "de" || value === "en";
}

export const i18n = createI18n<[MessageSchema], AppLocale, false>({
    legacy: false,
    locale: DEFAULT_LOCALE,
    fallbackLocale: DEFAULT_LOCALE,
    messages: { de, en },
    missingWarn: import.meta.env.DEV,
    fallbackWarn: false,
});

/** Translate outside a component. Call it where the text is used, never at module scope. */
export const t = i18n.global.t;
/** Is there a message for this key? For codes from the server that may be new or old prose. */
export function te(key: string): boolean {
    return i18n.global.te(key);
}

export function currentLocale(): AppLocale {
    return i18n.global.locale.value;
}

/** The BCP 47 tag for `Intl` and `toLocale*`: Swiss German, and British English (24 h, d/m). */
export function intlLocale(locale: AppLocale = currentLocale()): string {
    return locale === "en" ? "en-GB" : "de-CH";
}

/** Plotly's `layout.separators`: the decimal then the group character of the current language. */
export function plotlySeparators(locale: AppLocale = currentLocale()): string {
    const parts = new Intl.NumberFormat(intlLocale(locale)).formatToParts(12345.6);
    const decimal = parts.find(part => part.type === "decimal")?.value ?? ".";
    const group = parts.find(part => part.type === "group")?.value ?? ",";
    return decimal + group;
}

export function dateFnsLocale(locale: AppLocale = currentLocale()): DateFnsLocale {
    return locale === "en" ? dateFnsEnGB : dateFnsDe;
}
