import type { MerchantSpend } from "@/lib/api";
import { rupees } from "@/lib/utils";

export function TopMerchants({ data }: { data: MerchantSpend[] }) {
  const max = Math.max(...data.map((d) => d.total), 1);
  return (
    <div className="space-y-2.5">
      {data.slice(0, 8).map((m) => (
        <div key={m.merchant} className="space-y-1">
          <div className="flex items-baseline justify-between text-sm">
            <span className="truncate font-medium text-foreground">{m.merchant}</span>
            <span className="ml-3 shrink-0 tabular-nums text-muted-foreground">{rupees(m.total)}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-brand-50">
            <div
              className="h-full bg-gradient-to-r from-brand-400 to-brand-500"
              style={{ width: `${(m.total / max) * 100}%` }}
            />
          </div>
          <div className="flex justify-between text-[11px] text-muted-foreground">
            <span>{m.count} txns</span>
          </div>
        </div>
      ))}
    </div>
  );
}
