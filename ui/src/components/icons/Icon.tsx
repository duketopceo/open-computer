import { ICONS, type IconName } from "./paths";

export type { IconName };

/** One original icon from the set (paths.ts). Decorative by default: pair it with a visible word. Pass `title` when it
 *  stands alone, which gives it an accessible name instead of hiding it. */
export function Icon({
  name,
  size = 16,
  title,
  className,
}: {
  name: IconName;
  size?: 16 | 20;
  title?: string;
  className?: string;
}) {
  const segs: readonly { d: string; fill?: boolean; dash?: string }[] = ICONS[name];
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="square"
      strokeLinejoin="miter"
      className={className}
      data-icon={name}
      {...(title ? { role: "img", "aria-label": title } : { "aria-hidden": true })}
    >
      {title && <title>{title}</title>}
      {segs.map((g, i) => (
        <path key={i} d={g.d} fill={g.fill ? "currentColor" : "none"} strokeDasharray={g.dash} />
      ))}
    </svg>
  );
}
