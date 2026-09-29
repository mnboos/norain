<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import NiceMap from "@/components/NiceMap.vue";
import ForecastSummaryCard from "@/components/ForecastSummaryCard.vue";
import WindDistributionBar from "@/components/WindDistributionBar.vue";
import WeatherChart from "@/components/WeatherChart.vue";
import ElevationChart from "@/components/ElevationChart.vue";
import { useQueryClient } from "@tanstack/vue-query";
import {
    symSharpArrowForward,
    symSharpArrowBack,
    symSharpRepeat,
    symSharpPedalBike,
} from "@quasar/extras/material-symbols-sharp";
import WeekdaySelector from "@/components/WeekdaySelector.vue";
import { routePlace } from "@/services/gpx";
import { forecast, line, elevation, pois, schedule } from "./demo";
const { locale } = useI18n();
const en = computed(() => locale.value === "en");
const selectedDays = ref([...schedule.days]);
const placesReady = ref(false);
useQueryClient().setQueryData(["elevation", "stage", "landing-demo"], elevation);
const mapReady = ref(false);
const start = routePlace(line[0] ?? [], "Vevey · Place du Marché");
const end = routePlace(line.at(-1) ?? [], "Montreux · Quai du Casino");
</script>
<template>
    <main>
        <article id="route" class="frame" :data-map-ready="mapReady">
            <header class="route-heading">
                <div class="endpoint">
                    <i class="endpoint-dot start" />
                    <div>
                        <small>START</small>
                        <strong>Vevey</strong>
                    </div>
                </div>
                <div class="route-connector"><q-icon :name="symSharpPedalBike" size="25px" /></div>
                <div class="endpoint">
                    <i class="endpoint-dot finish" />
                    <div>
                        <small>{{ en ? "DESTINATION" : "ZIEL" }}</small>
                        <strong>Montreux</strong>
                    </div>
                </div>
            </header>
            <NiceMap
                :route-weather="undefined"
                :preview-line="line"
                :abfahrtsort="start"
                :zielort="end"
                height="312px"
                @ready="mapReady = true"
            />
            <div class="route-metrics">
                <q-icon :name="symSharpPedalBike" size="21px" />
                <strong>7.2 km</strong>
                <i />
                24 min
            </div>
        </article>
        <article id="schedule" class="frame inset schedule">
            <div class="eyebrow">{{ en ? "YOUR WEEK, YOUR RHYTHM" : "DEINE WOCHE, DEIN RHYTHMUS" }}</div>
            <h2>
                Vevey
                <span class="both-directions">⇄</span>
                Montreux
            </h2>
            <WeekdaySelector v-model="selectedDays" />
            <div class="departure-cards">
                <div
                    v-for="outbound in [true, false]"
                    :key="String(outbound)"
                    class="departure-card"
                    :class="{ returning: !outbound }"
                >
                    <div class="departure-label">
                        <q-icon :name="outbound ? symSharpArrowForward : symSharpArrowBack" size="19px" />
                        {{ outbound ? (en ? "Outward" : "Hinfahrt") : en ? "Return" : "Rückfahrt" }}
                    </div>
                    <strong>{{ outbound ? schedule.outward : schedule.return }}</strong>
                    <div class="departure-route">{{ outbound ? "Vevey → Montreux" : "Montreux → Vevey" }}</div>
                </div>
            </div>
            <div class="recurrence">
                <q-icon :name="symSharpRepeat" size="19px" />
                {{ en ? "Monday–Friday · every week" : "Montag–Freitag · jede Woche" }}
            </div>
        </article>
        <article id="forecast" class="frame inset">
            <div class="eyebrow">
                VEVEY → MONTREUX
                <span>·</span>
                08:00
            </div>
            <ForecastSummaryCard :forecast="forecast" />
            <footer>{{ en ? "Illustrative forecast · 24-minute ride" : "Beispielprognose · 24 Minuten Fahrt" }}</footer>
        </article>
        <article id="wind" class="frame inset wind">
            <div class="eyebrow">
                VEVEY → MONTREUX
                <span>·</span>
                7.2 KM
            </div>
            <h2>{{ en ? "Every bend changes the wind." : "Jede Kurve verändert den Wind." }}</h2>
            <svg
                viewBox="0 0 560 200"
                role="img"
                :aria-label="
                    en
                        ? 'Illustration of changing wind along a curved route'
                        : 'Illustration: wechselnder Wind entlang einer kurvigen Route'
                "
            >
                <defs>
                    <marker
                        id="arrow"
                        viewBox="0 0 10 10"
                        refX="8"
                        refY="5"
                        markerWidth="5"
                        markerHeight="5"
                        orient="auto-start-reverse"
                    >
                        <path d="M 0 0 L 10 5 L 0 10 z" fill="#70899c" />
                    </marker>
                </defs>
                <path
                    d="M 34 157 C 130 160 119 50 246 62 S 382 175 524 56"
                    fill="none"
                    stroke="#edf2f6"
                    stroke-width="25"
                    stroke-linecap="round"
                />
                <path
                    d="M 34 157 C 130 160 119 50 246 62 S 382 175 524 56"
                    fill="none"
                    stroke="#2d5a8e"
                    stroke-width="7"
                    stroke-linecap="round"
                />
                <g stroke="#70899c" stroke-width="3" marker-end="url(#arrow)">
                    <path d="M 65 58 l 35 35" />
                    <path d="M 260 8 l 35 35" />
                    <path d="M 416 30 l 35 35" />
                </g>
                <circle cx="34" cy="157" r="9" fill="#0f766e" stroke="white" stroke-width="4" />
                <circle cx="524" cy="56" r="9" fill="#d24d78" stroke="white" stroke-width="4" />
                <text x="26" y="192">Vevey</text>
                <text x="446" y="105">Montreux</text>
            </svg>
            <WindDistributionBar :distribution="forecast.summary.windDistribution!" />
            <footer>{{ en ? "Illustrative wind distribution" : "Beispielhafte Windverteilung" }}</footer>
        </article>
        <article id="temperature" class="frame detail-frame">
            <div class="chart-wrap">
                <WeatherChart kind="temperature" version="landing-demo" :samples="forecast.samples" />
            </div>
            <footer>
                {{ en ? "Example · weather throughout the ride" : "Beispiel · Wetter im Verlauf deiner Fahrt" }}
            </footer>
        </article>
        <article id="elevation" class="frame detail-frame">
            <ElevationChart stage-id="landing-demo" color="#2d5a8e" />
        </article>
        <article id="places" class="frame places" :data-map-ready="placesReady">
            <header>
                <div class="eyebrow">{{ en ? "STOPS ALONG THE WAY" : "STOPPS ENTLANG DER STRECKE" }}</div>
                <h2>{{ en ? "Make room for a break." : "Zeit für eine Pause." }}</h2>
            </header>
            <NiceMap
                :route-weather="undefined"
                :preview-line="line"
                :abfahrtsort="start"
                :zielort="end"
                :pois="pois"
                height="292px"
                @ready="placesReady = true"
            />
        </article>
    </main>
