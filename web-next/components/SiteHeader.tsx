"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "创作" },
  { href: "/catalog", label: "产品资料" },
  { href: "/settings", label: "设置" },
];

export default function SiteHeader() {
  const pathname = usePathname();
  return (
    <header className="border-b border-[var(--color-soft)]">
      <div className="wrap flex h-[84px] items-center justify-between gap-6">
        <Link href="/" className="display text-[27px] leading-none text-ink">
          MateQuill
          <span className="ml-3 align-middle font-sans text-[16px] text-secondary">
            场景有据
          </span>
        </Link>
        <nav className="flex items-center gap-7">
          {NAV.map((item) => {
            const active =
              item.href === "/"
                ? pathname === "/"
                : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`border-b-2 pb-1 text-[16px] transition-colors ${
                  active
                    ? "border-accent text-accent"
                    : "border-transparent text-secondary hover:text-accent"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
