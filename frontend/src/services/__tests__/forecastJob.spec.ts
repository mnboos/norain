import type { ForecastJobOut, RouteForecastOut } from "@norain/api/models";
import type { UseWebSocketOptions } from "@vueuse/core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { awaitForecastJob, ForecastJobError, type ForecastProgress } from "../forecastJob";
import { milestone } from "../telemetry";

vi.mock("../telemetry", () => ({ milestone: vi.fn() }));

interface WebSocketMockState {
    close: ReturnType<typeof vi.fn>;
    options: UseWebSocketOptions | undefined;
    url: string;
}

const webSocket = vi.hoisted<WebSocketMockState>(() => ({
    close: vi.fn(),
    options: undefined,
    url: "",
}));

vi.mock("@vueuse/core", () => ({
    useWebSocket: vi.fn((url: string, options: UseWebSocketOptions) => {
        webSocket.url = url;
        webSocket.options = options;
        return { close: webSocket.close };
    }),
}));

const pendingJob: ForecastJobOut = {
    jobId: "job-1",
    status: "fetching",
    cellsSettled: 1,
    cellsTotal: 3,
    wsUrl: "/ws/forecast/job-1/",
};

const rawResult = {
    job_id: "job-1",
    version: "v1",
    departure_time: "2026-09-14T08:00:00Z",
    line: [],
    total_seconds: 0,
    total_distance_m: 0,
    samples: [],
    summary: {
        will_rain: false,
        max_rain_mm: 0,
        rain_amount: 0,
        source: "open-meteo",
    },
};

const parsedResult: RouteForecastOut = {
    jobId: "job-1",
    version: "v1",
    departureTime: "2026-09-14T08:00:00Z",
    line: [],
    totalSeconds: 0,
    totalDistanceM: 0,
    samples: [],
    summary: {
        willRain: false,
        maxRainMm: 0,
        rainAmount: 0,
        source: "open-meteo",
    },
};

const disconnectCallbacks: ("onError" | "onDisconnected")[] = ["onError", "onDisconnected"];

function message(payload: unknown): MessageEvent<string> {
    return new MessageEvent("message", { data: JSON.stringify(payload) });
}

function socketOptions(): UseWebSocketOptions {
    const options = webSocket.options;
    if (!options) throw new Error("The WebSocket was not opened");
    return options;
}

function connect(options: UseWebSocketOptions): void {
    if (!options.onConnected) throw new Error("Missing onConnected callback");
    Reflect.apply(options.onConnected, undefined, [{}]);
}

function receive(options: UseWebSocketOptions, event: MessageEvent<string>): void {
    if (!options.onMessage) throw new Error("Missing onMessage callback");
    Reflect.apply(options.onMessage, undefined, [{}, event]);
}

function failSocket(options: UseWebSocketOptions): void {
    if (!options.onError) throw new Error("Missing onError callback");
    Reflect.apply(options.onError, undefined, [{}, new Event("error")]);
}

function disconnect(options: UseWebSocketOptions): void {
    if (!options.onDisconnected) throw new Error("Missing onDisconnected callback");
    Reflect.apply(options.onDisconnected, undefined, [{}, new CloseEvent("close")]);
}

