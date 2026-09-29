<route lang="json5">
{
    name: "welcome",
    path: "/",
    alias: "/welcome",
    meta: { bare: true },
}
</route>

<script setup lang="ts">
import { computed } from "vue";
import { symSharpAdd, symSharpRemove } from "@quasar/extras/material-symbols-sharp";
import { useSession } from "@/composables/useSession";
import { useLocale } from "@/composables/useLocale";

type Lang = "de" | "en";

const copy = {
    de: {
        navFeatures: "Funktionen",
        navPricing: "Preise",
        navCoverage: "Abdeckung",
        signIn: "Anmelden",
        toApp: "Route planen",
        cta: "Kostenlos starten",
        ctaNote: "Gratis für 2 Routen. Keine Kreditkarte.",
        heroTitle: "Das Wetter entlang deiner Route.",
        heroSub:
            "Regen, Temperatur und Wind für jeden Abschnitt deiner Strecke – genau dann, wenn du dort unterwegs bist. Inklusive Gegen- und Seitenwind.",
        howTitle: "So funktioniert’s",
        stepWord: "Schritt",
        steps: [
            {
                title: "Route festlegen",
                body: "Start und Ziel suchen, auf der Karte anpassen oder eine GPX-Datei importieren.",
                shot: "route",
                alt: "Beispielroute von Vevey nach Montreux am Genfersee",
            },
            {
                title: "Abfahrt wählen",
                body: "Einmalig oder wiederkehrend nach Wochentag – Hin- und Rückfahrt mit eigenen Zeiten.",
                shot: "schedule",
                alt: "Abfahrt an Werktagen um 08:00 und Rückfahrt um 17:00",
            },
            {
                title: "Prognose entlang der Strecke",
                body: "Hier siehst du die Wetterübersicht: Regenrisiko, Temperatur und Wind für jeden Abschnitt – zur passenden Zeit.",
                shot: "forecast",
                alt: "Ausschnitt der Prognose: Wetterübersicht mit einer kurzen Regenphase zwischen trockenen Abschnitten",
            },
        ],
        detailsTitle: "Deine Fahrt im Detail.",
        detailsBody: "Die Wetterübersicht ist nur ein Teil deiner Prognose. Diagramme zeigen den Verlauf von Temperatur, Regen und Wind; das Höhenprofil ergänzt die Strecke. In der Tourenplanung findest du außerdem passende Stopps und POIs.",
        details: [
            { shot: "temperature", title: "Temperatur & Regen", alt: "Temperatur- und Regenverlauf entlang der Beispielroute" },
            { shot: "elevation", title: "Höhenprofil", alt: "Beispielhaftes Höhenprofil mit Anstiegen und Abfahrten" },
            { shot: "places", title: "Stopps & POIs", alt: "Karte mit beispielhaften Stopps für Trinkwasser und Verpflegung" },
        ],
        windAlt: "Beispielhafte Windverteilung mit Gegen-, Seiten- und Rückenwind",
        windTitle: "Wind, gemessen an deiner Fahrtrichtung.",
        windBody:
            "Eine Windangabe für den Ort sagt wenig. MeteoLane rechnet Gegen- und Seitenwind entlang jeder Kurve deiner Route und zeigt den Windaufwand als Stufe – von niedrig bis sehr hoch.",
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
                q: "Wo funktioniert MeteoLane?",
                a: "Routenplanung und Ortssuche decken derzeit die Schweiz ab, die Ortssuche zusätzlich Liechtenstein. Unter „Abdeckung“ kannst du für dein Land stimmen.",
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
        footer: "Wetter für unterwegs",
    },
    en: {
        navFeatures: "Features",
        navPricing: "Pricing",
        navCoverage: "Coverage",
        signIn: "Sign in",
        toApp: "Start planning",
        cta: "Sign up free",
        ctaNote: "Free for 2 routes. No credit card.",
        heroTitle: "The weather along your route.",
        heroSub:
            "Rain, temperature and wind for every section of your ride – right when you’ll be there. Headwind and crosswind included.",
        howTitle: "How it works",
        stepWord: "Step",
        steps: [
            {
                title: "Set your route",
                body: "Search start and destination, adjust it on the map, or import a GPX file.",
                shot: "route",
                alt: "Example route from Vevey to Montreux along Lake Geneva",
            },
            {
                title: "Pick a departure",
                body: "One-off or recurring by weekday – outward and return rides with their own times.",
                shot: "schedule",
                alt: "Weekday departures at 08:00 and return rides at 17:00",
            },
            {
                title: "Forecast along the way",
                body: "This is the weather summary: rain risk, temperature and wind for every section – right when you’ll be there.",
                shot: "forecast",
                alt: "Part of the forecast: weather summary with a brief shower between dry sections",
            },
        ],
        detailsTitle: "Your ride in detail.",
        detailsBody: "The weather summary is just one part of your forecast. Charts show how temperature, rain and wind change along the way; the elevation profile adds the terrain. In the tour planner, you can also find stops and points of interest.",
        details: [
            { shot: "temperature", title: "Temperature & rain", alt: "Temperature and rain chart along the example route" },
            { shot: "elevation", title: "Elevation profile", alt: "Illustrative elevation profile showing climbs and descents" },
            { shot: "places", title: "Stops & points of interest", alt: "Map with example drinking water and food stops" },
        ],
        windAlt: "Example distribution of headwind, crosswind and tailwind",
        windTitle: "Wind, measured against your direction.",
        windBody:
            "A wind reading for a town tells you little. MeteoLane works out headwind and crosswind along every bend of your route and shows the wind effort as a level – from low to very high.",
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
                q: "Where does MeteoLane work?",
                a: "Routing and place search currently cover Switzerland; place search also includes Liechtenstein. Vote for your country under “Coverage”.",
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
        footer: "Weather for the way ahead",
    },
} as const;

