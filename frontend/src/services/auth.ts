import { ensureRecognized } from "@/services/browserRecognition";
import { type AllauthReply, allauthRequest, isRecord, type Parse, request } from "@/services/http";
import { type AppLocale, isAppLocale } from "@/i18n";

export interface SessionUser {
    /** Optional while older backend instances finish rolling out. */
    id?: string;
    email: string;
    username: string;
    /** False between the two sign-up steps: the email is verified, the username is not picked yet. */
    signupComplete: boolean;
    /** False for an account that signs in by emailed code only (the password is optional). */
    hasPassword: boolean;
    /** Pre-selected in the route, journey and map forms. */
    defaultProfile: BikeProfile;
    /** The account's language: the app's, the API's messages and every mail. */
    language: AppLocale | null;
}

export type BikeProfile = "bike" | "ebike" | "fast_ebike" | "hike";

const BIKE_PROFILES: readonly string[] = ["bike", "ebike", "fast_ebike", "hike"];

function isBikeProfile(value: unknown): value is BikeProfile {
    return typeof value === "string" && BIKE_PROFILES.includes(value);
}

export interface UsernameCheck {
    available: boolean;
    detail: string;
}

export interface SessionState {
    authenticated: boolean;
    user: SessionUser | null;
    system?: { allowed: boolean; loginUrl: string };
}

const parseUser: Parse<SessionUser> = value => {
    if (!isRecord(value)) return null;
    const { id, email, username, signup_complete, has_password, default_profile, language } = value;
    return typeof email === "string" && typeof username === "string"
        ? {
              email,
              username,
              // An older backend has no second step, so its accounts are always complete.
              signupComplete: typeof signup_complete === "boolean" ? signup_complete : true,
              hasPassword: typeof has_password === "boolean" ? has_password : true,
              defaultProfile: isBikeProfile(default_profile) ? default_profile : "bike",
              language: isAppLocale(language) ? language : null,
              ...(typeof id === "string" ? { id } : {}),
          }
        : null;
};

const parseSession: Parse<SessionState> = value => {
    if (!isRecord(value) || typeof value.authenticated !== "boolean") return null;
    const authenticated = value.authenticated;
    if (value.user == null) return { authenticated, user: null };
    const user = parseUser(value.user);
    const system =
        isRecord(value.system) &&
        typeof value.system.allowed === "boolean" &&
        typeof value.system.login_url === "string"
            ? { allowed: value.system.allowed, loginUrl: value.system.login_url }
            : undefined;
    return user ? { authenticated, user, system } : null;
};

const parseUsernameCheck: Parse<UsernameCheck> = value =>
    isRecord(value) && typeof value.available === "boolean"
        ? { available: value.available, detail: typeof value.detail === "string" ? value.detail : "" }
        : null;

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
    /** Step 2 of sign-up: the username, the default bike profile and, optionally, a password. */
    completeSignup: (username: string, password: string, defaultProfile: BikeProfile, language?: AppLocale) =>
        request<SessionState>("/api/auth/complete-signup", parseSession, "POST", {
            username,
            password,
            default_profile: defaultProfile,
            ...(language ? { language } : {}),
        }),
    /** Would step 2 accept this username? The same rules as the save. */
    usernameAvailable: (username: string) =>
        request<UsernameCheck>(
            `/api/auth/username-available?username=${encodeURIComponent(username)}`,
            parseUsernameCheck,
        ),
    /** Either setting alone; the other stays as it is. */
    updateProfile: (changes: { defaultProfile?: BikeProfile; language?: AppLocale }) =>
        request<SessionState>("/api/auth/profile", parseSession, "POST", {
            ...(changes.defaultProfile ? { default_profile: changes.defaultProfile } : {}),
            ...(changes.language ? { language: changes.language } : {}),
        }),

    /** Step 1 of sign-up: allauth creates the account and mails a code. Limited per browser. */
    signup: async (email: string) => {
        await ensureRecognized();
        return allauthRequest("/auth/signup", "POST", { email });
    },
    /** The code from the sign-up mail. Only works in the session that started the sign-up. */
    verifyEmailCode: (code: string) => allauthRequest("/auth/email/verify", "POST", { key: code }),
    /** A new sign-up code; allauth allows two, at least 10 s apart. */
    resendEmailCode: () => allauthRequest("/auth/email/verify/resend", "POST"),
    /**
     * `identifier` is an email address or a username. It always goes as `username`:
     * allauth tries that value as an email first and then as a username, so the app never
     * has to guess from an "@" (usernames may contain one).
     */
    login: (identifier: string, password: string) =>
        allauthRequest("/auth/login", "POST", { username: identifier, password }),
    /** Mails a sign-in code. Limited per browser, like `signup`. */
    requestLoginCode: async (email: string) => {
        await ensureRecognized();
        return allauthRequest("/auth/code/request", "POST", { email });
    },
    confirmLoginCode: (code: string) => allauthRequest("/auth/code/confirm", "POST", { code }),
    requestPasswordReset: (email: string) => allauthRequest("/auth/password/request", "POST", { email }),
    resetPassword: (key: string, password: string) => allauthRequest("/auth/password/reset", "POST", { key, password }),
    logout: () => allauthRequest("/auth/session", "DELETE"),
};
