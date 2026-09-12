import { defineComponent, h, nextTick } from "vue";
import { mount } from "@vue/test-utils";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { describe, expect, it } from "vitest";

import { reportForecastProgress, useForecastProgress } from "@/queries/forecastProgress";

describe("forecast progress", () => {
    it("reaches the page through the query cache", async () => {
        const client = new QueryClient();
        const forecastKey = ["routeWeather", "forecast", 1] as const;
        let progress: ReturnType<typeof useForecastProgress> | undefined;
        mount(
            defineComponent({
                setup() {
                    progress = useForecastProgress(forecastKey);
                    return () => h("div");
                },
            }),
            { global: { plugins: [[VueQueryPlugin, { queryClient: client }]] } },
        );

        expect(progress?.value).toBeNull();
        reportForecastProgress(client, forecastKey)({ status: "fetching", cellsSettled: 2, cellsTotal: 5 });
        await nextTick();
        expect(progress?.value).toEqual({ status: "fetching", cellsSettled: 2, cellsTotal: 5 });
    });
});
