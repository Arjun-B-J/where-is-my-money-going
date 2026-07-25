"use client";
import { ResponsiveContainer, Area, AreaChart, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";
import type { MonthlySpend } from "@/lib/api";
import { relativeMonth, rupees } from "@/lib/utils";

export function SpendChart({ data }: { data: MonthlySpend[] }) {
  const formatted = data.map((d) => ({
    name: relativeMonth(d.month),
    spent: d.debit_total,
    earned: d.credit_total,
  }));
  return (
    // Sized to sit level with the category card beside it, which is a donut
    // plus an eight-row legend. A short chart in a stretched grid cell leaves
    // an obvious empty band.
    <div className="h-[23rem] w-full">
      <ResponsiveContainer width="100%" height="100%" debounce={0}>
        <AreaChart data={formatted} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
          <defs>
            <linearGradient id="g-spent" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#FB923C" stopOpacity={0.5} />
              <stop offset="100%" stopColor="#FB923C" stopOpacity={0.05} />
            </linearGradient>
            <linearGradient id="g-earned" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#10B981" stopOpacity={0.4} />
              <stop offset="100%" stopColor="#10B981" stopOpacity={0.05} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="#FED7AA40" vertical={false} />
          <XAxis
            dataKey="name"
            stroke="#9A340280"
            fontSize={11}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            stroke="#9A340280"
            fontSize={11}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) => rupees(v, { compact: true })}
          />
          <Tooltip
            contentStyle={{
              borderRadius: 12,
              border: "1px solid #FED7AA",
              background: "white",
              fontSize: 12,
            }}
            formatter={(v: number) => rupees(v)}
          />
          <Area
            type="monotone"
            dataKey="earned"
            stroke="#059669"
            strokeWidth={2}
            fill="url(#g-earned)"
            name="Earned"
          />
          <Area
            type="monotone"
            dataKey="spent"
            stroke="#EA580C"
            strokeWidth={2}
            fill="url(#g-spent)"
            name="Spent"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
