<route lang="json5">
{
    name: "welcome",
    meta: { title: "Meteolane – Wetter entlang deiner Route", bare: true },
}
</route>

<script setup lang="ts">
import { computed, ref } from "vue";
import { symSharpAdd, symSharpRemove } from "@quasar/extras/material-symbols-sharp";
import { useSession } from "@/composables/useSession";

type Lang = "de" | "en";

const copy = {
    de: {
        navFeatures: "Funktionen",
        navPricing: "Preise",
        signIn: "Anmelden",
        toApp: "Route planen",
        cta: "Kostenlos starten",
        ctaNote: "Gratis für 2 Routen. Keine Kreditkarte.",
        heroTitle: "Wetter entlang deiner Route.",
        heroSub: "Regen, Temperatur und Wind für jeden Abschnitt deiner Strecke.",
        howTitle: "So funktioniert’s",
        stepWord: "Schritt",
        steps: [
            {
                title: "Route festlegen",
                body: "Start und Ziel suchen, auf der Karte anpassen oder eine GPX-Datei importieren.",
                shot: "route editor",
            },
            {
                title: "Abfahrt wählen",
                body: "Einmalig oder wiederkehrend nach Wochentag – Hin- und Rückfahrt mit eigenen Zeiten.",
                shot: "departure schedule",
            },
            {
                title: "Prognose entlang der Strecke",
                body: "Regenrisiko, Temperatur und Wind für jeden Abschnitt – zu der Zeit, in der du dort bist.",
                shot: "forecast sections",
            },
        ],
        windTitle: "Wind, gemessen an deiner Fahrtrichtung.",
        windBody:
            "Eine Windangabe für den Ort sagt wenig. Meteolane rechnet Gegen- und Seitenwind entlang jeder Kurve deiner Route und zeigt den Windaufwand als Stufe – von niedrig bis sehr hoch.",
        pricingTitle: "Preise",
        perYear: "/ Jahr",
        orMonthly: "oder 3,90 € pro Monat",
        trial: "14 Tage Plus kostenlos testen",
        free: ["2 Routen", "Wetterkarten, Regenrisiko, Temperatur und Wind", "Gegen- und Seitenwind"],
        plus: [
            "20 Routen",
            "Automatischer Abfahrtsvergleich",
            "Wetterdetails inkl. Unsicherheit",
            "Briefings für bis zu 5 Routen, per E-Mail oder Push",
        ],
        faq: [
            {
                q: "Wo funktioniert Meteolane?",
                a: "Routenplanung und Ortssuche decken derzeit die Schweiz ab, die Ortssuche zusätzlich Liechtenstein.",
            },
            {
                q: "Was ist ein Briefing?",
                a: "Eine Nachricht per E-Mail oder Push etwa 60 Minuten vor der frühesten Abfahrt in deinem Zeitfenster – je eine für Hin- und Rückfahrt. Es ist eine geplante Prognose, keine laufende Wetterwarnung.",
            },
            {
                q: "Brauche ich für den Test eine Kreditkarte?",
                a: "Nein. Die Testphase dauert 14 Tage und wechselt danach automatisch zu Free, ohne Zahlung.",
            },
            {
                q: "Was passiert mit meinen Routen nach der Testphase?",
                a: "Zwei Routen deiner Wahl bleiben aktiv. Weitere Routen bleiben gespeichert und pausieren, bis du Plus aktivierst.",
            },
            {
                q: "Woher kommen die Wetterdaten?",
                a: "Aus Open-Meteo, mit OpenWeatherMap als Rückfallebene. Fehlende oder unvollständige Daten werden immer als solche gekennzeichnet.",
            },
        ],
        footer: "Velowetter für die Schweiz",
    },
    en: {
        navFeatures: "Features",
        navPricing: "Pricing",
        signIn: "Sign in",
        toApp: "Start planning",
        cta: "Sign up free",
        ctaNote: "Free for 2 routes. No credit card.",
        heroTitle: "Weather along your ride.",
        heroSub:
            "Rain, temperature and wind for every section of your ride – right when you’ll be there. Headwind and crosswind included.",
        howTitle: "How it works",
        stepWord: "Step",
        steps: [
            {
                title: "Set your route",
                body: "Search start and destination, adjust it on the map, or import a GPX file.",
                shot: "route editor",
            },
            {
                title: "Pick a departure",
                body: "One-off or recurring by weekday – outward and return rides with their own times.",
                shot: "departure schedule",
            },
            {
                title: "Forecast along the way",
                body: "Rain risk, temperature and wind for every section – at the time you’ll actually be there.",
                shot: "forecast sections",
            },
        ],
        windTitle: "Wind, measured against your direction.",
        windBody:
            "A wind reading for a town tells you little. Meteolane works out headwind and crosswind along every bend of your route and shows the wind effort as a level – from low to very high.",
        pricingTitle: "Pricing",
        perYear: "/ year",
        orMonthly: "or €3.90 per month",
        trial: "Try Plus free for 14 days",
        free: ["2 routes", "Weather maps, rain risk, temperature and wind", "Headwind and crosswind"],
        plus: [
            "20 routes",
            "Automatic departure comparison",
            "Weather details incl. uncertainty",
            "Briefings for up to 5 routes, by email or push",
        ],
        faq: [
            {
                q: "Where does Meteolane work?",
                a: "Routing and place search currently cover Switzerland; place search also includes Liechtenstein.",
            },
            {
                q: "What is a briefing?",
                a: "An email or push message about 60 minutes before the earliest departure in your window – one each for outward and return. It’s a scheduled forecast, not continuous weather alerts.",
            },
            {
                q: "Do I need a credit card for the trial?",
                a: "No. The trial lasts 14 days, then switches back to Free automatically, with no payment.",
            },
            {
                q: "What happens to my routes after the trial?",
                a: "Two routes of your choice stay active. The rest stay saved and pause until you activate Plus.",
            },
            {
                q: "Where does the weather data come from?",
                a: "From Open-Meteo, with OpenWeatherMap as a fallback. Missing or incomplete data is always labelled as such.",
            },
        ],
        footer: "Ride weather for Switzerland",
    },
} as const;

