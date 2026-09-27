/** A short Turkish remaining-time label: "5 sa 12 dk", "12 dk", "40 sn". */
export function formatRemaining(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  if (total >= 3600) {
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    return minutes > 0 ? `${hours} sa ${minutes} dk` : `${hours} sa`;
  }
  if (total >= 60) return `${Math.floor(total / 60)} dk`;
  return `${total} sn`;
}
