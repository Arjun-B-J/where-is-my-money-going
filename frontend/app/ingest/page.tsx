"use client";
import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Upload, FileText, CheckCircle2, AlertCircle, Loader2 } from "lucide-react";
import { Shell } from "@/components/Shell";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/utils";

interface UploadResult {
  file: string;
  parsed: number;
  inserted: number;
  skipped_duplicates: number;
  rule_tagged?: number;
  llm_tagged?: number;
  needs_review?: number;
  error?: string;
}

export default function IngestPage() {
  const [files, setFiles] = useState<File[]>([]);
  const [results, setResults] = useState<UploadResult[]>([]);
  const [running, setRunning] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const dropped = Array.from(e.dataTransfer.files).filter((f) =>
      [".pdf", ".csv"].some((ext) =>
        f.name.toLowerCase().endsWith(ext),
      ),
    );
    setFiles((cur) => [...cur, ...dropped]);
  };

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files || []);
    setFiles((cur) => [...cur, ...picked]);
  };

  const removeFile = (idx: number) => {
    setFiles((cur) => cur.filter((_, i) => i !== idx));
  };

  const ingestAll = async () => {
    if (!files.length) return;
    setRunning(true);
    setResults([]);
    for (const f of files) {
      try {
        const fd = new FormData();
        fd.append("file", f);
        const r = await fetch("/api/backend/ingest/file?run_pipeline=true", {
          method: "POST",
          body: fd,
        });
        if (r.ok) {
          const data = (await r.json()) as UploadResult;
          setResults((cur) => [...cur, data]);
        } else {
          setResults((cur) => [
            ...cur,
            {
              file: f.name,
              parsed: 0,
              inserted: 0,
              skipped_duplicates: 0,
              error: `HTTP ${r.status}`,
            },
          ]);
        }
      } catch (e) {
        setResults((cur) => [
          ...cur,
          {
            file: f.name,
            parsed: 0,
            inserted: 0,
            skipped_duplicates: 0,
            error: e instanceof Error ? e.message : String(e),
          },
        ]);
      }
    }
    setRunning(false);
  };

  const totalParsed = results.reduce((s, r) => s + r.parsed, 0);
  const totalInserted = results.reduce((s, r) => s + r.inserted, 0);
  const totalDup = results.reduce((s, r) => s + r.skipped_duplicates, 0);
  const totalReview = results.reduce((s, r) => s + (r.needs_review ?? 0), 0);

  return (
    <Shell>
      <div className="mb-8 flex items-end justify-between">
        <div>
          <h1 className="font-display text-3xl font-semibold tracking-tight">
            Ingest your statements
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Drop bank or credit-card statements. Files are
            parsed locally and never leave your machine.
          </p>
        </div>
        <Badge tone="good">Local-first · gitignored</Badge>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1.2fr_1fr]">
        {/* Drop zone */}
        <Card>
          <CardHeader>
            <CardTitle>1. Add files</CardTitle>
            <CardDescription>PDF or CSV, up to 20 MB each.</CardDescription>
          </CardHeader>
          <CardContent>
            <label
              htmlFor="ingest-files"
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={onDrop}
              className={cn(
                "flex h-56 cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed transition-colors",
                dragOver
                  ? "border-brand-500 bg-brand-50"
                  : "border-brand-200 bg-brand-50/40 hover:border-brand-400 hover:bg-brand-50",
              )}
            >
              <Upload className="mb-3 h-12 w-12 text-brand-400" />
              <p className="text-base font-medium text-foreground">
                Drop bank statements here
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                or click to browse · PDF or CSV
              </p>
              <input
                id="ingest-files"
                type="file"
                accept=".pdf,.csv"
                multiple
                className="sr-only"
                onChange={onPick}
              />
            </label>

            {files.length > 0 && (
              <div className="mt-4 space-y-2">
                <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  {files.length} file{files.length === 1 ? "" : "s"} queued
                </p>
                <div className="max-h-48 space-y-1.5 overflow-y-auto">
                  {files.map((f, i) => (
                    <div
                      key={i}
                      className="flex items-center justify-between rounded-lg border border-brand-100 bg-white px-3 py-2 text-sm"
                    >
                      <div className="flex items-center gap-2 truncate">
                        <FileText className="h-4 w-4 shrink-0 text-brand-500" />
                        <span className="truncate">{f.name}</span>
                        <span className="shrink-0 text-xs text-muted-foreground">
                          ({Math.round(f.size / 1024)} KB)
                        </span>
                      </div>
                      <button
                        onClick={() => removeFile(i)}
                        className="ml-2 text-xs text-muted-foreground hover:text-red-600"
                      >
                        remove
                      </button>
                    </div>
                  ))}
                </div>

                <Button
                  variant="glow"
                  size="lg"
                  onClick={ingestAll}
                  disabled={running}
                  className="mt-3 w-full"
                >
                  {running ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Ingesting · LangGraph pipeline running…
                    </>
                  ) : (
                    <>Ingest {files.length} file{files.length === 1 ? "" : "s"}</>
                  )}
                </Button>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Privacy + steps */}
        <Card>
          <CardHeader>
            <CardTitle>2. What happens next</CardTitle>
            <CardDescription>
              Each file flows through the same LangGraph pipeline a demo run uses.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <Step n={1} title="Parse">
              Deterministic Python (pdfplumber + regex) extracts every transaction
              with the running balance for direction inference.
            </Step>
            <Step n={2} title="Dedup">
              Each row gets a deterministic <code className="text-xs">external_id</code>, so
              files with overlapping date ranges are safely re-ingested.
            </Step>
            <Step n={3} title="Tag">
              Gemma 4 (locally via Ollama) classifies every transaction.
              Low-confidence tags go to a review queue.
            </Step>
            <Step n={4} title="Friends">
              Bidirectional UPI flows + person-shaped names → auto-detected friends.
            </Step>
            <Step n={5} title="Validate">
              Second-pass LLM agent confirms or overrides tags. Disagreements lock
              to review.
            </Step>
            <Step n={6} title="Persist">
              SQLite at <code className="text-xs">backend/storage/wimmg.db</code>, which is
              gitignored.
            </Step>
          </CardContent>
        </Card>
      </div>

      {/* Results */}
      <AnimatePresence>
        {results.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            className="mt-6"
          >
            <Card>
              <CardHeader>
                <CardTitle>Ingest results</CardTitle>
                <CardDescription>
                  Parsed {totalParsed} · inserted {totalInserted} ·
                  {" "}skipped {totalDup} duplicate{totalDup === 1 ? "" : "s"}
                  {totalReview > 0 && ` · ${totalReview} need review`}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-2">
                {results.map((r, i) => (
                  <motion.div
                    key={i}
                    initial={{ opacity: 0, x: -8 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: i * 0.05 }}
                    className={cn(
                      "rounded-lg border p-3",
                      r.error
                        ? "border-red-200 bg-red-50"
                        : "border-emerald-200 bg-emerald-50/50",
                    )}
                  >
                    <div className="flex items-center gap-2">
                      {r.error ? (
                        <AlertCircle className="h-4 w-4 text-red-600" />
                      ) : (
                        <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                      )}
                      <span className="font-medium">{r.file}</span>
                      {!r.error && (
                        <Badge tone="muted" className="ml-auto">
                          parsed {r.parsed} · inserted {r.inserted} · dup {r.skipped_duplicates}
                        </Badge>
                      )}
                    </div>
                    {!r.error && (r.rule_tagged !== undefined || r.llm_tagged !== undefined) && (
                      <div className="mt-1.5 flex flex-wrap gap-2 text-xs text-muted-foreground">
                        {r.rule_tagged !== undefined && (
                          <span>rule: {r.rule_tagged}</span>
                        )}
                        {r.llm_tagged !== undefined && (
                          <span>LLM: {r.llm_tagged}</span>
                        )}
                        {r.needs_review !== undefined && r.needs_review > 0 && (
                          <span className="text-amber-700">
                            review: {r.needs_review}
                          </span>
                        )}
                      </div>
                    )}
                    {r.error && (
                      <p className="mt-1 text-xs text-red-700">{r.error}</p>
                    )}
                  </motion.div>
                ))}

                {!running && totalInserted > 0 && (
                  <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg bg-brand-50 p-3 text-sm">
                    <span>Done. </span>
                    <a
                      href="/dashboard"
                      className="font-medium text-brand-700 underline-offset-4 hover:underline"
                    >
                      Open the dashboard →
                    </a>
                    <span className="mx-1">·</span>
                    <a
                      href="/api/backend/report/pdf"
                      target="_blank"
                      rel="noopener"
                      className="font-medium text-brand-700 underline-offset-4 hover:underline"
                    >
                      Download PDF report
                    </a>
                  </div>
                )}
              </CardContent>
            </Card>
          </motion.div>
        )}
      </AnimatePresence>
    </Shell>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-3">
      <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-100 text-xs font-bold text-brand-700">
        {n}
      </div>
      <div className="flex-1">
        <p className="font-medium text-foreground">{title}</p>
        <p className="text-xs text-muted-foreground">{children}</p>
      </div>
    </div>
  );
}