// The page's own copy follows the app's language, and its switch is the app's switch: a
// visitor who picks English here gets the English app after signing up.
const { locale, setLocale } = useLocale();
const lang = computed<Lang>({
    get: () => locale.value,
    set: value => void setLocale(value),
});
const t = computed(() => copy[lang.value]);

const { isAuthenticated } = useSession();

const loginLabel = computed(() => (isAuthenticated.value ? t.value.toApp : t.value.cta));
</script>

<template>
    <q-page class="welcome bg-brand-page text-brand-ink" :lang="lang">
        <q-card tag="header" flat square :dark="false" class="hero bg-brand-gradient text-white">
            <q-toolbar class="landing-wrap row items-center justify-between q-py-xs">
                <router-link to="/" class="brand row items-center no-wrap text-white q-pa-none">
                    <span class="text-weight-bold">MeteoLane</span>
                </router-link>
                <div class="bar-right row items-center justify-between q-gutter-x-md">
                    <nav class="links gt-sm row q-gutter-x-md">
                        <a class="text-brand-mist" href="#features">{{ t.navFeatures }}</a>
                        <a class="text-brand-mist" href="#pricing">{{ t.navPricing }}</a>
                        <a class="text-brand-mist" href="#faq">FAQ</a>
                        <router-link class="text-brand-mist" to="/coverage">{{ t.navCoverage }}</router-link>
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
                        :to="isAuthenticated ? '/routes' : '/account'"
                        color="brand-gold"
                        text-color="brand-navy"
                        padding="9px 16px"
                        :label="isAuthenticated ? t.toApp : t.cta"
                    />
                </div>
            </q-toolbar>
            <q-card-section class="landing-wrap hero-content">
                <div class="hero-grid">
                    <div>
                        <q-card flat :dark="false" class="bg-transparent text-white hero-copy">
                            <h1 class="text-display q-ma-none q-mb-lg">{{ t.heroTitle }}</h1>
                            <p class="hero-description text-subheading text-weight-regular text-brand-mist q-mb-lg">
                                {{ t.heroSub }}
                            </p>
                            <q-card-actions class="q-pa-none q-gutter-sm">
                                <q-btn
                                    unelevated
                                    no-caps
                                    :to="isAuthenticated ? '/routes' :'/account'"
                                    color="brand-gold"
                                    text-color="brand-navy"
                                    size="16px"
                                    padding="14px 24px"
                                    :label="loginLabel"
                                />
                                <q-btn
                                    v-if="!isAuthenticated"
                                    outline
                                    no-caps
                                    to="/routes"
                                    color="white"
                                    size="16px"
                                    padding="13px 22px"
                                    :label="t.signIn"
                                />
                            </q-card-actions>
                        </q-card>
                    </div>
                    <div class="hero-visual">
                        <img class="hero-mark" src="/brand/mark-master%20-%20Copy.png" alt="" fetchpriority="high" />
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
                                <img
                                    class="shot rounded-borders q-mb-md"
                                    :src="`/landing/${step.shot}-${lang}.png`"
                                    :alt="step.alt"
                                    width="1280"
                                    height="800"
                                    loading="lazy"
                                    decoding="async"
                                />
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

            <q-card-section id="ride-details" class="section-space q-pa-none">
                <h2 class="text-heading text-brand-navy q-ma-none q-mb-md">{{ t.detailsTitle }}</h2>
                <p class="details-intro text-body1 text-brand-muted q-mb-lg">{{ t.detailsBody }}</p>
                <div class="row q-col-gutter-lg">
                    <div v-for="detail in t.details" :key="detail.shot" class="col-12 col-md-4">
                        <figure class="detail-preview q-ma-none">
                            <img class="shot rounded-borders" :src="`/landing/${detail.shot}-${lang}.png`" :alt="detail.alt" width="1280" height="800" loading="lazy" decoding="async" />
                            <figcaption class="text-body2 text-weight-medium text-brand-navy q-mt-md">{{ detail.title }}</figcaption>
                        </figure>
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
                                <img
                                    class="shot rounded-borders"
                                    :src="`/landing/wind-${lang}.png`"
                                    :alt="t.windAlt"
                                    width="1280"
                                    height="960"
                                    loading="lazy"
                                    decoding="async"
                                />
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
                                        MeteoLane {{ plus ? "Plus" : "Free" }}
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
                <span class="footer-brand text-weight-bold text-white">MeteoLane</span>
                <router-link class="text-caption text-brand-mist" to="/coverage">{{ t.navCoverage }}</router-link>
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
}
.hero-content {
    padding-top: 72px;
    padding-bottom: 0;
}
.hero-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 420px), 1fr));
    gap: 56px;
    align-items: center;
}
.hero-copy {
    max-width: 560px;
    padding-bottom: 72px;
}
.hero-visual {
    align-self: end;
    display: flex;
    justify-content: center;
    margin-top: -24px;
}
.hero-mark {
    display: block;
    width: 100%;
    max-width: 500px;
    height: auto;
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
    display: block;
    width: 100%;
    height: auto;
    background: white;
}
.details-intro {
    max-width: 850px;
    line-height: 1.7;
}
.detail-preview .shot {
    border: 1px solid #dce3eb;
}
.footer-brand {
    font-size: 17px;
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
