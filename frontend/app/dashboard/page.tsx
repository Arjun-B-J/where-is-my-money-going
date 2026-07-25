"use client";
import { useEffect, useState } from "react";
import {
  TrendingDown, TrendingUp, Activity, AlertCircle,
} from "lucide-react";
import { Shell } from "@/components/Shell";
import { KPICard } from "@/components/KPICard";
import { SpendChart } from "@/components/SpendChart";
import { CategoryPie } from "@/components/CategoryPie";
import { TopMerchants } from "@/components/TopMerchants";
import { InsightsList } from "@/components/InsightsList";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { rupees } from "@/lib/utils";
import { api, type DashboardSummary, type InsightCard } from "@/lib/api";

export default function DashboardPage() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [insights, setInsights] = useState<InsightCard[]>([]);
  const [loading, setLoading] = useState(true);
  const [insightsLoading, setInsightsLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setErr(null);
    try {
      const s = await api.dashboard(12);
      setSummary(s);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const loadInsights = async () => {
    setInsightsLoading(true);
    try {
      const i = await api.insights(12);
      setInsights(i);
    } finally {
      setInsightsLoading(false);
    }
  };

  useEffect(() => {
    load();
    loadInsights();
  }, []);

  if (err) {
    return (
      <Shell>
        <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-red-700" data-testid="error">
          <h3 className="font-semibold">Couldn&apos;t load dashboard</h3>
          <p className="mt-1 text-sm">{err}</p>
          <p className="mt-3 text-xs text-red-600">
            Load some data first. Open the home page and choose <strong>See it on demo data</strong>.
          </p>
        </div>
      </Shell>
    );
  }

  if (loading || !summary) {
    return (
      <Shell>
        <div className="space-y-4">
          <div className="h-9 w-72 animate-pulse rounded-lg bg-brand-100" />
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="h-28 animate-pulse rounded-2xl bg-brand-100/60" />
            ))}
          </div>
          <div className="h-72 animate-pulse rounded-2xl bg-brand-100/40" />
        </div>
      </Shell>
    );
  }

  return (
    <Shell>
      <div className="mb-8 flex items-end justify-between">
        <div>
          <h1 className="font-display text-3xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-sm text-muted-foreground">
            {summary.transaction_count} transactions over the last 12 months
            {summary.needs_review > 0 && (
              <Badge tone="warn" className="ml-2">
                {summary.needs_review} need review
              </Badge>
            )}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={load}>
          Refresh
        </Button>
      </div>

      {/* KPIs */}
      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
        <KPICard
          label="Spent"
          value={rupees(summary.spend, { compact: true })}
          icon={TrendingDown}
          delta={
            summary.internal_transfers > 0
              ? `excl. ${rupees(summary.internal_transfers, { compact: true })} card bills`
              : undefined
          }
        />
        <KPICard
          label="Money in"
          value={rupees(summary.total_credit, { compact: true })}
          icon={TrendingUp}
          tone="good"
        />
        <KPICard
          label="Net"
          value={rupees(summary.net, { compact: true })}
          tone={summary.net >= 0 ? "good" : "warn"}
        />
        <KPICard
          label="Transactions"
          value={String(summary.transaction_count)}
          icon={Activity}
          delta={summary.needs_review > 0 ? `${summary.needs_review} need review` : "all tagged"}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Trend chart */}
        <Card className="min-w-0 lg:col-span-2">
          <CardHeader>
            <CardTitle>Monthly cash flow</CardTitle>
            <CardDescription>Spent vs earned by month</CardDescription>
          </CardHeader>
          <CardContent>
            <SpendChart data={summary.monthly} />
          </CardContent>
        </Card>

        {/* Category pie */}
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>Where it went</CardTitle>
            <CardDescription>Spend by category</CardDescription>
          </CardHeader>
          <CardContent>
            <CategoryPie data={summary.by_category} />
          </CardContent>
        </Card>

        {/* Insights */}
        <Card className="min-w-0 lg:col-span-2">
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <AlertCircle className="h-4 w-4 text-brand-500" />
                  What stands out
                </CardTitle>
                <CardDescription>
                  Written by the local model from your aggregates. Never sent anywhere.
                </CardDescription>
              </div>
              <Button size="sm" variant="ghost" onClick={loadInsights} disabled={insightsLoading}>
                {insightsLoading ? "…" : "Re-run"}
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            <InsightsList cards={insights} />
          </CardContent>
        </Card>

        {/* Top merchants */}
        <Card>
          <CardHeader>
            <CardTitle>Top merchants</CardTitle>
            <CardDescription>Where your money kept going</CardDescription>
          </CardHeader>
          <CardContent>
            <TopMerchants data={summary.top_merchants} />
          </CardContent>
        </Card>

        {/* People summary */}
        <Card className="lg:col-span-3">
          <CardHeader>
            <CardTitle>People graph</CardTitle>
            <CardDescription>Running balance with people you move money with both ways</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {summary.people.map((p) => (
                <div
                  key={p.person.id}
                  className="rounded-xl border border-brand-100 bg-white/70 p-4"
                >
                  <div className="flex items-baseline justify-between">
                    <p className="font-medium">{p.person.name}</p>
                    <Badge tone={p.they_owe_you > 0 ? "good" : p.they_owe_you < 0 ? "warn" : "muted"}>
                      {p.person.relationship_type}
                    </Badge>
                  </div>
                  <p className="mt-2 font-display text-2xl font-semibold tabular-nums">
                    {p.they_owe_you > 0 ? "+" : ""}
                    {rupees(p.they_owe_you, { compact: true })}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {p.they_owe_you > 0 ? "they owe you" : p.they_owe_you < 0 ? "you owe them" : "settled"}
                    {" · "}
                    {p.transaction_count} txns
                  </p>
                </div>
              ))}
              {summary.people.length === 0 && (
                <p className="col-span-full text-sm text-muted-foreground">
                  No people detected yet. They are found from two-way transfers.
                </p>
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    </Shell>
  );
}
