import { describe, expect, it } from "vitest";
import { type CoverageAreaOut, CoverageAreaOutStatusEnum as Status } from "@norain/api/models";

import { areaName, flagCode, rankedWishes, voteOptions } from "@/utils/coverage";

const area = (code: string, extra: Partial<CoverageAreaOut> = {}): CoverageAreaOut => ({ code, ...extra });

describe("areaName", () => {
    it("names a country from its code in the reader's language", () => {
        expect(areaName("DE", null, "de")).toBe("Deutschland");
        expect(areaName("DE", null, "en")).toBe("Germany");
    });

    it("uses the admin's names for a region, English falling back to German", () => {
        expect(areaName("IT-32", { name: "Südtirol", nameEn: "South Tyrol" }, "en")).toBe("South Tyrol");
        expect(areaName("IT-32", { name: "Südtirol" }, "en")).toBe("Südtirol");
        expect(areaName("IT-32", null, "de")).toBe("IT-32");
    });
});

describe("flagCode", () => {
    it("gives the country's flag, also for a region below it", () => {
        expect(flagCode("CH")).toBe("ch");
        expect(flagCode("IT-32")).toBe("it");
        expect(flagCode("xx")).toBeNull();
        expect(flagCode("")).toBeNull();
    });
});

describe("rankedWishes", () => {
    it("lists areas with votes that are not covered, most wanted first", () => {
        const areas = [
            area("CH", { status: Status.Covered, votes: 9 }),
            area("AT", { votes: 2 }),
            area("FR", { votes: 5 }),
            area("DE", { votes: 2 }),
            area("IT-32", { status: Status.Candidate, name: "Südtirol", votes: 0 }),
        ];
        expect(rankedWishes(areas, "de").map(a => a.code)).toEqual(["FR", "DE", "AT"]);
    });

    it("keeps the viewer's own vote before the daily count includes it", () => {
        const areas = [area("AT", { votes: 2 }), area("ES", { votes: 0, voted: true }), area("PT", { votes: 0 })];
        expect(rankedWishes(areas, "de").map(a => a.code)).toEqual(["AT", "ES"]);
    });
});

describe("voteOptions", () => {
    it("offers the countries plus listed regions, sorted by name", () => {
        const options = voteOptions(
            ["AT", "DE"],
            [area("IT-32", { status: Status.Candidate, name: "Südtirol" })],
            "de",
        );
        expect(options).toEqual([
            { code: "DE", label: "Deutschland" },
            { code: "AT", label: "Österreich" },
            { code: "IT-32", label: "Südtirol" },
        ]);
    });
});
