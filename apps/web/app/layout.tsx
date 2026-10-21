import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";
import { IdentityProvider } from "@/lib/identity";
import { Shell } from "@/components/Shell";

export const metadata: Metadata = {
  title: "BioAdapt workbench",
  description:
    "Project screens for the single-cell platform: data registration, plan approval, the expert queue, evidence cards and delivery exports.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <IdentityProvider>
          <Shell>{children}</Shell>
        </IdentityProvider>
      </body>
    </html>
  );
}
