// The ambient background renderer: one full-screen triangle and the vendored ThreeUI dot-matrix shader on raw
// WebGL. Loaded on demand (its own chunk, see budget.json) and only when motion is allowed; Ambient.tsx decides.

import { DOT_MATRIX_FRAGMENT, DOT_MATRIX_VERTEX } from "@/vendor/threeui/dotMatrix";

export const AMBIENT = {
  fps: 30,
  gridScale: 46,
  pulseSpeed: 0.26,
  radius: 0.14,
  opacity: 0.3,
  maxPixelRatio: 1.5,
} as const;

export interface Clock {
  now: () => number;
  request: (cb: (t: number) => void) => number;
  cancel: (id: number) => void;
}

const realClock: Clock = {
  now: () => performance.now(),
  request: (cb) => requestAnimationFrame(cb),
  cancel: (id) => cancelAnimationFrame(id),
};

export interface Loop {
  start: () => void;
  stop: () => void;
  running: () => boolean;
}

/** A frame loop capped at `fps`: `draw` gets seconds since the first start. Stopping cancels the pending frame. */
export function frameLoop(draw: (seconds: number) => void, fps: number, clock: Clock = realClock): Loop {
  const gap = 1000 / fps;
  let id = 0;
  let last = -Infinity;
  let origin: number | null = null;
  const tick = (t: number) => {
    id = clock.request(tick);
    if (t - last < gap - 1) return;
    last = t;
    draw((t - (origin ?? t)) / 1000);
  };
  return {
    start() {
      if (id) return;
      origin ??= clock.now();
      id = clock.request(tick);
    },
    stop() {
      if (id) clock.cancel(id);
      id = 0;
    },
    running: () => id !== 0,
  };
}

/** "#rrggbb", "#rgb" or "rgb(r, g, b)" to 0..1 floats; null when the value is something else. */
export function parseColor(v: string): [number, number, number] | null {
  const s = v.trim();
  let m = /^#([0-9a-f]{6})$/i.exec(s);
  if (m) return [0, 2, 4].map((i) => parseInt(m![1].slice(i, i + 2), 16) / 255) as [number, number, number];
  m = /^#([0-9a-f]{3})$/i.exec(s);
  if (m) return [0, 1, 2].map((i) => parseInt(m![1][i] + m![1][i], 16) / 255) as [number, number, number];
  m = /^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/i.exec(s);
  if (m) return [m[1], m[2], m[3]].map((x) => Number(x) / 255) as [number, number, number];
  return null;
}

const inkColor = (): [number, number, number] =>
  parseColor(getComputedStyle(document.documentElement).getPropertyValue("--sl-ink")) ?? [0.6, 0.6, 0.6];

function compile(gl: WebGLRenderingContext, type: number, src: string): WebGLShader | null {
  const sh = gl.createShader(type);
  if (!sh) return null;
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  return gl.getShaderParameter(sh, gl.COMPILE_STATUS) ? sh : null;
}

/** Start drawing on `canvas`. Returns a stop function, or null when WebGL is unavailable or the shader fails,
 *  in which case the caller keeps the CSS weave. Pauses while the tab is hidden; follows theme changes. */
export function startAmbient(canvas: HTMLCanvasElement, clock: Clock = realClock): (() => void) | null {
  const gl = canvas.getContext("webgl", { alpha: true, antialias: false, premultipliedAlpha: true, powerPreference: "low-power" });
  if (!gl) return null;
  const vs = compile(gl, gl.VERTEX_SHADER, DOT_MATRIX_VERTEX);
  const fs = compile(gl, gl.FRAGMENT_SHADER, DOT_MATRIX_FRAGMENT);
  const prog = gl.createProgram();
  if (!vs || !fs || !prog) return null;
  gl.attachShader(prog, vs);
  gl.attachShader(prog, fs);
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return null;
  gl.useProgram(prog);
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const pos = gl.getAttribLocation(prog, "aPosition");
  gl.enableVertexAttribArray(pos);
  gl.vertexAttribPointer(pos, 2, gl.FLOAT, false, 0, 0);
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
  const u = (n: string) => gl.getUniformLocation(prog, n);
  const uTime = u("uTime");
  const uRes = u("uResolution");
  const uColor = u("uColor");
  gl.uniform1f(u("uGridScale"), AMBIENT.gridScale);
  gl.uniform1f(u("uPulseSpeed"), AMBIENT.pulseSpeed);
  gl.uniform1f(u("uRadius"), AMBIENT.radius);
  gl.uniform1f(u("uOpacity"), AMBIENT.opacity);

  const setColor = () => gl.uniform3fv(uColor, inkColor());
  const resize = () => {
    const dpr = Math.min(window.devicePixelRatio || 1, AMBIENT.maxPixelRatio);
    canvas.width = Math.max(1, Math.round(canvas.clientWidth * dpr));
    canvas.height = Math.max(1, Math.round(canvas.clientHeight * dpr));
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.uniform2f(uRes, canvas.width, canvas.height);
  };
  setColor();
  resize();

  const loop = frameLoop((t) => {
    gl.uniform1f(uTime, t);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }, AMBIENT.fps, clock);

  const onVisibility = () => (document.hidden ? loop.stop() : loop.start());
  const onLost = (e: Event) => {
    e.preventDefault();
    loop.stop();
  };
  const theme = new MutationObserver(setColor);
  theme.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  const scheme = typeof matchMedia === "function" ? matchMedia("(prefers-color-scheme: dark)") : null;
  scheme?.addEventListener?.("change", setColor);
  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("resize", resize);
  canvas.addEventListener("webglcontextlost", onLost);
  if (!document.hidden) loop.start();

  return () => {
    loop.stop();
    theme.disconnect();
    scheme?.removeEventListener?.("change", setColor);
    document.removeEventListener("visibilitychange", onVisibility);
    window.removeEventListener("resize", resize);
    canvas.removeEventListener("webglcontextlost", onLost);
    gl.getExtension("WEBGL_lose_context")?.loseContext();
  };
}
