/*
 * A-leggo v4 icon set — original line glyphs drawn on a 24×24 grid.
 *
 * Every path below is hand-authored for A-leggo's product language: 1.8px
 * rounded strokes, open counters, geometric primitives only. They are not
 * copied from any icon library. Use `<Icon name>` instead of pulling
 * one-off lucide glyphs so all surfaces share the same shapes.
 */

import type { CSSProperties } from "react";

export type IconName =
  | "spark" | "chat" | "flask" | "home" | "library" | "notebook" | "book-open"
  | "graduation" | "route" | "memory" | "bot" | "heart" | "history" | "help"
  | "grid" | "network" | "settings" | "user" | "users" | "shield" | "logout"
  | "plus" | "paperclip" | "send" | "stop" | "mic" | "cpu" | "image" | "wrench"
  | "search" | "more-h" | "more-v" | "x" | "menu" | "check" | "alert"
  | "play" | "pause" | "download" | "upload" | "folder" | "file" | "file-search"
  | "branch" | "layers" | "clock" | "refresh" | "eye" | "external" | "edit"
  | "save" | "list" | "trash" | "copy" | "target" | "lightbulb" | "shield-check"
  | "chart" | "atom" | "key" | "globe" | "arrow-right" | "arrow-up-right"
  | "panel-left" | "panel-right" | "chevron-down" | "chevron-left" | "chevron-right"
  | "chevrons-left" | "chevrons-right" | "link" | "pin";

