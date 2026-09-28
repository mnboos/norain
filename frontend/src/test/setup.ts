import { config } from "@vue/test-utils";

import { i18n } from "@/i18n";

// Every component test renders with the app's catalogs, in German, the source language.
// A mount's own `global.plugins` are added to this list, not put in its place.
i18n.global.locale.value = "de";
config.global.plugins.push(i18n);
