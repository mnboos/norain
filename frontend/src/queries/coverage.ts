import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { CoverageApi } from "@norain/api/apis";
import type { CoverageOut, VoteOut } from "@norain/api/models";

const coverageApi = new CoverageApi();

export const coverageKeys = {
    all: ["coverage"] as const,
};

/** Where Meteolane works and what visitors wish for. Open to anyone, signed in or not. */
export function useCoverage() {
    return useQuery({
        queryKey: coverageKeys.all,
        queryFn: () => coverageApi.coreApiCoverageGetCoverage(),
        staleTime: 60_000,
    });
}

/** Cast or withdraw a vote; the reply's count goes straight into the list. */
export function useCoverageVote() {
    const client = useQueryClient();
    return useMutation({
        mutationFn: ({ code, voted }: { code: string; voted: boolean }) =>
            voted ? coverageApi.coreApiCoverageVote({ code }) : coverageApi.coreApiCoverageWithdrawVote({ code }),
        onSuccess: (reply: VoteOut) => {
            client.setQueryData<CoverageOut>(coverageKeys.all, data => {
                if (!data) return data;
                const known = data.areas.some(area => area.code === reply.code);
                const areas = known
                    ? data.areas.map(area =>
                          area.code === reply.code ? { ...area, votes: reply.votes, voted: reply.voted } : area,
                      )
                    : [...data.areas, { code: reply.code, votes: reply.votes, voted: reply.voted }];
                return { ...data, areas };
            });
        },
    });
}

export function subscribeToArea(code: string, email: string) {
    return coverageApi.coreApiCoverageSubscribe({ code, subscribeIn: { email } });
}

export function confirmAreaSubscription(token: string) {
    return coverageApi.coreApiCoverageConfirmSubscription({ tokenIn: { token } });
}

export function unsubscribeFromArea(token: string) {
    return coverageApi.coreApiCoverageUnsubscribe({ tokenIn: { token } });
}
