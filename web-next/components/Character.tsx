"use client";

import { useEffect, useState } from "react";

export default function Character({
  state = "idle",
  className = "",
}: {
  state?: "idle" | "focus" | "thinking" | "waiting" | "done" | "error";
  className?: string;
}) {
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    const update = () => setPaused(document.hidden);
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  return (
    <svg
      className={`youju-character ${className}`}
      data-state={state}
      data-paused={paused}
      viewBox="0 0 144 144"
      aria-hidden="true"
    >
      <path
        d="M22 28C38 9 77 8 104 23c29 16 38 53 22 79-16 26-50 38-79 24C17 113 6 80 13 53c2-10 5-18 9-25Z"
        fill="#d5ed83"
      />
      <g transform="rotate(-7 72 72)">
        <g className="yj-head">
          <path
            d="M35 40c0-10 8-17 18-17h43c13 0 23 11 23 24v35c0 15-11 26-26 26H63l-21 15 3-20c-13-3-22-14-22-28V58c0-8 4-14 12-18Z"
            fill="#24261f"
          />
          <g className="yj-gaze">
            <g className="yj-open-eyes" fill="#fffef8">
              <rect
                className="yj-eye yj-eye-left"
                x="49"
                y="51"
                width="14"
                height="28"
                rx="7"
              />
              <rect
                className="yj-eye yj-eye-right"
                x="83"
                y="50"
                width="14"
                height="28"
                rx="7"
              />
            </g>
            <g
              className="yj-smile-eyes"
              stroke="#fffef8"
              strokeWidth="4"
              strokeLinecap="round"
              fill="none"
            >
              <path d="M49 64q7-12 14 0M83 63q7-12 14 0" />
            </g>
            <path
              d="M68 88q7 5 14-1"
              stroke="#fffef8"
              strokeWidth="3"
              strokeLinecap="round"
              fill="none"
            />
          </g>
        </g>
      </g>
    </svg>
  );
}
