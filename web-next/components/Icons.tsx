import type { CSSProperties } from "react";

export function Icon({
  name,
  size = 22,
  style,
}: {
  name:
    | "write"
    | "history"
    | "book"
    | "store"
    | "photo"
    | "mic"
    | "close"
    | "arrow"
    | "settings";
  size?: number;
  style?: CSSProperties;
}) {
  const paths = {
    write: "M16 3l5 5-12 12-6 1 1-6L16 3zM14 5l5 5",
    history: "M8 3h10l3 3v15H8zM3 7v14M12 10h5m-5 5h5",
    book: "M3 4h7l2 2 2-2h7v16h-7l-2 2-2-2H3zM12 6v16",
    store: "M3 10h18M4 10v11h16V10M2 10l3-7h14l3 7M9 21v-7h6v7",
    photo: "M3 6h5l2-3h4l2 3h5v15H3zM8 13a4 4 0 1 0 8 0 4 4 0 0 0-8 0",
    mic: "M9 4a3 3 0 0 1 6 0v8a3 3 0 0 1-6 0zM5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8",
    close: "m6 6 12 12M6 18 18 6",
    arrow: "M5 12h14m-6-6 6 6-6 6",
    settings:
      "m12 3 3 2 3 1 1 4 2 2-2 3-1 3-4 1-2 2-3-2-3-1-1-4-2-2 2-3 1-3 4-1zM9 12a3 3 0 1 0 6 0 3 3 0 0 0-6 0",
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={style}
    >
      <path d={paths[name]} />
    </svg>
  );
}
