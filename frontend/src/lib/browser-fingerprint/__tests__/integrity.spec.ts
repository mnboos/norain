import { afterEach, describe, expect, it, vi } from "vitest";
import { canvasIntegrity } from "../realm";
import { audioIsExact, nanBits } from "../signals";

afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

/** Just enough of a 2D canvas to draw opaque rectangles and put pixels; `noise` alters the read-back. */
function fakeCanvas(noise: (data: Uint8ClampedArray) => void) {
    const canvas = { width: 0, height: 0, getContext: () => context };
    let pixels = new Uint8ClampedArray(0);
    let fill = [0, 0, 0];
    const context = {
        set fillStyle(value: string) {
            fill = (value.match(/\d+/g) ?? []).map(Number);
        },
        fillRect(x: number, y: number, w: number, h: number) {
            if (pixels.length !== canvas.width * canvas.height * 4)
                pixels = new Uint8ClampedArray(canvas.width * canvas.height * 4);
            for (let row = y; row < y + h; row++)
                for (let col = x; col < x + w; col++) pixels.set([...fill, 255], (row * canvas.width + col) * 4);
        },
        putImageData(image: { data: Uint8ClampedArray; width: number; height: number }, x: number, y: number) {
            for (let row = 0; row < image.height; row++)
                for (let col = 0; col < image.width; col++)
                    pixels.set(
                        image.data.subarray((row * image.width + col) * 4, (row * image.width + col + 1) * 4),
                        ((y + row) * canvas.width + x + col) * 4,
                    );
        },
        getImageData() {
            const data = pixels.slice();
            noise(data);
            return { data };
        },
    };
    // eslint-disable-next-line @typescript-eslint/consistent-type-assertions -- a stand-in for jsdom's missing canvas.
    vi.spyOn(document, "createElement").mockReturnValue(canvas as unknown as HTMLElement);
    vi.stubGlobal(
        "ImageData",
        class {
            data: Uint8ClampedArray;
            constructor(
                readonly width: number,
                readonly height: number,
            ) {
                this.data = new Uint8ClampedArray(width * height * 4);
            }
        },
    );
}

describe("canvas integrity", () => {
    it("reads an exact canvas as clean", () => {
        fakeCanvas(() => undefined);
        expect(canvasIntegrity()).toBe("clean");
    });

    it("sees noise that spares the solid fills and touches only varied pixels", () => {
        fakeCanvas(data => {
            // Pixel (20, 5) is in the per-pixel colour block; (40, 9) in the put one.
            data[(5 * 48 + 20) * 4] = 255 - (data[(5 * 48 + 20) * 4] ?? 0);
            data[(9 * 48 + 40) * 4 + 2] = 255 - (data[(9 * 48 + 40) * 4 + 2] ?? 0);
        });
        expect(canvasIntegrity()).toBe("noise:2");
    });
});

/** An offline context that plays its one buffer source through, optionally with a salt added. */
function fakeAudio(salt: number) {
    vi.stubGlobal(
        "OfflineAudioContext",
        class {
            destination = {};
            private source?: { buffer: { data: Float32Array } };
            createBuffer(_channels: number, length: number) {
                const data = new Float32Array(length);
                return {
                    data,
                    copyToChannel: (values: Float32Array) => {
                        data.set(values);
                    },
                };
            }
            createBufferSource() {
                const source = {
                    buffer: { data: new Float32Array(0) },
                    connect: vi.fn(),
                    start: vi.fn(),
                    disconnect: vi.fn(),
                };
                this.source = source;
                return source;
            }
            startRendering() {
                const output = Float32Array.from(this.source?.buffer.data ?? [], value => value + salt);
                return Promise.resolve({ getChannelData: () => output });
            }
        },
    );
}

describe("audio known answer", () => {
    it("accepts a pass-through that is exact", async () => {
        fakeAudio(0);
        expect(await audioIsExact()).toBe(true);
    });

    it("refuses one with noise too small for a repeated read to notice", async () => {
        fakeAudio(1e-7);
        expect(await audioIsExact()).toBe(false);
    });
});

describe("WebAssembly NaN bits", () => {
    it("reads the hardware's NaN for f32 and f64", () => {
        const bits = nanBits();
        // Quiet NaN, sign by architecture: 7fc00000 on ARM, ffc00000 on x86.
        expect(bits?.[0]).toMatch(/^[7f]fc00000$/);
        expect(bits?.[1]).toMatch(/^[7f]ff80000$/);
    });

    it("is null where WebAssembly is unavailable", () => {
        vi.stubGlobal("WebAssembly", undefined);
        expect(nanBits()).toBeNull();
    });
});
