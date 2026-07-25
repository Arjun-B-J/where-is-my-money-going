"use client";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Brain, Cpu, Play, Loader2 } from "lucide-react";
import { Shell } from "@/components/Shell";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { api, type PipelineRun, type PipelineTopology } from "@/lib/api";

export default function PipelinePage() {
  const [topology, setTopology] = useState<PipelineTopology | null>(null);
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [running, setRunning] = useState(false);

  const reload = async () => {
    const [t, r] = await Promise.all([api.topology(), api.runs()]);
    setTopology(t);
    setRuns(r);
  };

  useEffect(() => {
    reload();
  }, []);

  const runNow = async () => {
    setRunning(true);
    try {
      await api.runDemo(42, 12);
      await reload();
    } finally {
      setRunning(false);
    }
  };

  const latest = runs[0];

  return (
    <Shell>
      <div className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="font-display text-3xl font-semibold tracking-tight">LangGraph pipeline</h1>
          <p className="text-sm text-muted-foreground">
            How a transaction flows from raw description to a confidence-scored category.
          </p>
        </div>
        <Button onClick={runNow} disabled={running} variant="glow">
          {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
          Run pipeline
        </Button>
      </div>

      {/* Topology */}
      <Card className="mb-6">
        <CardHeader>
          <CardTitle>Graph topology</CardTitle>
          <CardDescription>
            Each node is a discrete step. Deterministic steps are blue, steps that
            call the local model are orange. After seeding, the graph branches: a
            demo run generates data, a real run parses your statements.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-3">
            {topology?.nodes.map((n, i) => (
              <motion.div
                key={n.id}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: i * 0.05 }}
                className="flex items-center gap-3"
              >
                {i > 0 && (() => {
                  const previous = (topology?.nodes ?? [])[i - 1];
                  // The graph branches after `seed`: one source node runs, not both.
                  const alternatives = Boolean(n.mode && previous?.mode);
                  return (
                    <span
                      className={
                        alternatives
                          ? "text-[11px] font-semibold uppercase tracking-wider text-muted-foreground"
                          : "text-brand-300"
                      }
                      aria-hidden="true"
                    >
                      {alternatives ? "or" : "→"}
                    </span>
                  );
                })()}
                <div
                  className={
                    n.kind === "llm"
                      ? "flex items-center gap-2 rounded-xl border border-brand-300 bg-brand-50 px-4 py-2 shadow-sm"
                      : "flex items-center gap-2 rounded-xl border border-sky-200 bg-sky-50 px-4 py-2 shadow-sm"
                  }
                >
                  {n.kind === "llm" ? (
                    <Brain className="h-4 w-4 text-brand-600" />
                  ) : (
                    <Cpu className="h-4 w-4 text-sky-600" />
                  )}
                  <div className="text-sm">
                    <div className="font-medium">{n.label}</div>
                    <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                      {n.mode ? `${n.kind} · ${n.mode} only` : n.kind}
                    </div>
                  </div>
                </div>

              </motion.div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Latest run timings */}
      {latest && (
        <Card className="mb-6">
          <CardHeader>
            <CardTitle>Latest run · #{latest.id}</CardTitle>
            <CardDescription>
              Status:{" "}
              <Badge tone={latest.status === "ok" ? "good" : "critical"}>{latest.status}</Badge>
              {" · "}Processed {latest.transactions_processed} transactions
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3 md:grid-cols-3">
              <Stat label="Rule-tagged" value={latest.rule_tagged} hint="deterministic" />
              <Stat label="LLM-tagged" value={latest.llm_tagged} hint="Gemma 4" />
              <Stat label="Need review" value={latest.needs_review} hint="low confidence" />
            </div>

            <div className="mt-5">
              <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                Per-node timings (ms)
              </p>
              <div className="grid grid-cols-2 gap-2 md:grid-cols-3 lg:grid-cols-6">
                {Object.entries(latest.node_timings_ms ?? {}).map(([node, ms]) => (
                  <div key={node} className="rounded-lg border border-brand-100 bg-white p-3">
                    <div className="text-xs text-muted-foreground">{node}</div>
                    <div className="font-display text-lg font-semibold tabular-nums">
                      {ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* History */}
      <Card>
        <CardHeader>
          <CardTitle>Run history</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto px-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-brand-100 text-left text-xs uppercase tracking-wider text-muted-foreground">
                <th className="px-5 py-2 font-medium">#</th>
                <th className="px-3 py-2 font-medium">Started</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Processed</th>
                <th className="px-3 py-2 font-medium">Rule</th>
                <th className="px-3 py-2 font-medium">LLM</th>
                <th className="px-5 py-2 font-medium">Review</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className="border-b border-brand-50">
                  <td className="px-5 py-3 font-mono text-xs">{r.id}</td>
                  <td className="px-3 py-3 text-xs text-muted-foreground">
                    {new Date(r.started_at).toLocaleString("en-IN", {
                      month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit",
                    })}
                  </td>
                  <td className="px-3 py-3">
                    <Badge tone={r.status === "ok" ? "good" : "critical"}>{r.status}</Badge>
                  </td>
                  <td className="px-3 py-3 tabular-nums">{r.transactions_processed}</td>
                  <td className="px-3 py-3 tabular-nums text-sky-700">{r.rule_tagged}</td>
                  <td className="px-3 py-3 tabular-nums text-brand-700">{r.llm_tagged}</td>
                  <td className="px-5 py-3 tabular-nums text-amber-700">{r.needs_review}</td>
                </tr>
              ))}
              {runs.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-5 py-8 text-center text-muted-foreground">
                    No runs yet. Kick one off above.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </Shell>
  );
}

function Stat({ label, value, hint }: { label: string; value: number; hint: string }) {
  return (
    <div className="rounded-xl border border-brand-100 bg-white p-4">
      <p className="text-xs uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className="mt-2 font-display text-3xl font-semibold tabular-nums">{value}</p>
      <p className="mt-1 text-[11px] text-muted-foreground">{hint}</p>
    </div>
  );
}