const initialLang: Lang = navigator.language.toLowerCase().startsWith("de") ? "de" : "en";
const lang = ref<Lang>(initialLang);
const t = computed(() => copy[lang.value]);

const { isAuthenticated } = useSession();
</script>

<template>
    <q-page class="welcome bg-brand-page text-brand-ink" :lang="lang">
        <q-card tag="header" flat square :dark="false" class="hero bg-brand-gradient text-white">
            <q-toolbar class="landing-wrap row items-center justify-between q-py-xs">
                <router-link to="/welcome" class="brand row items-center no-wrap text-white q-pa-none">
                    <img src="/brand/mark-master%20-%20Copy.png" alt="" />
                    <span class="text-weight-bold">Meteolane</span>
                </router-link>
                <div class="bar-right row items-center justify-between q-gutter-x-md">
                    <nav class="links gt-sm row q-gutter-x-md">
                        <a class="text-brand-mist" href="#features">{{ t.navFeatures }}</a>
                        <a class="text-brand-mist" href="#pricing">{{ t.navPricing }}</a>
                        <a class="text-brand-mist" href="#faq">FAQ</a>
                    </nav>
                    <q-btn-group
                        flat
                        class="language bg-brand-glass rounded-borders q-pa-xs"
                        aria-label="Sprache / Language"
                    >
                        <q-btn
                            v-for="language in ['de', 'en'] as const"
                            :key="language"
                            unelevated
                            dense
                            :label="language.toUpperCase()"
                            :color="lang === language ? 'brand-gold' : 'transparent'"
                            :text-color="lang === language ? 'brand-navy' : 'white'"
                            size="13px"
                            padding="5px 9px"
                            :aria-pressed="lang === language"
                            @click="lang = language"
                        />
                    </q-btn-group>
                    <q-btn
                        unelevated
                        no-caps
                        :to="isAuthenticated ? '/' : '/account'"
                        color="brand-gold"
                        text-color="brand-navy"
                        padding="9px 16px"
                        :label="isAuthenticated ? t.toApp : t.cta"
                    />
                </div>
            </q-toolbar>
            <q-card-section class="landing-wrap hero-content">
                <div class="row items-center q-col-gutter-xl">
                    <div class="col-12 col-md-6">
                        <q-card flat :dark="false" class="bg-transparent text-white hero-copy">
                            <h1 class="text-display q-ma-none q-mb-lg">{{ t.heroTitle }}</h1>
                            <p class="hero-description text-subheading text-weight-regular text-brand-mist q-mb-lg">
                                {{ t.heroSub }}
                            </p>
                            <q-card-actions class="q-pa-none q-gutter-sm">
                                <q-btn
                                    unelevated
                                    no-caps
                                    to="/account"
                                    color="brand-gold"
                                    text-color="brand-navy"
                                    size="16px"
                                    padding="14px 24px"
                                    :label="t.cta"
                                />
                                <q-btn
                                    outline
                                    no-caps
                                    to="/account"
                                    color="white"
                                    size="16px"
                                    padding="13px 22px"
                                    :label="t.signIn"
                                />
                            </q-card-actions>
                        </q-card>
                    </div>
                    <div class="col-12 col-md-6 self-end">
                        <!-- Replace the placeholder with a product screenshot when available. -->
                        <q-responsive :ratio="4 / 3" class="shot hero-shot shadow-10">
                            <div class="flex flex-center">
                                <span class="text-caption bg-white text-brand-faint rounded-borders q-px-sm q-py-xs">
                                    product shot — route detail
                                </span>
                            </div>
                        </q-responsive>
                    </div>
                </div>
            </q-card-section>
        </q-card>

        <q-card flat square :dark="false" class="landing-wrap main-content bg-transparent">
            <q-card-section id="features" class="section-space q-pa-none column no-wrap">
                <h2 class="text-heading text-brand-navy q-ma-none q-mb-lg">{{ t.howTitle }}</h2>
                <div class="row q-col-gutter-lg">
                    <div v-for="(step, i) in t.steps" :key="i" class="col-12 col-md-4">
                        <q-card tag="article" flat :dark="false" class="brand-surface full-height">
                            <q-card-section class="q-pa-lg">
                                <q-responsive :ratio="16 / 10" class="shot rounded-borders q-mb-md">
                                    <div class="flex flex-center">
                                        <span
                                            class="text-caption bg-white text-brand-faint rounded-borders q-px-sm q-py-xs"
                                        >
                                            {{ step.shot }}
                                        </span>
                                    </div>
                                </q-responsive>
                                <div class="text-caption text-weight-bold text-brand-navy q-mb-sm">
                                    {{ t.stepWord }} {{ i + 1 }}
                                </div>
                                <h3 class="text-subheading text-brand-navy q-ma-none q-mb-sm">{{ step.title }}</h3>
                                <p class="text-brand-muted q-ma-none">{{ step.body }}</p>
                            </q-card-section>
                        </q-card>
                    </div>
                </div>
            </q-card-section>

            <q-card-section class="section-space q-pa-none">
                <q-card flat :dark="false" class="brand-surface">
                    <q-card-section class="q-pa-lg q-pa-md-xl">
                        <div class="row items-center q-col-gutter-xl">
                            <div class="col-12 col-md-6">
                                <h2 class="text-heading text-brand-navy q-ma-none q-mb-md">{{ t.windTitle }}</h2>
                                <p class="text-body1 text-brand-muted q-ma-none">{{ t.windBody }}</p>
                            </div>
                            <div class="col-12 col-md-6">
                                <q-responsive :ratio="4 / 3" class="shot rounded-borders">
                                    <div class="flex flex-center">
                                        <span
                                            class="text-caption bg-white text-brand-faint rounded-borders q-px-sm q-py-xs"
                                        >
                                            screenshot — wind distribution bar
                                        </span>
                                    </div>
                                </q-responsive>
                            </div>
                        </div>
                    </q-card-section>
                </q-card>
            </q-card-section>

            <q-card-section id="pricing" class="section-space q-pa-none column no-wrap">
                <h2 class="text-heading text-brand-navy q-ma-none q-mb-lg">{{ t.pricingTitle }}</h2>
                <div class="row q-col-gutter-lg">
                    <div v-for="plus in [false, true]" :key="String(plus)" class="col-12 col-md-6">
                        <q-card
                            flat
                            :dark="false"
                            class="plan column no-wrap full-height"
                            :class="plus ? 'bg-brand-gradient text-white' : 'brand-surface text-brand-navy'"
                        >
                            <q-card-section class="col column no-wrap q-pa-lg">
                                <div class="row items-baseline justify-between q-col-gutter-sm q-mb-md">
                                    <div class="col-12 col-sm-auto text-h6 text-weight-bold">
                                        <q-badge v-if="plus" rounded color="brand-gold" class="q-mr-sm" />
                                        Meteolane {{ plus ? "Plus" : "Free" }}
                                    </div>
                                    <div class="col-12 col-sm-auto price text-weight-bold">
                                        {{ plus ? "29 €" : "0 €" }}
                                        <span v-if="plus" class="text-body2 text-weight-regular text-brand-mist">
                                            {{ t.perYear }}
                                        </span>
                                    </div>
                                </div>
                                <q-list tag="ul" :dark="false" class="q-pa-none q-ma-none q-mb-md">
                                    <q-item
                                        v-for="feature in plus ? t.plus : t.free"
                                        :key="feature"
                                        tag="li"
                                        dense
                                        :dark="false"
                                        class="q-pa-none q-mb-xs"
                                    >
                                        <q-item-section>
                                            <span>· {{ feature }}</span>
                                        </q-item-section>
                                    </q-item>
                                </q-list>
                                <q-space />
                                <q-card-actions class="q-pa-none">
                                    <q-btn
                                        :unelevated="plus"
                                        :outline="!plus"
                                        no-caps
                                        to="/account"
                                        class="full-width"
                                        :color="plus ? 'brand-gold' : 'brand-navy'"
                                        text-color="brand-navy"
                                        size="16px"
                                        padding="12px"
                                        :label="plus ? t.trial : t.cta"
                                    />
                                </q-card-actions>
                                <p v-if="plus" class="text-caption text-brand-mist text-center q-mt-md q-mb-none">
                                    {{ t.orMonthly }} · {{ t.ctaNote }}
                                </p>
                            </q-card-section>
                        </q-card>
                    </div>
                </div>
            </q-card-section>

            <q-card-section id="faq" class="section-space q-pa-none">
                <h2 class="text-heading text-brand-navy q-ma-none q-mb-lg">FAQ</h2>
                <q-card flat :dark="false" class="brand-surface overflow-hidden">
                    <q-list :dark="false">
                        <template v-for="(faq, i) in t.faq" :key="i">
                            <q-separator v-if="i > 0" :dark="false" />
                            <q-expansion-item
                                class="faq-item"
                                group="welcome-faq"
                                :dark="false"
                                :default-opened="i === 0"
                                :label="faq.q"
                                :toggle-aria-label="faq.q"
                                :expand-icon="symSharpAdd"
                                :expanded-icon="symSharpRemove"
                                header-class="text-brand-navy q-px-lg q-py-md"
                                expand-icon-class="text-brand-navy"
                            >
                                <template #header>
                                    <q-item-section class="text-body1 text-weight-medium">{{ faq.q }}</q-item-section>
                                </template>
                                <q-card-section class="q-pt-none q-px-lg q-pb-lg text-brand-muted">
                                    {{ faq.a }}
                                </q-card-section>
                            </q-expansion-item>
                        </template>
                    </q-list>
                </q-card>
            </q-card-section>
        </q-card>

        <q-card tag="footer" flat square :dark="false" class="bg-brand-navy text-brand-mist">
            <q-card-section class="landing-wrap row items-center justify-between q-gutter-y-md q-py-lg">
                <img class="footer-logo" src="/brand/logo-light.png" alt="Meteolane" />
                <span class="text-caption">{{ t.footer }}</span>
            </q-card-section>
        </q-card>
    </q-page>
