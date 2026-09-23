/*
 * Animated wind particles, after mapbox/webgl-wind (https://github.com/mapbox/webgl-wind,
 * ISC License, Copyright (c) 2017, Mapbox): particles advected through a wind field, drawn
 * into a screen-sized texture that fades a little every frame, so each particle leaves a
 * short trail.
 *
 * Differences from webgl-wind, all on purpose:
 * - The field is a corridor along the route in Web Mercator (utils/windField.ts), not a
 *   global equirectangular texture, so its cos(lat) stretch is gone (Mercator is conformal)
 *   and particles respawn only inside the corridor and in view.
 * - A route's corridor holds a few thousand particles, so they are moved on the CPU instead of
 *   in a float state texture; only the trails are drawn with WebGL.
 * - The speed is in screen pixels per second per km/h and scales with the frame time, so it
 *   looks the same at every zoom and on a 120 Hz display.
 * - Offscreen drawing happens in `prerender`; `render` only composites the trails (MapLibre's
 *   custom-layer contract).
 */
import type { CustomLayerInterface, CustomRenderMethodInput, Map as MapLibreMap } from "maplibre-gl";
import { corridorHalfWidthM, corridorMask, sampleWindField, type WindField } from "@/utils/windField";

/** Screen speed of a particle: px per second per km/h of wind. */
const PX_PER_S_PER_KMH = 11.9;
/** One particle per this many square px of corridor on screen. */
const PX2_PER_PARTICLE = 60 * 30;
const MAX_PARTICLES = 16000;
/** Trail opacity kept per 60 Hz frame. */
const FADE_PER_FRAME = 0.94;
/** Seconds a particle lives before it is reborn somewhere else. */
const MIN_LIFE_S = 1.5;
const MAX_LIFE_S = 4;
/** Wind speed (km/h) at the strong end of the colour ramp. */
const RAMP_TOP_KMH = 40;
/** Tile size MapLibre uses for the world: world px = 512 · 2^zoom. */
const WORLD_TILE_PX = 512;

/** Premultiplied-ready RGB (0..1): calm colour, strong colour. Readable on each basemap. */
const RAMP = {
    light: [
        [0.49, 0.7, 0.94],
        [0.11, 0.25, 0.56],
    ],
    dark: [
        [0.36, 0.56, 0.84],
        [0.86, 0.93, 1.0],
    ],
} as const;

const PARTICLE_VS = `#version 300 es
uniform mat4 u_matrix;
uniform float u_point_size;
in vec2 a_pos;
in vec2 a_style; // x: speed 0..1, y: alpha 0..1
out vec2 v_style;
void main() {
    v_style = a_style;
    gl_Position = u_matrix * vec4(a_pos, 0.0, 1.0);
    gl_PointSize = u_point_size;
}`;

const PARTICLE_FS = `#version 300 es
precision mediump float;
uniform vec3 u_calm;
uniform vec3 u_strong;
in vec2 v_style;
out vec4 fragColor;
void main() {
    vec3 color = mix(u_calm, u_strong, v_style.x);
    fragColor = vec4(color * v_style.y, v_style.y);
}`;

const QUAD_VS = `#version 300 es
in vec2 a_pos;
out vec2 v_uv;
void main() {
    v_uv = a_pos;
    gl_Position = vec4(a_pos * 2.0 - 1.0, 0.0, 1.0);
}`;

const QUAD_FS = `#version 300 es
precision mediump float;
uniform sampler2D u_texture;
uniform float u_opacity;
in vec2 v_uv;
out vec4 fragColor;
void main() {
    // Premultiplied, so scaling every channel fades colour and coverage together.
    fragColor = texture(u_texture, v_uv) * u_opacity;
}`;

function compile(gl: WebGL2RenderingContext, vertex: string, fragment: string): WebGLProgram {
    const program = gl.createProgram();
    for (const [type, source] of [
        [gl.VERTEX_SHADER, vertex],
        [gl.FRAGMENT_SHADER, fragment],
    ] as const) {
        const shader = gl.createShader(type);
        if (!shader) throw new Error("wind particles: cannot create shader");
        gl.shaderSource(shader, source);
        gl.compileShader(shader);
        if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
            throw new Error(`wind particles: ${gl.getShaderInfoLog(shader) ?? "shader error"}`);
        }
        gl.attachShader(program, shader);
        gl.deleteShader(shader);
    }
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
        throw new Error(`wind particles: ${gl.getProgramInfoLog(program) ?? "link error"}`);
    }
    return program;
}

