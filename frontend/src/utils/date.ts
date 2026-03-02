/**
 * Date formatting utilities — all times displayed in BRT (UTC-3), 24h format.
 */

const BRT: Intl.DateTimeFormatOptions = { timeZone: "America/Sao_Paulo" };

/** Parse a backend timestamp as UTC (appends "Z" if missing). */
function parseUTC(ts: string): Date {
  if (!ts.endsWith("Z") && !ts.includes("+")) {
    return new Date(ts + "Z");
  }
  return new Date(ts);
}

/** "02/03 07:20" — date + time in 24h BRT */
export function formatDateTime(ts: string): string {
  const d = parseUTC(ts);
  return d.toLocaleString("pt-BR", {
    ...BRT,
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/** "07:20:15" — time only in 24h BRT */
export function formatTime(ts: string): string {
  const d = parseUTC(ts);
  return d.toLocaleTimeString("pt-BR", {
    ...BRT,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

/** "02/03" — date only in BRT */
export function formatDate(ts: string): string {
  const d = parseUTC(ts);
  return d.toLocaleDateString("pt-BR", {
    ...BRT,
    day: "2-digit",
    month: "2-digit",
  });
}

/** "just now", "5m ago", "2h ago", or full date+time — relative format */
export function formatRelative(ts: string): string {
  const d = parseUTC(ts);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMin = Math.floor(diffMs / 60000);

  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${diffH}h ago`;

  return formatDateTime(ts);
}
