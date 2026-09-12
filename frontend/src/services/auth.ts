import { type DetailResponse, request } from "@/services/http";

export interface SessionUser {
    email: string;
    username: string;
}

export interface SessionState {
    authenticated: boolean;
    user: SessionUser | null;
}

export const authApi = {
    session: () => request<SessionState>("/api/auth/session"),
    signup: (email: string, username: string, password: string) =>
        request<DetailResponse>("/api/auth/signup", "POST", { email, username, password }),
    verifyEmail: (uid: string, token: string) => request<DetailResponse>("/api/auth/verify-email", "POST", { uid, token }),
    /** `identifier` is an email address or a username — the backend resolves either. */
    login: (identifier: string, password: string) =>
        request<SessionState>("/api/auth/login", "POST", { identifier, password }),
    logout: () => request<SessionState>("/api/auth/logout", "POST"),
    passwordReset: (email: string) => request<DetailResponse>("/api/auth/password-reset", "POST", { email }),
    passwordResetConfirm: (uid: string, token: string, password: string) =>
        request<DetailResponse>("/api/auth/password-reset/confirm", "POST", { uid, token, password }),
};
