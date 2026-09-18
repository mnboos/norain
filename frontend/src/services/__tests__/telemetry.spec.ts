import { beforeEach, describe, expect, it, vi } from "vitest";
import { identifyPlan, identifyUser, measureForecastLoad, milestone, type MetricAttributes } from "../telemetry";

type MetricFn = (name: string, value: number, options: { attributes: MetricAttributes; unit?: string }) => void;

const sentry = vi.hoisted(() => ({
    metrics: { count: vi.fn<MetricFn>(), distribution: vi.fn<MetricFn>() },
    logger: { info: vi.fn() },
    setUser: vi.fn(),
}));
vi.mock("@sentry/vue", () => sentry);

describe("product telemetry", () => {
    beforeEach(() => {
        identifyUser();
        vi.resetAllMocks();
    });

    it("counts once from request start through immediate completion with detailed attributes", async () => {
        const value = await measureForecastLoad(
            delivery => {
                delivery("immediate");
                return Promise.resolve("forecast");
            },
            { feature: "adhoc", start_lat: 47.423, "route.name": "Commute" },
        );
        expect(value).toBe("forecast");
        expect(sentry.metrics.count).toHaveBeenCalledExactlyOnceWith("norain.forecast.load", 1, {
            attributes: {
                component: "browser",
                feature: "adhoc",
                start_lat: 47.423,
                "route.name": "Commute",
                delivery: "immediate",
                outcome: "success",
                "user.id": "",
                plan: "unknown",
            },
        });
        expect(sentry.metrics.distribution).toHaveBeenCalledWith(
            "norain.forecast.load.duration",
            expect.any(Number),
            expect.objectContaining({ unit: "second" }),
        );
    });

    it("includes initial HTTP failures", async () => {
        const error = new Error("HTTP failed");
        await expect(measureForecastLoad(() => Promise.reject(error), {})).rejects.toBe(error);
        expect(sentry.metrics.count).toHaveBeenCalledOnce();
        expect(sentry.metrics.count.mock.calls[0]?.[2].attributes).toMatchObject({
            delivery: "initial_request",
            outcome: "failed",
        });
    });

    it("treats cancellation separately from failures", async () => {
        const controller = new AbortController();
        controller.abort();
        await expect(
            measureForecastLoad(() => Promise.reject(new DOMException("Aborted", "AbortError")), {}, controller.signal),
        ).rejects.toThrow("Aborted");
        expect(sentry.metrics.count.mock.calls[0]?.[2].attributes.outcome).toBe("cancelled");
    });

    it("records the final delivery channel after polling recovery", async () => {
        await measureForecastLoad(delivery => {
            delivery("websocket");
            delivery("polling");
            return Promise.resolve();
        }, {});
        expect(sentry.metrics.count).toHaveBeenCalledOnce();
        expect(sentry.metrics.count.mock.calls[0]?.[2].attributes).toMatchObject({
            delivery: "polling",
            outcome: "success",
        });
    });

    it("does not replace a successful result or original error when telemetry fails", async () => {
        sentry.metrics.count.mockImplementation(() => {
            throw new Error("SDK failed");
        });
        sentry.metrics.distribution.mockImplementation(() => {
            throw new Error("SDK failed");
        });
        sentry.logger.info.mockImplementation(() => {
            throw new Error("SDK failed");
        });
        expect(() => {
            milestone("test", {});
        }).not.toThrow();
        await expect(measureForecastLoad(() => Promise.resolve(42), {})).resolves.toBe(42);
        const original = new Error("original");
        await expect(measureForecastLoad(() => Promise.reject(original), {})).rejects.toBe(original);
    });

    it("sets a stable identity and clears it on logout or an older session payload", () => {
        identifyUser("42");
        identifyUser();
        expect(sentry.setUser.mock.calls).toEqual([[{ id: "42" }], [null]]);
    });

    it("keeps an in-flight request attributed to the account that started it", async () => {
        identifyUser("first");
        identifyPlan("pro", "first");
        await measureForecastLoad(() => {
            identifyUser("second");
            identifyPlan("pro", "first"); // late entitlement response from the old account
            return Promise.resolve();
        }, {});
        expect(sentry.metrics.count.mock.calls[0]?.[2].attributes).toMatchObject({ "user.id": "first", plan: "pro" });
        milestone("test", {});
        expect(sentry.metrics.count.mock.calls[1]?.[2].attributes).toMatchObject({ "user.id": "second" });
        expect(sentry.metrics.count.mock.calls[1]?.[2].attributes.plan).toBeUndefined();
    });
});
