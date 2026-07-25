import { cn } from "@/lib/utils";

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: "default" | "warn" | "good" | "critical" | "info" | "muted";
}

export function Badge({ tone = "default", className, ...props }: BadgeProps) {
  const tones: Record<string, string> = {
    default: "bg-brand-100 text-brand-700 border-brand-200",
    warn: "bg-amber-100 text-amber-800 border-amber-200",
    good: "bg-emerald-100 text-emerald-800 border-emerald-200",
    critical: "bg-red-100 text-red-800 border-red-200",
    info: "bg-sky-100 text-sky-800 border-sky-200",
    muted: "bg-slate-100 text-slate-700 border-slate-200",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md border px-2 py-0.5 text-[11px] font-medium",
        tones[tone],
        className,
      )}
      {...props}
    />
  );
}
