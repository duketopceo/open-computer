import { afterEach, describe, expect, it, vi } from "vitest";
import { frameLoop, parseColor, startAmbient, type Clock } from "./ambient";

/** A manual clock: frames run only when the test calls `frame(t)`. */
function manualClock() {
  let next = 1;
  const queue = new Map<number, (t: number) => void>();
  const clock: Clock = {
    now: () => 0,
    request: (cb) => {
      const id = next++;
      queue.set(id, cb);
      return id;
    },
    cancel: (id) => {
      queue.delete(id);
    },
  };
  const frame = (t: number) => {
    const cbs = [...queue.values()];
    queue.clear();
    cbs.forEach((cb) => cb(t));
  };
  return { clock, frame, pending: () => queue.size };
}

describe("frameLoop", () => {
  it("draws at most `fps` times a second and passes seconds since start", () => {
    const { clock, frame } = manualClock();
    const draws: number[] = [];
    const loop = frameLoop((s) => draws.push(s), 30, clock);
    loop.start();
    for (let t = 0; t <= 1000; t += 1000 / 120) frame(t); // a 120 Hz display for one second
    expect(draws.length).toBeGreaterThanOrEqual(29);
    expect(draws.length).toBeLessThanOrEqual(31);
    expect(draws[0]).toBe(0);
    expect(draws[draws.length - 1]).toBeCloseTo(1, 1);
  });

  it("stop cancels the pending frame and start resumes", () => {
    const { clock, frame, pending } = manualClock();
    const draw = vi.fn();
    const loop = frameLoop(draw, 30, clock);
    loop.start();
    expect(loop.running()).toBe(true);
    loop.stop();
    expect(pending()).toBe(0);
    frame(100);
    expect(draw).not.toHaveBeenCalled();
    loop.start();
    frame(200);
    expect(draw).toHaveBeenCalledOnce();
  });
});

describe("parseColor", () => {
  it("reads the token formats the theme uses", () => {
    expect(parseColor("#ffffff")).toEqual([1, 1, 1]);
    expect(parseColor(" #000 ")).toEqual([0, 0, 0]);
    expect(parseColor("rgb(255, 0, 51)")?.map((x) => Math.round(x * 255))).toEqual([255, 0, 51]);
    expect(parseColor("var(--x)")).toBeNull();
  });
});

/** Just enough of WebGLRenderingContext for startAmbient to compile, link and draw. */
function stubGl() {
  const ok = {};
  const calls = { draw: 0, lost: 0 };
  const gl = new Proxy(
    {
      VERTEX_SHADER: 1, FRAGMENT_SHADER: 2, COMPILE_STATUS: 3, LINK_STATUS: 4, ARRAY_BUFFER: 5, STATIC_DRAW: 6,
      FLOAT: 7, TRIANGLES: 8, BLEND: 9, ONE: 10, ONE_MINUS_SRC_ALPHA: 11, COLOR_BUFFER_BIT: 12,
      createShader: () => ok, createProgram: () => ok, createBuffer: () => ok,
      getShaderParameter: () => true, getProgramParameter: () => true,
      getAttribLocation: () => 0, getUniformLocation: () => ok,
      drawArrays: () => void calls.draw++,
      getExtension: () => ({ loseContext: () => void calls.lost++ }),
    } as Record<string, unknown>,
    { get: (t, k: string) => (k in t ? t[k] : () => undefined) },
  );
  return { gl, calls };
}

describe("startAmbient", () => {
  afterEach(() => {
    Object.defineProperty(document, "hidden", { value: false, configurable: true });
  });

  it("returns null without WebGL so the caller keeps the weave", () => {
    const canvas = document.createElement("canvas");
    canvas.getContext = (() => null) as typeof canvas.getContext;
    expect(startAmbient(canvas, manualClock().clock)).toBeNull();
  });

  it("pauses while the tab is hidden, resumes when visible, and releases the context on stop", () => {
    const { gl, calls } = stubGl();
    const canvas = document.createElement("canvas");
    canvas.getContext = (() => gl) as unknown as typeof canvas.getContext;
    const m = manualClock();
    const stop = startAmbient(canvas, m.clock);
    expect(stop).not.toBeNull();
    m.frame(0);
    expect(calls.draw).toBe(1);

    Object.defineProperty(document, "hidden", { value: true, configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(m.pending()).toBe(0);
    m.frame(1000);
    expect(calls.draw).toBe(1);

    Object.defineProperty(document, "hidden", { value: false, configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    m.frame(2000);
    expect(calls.draw).toBe(2);

    stop!();
    expect(m.pending()).toBe(0);
    expect(calls.lost).toBe(1);
  });
});
