export function getCookie(name: string) {
    let cookieValue: string | null = null;
    if (document.cookie && document.cookie !== "") {
        const cookies = document.cookie.split(";");
        for (const item of cookies) {
            const cookie = item.trim();
            // Does this cookie string begin with the name we want?
            if (cookie.substring(0, name.length + 1) === name + "=") {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

export function useIsLocalhost() {
    return location.hostname === "localhost" || location.hostname === "127.0.0.1";
}

/**
 * Returns the correct hostname, port and protocol to be used for the communication with the backend.
 * @param protocol The protocol to be prepended to the host. Has to end with doublepoint, e.g. `http:`, `https:`, `wss:`
 */
export function useBackendHost(protocol?: string  ) {
    const isLocalhost = useIsLocalhost();
    const host = isLocalhost ? `${location.hostname}:${import.meta.env.VITE_BACKEND_PORT}` : location.host;
    protocol = protocol ?? location.protocol;
    return `${protocol}//${host}`;
}
