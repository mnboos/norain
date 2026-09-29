import { defineConfig } from "@playwright/test";

// Isolated fixture uses the real Django router and an in-memory cache, never the user's DB.
export default defineConfig({
    testDir: "./e2e",
    testMatch: "fingerprint.spec.ts",
    timeout: 45000,
    workers: 1,
    reporter: "list",
    use: { baseURL: "http://localhost:8128", headless: true },
    projects: [
        {
            name: "chromium",
            use: { browserName: "chromium", launchOptions: { args: ["--enable-unsafe-swiftshader"] } },
        },
        { name: "firefox", use: { browserName: "firefox" } },
        { name: "webkit", use: { browserName: "webkit" } },
        {
            // A hardened browser spoofs values, but consistently: it must read as honest, not lying.
            name: "firefox-resist-fingerprinting",
            grep: /an honest browser/,
            use: {
                browserName: "firefox",
                launchOptions: { firefoxUserPrefs: { "privacy.resistFingerprinting": true } },
            },
        },
    ],
    webServer: [
        { command: "npx vite --config vite.fingerprint.config.ts", port: 8128 },
        { command: "uv run --project ../backend python ../scripts/fingerprint-test-server.py", port: 8127 },
    ],
});
