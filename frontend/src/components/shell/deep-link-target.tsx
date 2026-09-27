"use client";

import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";

export function deepLinkElementId(kind: string, id: string): string {
  return `${kind}-${id}`;
}

/**
 * The item a `?<param>=<id>` deep link points at (Phase 4 branch 4: chat
 * "Kaynaklar" and "Görev oluşturuldu" links into /memory, /knowledge and
 * /tasks). Once `ready` (the list has loaded), the item is scrolled into
 * view -- once; the returned id drives its highlight.
 *
 * Read from `window.location` after mount rather than `useSearchParams`:
 * the views are statically prerendered without a Suspense boundary, and the
 * app router has already updated the URL (in an insertion effect) by the
 * time a page's effects run.
 */
export function useDeepLinkTarget(param: string, ready: boolean): string | null {
  const [target, setTarget] = useState<string | null>(null);
  const scrolledRef = useRef(false);

  /* eslint-disable react-hooks/set-state-in-effect -- reading the URL once after mount (not available during prerender) */
  useEffect(() => {
    setTarget(new URLSearchParams(window.location.search).get(param));
  }, [param]);
  /* eslint-enable react-hooks/set-state-in-effect */

  useEffect(() => {
    if (!ready || !target || scrolledRef.current) return;
    const el = document.getElementById(deepLinkElementId(param, target));
    if (!el) return;
    scrolledRef.current = true;
    if (typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "center" });
  }, [ready, target, param]);

  return target;
}

/** Wraps a list item so a deep link can find (and highlight) it. */
export function DeepLinkTarget({
  kind,
  id,
  active,
  children,
}: {
  kind: string;
  id: string;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <div
      id={deepLinkElementId(kind, id)}
      data-deep-link-active={active || undefined}
      className={cn(
        "scroll-mt-4 rounded-xl",
        active && "ring-2 ring-primary ring-offset-2 ring-offset-background",
      )}
    >
      {children}
    </div>
  );
}
