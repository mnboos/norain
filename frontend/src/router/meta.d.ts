import "vue-router";

declare module "vue-router" {
    interface RouteMeta {
        bare?: boolean;
    }
}
