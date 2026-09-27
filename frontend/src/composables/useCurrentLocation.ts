import { ref } from "vue";
import { PlacesApi } from "@norain/api/apis";
import type { PlacesSearchResult } from "@norain/api/models";

const api = new PlacesApi();

/** Only secure contexts (HTTPS, localhost) have the Geolocation API; elsewhere the button stays hidden. */
export const geolocationAvailable = typeof navigator !== "undefined" && "geolocation" in navigator;

/** Why the browser gave no position, in words the user can act on. */
export function geolocationErrorMessage(error: GeolocationPositionError): string {
    if (error.code === error.PERMISSION_DENIED) return "Der Zugriff auf den Standort wurde nicht erlaubt.";
    if (error.code === error.TIMEOUT) return "Der Standort konnte nicht rechtzeitig bestimmt werden.";
    return "Der Standort konnte nicht bestimmt werden.";
}

/** A place at the point itself, named by its coordinates, as the map picker names one. */
export function coordinatePlace(lat: number, lon: number): PlacesSearchResult {
    return {
        type: "Feature",
        geometry: { type: "Point", coordinates: [lon, lat] },
        properties: { name: `${lat.toFixed(5)}, ${lon.toFixed(5)}`, city: null, state: "", countrycode: "", showCanton: false },
    };
}

function position(): Promise<GeolocationPosition> {
    return new Promise((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(
            resolve,
            error => {
                reject(new Error(geolocationErrorMessage(error)));
            },
            { enableHighAccuracy: true, timeout: 15_000, maximumAge: 60_000 },
        );
    });
}

/**
 * The user's position as a place. The name comes from the geocoder; when it has none (or fails),
 * the coordinates name it, since the position is what matters. Rejects with a message to show.
 */
export function useCurrentLocation() {
    const locating = ref(false);

    async function locate(): Promise<PlacesSearchResult> {
        locating.value = true;
        try {
            const { latitude: lat, longitude: lon } = (await position()).coords;
            try {
                return await api.coreApiPlacesReverse({ lat, lon });
            } catch {
                return coordinatePlace(lat, lon);
            }
        } finally {
            locating.value = false;
        }
    }

    return { locating, locate };
}
