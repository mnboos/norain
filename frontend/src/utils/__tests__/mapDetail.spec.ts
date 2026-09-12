import { describe, expect, it } from "vitest";
import { finerDetail, lineDetailForZoom } from "../mapDetail";

describe("route line detail", () => {
    it("asks for more detail only as the map zooms in", () => {
        expect(lineDetailForZoom(6)).toBe("coarse");
        expect(lineDetailForZoom(10.99)).toBe("coarse");
        expect(lineDetailForZoom(11)).toBe("medium");
        expect(lineDetailForZoom(13.5)).toBe("medium");
        expect(lineDetailForZoom(14)).toBe("full");
        expect(lineDetailForZoom(18)).toBe("full");
    });
    it("never trades a loaded finer line for a coarser one", () => {
        expect(finerDetail("full", "medium")).toBe("full");
        expect(finerDetail("coarse", "medium")).toBe("medium");
        expect(finerDetail("coarse", "coarse")).toBe("coarse");
    });
});
