import { computed, toValue, type MaybeRefOrGetter } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import {
    CoreApiCommunityListPublicRoutesSortEnum as PublicRouteSort,
    PublicRoutesApi,
    SharingApi,
} from "@norain/api/apis";
import type { CommentOut, PublicRouteDetail, SharingIn } from "@norain/api/models";

import { reportForecastProgress, reportStaleForecast, useForecastProgress } from "@/queries/forecastProgress";
import { recurringRouteKeys } from "@/queries/recurringRoutes";
import { awaitForecastJob } from "@/services/forecastJob";
import { useBackendHost } from "@/utils";

const publicApi = new PublicRoutesApi();
const sharingApi = new SharingApi();

export const publicRouteKeys = {
    all: ["publicRoutes"] as const,
    list: (sort: string, q: string) => [...publicRouteKeys.all, "list", sort, q] as const,
    detail: (slug: string) => [...publicRouteKeys.all, "detail", slug] as const,
    comments: (slug: string) => [...publicRouteKeys.all, "comments", slug] as const,
    forecast: (slug: string, date: string, time: string) =>
        [...publicRouteKeys.all, "forecast", slug, date, time] as const,
    sharing: (routeId: string) => ["sharing", routeId] as const,
    photos: (routeId: string) => ["sharing", routeId, "photos"] as const,
};

/** Photo and cover URLs are API paths; the images live on the backend host. */
export function mediaUrl(path: string | null | undefined): string | undefined {
    return path ? `${useBackendHost()}${path}` : undefined;
}

/** The link someone shares: the SPA's own `/r/<slug>` page. */
export function publicRouteLink(slug: string): string {
    return `${window.location.origin}/r/${slug}`;
}

export { PublicRouteSort };

export function usePublicRoutes(sort: MaybeRefOrGetter<PublicRouteSort>, q: MaybeRefOrGetter<string>) {
    return useQuery({
        queryKey: computed(() => publicRouteKeys.list(toValue(sort), toValue(q))),
        queryFn: () => publicApi.coreApiCommunityListPublicRoutes({ sort: toValue(sort), q: toValue(q) }),
        staleTime: 60_000,
    });
}

export function usePublicRoute(slug: MaybeRefOrGetter<string>) {
    return useQuery({
        queryKey: computed(() => publicRouteKeys.detail(toValue(slug))),
        queryFn: () => publicApi.coreApiCommunityGetPublicRoute({ slug: toValue(slug) }),
        enabled: () => !!toValue(slug),
        retry: false,
    });
}

/**
 * The weather on a public route for the visitor's own departure. The job belongs to the
 * visitor (or nobody), runs on the public line only, and is shaped for the visitor's tier.
 */
export function usePublicRouteForecast(
    slug: MaybeRefOrGetter<string>,
    date: MaybeRefOrGetter<string>,
    time: MaybeRefOrGetter<string>,
    enabled: MaybeRefOrGetter<boolean>,
) {
    const queryKey = computed(() => publicRouteKeys.forecast(toValue(slug), toValue(date), toValue(time)));
    const query = useQuery({
        queryKey,
        enabled: () => toValue(enabled) && !!toValue(slug) && !!toValue(date) && !!toValue(time),
        queryFn: async ({ client, queryKey: key, signal }) => {
            const job = await publicApi.coreApiCommunityPublicRouteForecast(
                { slug: toValue(slug), date: toValue(date), time: toValue(time) },
                { signal },
            );
            return await awaitForecastJob(
                job,
                reportForecastProgress(client, key),
                signal,
                undefined,
                reportStaleForecast(client, key),
            );
        },
        staleTime: 5 * 60 * 1000,
        retry: false,
    });
    return Object.assign(query, { progress: useForecastProgress(queryKey) });
}

export function useRouteComments(slug: MaybeRefOrGetter<string>) {
    return useQuery({
        queryKey: computed(() => publicRouteKeys.comments(toValue(slug))),
        queryFn: () => publicApi.coreApiCommunityListComments({ slug: toValue(slug) }),
        enabled: () => !!toValue(slug),
    });
}

