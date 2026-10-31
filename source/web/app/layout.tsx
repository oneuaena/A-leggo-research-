import type { Metadata } from "next";
import "./globals.css";
import "./aleggo-controls.css";
import "./standalone.css";
import "./research-phase1.css";
import "./research-reference.css";
import { AppShellProvider } from "@/context/AppShellContext";
export const metadata: Metadata = { title: "A-leggo Research", description: "数学研究工作台" };
export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  return <html lang="zh-CN"><body><AppShellProvider>{children}</AppShellProvider></body></html>;
}
