"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { Role } from "@/lib/auth/policy";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard" },
  { href: "/machines", label: "Machines" },
  { href: "/system-prompts", label: "System Prompts" },
  { href: "/toolsets", label: "Toolsets" },
  { href: "/prompts", label: "Prompts" },
  { href: "/runs", label: "Runs" },
  { href: "/results", label: "Results" },
  { href: "/customers", label: "Workspaces" },
  { href: "/account/tokens", label: "API tokens" },
  { href: "/admin/users", label: "Users", adminOnly: true },
] as const;

export function SidebarNav({ role }: { role: Role }) {
  const pathname = usePathname();

  return (
    <nav className="flex flex-col gap-1 p-4">
      {NAV_ITEMS.filter((item) => !("adminOnly" in item) || role === "admin").map((item) => {
        const isActive =
          item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);

        return (
          <Link
            key={item.href}
            href={item.href}
            className={`rounded-md px-3 py-2 text-sm font-medium transition-colors ${
              isActive
                ? "bg-zinc-900 text-zinc-50 dark:bg-zinc-100 dark:text-zinc-900"
                : "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-50"
            }`}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