/** a · b for column-major 4×4 matrices, in float64. */
function multiply(a: ArrayLike<number>, b: ArrayLike<number>): Float64Array {
    const out = new Float64Array(16);
    for (let col = 0; col < 4; col++) {
        for (let row = 0; row < 4; row++) {
            let sum = 0;
            for (let k = 0; k < 4; k++) sum += (a[k * 4 + row] ?? 0) * (b[col * 4 + k] ?? 0);
            out[col * 4 + row] = sum;
        }
    }
    return out;
}

interface Trail {
    texture: WebGLTexture;
    framebuffer: WebGLFramebuffer;
}

interface GlState {
    particleProgram: WebGLProgram;
    quadProgram: WebGLProgram;
    particleVao: WebGLVertexArrayObject;
    particleBuffer: WebGLBuffer;
    quadVao: WebGLVertexArrayObject;
    quadBuffer: WebGLBuffer;
    trails?: [Trail, Trail];
    trailSize: [number, number];
}

export class WindParticleLayer implements CustomLayerInterface {
    readonly id: string;
    readonly type = "custom";
    readonly renderingMode = "2d";

    private map?: MapLibreMap;
    private gl?: GlState;
    private field?: WindField;
    private dark = false;
    private running = false;

    /** Particles in field cell units, their previous position, and life left in seconds. */
    private x = new Float32Array(0);
    private y = new Float32Array(0);
    private px = new Float32Array(0);
    private py = new Float32Array(0);
    private life = new Float32Array(0);
    /** Per particle: 2 vertices × (x, y, speed, alpha). */
    private vertices = new Float32Array(0);
    /** Corridor cells in view that particles may be born in, and how strongly each shows. */
    private spawn = new Uint32Array(0);
    private spawnWeight = new Float32Array(0);
    private lastFrame?: number;
    private clearTrails = true;

    constructor(id = "wind-particles") {
        this.id = id;
    }

    /** Replace the wind; `undefined` draws nothing. */
    setField(field: WindField | undefined) {
        this.field = field;
        this.clearTrails = true;
        this.updateView();
        this.map?.triggerRepaint();
    }

    setDark(dark: boolean) {
        this.dark = dark;
        this.map?.triggerRepaint();
    }

    /** Start or stop the animation loop. Stopped, the layer keeps its last frame. */
    setRunning(running: boolean) {
        if (running === this.running) return;
        this.running = running;
        this.lastFrame = undefined;
        if (running) this.map?.triggerRepaint();
    }

