import { Fragment } from "react";

/** Gentle per-word fade-in for text that arrives after the page loaded. The words are plain text
 *  nodes inside spans; CSS (.fx-word) only animates when effects are on, so nothing is ever hidden. */
export function Reveal({ text, animate }: { text: string; animate: boolean }) {
  if (!animate) return <>{text}</>;
  const parts = text.split(/(\s+)/);
  const words = parts.filter((p) => p && !/^\s+$/.test(p)).length;
  const step = Math.min(35, 900 / Math.max(words, 1)); // whole reveal stays under ~1 s
  let i = 0;
  return (
    <>
      {parts.map((p, k) =>
        !p || /^\s+$/.test(p) ? (
          <Fragment key={k}>{p}</Fragment>
        ) : (
          <span key={k} className="fx-word" style={{ animationDelay: `${Math.round(i++ * step)}ms` }}>
            {p}
          </span>
        ),
      )}
    </>
  );
}
