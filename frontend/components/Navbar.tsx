"use client";

import {
  BarChart3,
  FileUp,
  GitBranch,
  MessageSquare,
  Receipt,
  Users,
  Wallet,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Wordmark } from "./Wordmark";

const LINKS = [
  { href: "/dashboard", label: "Dashboard", icon: BarChart3 },
  { href: "/ingest", label: "Statements", icon: FileUp },
  { href: "/transactions", label: "Transactions", icon: Wallet },
  { href: "/people", label: "People", icon: Users },
  { href: "/chat", label: "Ask", icon: MessageSquare },
  { href: "/receipt", label: "Receipt", icon: Receipt },
  { href: "/pipeline", label: "Pipeline", icon: GitBranch },
];

export function Navbar() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-40 border-b border-brand-100/70 bg-cream/85 backdrop-blur-md">
      <div className="mx-auto flex max-w-7xl items-center gap-4 px-6 py-3">
        <Wordmark />

        <nav
          className="ml-auto flex items-center gap-0.5 overflow-x-auto"
          aria-label="Main navigation"
        >
          {LINKS.map(({ href, label, icon: Icon }) => {
            const active = pathname === href;
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={`flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                  active
                    ? "bg-brand-100 text-brand-700"
                    : "text-ink-muted hover:bg-brand-50 hover:text-ink"
                }`}
              >
                <Icon className="h-4 w-4" aria-hidden="true" />
                <span className="hidden lg:inline">{label}</span>
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