</template>

<style scoped lang="scss">
// Layout and image treatment specific to this page; colors and type come from the theme.
.welcome {
    a:not(.q-btn) {
        padding: 0;
    }
    a:not(.q-btn):hover {
        background: transparent;
    }
}
.landing-wrap {
    padding-left: max(24px, calc((100% - 1152px) / 2));
    padding-right: max(24px, calc((100% - 1152px) / 2));
}
.brand {
    height: 56px;
    font-size: 17px;
    img {
        height: 64px;
        translate: -10px 3px;
    }
}
.hero-content {
    padding-top: 72px;
    padding-bottom: 0;
}
.hero-copy {
    padding-bottom: 72px;
}
.main-content {
    padding-top: 80px;
}
.section-space {
    padding-bottom: 80px;
}
.hero-description {
    line-height: 1.6;
    text-wrap: pretty;
}
.price {
    font-size: 28px;
}
.shot {
    background: repeating-linear-gradient(135deg, var(--q-brand-placeholder) 0 10px, var(--q-brand-stripe) 10px 20px);
    span {
        font-family: ui-monospace, monospace;
    }
}
.hero-shot {
    border-radius: 12px 12px 0 0;
}
.footer-logo {
    height: 28px;
}
@media (max-width: 599px) {
    .hero .q-toolbar {
        flex-wrap: wrap;
        padding-bottom: 16px;
    }
    .bar-right {
        width: 100%;
        margin-left: 0;
        > .language {
            margin-left: 0;
        }
    }
}
</style>
