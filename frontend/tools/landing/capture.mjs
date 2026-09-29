import { chromium } from "@playwright/test";
import { createServer } from "vite";
import { mkdir, rename, rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL("../../", import.meta.url));
process.chdir(root);
const output = path.join(root, "public/landing");
const staging = path.join(output, ".staging");
await mkdir(staging, { recursive: true });
// Avoid consuming native file watchers during this short-lived capture run.
process.env.CHOKIDAR_USEPOLLING = "true";
const server = await createServer({
    root,
    mode: "landing",
    server: { port: 4317, strictPort: true, watch: null, warmup: { clientFiles: [] } },
});
let browser;
try {
    await server.listen();
    browser = await chromium.launch({
        executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined,
        args: ["--enable-unsafe-swiftshader"],
    });
    for (const lang of ["de", "en"]) {
        const context = await browser.newContext({
            viewport: { width: 1000, height: 900 },
            deviceScaleFactor: 2,
            locale: lang === "de" ? "de-CH" : "en-GB",
            timezoneId: "Europe/Zurich",
            colorScheme: "light",
            reducedMotion: "reduce",
        });
        const page = await context.newPage();
        const errors = [];
        page.on("pageerror", error => {
            errors.push(error.message);
            console.error(error.message);
        });
        page.on("console", message => {
            if (message.type() === "error") {
                errors.push(message.text());
                console.error(message.text());
            }
        });
        page.on("requestfailed", request => {
            if (request.failure()?.errorText === "net::ERR_ABORTED") return;
            errors.push(`Request failed: ${request.url()} (${request.failure()?.errorText})`);
        });
        page.on("response", response => {
            if (response.status() >= 400) errors.push(`HTTP ${response.status()}: ${response.url()}`);
        });
        // The studio is isolated from real accounts and all backend services.
        await page.route(
            url => url.pathname.startsWith("/api/"),
            route => route.fulfill({ json: [] }),
        );
        await page.goto(`http://127.0.0.1:4317/tools/landing/index.html?lang=${lang}`);
        await page.evaluate(() => document.fonts.ready);
        await page.locator("#route .maplibregl-marker").first().waitFor();
        // MapLibre's idle event is the reliable completion signal, including tiles and camera transitions.
        await page.waitForFunction(() => document.querySelector('#route[data-map-ready="true"]'), null, {
            timeout: 60000,
        });
        async function capture(name) {
            const frame = page.locator(`#${name}`);
            const fits = await frame.evaluate(
                element => element.scrollWidth <= element.clientWidth && element.scrollHeight <= element.clientHeight,
            );
            if (!fits) throw new Error(`${name}-${lang}: content overflows the screenshot frame`);
            await frame.screenshot({ path: path.join(staging, `${name}-${lang}.png`), animations: "disabled" });
        }
        await page.waitForFunction(() => document.querySelector('#places[data-map-ready="true"]'), null, {
            timeout: 60000,
        });
        for (const name of ["temperature", "elevation"]) {
            await page.locator(`#${name} .js-plotly-plot .main-svg`).first().waitFor();
        }
        for (const name of ["route", "schedule", "forecast", "wind", "temperature", "elevation", "places"])
            await capture(name);
        if (errors.length) throw new Error(errors.join("\n"));
        await context.close();
        console.log(`Captured ${lang}: route, schedule, forecast, wind, temperature, elevation, places`);
    }
    // Publish only after both languages succeed. Failed runs leave the checked-in images intact.
    for (const lang of ["de", "en"])
        for (const name of ["route", "schedule", "forecast", "wind", "temperature", "elevation", "places"]) {
            await rename(path.join(staging, `${name}-${lang}.png`), path.join(output, `${name}-${lang}.png`));
        }
    console.log(`Landing images saved to ${output}`);
} finally {
    await browser?.close();
    await server.close();
    await rm(staging, { recursive: true, force: true });
}
