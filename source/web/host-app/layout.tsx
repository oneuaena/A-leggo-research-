import type { Metadata } from "next";
import "./globals.css";
import "./aleggo-controls.css";
import ThemeScript from "@/components/ThemeScript";
import ToastViewport from "@/components/common/ToastViewport";
import { AppShellProvider } from "@/context/AppShellContext";
import { I18nClientBridge } from "@/i18n/I18nClientBridge";
import PwaRegistration from "@/components/PwaRegistration";

export const metadata: Metadata = {
  title: "A-leggo Next",
  description: "Evidence-driven university mathematics learning companion",
  manifest: "/manifest.webmanifest",
  appleWebApp: {
    capable: true,
    statusBarStyle: "default",
    title: "A-leggo Next",
  },
  icons: {
    icon: [
      { url: "/aleggo-mark.png", sizes: "256x256", type: "image/png" },
      { url: "/favicon-16x16.png", sizes: "16x16", type: "image/png" },
      { url: "/favicon-32x32.png", sizes: "32x32", type: "image/png" },
    ],
    apple: "/apple-touch-icon.png",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      data-scroll-behavior="smooth"
      className="font-runtime"
    >
      <head>
        <ThemeScript />
      </head>
      <body
        className="font-sans bg-[var(--background)] text-[var(--foreground)]"
        suppressHydrationWarning
      >
        <AppShellProvider>
          <I18nClientBridge>{children}</I18nClientBridge>
          <PwaRegistration />
          <ToastViewport />
        </AppShellProvider>
      </body>
    </html>
  );
}
