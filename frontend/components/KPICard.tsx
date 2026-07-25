import { Card, CardContent } from "./ui/Card";
import { cn } from "@/lib/utils";
import type { LucideIcon } from "lucide-react";

interface KPICardProps {
  label: string;
  value: string;
  delta?: string;
  icon?: LucideIcon;
  tone?: "default" | "good" | "warn";
}

export function KPICard({ label, value, delta, icon: Icon, tone = "default" }: KPICardProps) {
  const tones = {
    default: "text-foreground",
    good: "text-emerald-700",
    warn: "text-amber-700",
  } as const;
  return (
    <Card className="lift">
      <CardContent className="p-5">
        <div className="flex items-start justify-between">
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{label}</p>
            <p className={cn("mt-2 font-display text-3xl font-semibold tabular-nums", tones[tone])}>
              {value}
            </p>
            {delta && <p className="mt-1 text-xs text-muted-foreground">{delta}</p>}
          </div>
          {Icon && (
            <div className="rounded-lg bg-brand-50 p-2 text-brand-600">
              <Icon className="h-4 w-4" />
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
