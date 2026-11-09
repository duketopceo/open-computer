import { useEffect, useState, type ReactNode } from "react";
import { LazyMotion, MotionConfig } from "motion/react";
import { motionAllowed } from "./motion";

const loadFeatures = () => import("./motionFeatures").then((r) => r.default);

/** Live answer to "may anything move?": follows the OS reduced-motion setting and Settings "Reduce effects"
 *  (the data-effects attribute) while the page is open. */
export function useMotionAllowed(): boolean {
  const [allowed, setAllowed] = useState(motionAllowed);
  useEffect(() => {
    const sync = () => setAllowed(motionAllowed());
    const root = document.documentElement;
    const watch = new MutationObserver(sync);
    watch.observe(root, { attributes: true, attributeFilter: ["data-effects"] });
    const mq = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : null;
    mq?.addEventListener?.("change", sync);
    sync();
    return () => {
      watch.disconnect();
      mq?.removeEventListener?.("change", sync);
    };
  }, []);
  return allowed;
}

/** Wraps a subtree that uses `m.*` components. When motion is not allowed every transition is instant,
 *  so a component can never sit hidden waiting on an animation. */
export function MotionRoot({ children }: { children: ReactNode }) {
  const allowed = useMotionAllowed();
  return (
    <LazyMotion features={loadFeatures} strict>
      <MotionConfig reducedMotion={allowed ? "never" : "always"} transition={allowed ? undefined : { duration: 0 }}>
        {children}
      </MotionConfig>
    </LazyMotion>
  );
}
