import { describe, expect, it } from "vitest";
import { backendTraceTargets } from "../tracing";

describe("backend trace propagation", () => {
    it.each(["http://localhost:8000", "http://127.0.0.1:8000", "https://norain.example"])(
        "matches all API transports for %s",
        origin => {
            const targets = backendTraceTargets(origin);
            for (const path of [
                "/api/auth/session",
                "/api/route_weather?lat=47",
                "/api/forecast_jobs/123",
                "/api/billing/checkout",
            ]) {
                expect(targets.some(target => target.test(origin + path))).toBe(true);
                expect(targets.some(target => target.test(path))).toBe(true);
            }
        },
    );

    it("does not leak trace headers to other origins or non-API paths", () => {
        const targets = backendTraceTargets("https://norain.example");
        for (const url of [
            "https://norainXexample/api/search",
            "https://norain.example.evil.test/api/search",
            "https://other.test/api/search",
            "https://norain.example/apiary",
            "https://norain.example/tiles/1",
            "//other.test/api/search",
        ]) {
            expect(targets.some(target => target.test(url))).toBe(false);
        }
    });
});
