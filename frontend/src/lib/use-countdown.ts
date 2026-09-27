"use client";

import { useEffect, useState } from "react";

function secondsUntil(deadline: string): number {
  return Math.max(0, Math.round((new Date(deadline).getTime() - Date.now()) / 1000));
}

/**
 * Whole seconds left until `deadline` (ISO string), ticking once a second
 * while there is one; `null` deadline -> 0. A local clock, not a server
 * poll -- nothing is fetched.
 */
export function useCountdown(deadline: string | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!deadline) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [deadline]);
  // `now` is read only to re-render every tick.
  void now;
  return deadline ? secondsUntil(deadline) : 0;
}
