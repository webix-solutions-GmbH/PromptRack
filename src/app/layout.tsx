import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { SidebarNav } from "@/components/sidebar-nav";

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

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-zinc-50 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-50">
        <div className="flex min-h-screen flex-1">
          <aside className="w-56 shrink-0 border-r border-zinc-200 dark:border-zinc-800">
            <div className="px-4 py-4 text-sm font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
              Agent Model Evaluator
            </div>
            <SidebarNav />
          </aside>
          <main className="flex flex-1 flex-col">{children}</main>
        </div>
      </body>
    </html>
  );
}
