import { type DetailResponse, isRecord, type Parse, parseDetail, request } from "@/services/http";

export interface SessionUser {
    email: string;
    username: string;
}

export interface SessionState {
    authenticated: boolean;
    user: SessionUser | null;
}

const parseUser: Parse<SessionUser> = (value) => {
    if (!isRecord(value)) return null;
    const { email, username } = value;
    return typeof email === "string" && typeof username === "string" ? { email, username } : null;
};

const parseSession: Parse<SessionState> = (value) => {
    if (!isRecord(value) || typeof value.authenticated !== "boolean") return null;
    const authenticated = value.authenticated;
    if (value.user == null) return { authenticated, user: null };
    const user = parseUser(value.user);
    return user ? { authenticated, user } : null;
};

export const authApi = {
    session: () => request<SessionState>("/api/auth/session", parseSession),
    signup: (email: string, username: string, password: string) =>
        request<DetailResponse>("/api/auth/signup", parseDetail, "POST", { email, username, password }),
    verifyEmail: (uid: string, token: string) =>
        request<DetailResponse>("/api/auth/verify-email", parseDetail, "POST", { uid, token }),
    /** `identifier` is an email address or a username — the backend resolves either. */
    login: (identifier: string, password: string) =>
        request<SessionState>("/api/auth/login", parseSession, "POST", { identifier, password }),
    logout: () => request<SessionState>("/api/auth/logout", parseSession, "POST"),
    passwordReset: (email: string) =>
        request<DetailResponse>("/api/auth/password-reset", parseDetail, "POST", { email }),
    passwordResetConfirm: (uid: string, token: string, password: string) =>
        request<DetailResponse>("/api/auth/password-reset/confirm", parseDetail, "POST", { uid, token, password }),
};
