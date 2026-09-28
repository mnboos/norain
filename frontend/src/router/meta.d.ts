import "vue-router";

declare module "vue-router" {
    interface RouteMeta {
        bare?: boolean;
        /** The page's name in the catalogs (`pages.*`), for the document title. */
        titleKey?: string;
    }
}
