/// <reference types="vite/client" />

interface ImportMetaEnv {
    /** Django's port on localhost, from BACKEND_PORT in the root .env (see vite.config.ts). */
    readonly VITE_BACKEND_PORT: string;
}

declare module "*.vue" {
    import type { DefineComponent } from "vue";
    const component: DefineComponent<object, object, unknown>;
    export default component;
}
