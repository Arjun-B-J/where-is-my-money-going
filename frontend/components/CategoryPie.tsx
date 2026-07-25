"use client";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import type { CategorySpend } from "@/lib/api";
import { rupees } from "@/lib/utils";

/** "loan_repayment" -> "loan repayment". Category names are snake_case in the DB. */
const label = (category: string) => category.replace(/_/g, " ");

const PALETTE = [
  "#F97316", "#FB923C", "#FDBA74", "#FED7AA", "#FACC15",
  "#84CC16", "#10B981", "#06B6D4", "#A855F7", "#EC4899",
  "#EF4444", "#8B5CF6", "#0EA5E9", "#14B8A6", "#94A3B8",
];

export function CategoryPie({ data }: { data: CategorySpend[] }) {
  const top = data.slice(0, 8);
  return (
    // The chart gets a fixed height of its own and the legend flows after it.
    // Both used to live inside one `h-72` box with the chart at height="100%",
    // which left the legend no room and let the card clip its last rows.
    <div className="w-full">
      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%" debounce={0}>
          <PieChart>
            <Pie
              data={top}
              dataKey="total"
              nameKey="category"
              innerRadius={52}
              outerRadius={90}
              paddingAngle={2}
              cornerRadius={4}
            >
              {top.map((_, i) => (
                <Cell key={i} fill={PALETTE[i % PALETTE.length]} stroke="white" strokeWidth={2} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{
                borderRadius: 12,
                border: "1px solid #FED7AA",
                background: "white",
                fontSize: 12,
              }}
              formatter={(v: number, _n, p) => [rupees(v), label(p.payload.category)]}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>

      <ul className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
        {top.map((c, i) => (
          <li key={c.category} className="flex min-w-0 items-center gap-1.5">
            <span
              className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm"
              style={{ background: PALETTE[i % PALETTE.length] }}
              aria-hidden="true"
            />
            {/* Truncate rather than wrap: a wrapped label pushes the amount out of
                alignment and makes the two columns different heights. */}
            <span className="truncate capitalize" title={label(c.category)}>
              {label(c.category)}
            </span>
            <span className="ml-auto shrink-0 tabular-nums text-muted-foreground">
              {rupees(c.total, { compact: true })}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
