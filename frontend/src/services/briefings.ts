import { isRecord, request, type Parse } from "@/services/http";

export interface BriefingRoute {
    id: string;
    name: string;
    active: boolean;
    available: boolean;
    freeSelected: boolean;
    channel: string;
    briefingActive: boolean;
}
export interface BriefingPreferences {
    pushPublicKey: string;
    emailConfigured: boolean;
    pushDeviceCount: number;
    routes: BriefingRoute[];
    recent: { id: number; routeName: string; body: string; status: string; departure: string }[];
}
const parse: Parse<BriefingPreferences> = value => {
    if (
        !isRecord(value) ||
        typeof value.pushPublicKey !== "string" ||
        typeof value.emailConfigured !== "boolean" ||
        typeof value.pushDeviceCount !== "number" ||
        !Array.isArray(value.routes) ||
        !Array.isArray(value.recent)
    )
        return null;
    const routes: BriefingRoute[] = [];
    for (const r of value.routes) {
        if (
            !isRecord(r) ||
            typeof r.id !== "string" ||
            typeof r.name !== "string" ||
            typeof r.active !== "boolean" ||
            typeof r.available !== "boolean" ||
            typeof r.freeSelected !== "boolean" ||
            typeof r.channel !== "string" ||
            typeof r.briefingActive !== "boolean"
        )
            return null;
        routes.push({
            id: r.id,
            name: r.name,
            active: r.active,
            available: r.available,
            freeSelected: r.freeSelected,
            channel: r.channel,
            briefingActive: r.briefingActive,
        });
    }
    const recent: BriefingPreferences["recent"] = [];
    for (const b of value.recent) {
        if (
            !isRecord(b) ||
            typeof b.id !== "number" ||
            typeof b.routeName !== "string" ||
            typeof b.body !== "string" ||
            typeof b.status !== "string" ||
            typeof b.departure !== "string"
        )
            return null;
        recent.push({ id: b.id, routeName: b.routeName, body: b.body, status: b.status, departure: b.departure });
    }
    return {
        pushPublicKey: value.pushPublicKey,
        emailConfigured: value.emailConfigured,
        pushDeviceCount: value.pushDeviceCount,
        routes,
        recent,
    };
};
const ok = (value: unknown) => (isRecord(value) && value.ok === true ? true : null);
export const briefingsApi = {
    preferences: () => request("/api/briefings/preferences", parse),
    update: (routeId: string, channel: string) =>
        request("/api/briefings/preferences", parse, "POST", { routeId, channel }),
    subscribe: (subscription: PushSubscriptionJSON) => request("/api/briefings/push", ok, "POST", subscription),
    unsubscribe: (endpoint: string) => request("/api/briefings/push", ok, "DELETE", { endpoint }),
};

export function pushSupported() {
    return (
        window.isSecureContext && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window
    );
}
export async function enablePush(publicKey: string) {
    if (!pushSupported())
        throw new Error(
            "Push ist hier nicht verfügbar. Nutze E-Mail oder installiere NoRain auf deinem Startbildschirm.",
        );
    const permission = await Notification.requestPermission();
    if (permission !== "granted") throw new Error("Bitte erlaube Benachrichtigungen in den Browser-Einstellungen.");
    await navigator.serviceWorker.register("/sw.js");
    const registration = await navigator.serviceWorker.ready;
    const bytes = Uint8Array.from(
        atob(publicKey.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (publicKey.length % 4)) % 4)),
        c => c.charCodeAt(0),
    );
    const subscription =
        (await registration.pushManager.getSubscription()) ??
        (await registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: bytes,
        }));
    await briefingsApi.subscribe(subscription.toJSON());
}
export async function disablePush() {
    if (!("serviceWorker" in navigator)) return;
    const registration = await navigator.serviceWorker.getRegistration("/");
    const subscription = await registration?.pushManager.getSubscription();
    if (subscription) {
        await briefingsApi.unsubscribe(subscription.endpoint);
        await subscription.unsubscribe();
    }
}
