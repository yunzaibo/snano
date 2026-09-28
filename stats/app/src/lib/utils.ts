import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmt(n: number): string {
  return (n || 0).toLocaleString("en-US");
}

export function pct(part: number, total: number): string {
  return total ? Math.round((part / total) * 100) + "%" : "0%";
}
