"use client";

import Link from "next/link";

import { useT } from "@/lib/i18n";

export default function NotFound() {
  const t = useT();
  return (
    <section className="panel not-found">
      <h1>404</h1>
      <p>{t("This page could not be found.")}</p>
      <p className="muted">
        {t(
          "A card is reachable only through a run and the expert queue; every screen starts from the project list.",
        )}
      </p>
      <Link href="/" className="button-link">
        {t("Back to the start")}
      </Link>
    </section>
  );
}
