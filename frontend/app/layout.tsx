import type { Metadata, Viewport } from "next";
import "./globals.css";

const TITLE = "Where Is My Money Going?";
const DESCRIPTION =
  "Drop in your bank and credit-card statements and get a categorised, " +
  "transaction-level answer. Parsing is deterministic, categorising runs on a " +
  "local model, and nothing leaves your machine.";

export const metadata: Metadata = {
  title: { default: TITLE, template: `%s · ${TITLE}` },
  description: DESCRIPTION,
  metadataBase: new URL("http://localhost:3000"),
  applicationName: TITLE,
  openGraph: { title: TITLE, description: DESCRIPTION, type: "website" },
  // No analytics, no third-party fonts, no external scripts. The privacy claim
  // in the README has to be true of the frontend too.
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: "#0F172A",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen bg-cream font-sans antialiased">{children}</body>
    </html>
  );
}
