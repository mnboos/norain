import { Dark, LocalStorage, setCssVar } from "quasar";
import { watch } from "vue";

const STORAGE_KEY = "norain-theme";

/**
 * Brand and semantic colors per mode, applied with Quasar's setCssVar. That writes them inline
 * on <body>, so they beat the `:root` defaults in quasar.css no matter which stylesheet loads
 * last. The light theme is a cool palette without orange: dry weather is mint/teal and rain is
 * blue (cards, map, charts), errors are rose - no traffic-light green/red. The dark theme still
 * uses sunny amber for dry. The `tint-*` and `text-muted` values back the utility classes in
 * src/assets/base.css.
 */
const PALETTE: Record<"light" | "dark", Record<string, string>> = {
    light: {
        primary: "#2b6cb0",
        secondary: "#0f766e",
        accent: "#1a9e8f",
        positive: "#2a9d8f",
        negative: "#d24d78",
        info: "#3b8fd9",
        warning: "#b58900",
        dark: "#1c2533",
        "dark-page": "#121821",
        "tint-dry": "#e3f4ef",
        "tint-wet": "#e2edf9",
        "tint-heavy": "#e7e4f8",
        "tint-warn": "#f7f3d9",
        "tint-neutral": "#eef1f5",
        "tint-error": "#fbe6ed",
        "text-muted": "rgba(0, 0, 0, 0.6)",
    },
    dark: {
        // Quasar also paints primary/negative as backgrounds under white text (QDate/QTime
        // headers, toggles, filled buttons), so these stay dark enough for that (~4.3:1 and
        // ~3.5:1) while still reading as foreground colors on the dark page.
        primary: "#3b7dc4",
        secondary: "#3cb8ab",
        accent: "#f2b95c",
        positive: "#4cc2a4",
        negative: "#e0604f",
        info: "#7cb8ee",
        warning: "#f2b95c",
        dark: "#1c2533",
        "dark-page": "#121821",
        "tint-dry": "#352d1c",
        "tint-wet": "#1b2d42",
        "tint-heavy": "#28244a",
        "tint-warn": "#3a2e1a",
        "tint-neutral": "#232c3a",
        "tint-error": "#43231f",
        "text-muted": "rgba(255, 255, 255, 0.65)",
    },
};

/** Quasar `config.dark` from the persisted choice; "auto" (follow the OS) when nothing is stored. */
export function initialDarkConfig(): boolean | "auto" {
    const stored = LocalStorage.getItem(STORAGE_KEY);
    return typeof stored === "boolean" ? stored : "auto";
}

// Dark is a reactive singleton, so this re-applies the palette whenever it flips: via the
// toggle, the plugin's install-time set(), or the OS theme changing while in "auto".
watch(
    () => Dark.isActive,
    dark => {
        for (const [name, value] of Object.entries(PALETTE[dark ? "dark" : "light"])) setCssVar(name, value);
    },
    { immediate: true, flush: "sync" },
);

/** Flip light/dark and remember the explicit choice. */
export function toggleDark() {
    Dark.toggle();
    LocalStorage.set(STORAGE_KEY, Dark.isActive);
}
