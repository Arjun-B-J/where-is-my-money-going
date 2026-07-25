"use client";
import { motion } from "framer-motion";
import { AlertCircle, CheckCircle2, Info, TrendingDown } from "lucide-react";
import type { InsightCard as IC } from "@/lib/api";
import { cn } from "@/lib/utils";

const TONES = {
  info: { wrap: "border-sky-200 bg-sky-50/60", icon: Info, fg: "text-sky-700" },
  warn: { wrap: "border-amber-200 bg-amber-50/60", icon: AlertCircle, fg: "text-amber-700" },
  critical: { wrap: "border-red-200 bg-red-50/60", icon: TrendingDown, fg: "text-red-700" },
  good: { wrap: "border-emerald-200 bg-emerald-50/60", icon: CheckCircle2, fg: "text-emerald-700" },
} as const;

export function InsightsList({ cards }: { cards: IC[] }) {
  if (!cards?.length) {
    return (
      <div className="rounded-xl border border-brand-100 bg-white p-5 text-sm text-muted-foreground">
        Nothing to show yet. Load the demo data or add a statement.
      </div>
    );
  }
  return (
    <div className="space-y-3">
      {cards.map((c, i) => {
        const tone = TONES[c.severity] ?? TONES.info;
        const Icon = tone.icon;
        return (
          <motion.div
            key={i}
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: i * 0.05 }}
            className={cn("rounded-xl border p-4", tone.wrap)}
          >
            <div className="flex items-start gap-3">
              <Icon className={cn("mt-0.5 h-5 w-5 shrink-0", tone.fg)} />
              <div className="flex-1">
                <div className="flex items-baseline justify-between gap-2">
                  <h4 className="font-semibold text-foreground">{c.title}</h4>
                  {c.metric && (
                    <span className={cn("text-xs font-semibold tabular-nums", tone.fg)}>
                      {c.metric}
                    </span>
                  )}
                </div>
                <p className="mt-1 text-sm text-muted-foreground">{c.body}</p>
                {/* Cards assembled from the numbers are labelled, so a model
                    outage is visible rather than silently papered over. */}
                {c.generated_by === "computed" && (
                  <p className="mt-1.5 text-[11px] uppercase tracking-wide text-muted-foreground/70">
                    Computed from your figures. The local model was unavailable
                  </p>
                )}
              </div>
            </div>
          </motion.div>
        );
      })}
    </div>
  );
}
