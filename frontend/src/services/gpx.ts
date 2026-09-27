import { GeometrySource, RoutingProfile } from "@norain/api/models";
import { GPXApi } from "@norain/api/apis";
import type { PlacesSearchResult, RecurringRouteOut, RoutePlanIn, RoutePlanOut } from "@norain/api/models";
import { ResponseError } from "@norain/api/runtime";
import { Notify } from "quasar";
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

function gpxFileName(name: string): string {
    return (Array.from(name).filter(c => c.charCodeAt(0) >= 32).join("").replace(/[<>:"/\\|?*]/g, "-").slice(0, 100) || "route") + ".gpx";
}

function downloadFile(file: File): void {
    const url = URL.createObjectURL(file);
    const link = document.createElement("a");
    link.href = url;
    link.download = file.name;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => { URL.revokeObjectURL(url); }, 1000);
}

export type ShareOutcome = "shared" | "cancelled" | "downloaded" | "blocked";

/** A phone or tablet whose browser can hand files to other apps (Garmin Connect, Komoot, mail, …). */
export function canShareFiles(): boolean {
    if (typeof matchMedia !== "function" || !matchMedia("(pointer: coarse)").matches) return false;
    // Firefox on the desktop has no canShare at all.
    if (!("canShare" in navigator)) return false;
    return navigator.canShare({ files: [new File([""], "route.gpx", { type: "application/gpx+xml" })] });
}

/** Opens the share sheet on a mobile device, downloads the file everywhere else. */
export async function shareFile(file: File): Promise<ShareOutcome> {
    const data: ShareData = { files: [file], title: file.name.replace(/\.gpx$/, "") };
    if (!canShareFiles() || !navigator.canShare(data)) {
        downloadFile(file);
        return "downloaded";
    }
    try {
        await navigator.share(data);
        return "shared";
    } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") return "cancelled";
        // Safari lets the user gesture expire while the file is being fetched; a fresh tap is needed.
        if (e instanceof DOMException && e.name === "NotAllowedError") return "blocked";
        downloadFile(file);
        return "downloaded";
    }
}

export async function shareGpx(response: Response, name = "route"): Promise<void> {
    const blob = await response.blob();
    const file = new File([blob], gpxFileName(name), { type: "application/gpx+xml" });
    if (await shareFile(file) !== "blocked") return;
    Notify.create({
        message: "GPX ist bereit.",
        timeout: 10000,
        actions: [
            { label: "Teilen", color: "white", handler: () => { void shareFile(file); } },
            { label: "Herunterladen", color: "white", handler: () => { downloadFile(file); } },
        ],
    });
}

export async function exportDraft(draft: RouteDraft): Promise<void> {
    const response = await gpxApi.coreApiGpxExportGpxRaw({ gpxExportIn: {
        name: draft.plan.name, coordinates: draft.preview.coordinates,
    } });
    await shareGpx(response.raw, draft.plan.name);
}