export const PATHS: Record<IconName, React.ReactNode> = {
  spark: <path d="M12 3.5l1.7 4.9 4.9 1.7-4.9 1.7L12 16.7l-1.7-4.9-4.9-1.7 4.9-1.7L12 3.5z" />,
  chat: <><path d="M5 5h12l3 3v8H9l-4 4V5z" /><path d="M8.5 9h8M8.5 12.5h5" /></>,
  flask: (
    <>
      <path d="M9.5 3.5h5M10.5 3.5v5.2L5.9 16.9A2 2 0 0 0 7.7 20h8.6a2 2 0 0 0 1.8-3.1L13.5 8.7V3.5" />
      <path d="M8.2 14.5h7.6" />
    </>
  ),
  home: <><path d="M4 12 12 4l8 8M6 10v10h12V10" /><path d="M10 20v-6h4v6" /></>,
  library: (
    <>
      <path d="M6 4.5h9a3 3 0 0 1 3 3v12H9a3 3 0 0 1-3-3v-12z" />
      <path d="M6 16.5a3 3 0 0 1 3-3h9" />
    </>
  ),
  notebook: (
    <>
      <path d="M7 3.5h10.5a1 1 0 0 1 1 1v15a1 1 0 0 1-1 1H7a2 2 0 0 1-2-2v-13a2 2 0 0 1 2-2z" />
      <path d="M9.5 3.5v17M12.5 8h3.5M12.5 11.5h3.5" />
    </>
  ),
  "book-open": (
    <>
      <path d="M12 6.7C10.4 5.1 8.4 4.5 5.8 4.5H4v12.8h1.8c2.6 0 4.6.6 6.2 2.2 1.6-1.6 3.6-2.2 6.2-2.2H20V4.5h-1.8c-2.6 0-4.6.6-6.2 2.2z" />
      <path d="M12 6.7v12.8" />
    </>
  ),
  graduation: (
    <>
      <path d="M2.5 9.3L12 4.8l9.5 4.5L12 13.8 2.5 9.3z" />
      <path d="M6.5 11.3v4.2c0 1.3 2.5 2.7 5.5 2.7s5.5-1.4 5.5-2.7v-4.2M21 10v4.5" />
    </>
  ),
  route: (
    <>
      <path d="M5 19.5a2 2 0 1 1 .01-4.01A2 2 0 0 1 5 19.5zM19 8.5a2 2 0 1 1 .01-4.01A2 2 0 0 1 19 8.5z" />
      <path d="M7 17.5h6.3a3.7 3.7 0 0 0 3.7-3.7V8.6" />
    </>
  ),
  memory: (
    <>
      <path d="M5 6.2C5 4.7 8.1 3.5 12 3.5s7 1.2 7 2.7-3.1 2.7-7 2.7-7-1.2-7-2.7z" />
      <path d="M5 6.2v11.6c0 1.5 3.1 2.7 7 2.7s7-1.2 7-2.7V6.2M5 12c0 1.5 3.1 2.7 7 2.7s7-1.2 7-2.7" />
    </>
  ),
  bot: (
    <>
      <path d="M12 3.8V6M7.5 6.5h9A2.5 2.5 0 0 1 19 9v6a2.5 2.5 0 0 1-2.5 2.5h-9A2.5 2.5 0 0 1 5 15V9a2.5 2.5 0 0 1 2.5-2.5z" />
      <path d="M9.5 12.2h.01M14.5 12.2h.01" strokeWidth={2.4} />
    </>
  ),
  heart: (
    <path d="M12 20.5C7.2 16.7 3.5 13.4 3.5 9.8 3.5 7.1 5.5 5 8 5c1.6 0 3.1.8 4 2.1C12.9 5.8 14.4 5 16 5c2.5 0 4.5 2.1 4.5 4.8 0 3.6-3.7 6.9-8.5 10.7z" />
  ),
  history: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 8v4.4l3 1.8" />
    </>
  ),
  help: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M9.6 9.4a2.5 2.5 0 0 1 4.9.7c0 1.6-2.2 1.9-2.5 3.2M12 16.6h.01" />
    </>
  ),
  grid: <path d="M4.5 4.5H11V11H4.5zM13 4.5h6.5V11H13zM4.5 13H11v6.5H4.5zM13 13h6.5v6.5H13z" />,
  network: (
    <>
      <circle cx="5.5" cy="18" r="2" />
      <circle cx="18.5" cy="18" r="2" />
      <circle cx="12" cy="5.5" r="2" />
      <path d="M12 7.5V11M12 11l-4.8 5.4M12 11l4.8 5.4" />
    </>
  ),
  settings: (
    <>
      <path d="M5 7.5h7.5M16.5 7.5H19M5 16.5h3M12 16.5h7" />
      <circle cx="14.5" cy="7.5" r="2" />
      <circle cx="9.5" cy="16.5" r="2" />
    </>
  ),
  user: (
    <>
      <circle cx="12" cy="8" r="3.5" />
      <path d="M5 20c0-3.4 3-5.5 7-5.5s7 2.1 7 5.5" />
    </>
  ),
  users: (
    <>
      <circle cx="9" cy="8.5" r="3.2" />
      <path d="M3.5 19.5c0-3 2.4-5 5.5-5s5.5 2 5.5 5M15.5 5.7a3.2 3.2 0 0 1 0 5.6M17.6 14.8c1.8.8 2.9 2.3 2.9 4.7" />
    </>
  ),
  shield: <path d="M12 3.5L19 6v6.2c0 4.4-3 7.4-7 8.3-4-.9-7-3.9-7-8.3V6l7-2.5z" />,
  "shield-check": (
    <>
      <path d="M12 3.5L19 6v6.2c0 4.4-3 7.4-7 8.3-4-.9-7-3.9-7-8.3V6l7-2.5z" />
      <path d="m9.2 11.8 2 2 3.6-3.8" />
    </>
  ),
  logout: (
    <path d="M14.5 8.5V6A1.5 1.5 0 0 0 13 4.5H6A1.5 1.5 0 0 0 4.5 6v12A1.5 1.5 0 0 0 6 19.5h7a1.5 1.5 0 0 0 1.5-1.5v-2.5M9.5 12H20M17 9l3 3-3 3" />
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  paperclip: (
    <path d="M20.2 11.2l-8 8a5 5 0 0 1-7.1-7.1l8-8a3.5 3.5 0 0 1 5 5l-8 8a1.9 1.9 0 0 1-2.7-2.7l7.1-7.1" />
  ),
  send: <path d="M12 19V5.5M5.5 11.5L12 5l6.5 6.5" />,
  stop: <rect x="7" y="7" width="10" height="10" rx="1.6" />,
  mic: (
    <>
      <path d="M12 4a3 3 0 0 1 3 3v4.8a3 3 0 0 1-6 0V7a3 3 0 0 1 3-3z" />
      <path d="M6.5 11.5a5.5 5.5 0 0 0 11 0M12 17.2v3M9.5 20.2h5" />
    </>
  ),
  cpu: (
    <>
      <rect x="7" y="7" width="10" height="10" rx="1.8" />
      <rect x="10.4" y="10.4" width="3.2" height="3.2" rx="0.6" />
      <path d="M9.5 4v3M14.5 4v3M9.5 17v3M14.5 17v3M4 9.5h3M4 14.5h3M17 9.5h3M17 14.5h3" />
    </>
  ),
  image: (
    <>
      <rect x="4" y="4.5" width="16" height="15" rx="1.6" />
      <circle cx="9" cy="9.8" r="1.4" />
      <path d="m6.5 17 4-4.2 2.4 2.4 2.6-2.7 4 4.3" />
    </>
  ),
  wrench: (
    <path d="M17.2 6.8a4.4 4.4 0 0 0-5.8 5.1l-7 7a1.9 1.9 0 0 0 2.7 2.7l7-7a4.4 4.4 0 0 0 5.1-5.8l-2.8 2.8-2.4-.6-.6-2.4 2.8-2.8z" />
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4.5 4.5" />
    </>
  ),
  "more-h": <path d="M5.5 12h.01M12 12h.01M18.5 12h.01" strokeWidth={2.6} />,
  "more-v": <path d="M12 5.5v.01M12 12v.01M12 18.5v.01" strokeWidth={2.6} />,
  x: <path d="m6 6 12 12M18 6L6 18" />,
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  alert: (
    <>
      <path d="M12 4.2L21 19H3l9-14.8z" />
      <path d="M12 10v4M12 16.6h.01" />
    </>
  ),
  play: <path d="M8 5.5v13l11-6.5-11-6.5z" />,
  pause: <path d="M8.5 5.5v13M15.5 5.5v13" strokeWidth={2.2} />,
  download: <path d="M12 4v10M7.5 10l4.5 4.5L16.5 10M5 19.5h14" />,
  upload: <path d="M12 14V4M7.5 8.5 12 4l4.5 4.5M5 19.5h14" />,
  folder: <path d="M3.5 6.5A1.5 1.5 0 0 1 5 5h4l2 2.5h8A1.5 1.5 0 0 1 20.5 9v9a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 18V6.5z" />,
  file: (
    <>
      <path d="M6.5 3.5h7L19 9v10.5a1 1 0 0 1-1 1h-11.5a1 1 0 0 1-1-1v-15a1 1 0 0 1 1-1z" />
      <path d="M13.5 3.5V9H19" />
    </>
  ),
  "file-search": (
    <>
      <path d="M6.5 3.5h7L19 9v10.5a1 1 0 0 1-1 1h-11.5a1 1 0 0 1-1-1v-15a1 1 0 0 1 1-1z" />
      <path d="M13.5 3.5V9H19" />
      <circle cx="11.2" cy="13.8" r="2.4" />
      <path d="m13 15.6 1.8 1.8" />
    </>
  ),
  branch: (
    <>
      <circle cx="6.5" cy="6" r="2.4" />
      <circle cx="6.5" cy="18" r="2.4" />
      <circle cx="17.5" cy="6" r="2.4" />
      <path d="M6.5 8.4v7.2M17.5 8.4v1.1c0 2.6-2.2 3.9-5 4.4" />
    </>
  ),
  layers: (
    <>
      <path d="M12 3.5L21 8l-9 4.5L3 8l9-4.5z" />
      <path d="m4 12.2 8 4 8-4M4 16.2l8 4 8-4" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 1.8" />
    </>
  ),
  refresh: <path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 3.8V8.3H15" />,
  eye: (
    <>
      <path d="M3 12s3.4-6 9-6 9 6 9 6-3.4 6-9 6-9-6-9-6z" />
      <circle cx="12" cy="12" r="2.5" />
    </>
  ),
  external: <path d="M13.5 5H19v5.5M19 5l-8 8M10.5 5H6a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-4.5" />,
  edit: (
    <>
      <path d="M14.2 5.3l4.5 4.5L8.5 20H4v-4.5L14.2 5.3z" />
      <path d="m12.5 7 4.5 4.5" />
    </>
  ),
  save: (
    <>
      <path d="M5.5 4.5h10.4l3.6 3.6v10.4a1 1 0 0 1-1 1H5.5a1 1 0 0 1-1-1v-13a1 1 0 0 1 1-1z" />
      <path d="M8 4.5v4.2h6V4.5M8 19.5v-4.7h8v4.7" />
    </>
  ),
  list: <path d="M8.5 6.5H20M8.5 12H20M8.5 17.5H20M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01" />,
  trash: (
    <>
      <path d="M4.5 7h15M9.5 7V5.2a1.2 1.2 0 0 1 1.2-1.2h2.6a1.2 1.2 0 0 1 1.2 1.2V7" />
      <path d="M6.5 7l.7 12a1.6 1.6 0 0 0 1.6 1.5h6.4a1.6 1.6 0 0 0 1.6-1.5l.7-12" />
    </>
  ),
  copy: (
    <>
      <rect x="8.5" y="8.5" width="11" height="11" rx="1.6" />
      <path d="M5.5 14.5h-.4a1.6 1.6 0 0 1-1.6-1.6V5.5a1.6 1.6 0 0 1 1.6-1.6h7.4a1.6 1.6 0 0 1 1.6 1.6v.4" />
    </>
  ),
  target: (
    <>
      <circle cx="12" cy="12" r="8.2" />
      <circle cx="12" cy="12" r="4.2" />
      <circle cx="12" cy="12" r="0.6" strokeWidth={2.4} />
    </>
  ),
  lightbulb: (
    <>
      <path d="M9.5 18h5M10.2 21h3.6" />
      <path d="M12 3.5a6 6 0 0 1 3.6 10.8c-.7.5-1.1 1.3-1.1 2.2h-5c0-.9-.4-1.7-1.1-2.2A6 6 0 0 1 12 3.5z" />
    </>
  ),
  chart: <path d="M4 19.5h16M7 15.5v-4M12 15.5V8M17 15.5V9M17 15.5v-6.5" />,
  atom: (
    <>
      <circle cx="12" cy="12" r="1.2" strokeWidth={2.2} />
      <ellipse cx="12" cy="12" rx="9" ry="3.6" />
      <ellipse cx="12" cy="12" rx="9" ry="3.6" transform="rotate(60 12 12)" />
      <ellipse cx="12" cy="12" rx="9" ry="3.6" transform="rotate(120 12 12)" />
    </>
  ),
  key: (
    <>
      <circle cx="8" cy="14.5" r="3.8" />
      <path d="M10.8 11.7 20 2.5M15.5 7l2.2 2.2M18.5 4l2.2 2.2" />
    </>
  ),
  globe: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M3.5 12h17M12 3.5c2.6 2.3 4 5.2 4 8.5s-1.4 6.2-4 8.5c-2.6-2.3-4-5.2-4-8.5s1.4-6.2 4-8.5z" />
    </>
  ),
  "arrow-right": <path d="M4.5 12h14M13 6.5l5.5 5.5-5.5 5.5" />,
  "arrow-up-right": <path d="M7 17L17 7M9 7h8v8" />,
  "panel-left": (
    <>
      <rect x="3.5" y="4.5" width="17" height="15" rx="1.6" />
      <path d="M9.5 4.5v15" />
    </>
  ),
  "panel-right": (
    <>
      <rect x="3.5" y="4.5" width="17" height="15" rx="1.6" />
      <path d="M14.5 4.5v15" />
    </>
  ),
  "chevron-down": <path d="m6.5 9.5 5.5 5.5 5.5-5.5" />,
  "chevron-left": <path d="m14.5 6.5-5.5 5.5 5.5 5.5" />,
  "chevron-right": <path d="m9.5 6.5 5.5 5.5-5.5 5.5" />,
  "chevrons-left": <path d="m11 7-5 5 5 5M18 7l-5 5 5 5" />,
  "chevrons-right": <path d="m13 7 5 5-5 5M6 7l5 5-5 5" />,
  link: (
    <path d="M10 14a3.5 3.5 0 0 0 5 0l3.5-3.5a3.5 3.5 0 0 0-5-5L12 7M14 10a3.5 3.5 0 0 0-5 0L5.5 13.5a3.5 3.5 0 0 0 5 5L12 17" />
  ),
  pin: (
    <>
      <path d="M12 16.5V21" />
      <path d="M7.5 3.5h9l-1.4 6 2.4 3.5H6.5L8.9 9.5l-1.4-6z" />
    </>
  ),
};

export interface IconProps {
  name: IconName;
  size?: number;
  strokeWidth?: number;
  className?: string;
  style?: CSSProperties;
  /** Decorative by default; pass a label when the icon stands alone. */
  label?: string;
}

export function Icon({ name, size = 16, strokeWidth = 1.8, className, style, label }: IconProps) {
  const glyph = PATHS[name];
  if (!glyph) return null;
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={style}
      aria-hidden={label ? undefined : true}
      role={label ? "img" : undefined}
      aria-label={label}
      focusable="false"
    >
      <rect x="20" y="2.2" width="1.8" height="1.8" rx=".3" fill="currentColor" stroke="none" opacity=".45" />
      {glyph}
    </svg>
  );
}

export function hasIcon(name: string): name is IconName {
  return Object.prototype.hasOwnProperty.call(PATHS, name);
}

export default Icon;
