import { ForecastJobOutFromJSON, type ForecastJobOut, type RouteForecastOut } from "@norain/api/models";
import { useWebSocket } from "@vueuse/core";

import { milestone } from "@/services/telemetry";

import { useBackendHost } from "@/utils";

/** How far along a forecast job is, for a progress indicator. */
export interface ForecastProgress {
    status: string;
    cellsSettled: number;
    cellsTotal: number;
}

/** Polling cadence used when the WebSocket cannot be established. */
const POLL_INTERVAL_MS = 2000;

/** How long to wait for the socket before falling back to polling. */
const SOCKET_OPEN_TIMEOUT_MS = 3000;

export class ForecastJobError extends Error {
    constructor(message: string) {
        super(message);
        this.name = "ForecastJobError";
    }
}

function isTerminal(job: ForecastJobOut): boolean {
    return job.status === "done" || job.status === "failed";
}

function settle(job: ForecastJobOut): RouteForecastOut {
    if (job.status === "failed" || !job.result) {
        // An empty error string means the backend gave no reason, same as a missing one.
        const reason =
            job.error !== undefined && job.error !== ""
                ? job.error
                : "Die Wettervorhersage konnte nicht berechnet werden.";
        throw new ForecastJobError(reason);
    }
    return job.result;
}

function reportProgress(job: ForecastJobOut, onProgress?: (progress: ForecastProgress) => void): void {
    onProgress?.({
        status: job.status,
        cellsSettled: job.cellsSettled ?? 0,
        cellsTotal: job.cellsTotal ?? 0,
    });
}

/**
 * Wait for a forecast job to finish.
 *
 * The backend computes forecasts on workers, so the request that starts one returns an
 * envelope rather than weather. Progress arrives over a WebSocket; polling is the fallback
 * for connections that cannot upgrade (a proxy stripping the upgrade header, say). Both
 * channels carry the identical payload, so one parser handles them.
 */
export async function awaitForecastJob(
    job: ForecastJobOut,
    onProgress?: (progress: ForecastProgress) => void,
    signal?: AbortSignal,
    onDelivery?: (delivery: string) => void,
): Promise<RouteForecastOut> {
    reportProgress(job, onProgress);
    if (isTerminal(job)) {
        onDelivery?.("immediate");
        return settle(job);
    }

    try {
        onDelivery?.("websocket");
        return await watchOverSocket(job, onProgress, signal);
    } catch (error) {
        if (error instanceof ForecastJobError) throw error;
        if (signal?.aborted) throw error;
        // The socket never opened or dropped before the job finished; polling still gets
        // the answer, just less promptly.
        onDelivery?.("polling");
        milestone("forecast.delivery_fallback", { "job.id": job.jobId, outcome: "polling" });
        return await pollUntilDone(job, onProgress, signal);
    }
}

function watchOverSocket(
    job: ForecastJobOut,
    onProgress?: (progress: ForecastProgress) => void,
    signal?: AbortSignal,
): Promise<RouteForecastOut> {
    return new Promise((resolve, reject) => {
        const wsUrl = `${useBackendHost(location.protocol === "https:" ? "wss:" : "ws:")}${job.wsUrl ?? `/ws/forecast/${job.jobId}/`}`;
        let settled = false;
        const resources: {
            openTimer: ReturnType<typeof setTimeout> | undefined;
            closeSocket: (() => void) | undefined;
        } = { openTimer: undefined, closeSocket: undefined };

        const cleanup = () => {
            if (resources.openTimer !== undefined) clearTimeout(resources.openTimer);
            signal?.removeEventListener("abort", onAbort);
            resources.closeSocket?.();
        };
        const finish = (complete: () => void) => {
            if (settled) return;
            settled = true;
            cleanup();
            complete();
        };
        const fail = (error: Error) => {
            finish(() => {
                reject(error);
            });
        };
        const onAbort = () => {
            fail(new DOMException("Aborted", "AbortError"));
        };

        if (signal?.aborted) {
            onAbort();
            return;
        }

        const socket = useWebSocket(wsUrl, {
            immediate: true,
            autoConnect: false,
            autoClose: false,
            autoReconnect: false,
            onConnected: () => {
                if (resources.openTimer !== undefined) clearTimeout(resources.openTimer);
            },
            onMessage: (_socket, event) => {
                if (typeof event.data !== "string") return;
                let update: ForecastJobOut;
                try {
                    update = ForecastJobOutFromJSON(JSON.parse(event.data));
                } catch {
                    return; // ignore a frame we cannot read; the next one or the poll will do
                }
                reportProgress(update, onProgress);
                if (!isTerminal(update)) return;
                try {
                    const result = settle(update);
                    finish(() => {
                        resolve(result);
                    });
                } catch (error) {
                    fail(error instanceof Error ? error : new Error(String(error)));
                }
            },
            onError: () => {
                fail(new Error("WebSocket error"));
            },
            onDisconnected: () => {
                fail(new Error("WebSocket closed before the job finished"));
            },
        });
        resources.closeSocket = socket.close;

        // Don't let a silently-hanging upgrade stall the forecast indefinitely.
        resources.openTimer = setTimeout(() => {
            fail(new Error("WebSocket did not open"));
        }, SOCKET_OPEN_TIMEOUT_MS);
        signal?.addEventListener("abort", onAbort);
    });
}

async function pollUntilDone(
    job: ForecastJobOut,
    onProgress?: (progress: ForecastProgress) => void,
    signal?: AbortSignal,
): Promise<RouteForecastOut> {
    for (;;) {
        if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
        const response = await fetch(`${useBackendHost()}/api/forecast_jobs/${job.jobId}`, {
            credentials: "include",
            signal,
        });
        if (!response.ok) {
            throw new ForecastJobError("Die Wettervorhersage konnte nicht abgerufen werden.");
        }
        const update = ForecastJobOutFromJSON(await response.json());
        reportProgress(update, onProgress);
        if (isTerminal(update)) return settle(update);
        await new Promise(resolve => setTimeout(resolve, POLL_INTERVAL_MS));
    }
}
