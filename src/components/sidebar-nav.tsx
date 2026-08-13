"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { Role } from "@/lib/auth/policy";

type NavItem = { href: string; label: string; adminOnly?: boolean };

// Grouped in the order the work happens: configure it, run it, read it. The
// first section is deliberately unlabelled — a heading over Dashboard alone
// would name nothing the link does not already say. "Settings" rather than
// "Admin" because Workspaces and API tokens are member-accessible; only Users
// is not.
const NAV_SECTIONS: { label: string | null; items: NavItem[] }[] = [
  {
    label: null,
    items: [{ href: "/", label: "Dashboard" }],
  },
  {
    label: "Setup",
    items: [
      { href: "/prompts", label: "Prompts" },
      { href: "/system-prompts", label: "System Prompts" },
      { href: "/toolsets", label: "Toolsets" },
      { href: "/machines", label: "Machines" },
    ],
  },
  {
    label: "Evaluate",
    items: [
      { href: "/runs", label: "Runs" },
      { href: "/results", label: "Results" },
    ],
  },
  {
    label: "Settings",
    items: [
      { href: "/customers", label: "Workspaces" },
      { href: "/account/tokens", label: "API tokens" },
      { href: "/admin/users", label: "Users", adminOnly: true },
    ],
  },
];

export function SidebarNav({ role }: { role: Role }) {
  const pathname = usePathname();

  return (
    <nav className="flex flex-col p-4">
      {NAV_SECTIONS.map((section) => {
        const items = section.items.filter((item) => !item.adminOnly || role === "admin");
        // No section can empty out today, since Users is the only admin-only
        // item — the guard is what keeps a second one from ever leaving its
        // heading behind with nothing under it.
        if (items.length === 0) return null;

        return (
          <div
            key={section.label ?? "main"}
            className={`flex flex-col gap-1 ${section.label ? "mt-5" : ""}`}
          >
            {section.label ? (
              <h2 className="px-3 pb-1 text-[11px] font-medium uppercase tracking-wider text-zinc-400 dark:text-zinc-500">
                {section.label}
              </h2>
            ) : null}
            {items.map((item) => {
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
          </div>
        );
      })}
    </nav>
  );
}
