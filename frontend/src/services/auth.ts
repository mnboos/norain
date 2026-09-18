import { type AllauthReply, allauthRequest, isRecord, type Parse, request } from "@/services/http";

export interface SessionUser {
    /** Optional while older backend instances finish rolling out. */
    id?: string;
    email: string;
    username: string;
    /** False between the two sign-up steps: the email is verified, username and password are not set yet. */
    signupComplete: boolean;
}

export interface SessionState {
    authenticated: boolean;
    user: SessionUser | null;
}

const parseUser: Parse<SessionUser> = value => {
    if (!isRecord(value)) return null;
    const { id, email, username, signup_complete } = value;
    return typeof email === "string" && typeof username === "string"
        ? {
              email,
              username,
              // An older backend has no second step, so its accounts are always complete.
              signupComplete: typeof signup_complete === "boolean" ? signup_complete : true,
              ...(typeof id === "string" ? { id } : {}),
          }
        : null;
};

const parseSession: Parse<SessionState> = value => {
    if (!isRecord(value) || typeof value.authenticated !== "boolean") return null;
    const authenticated = value.authenticated;
    if (value.user == null) return { authenticated, user: null };
    const user = parseUser(value.user);
    return user ? { authenticated, user } : null;
};

/** The ids of the steps allauth is still waiting for, e.g. `verify_email` or `login_by_code`. */
export function pendingFlows(reply: AllauthReply): string[] {
    const flows = reply.data.flows;
    if (!Array.isArray(flows)) return [];
    return flows.flatMap(flow =>
        isRecord(flow) && flow.is_pending === true && typeof flow.id === "string" ? [flow.id] : [],
    );
}

/** Did this reply end with the user signed in? */
export function signedIn(reply: AllauthReply): boolean {
    return reply.status === 200 && reply.meta.is_authenticated === true;
}

export const authApi = {
    /** Our own endpoint: the session as the app needs it, and the CSRF cookie. */
    session: () => request<SessionState>("/api/auth/session", parseSession),
    /** Step 2 of sign-up: pick the username and password. */
    completeSignup: (username: string, password: string) =>
        request<SessionState>("/api/auth/complete-signup", parseSession, "POST", { username, password }),

    /** Step 1 of sign-up: allauth creates the account and mails a link. */
    signup: (email: string) => allauthRequest("/auth/signup", "POST", { email }),
    /** Which address a verification link belongs to, without using it up. */
    verifyEmailInfo: async (key: string): Promise<string | null> => {
        const reply = await allauthRequest("/auth/email/verify", "GET", undefined, { "X-Email-Verification-Key": key });
        return typeof reply.data.email === "string" ? reply.data.email : null;
    },
    verifyEmail: (key: string) => allauthRequest("/auth/email/verify", "POST", { key }),
    /**
     * `identifier` is an email address or a username. It always goes as `username`:
     * allauth tries that value as an email first and then as a username, so the app never
     * has to guess from an "@" (usernames may contain one).
     */
    login: (identifier: string, password: string) =>
        allauthRequest("/auth/login", "POST", { username: identifier, password }),
    requestLoginCode: (email: string) => allauthRequest("/auth/code/request", "POST", { email }),
    confirmLoginCode: (code: string) => allauthRequest("/auth/code/confirm", "POST", { code }),
    requestPasswordReset: (email: string) => allauthRequest("/auth/password/request", "POST", { email }),
    resetPassword: (key: string, password: string) => allauthRequest("/auth/password/reset", "POST", { key, password }),
    logout: () => allauthRequest("/auth/session", "DELETE"),
};