describe("awaitForecastJob", () => {
    beforeEach(() => {
        vi.mocked(milestone).mockClear();
        vi.useFakeTimers();
        webSocket.close.mockReset();
        webSocket.options = undefined;
        webSocket.url = "";
        vi.stubGlobal("fetch", vi.fn());
    });

    afterEach(() => {
        vi.unstubAllGlobals();
        vi.useRealTimers();
    });

    it("uses VueUse to stream progress and resolve the completed forecast", async () => {
        const progress: ForecastProgress[] = [];
        const delivery = vi.fn();
        const resultPromise = awaitForecastJob(pendingJob, update => progress.push(update), undefined, delivery);
        const options = socketOptions();

        expect(webSocket.url).toBe("ws://localhost:8000/ws/forecast/job-1/");
        expect(options).toMatchObject({ immediate: true, autoConnect: false, autoClose: false, autoReconnect: false });
        connect(options);
        receive(options, message({ job_id: "job-1", status: "fetching", cells_settled: 2, cells_total: 3 }));
        receive(options, message({ job_id: "job-1", status: "done", result: rawResult }));

        const result = await resultPromise;
        expect(result).toMatchObject({ jobId: "job-1", version: "v1" });
        expect(progress).toEqual([
            { status: "fetching", cellsSettled: 1, cellsTotal: 3 },
            { status: "fetching", cellsSettled: 2, cellsTotal: 3 },
            { status: "done", cellsSettled: 0, cellsTotal: 0 },
        ]);
        expect(webSocket.close).toHaveBeenCalledOnce();
        expect(delivery).toHaveBeenCalledExactlyOnceWith("websocket");
        expect(milestone).not.toHaveBeenCalled();
    });

    it("ignores malformed messages and surfaces a failed job", async () => {
        const resultPromise = awaitForecastJob(pendingJob);
        const options = socketOptions();

        receive(options, new MessageEvent("message", { data: "not json" }));
        expect(webSocket.close).not.toHaveBeenCalled();
        receive(options, message({ job_id: "job-1", status: "failed", error: "Provider failed" }));

        await expect(resultPromise).rejects.toEqual(new ForecastJobError("Provider failed"));
        expect(fetch).not.toHaveBeenCalled();
        expect(webSocket.close).toHaveBeenCalledOnce();
    });

    it.each(disconnectCallbacks)("falls back to polling after %s", async callback => {
        vi.mocked(fetch).mockResolvedValue(
            new Response(JSON.stringify({ job_id: "job-1", status: "done", result: rawResult }), {
                status: 200,
                headers: { "Content-Type": "application/json" },
            }),
        );
        const delivery = vi.fn();
        const resultPromise = awaitForecastJob(pendingJob, undefined, undefined, delivery);
        const options = socketOptions();

        if (callback === "onError") failSocket(options);
        else disconnect(options);

        await expect(resultPromise).resolves.toMatchObject({ jobId: "job-1", version: "v1" });
        expect(fetch).toHaveBeenCalledOnce();
        expect(webSocket.close).toHaveBeenCalledOnce();
        expect(delivery.mock.calls).toEqual([["websocket"], ["polling"]]);
        expect(milestone).toHaveBeenCalledExactlyOnceWith("forecast.delivery_fallback", {
            "job.id": "job-1",
            outcome: "polling",
        });
    });

    it("falls back to polling when the connection does not open", async () => {
        vi.mocked(fetch).mockResolvedValue(
            new Response(JSON.stringify({ job_id: "job-1", status: "done", result: rawResult }), { status: 200 }),
        );
        const resultPromise = awaitForecastJob(pendingJob);

        await vi.advanceTimersByTimeAsync(3000);

        await expect(resultPromise).resolves.toMatchObject({ jobId: "job-1", version: "v1" });
        expect(fetch).toHaveBeenCalledOnce();
        expect(webSocket.close).toHaveBeenCalledOnce();
    });

    it("closes and rejects on abort without polling", async () => {
        const controller = new AbortController();
        const resultPromise = awaitForecastJob(pendingJob, undefined, controller.signal);

        controller.abort();

        await expect(resultPromise).rejects.toMatchObject({ name: "AbortError" });
        expect(fetch).not.toHaveBeenCalled();
        expect(webSocket.close).toHaveBeenCalledOnce();
        expect(milestone).not.toHaveBeenCalled();
    });

    it("returns an initially completed job without opening a socket", async () => {
        const completed = {
            jobId: "job-1",
            status: "done",
            result: parsedResult,
        } satisfies ForecastJobOut;

        await expect(awaitForecastJob(completed)).resolves.toBe(completed.result);
        expect(webSocket.options).toBeUndefined();
    });

    it("hands over a refreshing job's stale result at once, then resolves with the fresh one", async () => {
        const stale = { ...parsedResult, version: "old" };
        const onStale = vi.fn();
        const resultPromise = awaitForecastJob(
            { ...pendingJob, stale: true, result: stale },
            undefined,
            undefined,
            undefined,
            onStale,
        );
        expect(onStale).toHaveBeenCalledExactlyOnceWith(stale);

        const options = socketOptions();
        connect(options);
        receive(options, message({ job_id: "job-1", status: "done", result: rawResult }));
        await expect(resultPromise).resolves.toMatchObject({ version: "v1" });
        expect(onStale).toHaveBeenCalledOnce();
    });

    it("never calls onStale for a finished job", async () => {
        const onStale = vi.fn();
        const completed = { jobId: "job-1", status: "done", result: parsedResult } satisfies ForecastJobOut;
        await awaitForecastJob(completed, undefined, undefined, undefined, onStale);
        expect(onStale).not.toHaveBeenCalled();
    });
});
