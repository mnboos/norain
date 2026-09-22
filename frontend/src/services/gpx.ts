import { GeometrySource, RoutingProfile } from "@norain/api/models";
import { GPXApi } from "@norain/api/apis";
import type { PlacesSearchResult, RecurringRouteOut, RoutePlanIn, RoutePlanOut } from "@norain/api/models";
import { ResponseError } from "@norain/api/runtime";
import { isRecord } from "@/services/http";

export const gpxApi = new GPXApi();
export interface RouteDraft {
    plan: RoutePlanIn;
    preview: RoutePlanOut;
}

export function routingProfile(value: string): RoutingProfile {
    if (value === "ebike") return RoutingProfile.Ebike;
    if (value === "fast_ebike") return RoutingProfile.FastEbike;
    return RoutingProfile.Bike;
}

export function parseDraft(raw: string | null): RouteDraft | null {
    if (!raw) return null;
    const value: unknown = JSON.parse(raw);
    if (!isRecord(value) || !isRecord(value.plan) || !isRecord(value.preview)) return null;
    const plan = value.plan;
    const preview = value.preview;
    const coordinates = readCoordinates(plan.coordinates);
    const line = readCoordinates(preview.coordinates);
    if (!coordinates || !line || typeof preview.distanceM !== "number" || typeof preview.timeS !== "number") return null;
    return { plan: {
        name: typeof plan.name === "string" ? plan.name : "NoRain",
        geometrySource: plan.geometrySource === "imported" ? GeometrySource.Imported : GeometrySource.Graphhopper,
        profile: routingProfile(typeof plan.profile === "string" ? plan.profile : "bike"),
        coordinates, durationSeconds: typeof plan.durationSeconds === "number" ? plan.durationSeconds : null,
    }, preview: { coordinates: line, distanceM: preview.distanceM, timeS: preview.timeS } };
}
function readCoordinates(value: unknown): number[][] | null {
    if (!Array.isArray(value) || value.length < 2 || value.length > 100000) return null;
    const points: number[][] = [];
    for (const point of value) {
        if (!Array.isArray(point) || point.length < 2 || point.length > 3) return null;
        const numbers: number[] = [];
        for (const n of point) { if (typeof n !== "number" || !Number.isFinite(n)) return null; numbers.push(n); }
        points.push(numbers);
    }
    return points;
}

export function routePlace(point: number[], name: string): PlacesSearchResult {
    return { type: "Feature", properties: { name, city: null, state: "", countrycode: "", showCanton: false },
        geometry: { type: "Point", coordinates: point.slice(0, 2) } };
}

export function savedRoutePlan(route: RecurringRouteOut): RoutePlanIn {
    return { name: route.name, geometrySource: route.geometrySource ?? GeometrySource.Graphhopper,
        coordinates: route.geometrySource === GeometrySource.Imported ? route.importedCoordinates ?? [] :
            [[route.startLon, route.startLat], ...(route.viaPoints ?? []), [route.destLon, route.destLat]],
        durationSeconds: route.durationSeconds, profile: routingProfile(route.profile) };
}

export async function gpxError(error: unknown): Promise<string> {
    if (error instanceof ResponseError) {
        const body: unknown = await error.response.json().catch(() => null);
        if (isRecord(body) && typeof body.detail === "string") return body.detail;
    }
    return error instanceof Error && !(error instanceof ResponseError) ? error.message : "Die GPX-Anfrage ist fehlgeschlagen.";
}

export async function downloadGpx(response: Response, name = "route"): Promise<void> {
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = (Array.from(name).filter(c => c.charCodeAt(0) >= 32).join("").replace(/[<>:"/\\|?*]/g, "-").slice(0, 100) || "route") + ".gpx";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => { URL.revokeObjectURL(url); }, 1000);
}

export async function exportDraft(draft: RouteDraft): Promise<void> {
    const response = await gpxApi.coreApiGpxExportGpxRaw({ gpxExportIn: {
        name: draft.plan.name, coordinates: draft.preview.coordinates,
    } });
    await downloadGpx(response.raw, draft.plan.name);
}
