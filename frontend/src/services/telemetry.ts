import * as Sentry from "@sentry/vue";

export type MetricAttributes = Record<string, string | number | boolean>;
let identity: MetricAttributes = {};

export function getMetricUserId(): string | undefined {
    const id = identity["user.id"];
    return typeof id === "string" ? id : undefined;
}

export function identifyUser(id?: string): void {
    if (!id || identity["user.id"] !== id) identity = id ? { "user.id": id } : {};
    try {
        Sentry.setUser(id ? { id } : null);
    } catch {
        /* Telemetry must not change session behavior. */
    }
}

export function identifyPlan(plan: string, userId?: string): void {
    if (userId && identity["user.id"] === userId) identity = { ...identity, plan };
}

export function metric(
    type: "count" | "distribution",
    name: string,
    value: number,
    attributes: MetricAttributes,
    unit?: "second",
): void {
    try {
        Sentry.metrics[type](`norain.${name}`, value, {
            attributes: { component: "browser", ...identity, ...attributes },
            ...(unit ? { unit } : {}),
        });
    } catch {
        /* Best effort. */
    }
}

export function milestone(name: string, attributes: MetricAttributes): void {
    metric("count", name, 1, attributes);
    try {
        Sentry.logger.info(`norain.${name}`, { component: "browser", ...identity, ...attributes });
    } catch {
        /* Best effort. */
    }
}

/** One observation per query execution, including its initial HTTP request. */
export async function measureForecastLoad<T>(
    load: (onDelivery: (delivery: string) => void) => Promise<T>,
    attributes: MetricAttributes,
    signal?: AbortSignal,
): Promise<T> {
    const started = performance.now();
    const startedIdentity = { "user.id": identity["user.id"] ?? "", plan: identity.plan ?? "unknown" };
    let delivery = "initial_request";
    let outcome = "success";
    try {
        return await load(value => {
            delivery = value;
        });
    } catch (error) {
        outcome =
            signal?.aborted || (error instanceof DOMException && error.name === "AbortError") ? "cancelled" : "failed";
        throw error;
    } finally {
        const context = { ...startedIdentity, ...attributes, delivery, outcome };
        milestone("forecast.load", context);
        metric("distribution", "forecast.load.duration", (performance.now() - started) / 1000, context, "second");
    }
}
