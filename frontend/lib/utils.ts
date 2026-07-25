import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function rupees(amount: number, opts: { compact?: boolean } = {}): string {
  if (opts.compact && Math.abs(amount) >= 1_00_000) {
    return `₹${(amount / 1_00_000).toFixed(1)}L`;
  }
  if (opts.compact && Math.abs(amount) >= 1_000) {
    return `₹${(amount / 1_000).toFixed(1)}k`;
  }
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(amount);
}

export function pctOf(part: number, whole: number): number {
  if (!whole) return 0;
  return Math.round((part / whole) * 100);
}

export function relativeMonth(monthKey: string): string {
  const [y, m] = monthKey.split("-").map(Number);
  const d = new Date(y, m - 1);
  return d.toLocaleString("en-IN", { month: "short", year: "2-digit" });
}
