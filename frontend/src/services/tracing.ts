/** Only send trace context to our API, including the separate local backend port. */
export function backendTraceTargets(backendHost: string): RegExp[] {
    const origin = new URL(backendHost).origin.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return [new RegExp(`^${origin}/api(?:/|[?#]|$)`), /^\/api(?:\/|[?#]|$)/];
}
