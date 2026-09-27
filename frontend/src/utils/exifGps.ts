/**
 * Where a JPEG says it was taken, read in the browser before upload.
 *
 * The server re-encodes every photo without metadata, so this is the only place the position
 * can come from. It is sent as a separate field, only when the uploader leaves "Aufnahmeort
 * übernehmen" on, and the public page still hides it inside a privacy zone.
 */
export interface LatLon {
    lat: number;
    lon: number;
}

const GPS_IFD_POINTER = 0x8825;
const TAG = { latRef: 1, lat: 2, lonRef: 3, lon: 4 } as const;

function readIfd(view: DataView, tiff: number, offset: number, little: boolean): Map<number, number> {
    // tag -> offset of its 12-byte entry
    const entries = new Map<number, number>();
    const count = view.getUint16(tiff + offset, little);
    for (let i = 0; i < count; i++) {
        const entry = tiff + offset + 2 + i * 12;
        entries.set(view.getUint16(entry, little), entry);
    }
    return entries;
}

function rationals(view: DataView, tiff: number, entry: number, little: boolean): number[] {
    const at = tiff + view.getUint32(entry + 8, little);
    return [0, 1, 2].map(i => {
        const denominator = view.getUint32(at + i * 8 + 4, little);
        return denominator ? view.getUint32(at + i * 8, little) / denominator : NaN;
    });
}

function degrees(view: DataView, tiff: number, entry: number | undefined, little: boolean): number | null {
    if (entry === undefined) return null;
    const [d = NaN, m = NaN, s = NaN] = rationals(view, tiff, entry, little);
    const value = d + m / 60 + s / 3600;
    return Number.isFinite(value) ? value : null;
}

function ref(view: DataView, entry: number | undefined): string {
    // An ASCII value of up to 4 bytes sits inside the entry itself.
    return entry === undefined ? "" : String.fromCharCode(view.getUint8(entry + 8));
}

/** The GPS position in a JPEG's EXIF block, or null when it has none (or is not a JPEG). */
export function readExifGps(buffer: ArrayBuffer): LatLon | null {
    const view = new DataView(buffer);
    try {
        if (view.getUint16(0) !== 0xffd8) return null;
        let offset = 2;
        while (offset + 4 <= view.byteLength) {
            const marker = view.getUint16(offset);
            const length = view.getUint16(offset + 2);
            if (marker === 0xffe1 && view.getUint32(offset + 4) === 0x45786966 /* "Exif" */) {
                const tiff = offset + 10;
                const little = view.getUint16(tiff) === 0x4949;
                const ifd0 = readIfd(view, tiff, view.getUint32(tiff + 4, little), little);
                const pointer = ifd0.get(GPS_IFD_POINTER);
                if (pointer === undefined) return null;
                const gps = readIfd(view, tiff, view.getUint32(pointer + 8, little), little);
                const lat = degrees(view, tiff, gps.get(TAG.lat), little);
                const lon = degrees(view, tiff, gps.get(TAG.lon), little);
                if (lat === null || lon === null || (lat === 0 && lon === 0)) return null;
                const position = {
                    lat: ref(view, gps.get(TAG.latRef)) === "S" ? -lat : lat,
                    lon: ref(view, gps.get(TAG.lonRef)) === "W" ? -lon : lon,
                };
                return Math.abs(position.lat) <= 90 && Math.abs(position.lon) <= 180 ? position : null;
            }
            if (marker === 0xffda || (marker & 0xff00) !== 0xff00) return null; // image data: no EXIF
            offset += 2 + length;
        }
    } catch {
        // A truncated or malformed block reads past the end: treat it as no position.
    }
    return null;
}
