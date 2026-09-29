/** First-party probes. No fingerprinting dependency, network calls or permission prompts. */
import { canvasIntegrity, engineTells, frameTimeZone, tamperedNatives, withFrame } from "./realm";

export type Probe = { status: "ok"; value: string } | { status: "unavailable" | "timeout" | "unstable" };
export type Signals = Record<string, Probe>;

export async function digest(value: string): Promise<string> {
    const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
    return Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, "0")).join("");
}

export async function probe(read: () => unknown, timeout = 1500): Promise<Probe> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
        return await Promise.race([
            Promise.resolve()
                .then(read)
                .then(value => {
                    const text: unknown = typeof value === "string" ? value : JSON.stringify(value);
                    if (typeof text !== "string" || text.length > 4096) throw new Error("Invalid probe");
                    return { status: "ok", value: text } as const;
                }),
            new Promise<Probe>(resolve => {
                timer = setTimeout(() => {
                    resolve({ status: "timeout" });
                }, timeout);
            }),
        ]);
    } catch {
        return { status: "unavailable" };
    } finally {
        clearTimeout(timer);
    }
}

async function repeated(read: () => Promise<string>): Promise<Probe> {
    const result = await probe(async () => {
        const first = await read();
        return first === (await read()) ? first : "unstable";
    });
    return result.status === "ok" && result.value === "unstable" ? { status: "unstable" } : result;
}

/** The rendering probe; `doc` lets the iframe draw the same picture with its own untouched canvas. */
async function canvas(doc: Document = document): Promise<string> {
    const element = doc.createElement("canvas");
    element.width = 280;
    element.height = 80;
    const ctx = element.getContext("2d");
    if (!ctx) throw new Error("Canvas unavailable");
    ctx.fillStyle = "#f39b27";
    ctx.fillRect(9, 11, 170, 45);
    ctx.font = "17px serif";
    ctx.fillStyle = "#2468ac";
    ctx.fillText("MeteoLane Ω é 🚲 0123456789", 4, 32);
    ctx.globalCompositeOperation = "multiply";
    ctx.fillStyle = "rgba(128, 40, 190, .7)";
    ctx.beginPath();
    ctx.arc(155, 40, 25, 0, Math.PI * 2);
    ctx.fill();
    return digest(element.toDataURL());
}

function renderer(doc: Document): unknown {
    const gl = doc.createElement("canvas").getContext("webgl");
    if (!gl) return null;
    try {
        const info = gl.getExtension("WEBGL_debug_renderer_info");
        const value: unknown = gl.getParameter(info?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER);
        return value;
    } finally {
        gl.getExtension("WEBGL_lose_context")?.loseContext();
    }
}

function graphics(): unknown {
    const gl = document.createElement("canvas").getContext("webgl");
    if (!gl) throw new Error("WebGL unavailable");
    const parameter = (name: number): unknown => {
        const value: unknown = gl.getParameter(name);
        return value;
    };
    try {
        const info = gl.getExtension("WEBGL_debug_renderer_info");
        return {
            vendor: parameter(info?.UNMASKED_VENDOR_WEBGL ?? gl.VENDOR),
            renderer: parameter(info?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER),
            texture: parameter(gl.MAX_TEXTURE_SIZE),
            attributes: parameter(gl.MAX_VERTEX_ATTRIBS),
            precision: gl.getShaderPrecisionFormat(gl.FRAGMENT_SHADER, gl.HIGH_FLOAT)?.precision,
            extensions: gl.getSupportedExtensions()?.sort(),
        };
    } finally {
        gl.getExtension("WEBGL_lose_context")?.loseContext();
    }
}

async function audio(): Promise<string> {
    const context = new OfflineAudioContext(1, 5000, 44100);
    const oscillator = context.createOscillator();
    const compressor = context.createDynamicsCompressor();
    oscillator.type = "triangle";
    oscillator.frequency.value = 10000;
    compressor.threshold.value = -50;
    compressor.knee.value = 40;
    compressor.ratio.value = 12;
    compressor.attack.value = 0;
    compressor.release.value = 0.25;
    oscillator.connect(compressor);
    compressor.connect(context.destination);
    oscillator.start();
    try {
        const buffer = await context.startRendering();
        return await digest(Array.from(buffer.getChannelData(0).slice(4500), n => n.toFixed(5)).join(","));
    } finally {
        oscillator.disconnect();
        compressor.disconnect();
    }
}

