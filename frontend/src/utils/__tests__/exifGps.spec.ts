import { describe, expect, it } from "vitest";

import { readExifGps } from "../exifGps";

/** A JPEG holding only an EXIF block with a GPS position, in either byte order. */
function jpegWithGps(
    lat: [number, number, number],
    latRef: string,
    lon: [number, number, number],
    lonRef: string,
    little = false,
) {
    const tiff = new DataView(new ArrayBuffer(128));
    const u16 = (at: number, v: number) => {
        tiff.setUint16(at, v, little);
    };
    const u32 = (at: number, v: number) => {
        tiff.setUint32(at, v, little);
    };
    tiff.setUint16(0, little ? 0x4949 : 0x4d4d);
    u16(2, 42);
    u32(4, 8);
    // IFD0: one entry, the GPS IFD pointer.
    u16(8, 1);
    u16(10, 0x8825);
    u16(12, 4);
    u32(14, 1);
    u32(18, 26);
    u32(22, 0);
    // GPS IFD at 26: LatRef, Lat, LonRef, Lon.
    u16(26, 4);
    const entry = (n: number, tag: number, type: number, count: number) => {
        const at = 28 + n * 12;
        u16(at, tag);
        u16(at + 2, type);
        u32(at + 4, count);
        return at + 8;
    };
    tiff.setUint8(entry(0, 1, 2, 2), latRef.charCodeAt(0));
    u32(entry(1, 2, 5, 3), 80);
    tiff.setUint8(entry(2, 3, 2, 2), lonRef.charCodeAt(0));
    u32(entry(3, 4, 5, 3), 104);
    [...lat, ...lon].forEach((value, i) => {
        u32(80 + i * 8, Math.round(value * 100));
        u32(80 + i * 8 + 4, 100);
    });

    const header = [0xff, 0xd8, 0xff, 0xe1, 0, 2 + 6 + 128, 0x45, 0x78, 0x69, 0x66, 0, 0];
    const bytes = new Uint8Array(header.length + 128 + 2);
    bytes.set(header);
    bytes.set(new Uint8Array(tiff.buffer), header.length);
    bytes.set([0xff, 0xd9], header.length + 128);
    return bytes.buffer;
}

describe("readExifGps", () => {
    it("reads degrees, minutes and seconds", () => {
        const gps = readExifGps(jpegWithGps([47, 22, 30], "N", [8, 32, 24], "E"));
        expect(gps?.lat).toBeCloseTo(47.375, 6);
        expect(gps?.lon).toBeCloseTo(8.54, 6);
    });

    it("honours the hemisphere and little-endian files", () => {
        const gps = readExifGps(jpegWithGps([33, 51, 0], "S", [151, 12, 0], "W", true));
        expect(gps?.lat).toBeCloseTo(-33.85, 6);
        expect(gps?.lon).toBeCloseTo(-151.2, 6);
    });

    it("returns null for anything without a position", () => {
        expect(readExifGps(new Uint8Array([0x89, 0x50, 0x4e, 0x47]).buffer)).toBeNull();
        expect(readExifGps(new Uint8Array([0xff, 0xd8, 0xff, 0xda, 0, 2]).buffer)).toBeNull();
        expect(readExifGps(jpegWithGps([0, 0, 0], "N", [0, 0, 0], "E"))).toBeNull();
        expect(readExifGps(jpegWithGps([47, 0, 0], "N", [8, 0, 0], "E").slice(0, 60))).toBeNull();
    });
});
