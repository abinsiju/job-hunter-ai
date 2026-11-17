import type { Metadata, Viewport } from "next";
import "./globals.css";
import { RegisterSW } from "@/components/register-sw";

export const metadata: Metadata = {
  title: { default: "Job Hunter", template: "%s · Job Hunter" },
  description: "Private job discovery, scoring and application tracking. Runs on your own Mac.",
  applicationName: "Job Hunter",
  manifest: "/manifest.webmanifest",
  icons: { icon: [{ url: "/icons/icon-192.png", sizes: "192x192", type: "image/png" }], apple: [{ url: "/icons/apple-touch-icon.png", sizes: "180x180" }] },
  appleWebApp: { capable: true, title: "Job Hunter", statusBarStyle: "default" },
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width", initialScale: 1, viewportFit: "cover",
  themeColor: [{ media: "(prefers-color-scheme: light)", color: "#f5f5f7" }, { media: "(prefers-color-scheme: dark)", color: "#000000" }],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en-AU">
      {/* Browser extensions (e.g. Grammarly) inject attributes into <body> before React hydrates */}
      <body suppressHydrationWarning>{children}<RegisterSW /></body>
    </html>
  );
}
