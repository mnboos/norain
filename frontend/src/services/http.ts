import { ResponseError } from "@norain/api/runtime";

import { getCookie, useBackendHost } from "@/utils";

export interface DetailResponse {
    detail: string;
}

/** A plain JSON object, whose fields can be read and checked one by one. */
export function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Reads a response body into the shape a caller expects, or null when it does not match. */
export type Parse<T> = (value: unknown) => T | null;

/** Pull the backend's `detail` message out of a body, without asserting its shape. */
function detailOf(value: unknown): string | null {
    if (!isRecord(value)) return null;
    const detail = value.detail;
    return typeof detail === "string" && detail ? detail : null;
}

export const parseDetail: Parse<DetailResponse> = (value) => {
    const detail = detailOf(value);
    return detail === null ? null : { detail };
};

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
export async function request<T>(path: string, parse: Parse<T>, method = "GET", body?: object): Promise<T> {
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
    const payload: unknown = await response.json();
    if (!response.ok) {
        throw new ApiError(detailOf(payload) ?? "Die Anfrage ist fehlgeschlagen.", response.status);
    }
    const parsed = parse(payload);
    if (parsed === null) {
        throw new ApiError("Unerwartete Antwort vom Server.", response.status);
    }
    return parsed;
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
