import { isRecord, request } from "@/services/http";

function parse(value: unknown) {
    if (!isRecord(value) || typeof value.connected !== "boolean") return null;
    return { connected: value.connected, token: typeof value.token === "string" ? value.token : "" };
}

export const garminApi = {
    state: () => request("/api/garmin/token", parse),
    change: (method: "POST" | "DELETE") => request("/api/garmin/token", parse, method, {}),
};