function fonts(): unknown {
    const ctx = document.createElement("canvas").getContext("2d");
    if (!ctx) throw new Error("Canvas unavailable");
    // Fixed small list, not local-font enumeration. Never waits for web fonts.
    return ["monospace", "serif", "sans-serif", "Arial", "Calibri", "Helvetica", "Times New Roman", "Verdana"].map(
        font => {
            ctx.font = `72px "${font}", monospace`;
            return Math.round(ctx.measureText("mmmmWWWW0123456789Ωé").width * 100) / 100;
        },
    );
}

function workerSignals(): Promise<string> {
    return new Promise((resolve, reject) => {
        const url = URL.createObjectURL(
            new Blob(
                [
                    "postMessage(JSON.stringify([navigator.userAgent,navigator.hardwareConcurrency,navigator.language,navigator.platform]))",
                ],
                { type: "text/javascript" },
            ),
        );
        let worker: Worker | undefined;
        const finish = () => {
            clearTimeout(timer);
            worker?.terminate();
            URL.revokeObjectURL(url);
        };
        const timer = setTimeout(() => {
            finish();
            reject(new Error("Worker timeout"));
        }, 1000);
        try {
            worker = new Worker(url);
            worker.onmessage = event => {
                finish();
                resolve(String(event.data));
            };
            worker.onerror = () => {
                finish();
                reject(new Error("Worker unavailable"));
            };
        } catch (error) {
            finish();
            reject(new Error("Worker unavailable", { cause: error }));
        }
    });
}

/**
 * The same questions asked through a fresh iframe: its navigator, WebGL, Intl and canvas are
 * the browser's own, whatever the page's realm was patched with. Unreadable parts are null.
 */
async function frameSignals(realm: Window): Promise<unknown> {
    const nav = realm.navigator;
    const readable = async (read: () => unknown): Promise<unknown> => {
        try {
            return await read();
        } catch {
            return null;
        }
    };
    return [
        nav.userAgent,
        nav.platform,
        nav.hardwareConcurrency,
        await readable(() => renderer(realm.document)),
        await readable(() => frameTimeZone(realm)),
        await readable(() => canvas(realm.document)),
    ];
}

function automation(): unknown {
    // chromedriver leaves `cdc_…` keys on window and document.
    const driverKeys = [...Object.keys(window), ...Object.keys(document)].some(key => key.includes("cdc_"));
    return { webdriver: navigator.webdriver, cdc: driverKeys, chrome: "chrome" in window };
}

export async function collectSignals(): Promise<Signals> {
    const probes: [string, Promise<Probe>][] = [
        ["canvas", repeated(canvas)],
        ["audio", repeated(audio)],
        ["graphics", probe(graphics)],
        ["fonts", probe(fonts)],
        [
            "hardware",
            probe(() => [
                navigator.hardwareConcurrency,
                navigator.maxTouchPoints,
                "deviceMemory" in navigator ? navigator.deviceMemory : null,
            ]),
        ],
        [
            "display",
            // No devicePixelRatio: it follows the zoom.
            probe(() => [Math.min(screen.width, screen.height), Math.max(screen.width, screen.height), screen.colorDepth]),
        ],
        [
            "locale",
            // No navigator.languages: it follows a setting. The time zone must come first (iframe check).
            probe(() => [
                Intl.DateTimeFormat().resolvedOptions().timeZone,
                Intl.NumberFormat().resolvedOptions().numberingSystem,
            ]),
        ],
        ["math", probe(() => [Math.acos(0.123), Math.asinh(1), Math.tan(-1e300), Math.expm1(1)].map(String))],
        [
            "media",
            probe(() => {
                const video = document.createElement("video");
                return [
                    'video/mp4; codecs="avc1.42E01E"',
                    'video/webm; codecs="vp9"',
                    'audio/ogg; codecs="vorbis"',
                ].map(type => video.canPlayType(type));
            }),
        ],
        [
            "navigator",
            probe(() => [navigator.userAgent, navigator.hardwareConcurrency, navigator.language, navigator.platform]),
        ],
        ["worker", probe(workerSignals)],
        ["iframe", probe(() => withFrame(frameSignals), 2500)],
        ["integrity", probe(() => withFrame(tamperedNatives))],
        ["canvasIntegrity", probe(canvasIntegrity)],
        ["engine", probe(engineTells)],
        ["automation", probe(automation)],
    ];
    const entries = await Promise.all(
        probes.map(async ([name, result]): Promise<[string, Probe]> => [name, await result]),
    );
    return Object.fromEntries(entries);
}
