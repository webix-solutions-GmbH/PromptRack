import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { SidebarNav } from "@/components/sidebar-nav";
import { UserMenu } from "@/components/auth/user-menu";
import { currentActor } from "@/lib/auth/guards";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Agent Model Evaluator",
  description: "Evaluate and compare local LLM models across machines.",
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // Null on /login, the one page the proxy lets a signed-out visitor reach:
  // there is no session to describe, so the app chrome is left off entirely.
  const actor = await currentActor();

  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-zinc-50 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-50">
        {actor ? (
          <div className="flex min-h-screen flex-1">
            <aside className="flex w-56 shrink-0 flex-col border-r border-zinc-200 dark:border-zinc-800">
              <div className="px-4 py-4 text-sm font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
                Agent Model Evaluator
              </div>
              <SidebarNav role={actor.role} />
              <UserMenu name={actor.name} email={actor.email} role={actor.role} />
            </aside>
            <main className="flex flex-1 flex-col">{children}</main>
          </div>
        ) : (
          <div className="flex min-h-screen flex-1">
            <main className="flex flex-1 flex-col">{children}</main>
          </div>
        )}
      </body>
    </html>
  );
}
