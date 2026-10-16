// All money arrives as integer cents; formatting stays in integer arithmetic.

export function fmtPx(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "—";
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  const whole = Math.trunc(abs / 100);
  return `${sign}${whole.toLocaleString("en-US")}.${String(abs % 100).padStart(2, "0")}`;
}

export function fmtUsd(cents: number | null | undefined): string {
  return cents === null || cents === undefined ? "—" : `$${fmtPx(cents)}`;
}

export function fmtQty(qty: number | null | undefined): string {
  return qty === null || qty === undefined ? "—" : qty.toLocaleString("en-US");
}

export function fmtCompactUsd(cents: number): string {
  const dollars = Math.trunc(cents / 100);
  const scaled = (unit: number, suffix: string) => {
    const hundredths = Math.trunc((dollars * 100) / unit);
    return `$${Math.trunc(hundredths / 100).toLocaleString("en-US")}.${String(hundredths % 100).padStart(2, "0")}${suffix}`;
  };
  if (dollars >= 1_000_000) return scaled(1_000_000, "M");
  if (dollars >= 1_000) return scaled(1_000, "K");
  return `$${dollars}`;
}

export function fmtBps(bpsE2: number | null | undefined): string {
  if (bpsE2 === null || bpsE2 === undefined) return "—";
  return `${Math.trunc(bpsE2 / 100)}.${String(bpsE2 % 100).padStart(2, "0")} bp`;
}

export function fmtMid(bid: number | null, ask: number | null): string {
  if (bid === null || ask === null) return "—";
  // bid + ask is the mid in half-cents; render to the tenth of a cent
  const halfCents = bid + ask;
  const whole = Math.trunc(halfCents / 200);
  const mills = (halfCents % 200) * 5;
  return `${whole.toLocaleString("en-US")}.${String(mills).padStart(3, "0")}`;
}

export function vwapCents(notionalCents: number, volume: number): number | null {
  if (volume <= 0) return null;
  return Math.trunc((notionalCents * 2 + volume) / (2 * volume));
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  let text = iso;
  // Database timestamps carry the server's offset; display everything in UTC
  if (/[+-]\d{2}:\d{2}$/.test(iso) && !iso.endsWith("+00:00")) {
    const parsed = new Date(iso);
    if (!Number.isNaN(parsed.getTime())) text = parsed.toISOString();
  }
  const match = /T(\d{2}:\d{2}:\d{2})(\.\d{1,6})?/.exec(text);
  if (!match) return iso;
  const millis = (match[2] ?? ".000").slice(1, 4).padEnd(3, "0");
  return `${match[1]}.${millis}`;
}

export function shortHash(hash: string, size = 10): string {
  return hash.length > size ? `${hash.slice(0, size)}…` : hash;
}

export function pct(part: number, whole: number): number {
  if (whole <= 0) return 0;
  return Math.min(100, Math.trunc((part * 100) / whole));
}