</template>
<style scoped>
main {
    padding: 32px;
    display: grid;
    gap: 32px;
    justify-content: start;
    background: #e6ebf1;
}
.frame {
    width: 640px;
    height: 400px;
    background: white;
    overflow: hidden;
    border-radius: 14px;
    color: #1b365d;
}
#route {
    position: relative;
    background: #f6f8fa;
}
.route-heading {
    height: 88px;
    padding: 16px 28px;
    display: flex;
    align-items: center;
    gap: 24px;
    background: white;
}
.endpoint {
    display: flex;
    align-items: center;
    gap: 12px;
}
.endpoint small {
    display: block;
    color: #6c7e92;
    font-size: 10px;
    letter-spacing: 1.5px;
    font-weight: 600;
}
.endpoint strong {
    display: block;
    font-size: 24px;
    font-weight: 500;
}
.endpoint-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    box-shadow: 0 0 0 5px #edf2f7;
}
.endpoint-dot.start {
    background: #2d5a8e;
}
.endpoint-dot.finish {
    background: #d24d78;
}
.route-connector {
    flex: 1;
    text-align: center;
    color: #8095a7;
    border-bottom: 1px dashed #cad5de;
    height: 24px;
}
.route-connector :deep(.q-icon) {
    background: white;
    padding: 0 10px;
    box-sizing: content-box;
    transform: translateY(10px);
}
.route-metrics {
    position: absolute;
    bottom: 34px;
    left: 22px;
    border: 1px solid #e6ecf1;
    box-shadow: 0 4px 16px #1b365d14;
    background: white;
    border-radius: 30px;
    padding: 10px 16px;
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 14px;
}
.route-metrics > i:not(.q-icon) {
    width: 3px;
    height: 3px;
    background: #99acbc;
    border-radius: 50%;
}
.schedule h2 {
    margin: 12px 0 20px;
}
.both-directions {
    color: #9aabba;
}
.departure-cards {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-top: 20px;
}
.departure-card {
    border: 1px solid #dce6ef;
    border-radius: 14px;
    padding: 14px 20px;
    background: #f4f7fb;
}
.departure-card.returning {
    border-color: #d8e9e4;
    background: #f1f8f5;
}
.departure-label {
    display: flex;
    gap: 8px;
    align-items: center;
    font-size: 13px;
    font-weight: 500;
}
.departure-card strong {
    font-size: 36px;
    line-height: 1.5;
    font-weight: 500;
    letter-spacing: -1px;
}
.returning .departure-label {
    color: #0f766e;
}
.departure-route {
    color: #6c7e92;
    font-size: 12px;
}
.recurrence {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    color: #6c7e92;
    margin-top: auto;
}
.detail-frame {
    padding: 20px;
    display: flex;
    flex-direction: column;
}
.chart-wrap {
    height: 324px;
}
#elevation :deep(.q-card) {
    border: 0;
}
#elevation :deep(.text-subtitle2) {
    font-size: 18px;
}
.places header {
    height: 108px;
    padding: 24px 28px;
}
.places h2 {
    margin: 10px 0 0;
    font-size: 25px;
}
h1 {
    font-size: 27px;
    line-height: 1.3;
    font-weight: 600;
    margin: 6px 0;
}
h2 {
    font-size: 27px;
    line-height: 1.3;
    font-weight: 500;
    margin: 20px 0 8px;
}
p {
    margin: 0;
    font-size: 14px;
    color: #56677d;
}
span {
    margin-inline: 6px;
}
.eyebrow {
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 1.2px;
    color: #56677d;
}
.inset {
    padding: 28px;
    display: flex;
    flex-direction: column;
}
.inset :deep(.q-card) {
    box-shadow: none;
}
#forecast :deep(.q-card) {
    flex: 1;
    margin-top: 14px;
}
#forecast :deep(.q-card__section--horiz > .col) {
    min-width: 0;
}
#forecast :deep(h5) {
    margin-top: 0;
    font-size: 25px;
    line-height: 1.35;
}
#forecast :deep(.q-chip) {
    font-size: 14px;
}
footer {
    font-size: 11px;
    color: #6c7e92;
    margin-top: auto;
    padding-top: 18px;
}
.wind {
    height: 480px;
}
.wind > svg {
    width: 100%;
    height: 200px;
    flex-shrink: 0;
    margin-bottom: 16px;
}
.wind > svg text {
    font-family: inherit;
    font-size: 14px;
    fill: #56677d;
}
.wind :deep(.q-linear-progress) {
    height: 13px !important;
    margin-block: 6px;
}
.wind :deep(.text-caption) {
    font-size: 14px;
}
</style>
