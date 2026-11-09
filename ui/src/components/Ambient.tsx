import { useEffect, useRef, useState } from "react";
import { useMotionAllowed } from "@/lib/MotionRoot";

/** The ambient dot field behind the canvas. Mounts only when motion is allowed; the renderer loads on demand and
 *  marks html[data-ambient="on"] while it draws, which hides the CSS weave. Any failure (no WebGL, a shader
 *  error, a failed chunk load) leaves the weave in place for the rest of the page's life. */
export function Ambient() {
  const allowed = useMotionAllowed();
  const ref = useRef<HTMLCanvasElement>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!allowed || failed) return;
    let dead = false;
    let stop: (() => void) | null = null;
    import("@/lib/ambient")
      .then(({ startAmbient }) => {
        if (dead || !ref.current) return;
        stop = startAmbient(ref.current);
        if (stop) document.documentElement.setAttribute("data-ambient", "on");
        else setFailed(true);
      })
      .catch(() => {
        if (!dead) setFailed(true);
      });
    return () => {
      dead = true;
      stop?.();
      document.documentElement.removeAttribute("data-ambient");
    };
  }, [allowed, failed]);
  if (!allowed || failed) return null;
  return <canvas ref={ref} aria-hidden="true" data-testid="ambient" className="pointer-events-none fixed inset-0 -z-10 h-full w-full" />;
}