    onAdd(map: MapLibreMap, gl: WebGL2RenderingContext) {
        this.map = map;
        const particleProgram = compile(gl, PARTICLE_VS, PARTICLE_FS);
        const quadProgram = compile(gl, QUAD_VS, QUAD_FS);

        const particleVao = gl.createVertexArray();
        const particleBuffer = gl.createBuffer();
        gl.bindVertexArray(particleVao);
        gl.bindBuffer(gl.ARRAY_BUFFER, particleBuffer);
        const pos = gl.getAttribLocation(particleProgram, "a_pos");
        const style = gl.getAttribLocation(particleProgram, "a_style");
        gl.enableVertexAttribArray(pos);
        gl.vertexAttribPointer(pos, 2, gl.FLOAT, false, 16, 0);
        gl.enableVertexAttribArray(style);
        gl.vertexAttribPointer(style, 2, gl.FLOAT, false, 16, 8);

        const quadVao = gl.createVertexArray();
        const quadBuffer = gl.createBuffer();
        gl.bindVertexArray(quadVao);
        gl.bindBuffer(gl.ARRAY_BUFFER, quadBuffer);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1]), gl.STATIC_DRAW);
        const quadPos = gl.getAttribLocation(quadProgram, "a_pos");
        gl.enableVertexAttribArray(quadPos);
        gl.vertexAttribPointer(quadPos, 2, gl.FLOAT, false, 0, 0);
        gl.bindVertexArray(null);
        gl.bindBuffer(gl.ARRAY_BUFFER, null);

        this.gl = { particleProgram, quadProgram, particleVao, particleBuffer, quadVao, quadBuffer, trailSize: [0, 0] };
        map.on("moveend", this.updateView);
        map.on("resize", this.onResize);
        document.addEventListener("visibilitychange", this.onVisibility);
        this.updateView();
    }

    onRemove(_map: MapLibreMap, gl: WebGL2RenderingContext) {
        this.map?.off("moveend", this.updateView);
        this.map?.off("resize", this.onResize);
        document.removeEventListener("visibilitychange", this.onVisibility);
        const state = this.gl;
        if (state) {
            gl.deleteProgram(state.particleProgram);
            gl.deleteProgram(state.quadProgram);
            gl.deleteVertexArray(state.particleVao);
            gl.deleteVertexArray(state.quadVao);
            gl.deleteBuffer(state.particleBuffer);
            gl.deleteBuffer(state.quadBuffer);
            this.deleteTrails(gl, state);
        }
        this.gl = undefined;
        this.map = undefined;
    }

    private readonly onResize = () => {
        this.clearTrails = true;
        this.updateView();
    };

    private readonly onVisibility = () => {
        this.lastFrame = undefined;
        if (!document.hidden) this.map?.triggerRepaint();
    };

    /**
     * Pick the corridor cells in view and size the particle count to them, so a zoomed-in map
     * is as full as a zoomed-out one and no particle is spent off screen.
     */
    private readonly updateView = () => {
        const map = this.map;
        const field = this.field;
        if (!map || !field) {
            this.resize(0);
            this.spawn = new Uint32Array(0);
            return;
        }
        const bounds = map.getBounds();
        const toCell = (lng: number, lat: number) => {
            const x = (lng + 180) / 360;
            const phi = (Math.max(-85, Math.min(85, lat)) * Math.PI) / 180;
            const y = (1 - Math.log(Math.tan(Math.PI / 4 + phi / 2)) / Math.PI) / 2;
            return [(x - field.x0) / field.cell, (y - field.y0) / field.cell] as const;
        };
        const [west, north] = toCell(bounds.getWest(), bounds.getNorth());
        const [east, south] = toCell(bounds.getEast(), bounds.getSouth());
        // Particles thin out with the corridor's fade: each cell carries its mask as a weight,
        // and the count follows the summed weight, so none are spent where they would not show.
        const halfWidthM = corridorHalfWidthM(field, map.getZoom());
        const spawn: number[] = [];
        const weights: number[] = [];
        let totalWeight = 0;
        for (const index of field.inside) {
            const cx = index % field.width;
            const cy = Math.floor(index / field.width);
            if (cx + 1 < west || cx > east || cy + 1 < north || cy > south) continue;
            const weight = corridorMask(field.distance[index] ?? Infinity, halfWidthM);
            if (weight <= 0) continue;
            spawn.push(index);
            weights.push(weight);
            totalWeight += weight;
        }
        this.spawn = Uint32Array.from(spawn);
        this.spawnWeight = Float32Array.from(weights);
        const cellPx = field.cell * WORLD_TILE_PX * 2 ** map.getZoom();
        this.resize(Math.min(MAX_PARTICLES, Math.round((totalWeight * cellPx * cellPx) / PX2_PER_PARTICLE)));
        // With no particles render() stops re-arming the loop; a route back in view restarts it.
        if (this.running) map.triggerRepaint();
    };

    private resize(count: number) {
        if (count === this.x.length) return;
        this.x = new Float32Array(count);
        this.y = new Float32Array(count);
        this.px = new Float32Array(count);
        this.py = new Float32Array(count);
        this.life = new Float32Array(count);
        this.vertices = new Float32Array(count * 8);
        for (let i = 0; i < count; i++) this.respawn(i, Math.random() * MAX_LIFE_S);
    }

    private respawn(i: number, life = MIN_LIFE_S + Math.random() * (MAX_LIFE_S - MIN_LIFE_S)) {
        const field = this.field;
        // Rejection sampling by weight: a few tries, then take whatever came up last.
        let cell: number | undefined;
        for (let attempt = 0; attempt < 6; attempt++) {
            const pick = Math.floor(Math.random() * this.spawn.length);
            cell = this.spawn[pick];
            if (Math.random() < (this.spawnWeight[pick] ?? 0)) break;
        }
        if (!field || cell === undefined) {
            this.life[i] = 0;
            return;
        }
        const x = (cell % field.width) + Math.random();
        const y = Math.floor(cell / field.width) + Math.random();
        this.x[i] = this.px[i] = x;
        this.y[i] = this.py[i] = y;
        this.life[i] = life;
    }

    /** Move every particle by `dt` seconds and write the line from where it was to where it is. */
    private step(dt: number, zoom: number) {
        const field = this.field;
        if (!field) return;
        // km/h → cells per second: screen px/s, over px per cell at this zoom.
        const cellsPerKmh = PX_PER_S_PER_KMH / (field.cell * WORLD_TILE_PX * 2 ** zoom);
        const { width, height } = field;
        // The corridor follows the zoom every frame, so it narrows and widens with a zoom animation.
        const halfWidthM = corridorHalfWidthM(field, zoom);
        for (let i = 0; i < this.x.length; i++) {
            let life = (this.life[i] ?? 0) - dt;
            let x = this.x[i] ?? 0;
            let y = this.y[i] ?? 0;
            let { u, v, distance } = sampleWindField(field, x, y);
            if (life <= 0 || corridorMask(distance, halfWidthM) <= 0) {
                this.respawn(i);
                life = this.life[i] ?? 0;
                x = this.x[i] ?? 0;
                y = this.y[i] ?? 0;
                ({ u, v, distance } = sampleWindField(field, x, y));
            }
            const mask = corridorMask(distance, halfWidthM);
            this.px[i] = x;
            this.py[i] = y;
            // Mercator y grows southward, so wind toward the north moves y down.
            x += u * cellsPerKmh * dt;
            y -= v * cellsPerKmh * dt;
            this.x[i] = x;
            this.y[i] = y;
            this.life[i] = life;
            const speed = Math.min(1, Math.hypot(u, v) / RAMP_TOP_KMH);
            // Fade out over the corridor's soft edge and in the particle's last half second.
            const alpha = life > 0 ? Math.min(1, mask) * Math.min(1, life * 2) * 0.9 : 0;
            const o = i * 8;
            const vertices = this.vertices;
            vertices[o] = (this.px[i] ?? 0) / width;
            vertices[o + 1] = (this.py[i] ?? 0) / height;
            vertices[o + 2] = vertices[o + 6] = speed;
            vertices[o + 3] = vertices[o + 7] = alpha;
            vertices[o + 4] = x / width;
            vertices[o + 5] = y / height;
        }
    }

    private deleteTrails(gl: WebGL2RenderingContext, state: GlState) {
        for (const trail of state.trails ?? []) {
            gl.deleteTexture(trail.texture);
            gl.deleteFramebuffer(trail.framebuffer);
        }
        state.trails = undefined;
        state.trailSize = [0, 0];
    }

    private ensureTrails(gl: WebGL2RenderingContext, state: GlState): [Trail, Trail] {
        const width = gl.drawingBufferWidth;
        const height = gl.drawingBufferHeight;
        if (state.trails && state.trailSize[0] === width && state.trailSize[1] === height) return state.trails;
        this.deleteTrails(gl, state);
        const make = (): Trail => {
            const texture = gl.createTexture();
            gl.bindTexture(gl.TEXTURE_2D, texture);
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, width, height, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
            const framebuffer = gl.createFramebuffer();
            gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
            gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);
            return { texture, framebuffer };
        };
        state.trails = [make(), make()];
        state.trailSize = [width, height];
        this.clearTrails = true;
        return state.trails;
    }

    prerender(gl: WebGL2RenderingContext, options: CustomRenderMethodInput) {
        const state = this.gl;
        const map = this.map;
        const field = this.field;
        if (!state || !map || !field || !this.x.length) return;

        const now = performance.now();
        const animate = this.running && !document.hidden;
        // Cap the step: after a stall, particles should not leap across the corridor.
        const dt = animate && this.lastFrame !== undefined ? Math.min(0.05, (now - this.lastFrame) / 1000) : 0;
        this.lastFrame = animate ? now : undefined;
        this.step(dt, map.getZoom());

        const [previous, current] = this.ensureTrails(gl, state);
        gl.bindFramebuffer(gl.FRAMEBUFFER, current.framebuffer);
        gl.viewport(0, 0, state.trailSize[0], state.trailSize[1]);
        gl.disable(gl.DEPTH_TEST);
        gl.disable(gl.STENCIL_TEST);
        gl.disable(gl.CULL_FACE);
        gl.disable(gl.SCISSOR_TEST);
        gl.clearColor(0, 0, 0, 0);
        gl.clear(gl.COLOR_BUFFER_BIT);

        // Trails live in screen space, so they only hold while the camera does: while it moves
        // every frame starts clean, and they build up again once it stops.
        if (!this.clearTrails && !map.isMoving() && dt > 0) {
            gl.disable(gl.BLEND);
            gl.useProgram(state.quadProgram);
            gl.activeTexture(gl.TEXTURE0);
            gl.bindTexture(gl.TEXTURE_2D, previous.texture);
            gl.uniform1i(gl.getUniformLocation(state.quadProgram, "u_texture"), 0);
            gl.uniform1f(gl.getUniformLocation(state.quadProgram, "u_opacity"), FADE_PER_FRAME ** (dt * 60));
            gl.bindVertexArray(state.quadVao);
            gl.drawArrays(gl.TRIANGLES, 0, 6);
        }
        this.clearTrails = false;

        // Field space 0..1 → Mercator → clip, composed in float64: the shader then works on
        // small numbers and does not jitter at street zoom.
        const extent = [field.width * field.cell, field.height * field.cell];
        const toMercator = [extent[0] ?? 0, 0, 0, 0, 0, extent[1] ?? 0, 0, 0, 0, 0, 1, 0, field.x0, field.y0, 0, 1];
        const matrix = multiply(options.defaultProjectionData.mainMatrix, toMercator);
        const ramp = this.dark ? RAMP.dark : RAMP.light;
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
        gl.useProgram(state.particleProgram);
        gl.uniformMatrix4fv(gl.getUniformLocation(state.particleProgram, "u_matrix"), false, Float32Array.from(matrix));
        gl.uniform1f(gl.getUniformLocation(state.particleProgram, "u_point_size"), 1.5 * devicePixelRatio);
        gl.uniform3fv(gl.getUniformLocation(state.particleProgram, "u_calm"), ramp[0]);
        gl.uniform3fv(gl.getUniformLocation(state.particleProgram, "u_strong"), ramp[1]);
        gl.bindVertexArray(state.particleVao);
        gl.bindBuffer(gl.ARRAY_BUFFER, state.particleBuffer);
        gl.bufferData(gl.ARRAY_BUFFER, this.vertices, gl.STREAM_DRAW);
        gl.drawArrays(gl.LINES, 0, this.x.length * 2);
        gl.drawArrays(gl.POINTS, 0, this.x.length * 2);
        gl.bindVertexArray(null);
        gl.bindBuffer(gl.ARRAY_BUFFER, null);
        gl.bindFramebuffer(gl.FRAMEBUFFER, null);

        state.trails = [current, previous];
    }

    render(gl: WebGL2RenderingContext) {
        const state = this.gl;
        const trail = state?.trails?.[0];
        if (!state || !trail || !this.field || !this.x.length) return;
        // MapLibre has set premultiplied blending; the trail texture is premultiplied.
        gl.disable(gl.DEPTH_TEST);
        gl.disable(gl.STENCIL_TEST);
        gl.viewport(0, 0, state.trailSize[0], state.trailSize[1]);
        gl.useProgram(state.quadProgram);
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, trail.texture);
        gl.uniform1i(gl.getUniformLocation(state.quadProgram, "u_texture"), 0);
        gl.uniform1f(gl.getUniformLocation(state.quadProgram, "u_opacity"), 1);
        gl.bindVertexArray(state.quadVao);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
        gl.bindVertexArray(null);
        if (this.running && !document.hidden) this.map?.triggerRepaint();
    }
}