export function useCommentMutations(slug: MaybeRefOrGetter<string>) {
    const client = useQueryClient();
    const refresh = async () => {
        await client.invalidateQueries({ queryKey: publicRouteKeys.comments(toValue(slug)) });
        await client.invalidateQueries({ queryKey: publicRouteKeys.detail(toValue(slug)) });
    };
    const add = useMutation({
        mutationFn: (body: string) =>
            publicApi.coreApiCommunityAddComment({ slug: toValue(slug), commentIn: { body } }),
        onSuccess: refresh,
    });
    const edit = useMutation({
        mutationFn: ({ id, body }: { id: string; body: string }) =>
            publicApi.coreApiCommunityEditComment({ commentId: id, commentIn: { body } }),
        onSuccess: refresh,
    });
    const remove = useMutation({
        mutationFn: (comment: CommentOut) => publicApi.coreApiCommunityDeleteComment({ commentId: comment.id }),
        onSuccess: refresh,
    });
    return { add, edit, remove };
}

export function useLikeMutation(slug: MaybeRefOrGetter<string>) {
    const client = useQueryClient();
    return useMutation({
        mutationFn: (like: boolean) =>
            like
                ? publicApi.coreApiCommunityLikeRoute({ slug: toValue(slug) })
                : publicApi.coreApiCommunityUnlikeRoute({ slug: toValue(slug) }),
        onSuccess: result => {
            client.setQueryData<PublicRouteDetail>(publicRouteKeys.detail(toValue(slug)), route =>
                route ? { ...route, liked: result.liked, likeCount: result.likeCount } : route,
            );
        },
    });
}

export function useCopyRoute(slug: MaybeRefOrGetter<string>) {
    const client = useQueryClient();
    return useMutation({
        mutationFn: () => publicApi.coreApiCommunityCopyRoute({ slug: toValue(slug), copyIn: {} }),
        onSuccess: () => client.invalidateQueries({ queryKey: recurringRouteKeys.all }),
    });
}

// -- the owner's side -------------------------------------------------------------

export function useSharing(routeId: MaybeRefOrGetter<string>) {
    return useQuery({
        queryKey: computed(() => publicRouteKeys.sharing(toValue(routeId))),
        queryFn: () => sharingApi.coreApiCommunityGetSharing({ routeId: toValue(routeId) }),
        enabled: () => !!toValue(routeId),
    });
}

export function useUpdateSharing(routeId: MaybeRefOrGetter<string>) {
    const client = useQueryClient();
    return useMutation({
        mutationFn: (sharingIn: SharingIn) =>
            sharingApi.coreApiCommunityUpdateSharing({ routeId: toValue(routeId), sharingIn }),
        onSuccess: async result => {
            client.setQueryData(publicRouteKeys.sharing(toValue(routeId)), result);
            await client.invalidateQueries({ queryKey: recurringRouteKeys.all });
            await client.invalidateQueries({ queryKey: publicRouteKeys.all });
        },
    });
}

export function useRoutePhotos(routeId: MaybeRefOrGetter<string>) {
    return useQuery({
        queryKey: computed(() => publicRouteKeys.photos(toValue(routeId))),
        queryFn: () => sharingApi.coreApiCommunityListPhotos({ routeId: toValue(routeId) }),
        enabled: () => !!toValue(routeId),
    });
}

export function usePhotoMutations(routeId: MaybeRefOrGetter<string>) {
    const client = useQueryClient();
    const refresh = async () => {
        await client.invalidateQueries({ queryKey: publicRouteKeys.photos(toValue(routeId)) });
        await client.invalidateQueries({ queryKey: publicRouteKeys.all });
    };
    const upload = useMutation({
        mutationFn: ({ file, lat, lon }: { file: Blob; lat?: number; lon?: number }) =>
            sharingApi.coreApiCommunityUploadPhoto({ routeId: toValue(routeId), file, lat, lon }),
        onSuccess: refresh,
    });
    const update = useMutation({
        mutationFn: ({
            id,
            caption,
            lat,
            lon,
        }: {
            id: string;
            caption: string;
            lat?: number | null;
            lon?: number | null;
        }) =>
            sharingApi.coreApiCommunityUpdatePhoto({
                routeId: toValue(routeId),
                photoId: id,
                photoUpdateIn: { caption, lat: lat ?? null, lon: lon ?? null },
            }),
        onSuccess: refresh,
    });
    const remove = useMutation({
        mutationFn: (id: string) => sharingApi.coreApiCommunityDeletePhoto({ routeId: toValue(routeId), photoId: id }),
        onSuccess: refresh,
    });
    return { upload, update, remove };
}
