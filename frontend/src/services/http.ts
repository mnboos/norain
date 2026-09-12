import { ResponseError } from "@norain/api/runtime";

import { getCookie, useBackendHost } from "@/utils";

export interface DetailResponse {
    detail: string;
}

/** Pull the backend's `detail` message out of an error body, without asserting its shape. */
function detailOf(value: unknown): string | null {
    if (typeof value !== "object" || value === null) return null;
    const detail: unknown = Reflect.get(value, "detail");
    return typeof detail === "string" && detail ? detail : null;
}

/** An error carrying the HTTP status, so callers can react to 402 (quota) specifically. */
export class ApiError extends Error {
    constructor(
        message: string,
        readonly status: number,
    ) {
        super(message);
        this.name = "ApiError";
    }
}

/**
 * Session-cookie JSON request against the plain-Django endpoints (`/api/auth/*`,
 * `/api/billing/*`). The generated `@norain/api` client covers the Ninja API instead.
 */
export async function request<T>(path: string, method = "GET", body?: object): Promise<T> {
    const response = await fetch(`${useBackendHost()}${path}`, {
        method,
        credentials: "include",
        headers: body
            ? {
                  "Content-Type": "application/json",
                  "X-CSRFToken": getCookie("csrftoken") ?? "",
              }
            : undefined,
        body: body ? JSON.stringify(body) : undefined,
    });
    // Deserialising JSON into a caller-chosen type is unchecked by nature; the rule has
    // no way to express that, and every caller here matches a schema the backend owns.
    // eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
    const payload: T = await response.json();
    if (!response.ok) {
        throw new ApiError(detailOf(payload) ?? "Die Anfrage ist fehlgeschlagen.", response.status);
    }
    return payload;
}

/**
 * Did the API refuse this because the account's tier is full?
 *
 * The generated `@norain/api` client raises `ResponseError` (which carries the whole
 * `Response`), while the hand-written calls above raise `ApiError` (which carries just
 * the status) — so both shapes have to be recognised, and 402 is the server saying the
 * quota is exhausted.
 */
export function isQuotaExceeded(error: unknown): boolean {
    if (error instanceof ApiError) return error.status === 402;
    if (error instanceof ResponseError) return error.response.status === 402;
    return false;
}
