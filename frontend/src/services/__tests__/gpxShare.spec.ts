import { afterEach, describe, expect, it, vi } from "vitest";
import { canShareFiles, shareFile } from "@/services/gpx";

const file = new File(["<gpx/>"], "Ride.gpx", { type: "application/gpx+xml" });

function stubShare(canShare: boolean, share: () => Promise<void>, touch = true) {
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: touch && query === "(pointer: coarse)" }));
    const shareMock = vi.fn(share);
    Object.assign(navigator, { canShare: vi.fn(() => canShare), share: shareMock });
    return shareMock;
}

describe("shareFile", () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    URL.createObjectURL = vi.fn(() => "blob:gpx");
    URL.revokeObjectURL = vi.fn();
    afterEach(() => { click.mockClear(); });

    it("opens the share sheet with the file on a touch device", async () => {
        const share = stubShare(true, () => Promise.resolve());
        expect(canShareFiles()).toBe(true);
        expect(await shareFile(file)).toBe("shared");
        expect(share).toHaveBeenCalledWith({ files: [file], title: "Ride" });
        expect(click).not.toHaveBeenCalled();
    });

    it("downloads where files cannot be shared", async () => {
        stubShare(false, () => Promise.resolve());
        expect(canShareFiles()).toBe(false);
        expect(await shareFile(file)).toBe("downloaded");
        expect(click).toHaveBeenCalledOnce();
    });

    it("downloads on a desktop even where it could share", async () => {
        const share = stubShare(true, () => Promise.resolve(), false);
        expect(canShareFiles()).toBe(false);
        expect(await shareFile(file)).toBe("downloaded");
        expect(share).not.toHaveBeenCalled();
        expect(click).toHaveBeenCalledOnce();
    });

    it("does nothing when the user closes the sheet", async () => {
        stubShare(true, () => Promise.reject(new DOMException("", "AbortError")));
        expect(await shareFile(file)).toBe("cancelled");
        expect(click).not.toHaveBeenCalled();
    });

    it("asks for a fresh tap when the gesture expired", async () => {
        stubShare(true, () => Promise.reject(new DOMException("", "NotAllowedError")));
        expect(await shareFile(file)).toBe("blocked");
        expect(click).not.toHaveBeenCalled();
    });
});
