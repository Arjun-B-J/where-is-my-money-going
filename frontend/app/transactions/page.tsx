"use client";
import { useEffect, useState } from "react";
import { Search } from "lucide-react";
import { Shell } from "@/components/Shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { rupees, cn } from "@/lib/utils";
import { api, type Transaction } from "@/lib/api";

export default function TransactionsPage() {
  const [rows, setRows] = useState<Transaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<"all" | "review">("all");

  const load = async () => {
    setLoading(true);
    const params: Parameters<typeof api.transactions>[0] = { limit: 200 };
    if (filter === "review") params.needs_review = true;
    if (search) params.search = search;
    const r = await api.transactions(params);
    setRows(r);
    setLoading(false);
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  return (
    <Shell>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-3xl font-semibold tracking-tight">Transactions</h1>
          <p className="text-sm text-muted-foreground">{rows.length} shown</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && load()}
              placeholder="Search description…"
              className="h-9 w-64 rounded-lg border border-brand-200 bg-white pl-8 pr-3 text-sm focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-200"
            />
          </div>
          <Button
            size="sm"
            variant={filter === "all" ? "default" : "outline"}
            onClick={() => setFilter("all")}
          >
            All
          </Button>
          <Button
            size="sm"
            variant={filter === "review" ? "default" : "outline"}
            onClick={() => setFilter("review")}
          >
            Needs review
          </Button>
          <Button size="sm" variant="ghost" onClick={load}>
            Refresh
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Transaction ledger</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto px-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-brand-100 text-left text-xs uppercase tracking-wider text-muted-foreground">
                <th className="px-5 py-2 font-medium">Date</th>
                <th className="px-3 py-2 font-medium">Description</th>
                <th className="px-3 py-2 font-medium">Source</th>
                <th className="px-3 py-2 font-medium">Tag</th>
                <th className="px-3 py-2 font-medium">Confidence</th>
                <th className="px-5 py-2 text-right font-medium">Amount</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr>
                  <td colSpan={6} className="px-5 py-8 text-center text-muted-foreground">
                    Loading…
                  </td>
                </tr>
              )}
              {!loading && rows.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-5 py-8 text-center text-muted-foreground">
                    No transactions found. Run the pipeline first.
                  </td>
                </tr>
              )}
              {rows.map((t) => (
                <tr key={t.id} className="border-b border-brand-50 hover:bg-brand-50/40">
                  <td className="px-5 py-3 tabular-nums text-xs text-muted-foreground">
                    {new Date(t.posted_at).toLocaleDateString("en-IN", {
                      month: "short", day: "2-digit",
                    })}
                  </td>
                  <td className="px-3 py-3">
                    <div className="font-medium">
                      {t.merchant_normalized || t.raw_description.slice(0, 50)}
                    </div>
                    <div className="truncate font-mono text-[11px] text-muted-foreground">
                      {t.raw_description}
                    </div>
                  </td>
                  <td className="px-3 py-3">
                    <Badge tone="muted">{t.source}</Badge>
                  </td>
                  <td className="px-3 py-3">
                    <div className="flex items-center gap-1.5">
                      {t.category && (
                        <Badge tone={t.tag_source === "user" ? "good" : "default"}>
                          {t.category}
                        </Badge>
                      )}
                      {t.subcategory && (
                        <span className="text-xs text-muted-foreground">/ {t.subcategory}</span>
                      )}
                      {t.needs_review && <Badge tone="warn">review</Badge>}
                    </div>
                    <div className="text-[11px] text-muted-foreground">
                      via {t.tag_source ?? "untagged"}
                    </div>
                  </td>
                  <td className="px-3 py-3 text-xs text-muted-foreground">
                    {t.tag_confidence != null ? `${(t.tag_confidence * 100).toFixed(0)}%` : "—"}
                  </td>
                  <td
                    className={cn(
                      "px-5 py-3 text-right tabular-nums font-medium",
                      t.direction === "credit" ? "text-emerald-700" : "text-foreground",
                    )}
                  >
                    {t.direction === "credit" ? "+" : "-"}
                    {rupees(t.amount)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </Shell>
  );
}
