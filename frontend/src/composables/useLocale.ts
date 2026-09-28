import { computed } from "vue";
import { Lang } from "quasar";

import { authApi } from "@/services/auth";
import { useSession } from "@/composables/useSession";
import { type AppLocale, currentLocale, DEFAULT_LOCALE, i18n, isAppLocale, LOCALES } from "@/i18n";

const STORAGE_KEY = "meteolane.locale";

const QUASAR_LANGS = {
    de: () => import("quasar/lang/de-CH"),
    en: () => import("quasar/lang/en-GB"),
} as const;

type QuasarPack = Parameters<typeof Lang.set>[0];

/**
 * Quasar's `lang/*.d.ts` and `Lang.set` disagree on a few optional callbacks' parameters
 * (`(range: number)` vs `(range?: number)`), although they are the same packs. Always true.
 */
function isQuasarPack(pack: unknown): pack is QuasarPack {
    return typeof pack === "object" && pack !== null && "isoName" in pack;
}

function storedLocale(): AppLocale | null {
    try {
        const value = localStorage.getItem(STORAGE_KEY);
        return isAppLocale(value) ? value : null;
    } catch {
        return null;
    }
}

function storeLocale(locale: AppLocale) {
    try {
        localStorage.setItem(STORAGE_KEY, locale);
    } catch {
        // Private mode or blocked storage: the choice lasts until the tab closes.
    }
}

/**
 * The browser's first supported language. A browser that names only others (French, Italian …)
 * gets English, the more widely read of the two; one that names none at all gets the default.
 */
function browserLocale(): AppLocale | null {
    const tags = typeof navigator === "undefined" ? [] : [...navigator.languages, navigator.language].filter(Boolean);
    for (const tag of tags) {
        const base = tag.toLowerCase().split("-")[0];
        if (isAppLocale(base)) return base;
    }
    return tags.length ? "en" : null;
}

/** The account's language wins; before sign-in the last choice here, then the browser's. */
export function detectLocale(accountLanguage?: AppLocale | null): AppLocale {
    return accountLanguage ?? storedLocale() ?? browserLocale() ?? DEFAULT_LOCALE;
}

/** Switch the app without a reload: messages, `<html lang>` and Quasar's own texts. */
export async function applyLocale(locale: AppLocale) {
    i18n.global.locale.value = locale;
    document.documentElement.lang = locale;
    const pack = await QUASAR_LANGS[locale]();
    const value: unknown = pack.default;
    if (isQuasarPack(value)) Lang.set(value);
}

export function useLocale() {
    const { session, setSession } = useSession();
    const locale = computed(() => i18n.global.locale.value);

    /** The user's choice: remembered in this browser and, when signed in, on the account. */
    async function setLocale(value: AppLocale) {
        storeLocale(value);
        await applyLocale(value);
        if (session.value.authenticated && session.value.user?.language !== value) {
            setSession(await authApi.updateProfile({ language: value }));
        }
    }

    return { locale, locales: LOCALES, setLocale, currentLocale };
}
