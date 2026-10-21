import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";
import { IdentityProvider } from "@/lib/identity";
import { LocaleProvider } from "@/lib/i18n";
import { Shell } from "@/components/Shell";

export const metadata: Metadata = {
  title: "BioAdapt workbench",
  description:
    "Project screens for the single-cell platform: data registration, plan approval, the expert queue, evidence cards and delivery exports.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // lang is what the server renders; LocaleProvider rewrites it from the stored preference, since
    // the whole workbench is client-side and a locale cannot be a route the server picks.
    <html lang="en">
      <body>
        <IdentityProvider>
          <LocaleProvider>
            <Shell>{children}</Shell>
          </LocaleProvider>
        </IdentityProvider>
      </body>
    </html>
  );
}
