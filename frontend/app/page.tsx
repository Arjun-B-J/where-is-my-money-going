"use client";

import { motion } from "framer-motion";
import {
  ArrowRight,
  FileSearch,
  Info,
  Lock,
  ScanLine,
  ShieldCheck,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { MoneyFlowCanvas } from "@/components/MoneyFlowCanvas";
import { TiltCard } from "@/components/TiltCard";
import { Wordmark } from "@/components/Wordmark";
import { Button } from "@/components/ui/Button";
import { api, type PipelineRun, type SystemHealth } from "@/lib/api";

const CAPABILITIES = [
  {
    icon: FileSearch,
    title: "Statements parsed, not guessed",
    body:
      "Dates, amounts and balances come out of your PDFs by regex and arithmetic. " +
      "The model never sees a number it could get wrong — it only labels rows that " +
      "have already been extracted.",
  },
  {
    icon: ShieldCheck,
    title: "It admits what it doesn't know",
    body:
      "Every category carries a confidence score, and anything below the threshold " +
      "goes to a review queue. If the model is unreachable, rows stay untagged " +
      "rather than being recorded as a confident guess.",
  },
  {
    icon: Users,
    title: "Finds the people in your ledger",
    body:
      "Two-way UPI flow with a person-shaped name is an informal loan, not a " +
      "merchant. Those get grouped into a running balance per person, so you can " +
      "see who is actually behind.",
  },
  {
    icon: ScanLine,
    title: "Catches what a chart hides",
    body:
      "Duplicate charges, instalment plans you forgot converting, subscriptions " +
      "that quietly renew, refunds that never arrived. All deterministic checks, " +
      "all explainable.",
  },
];

export default function Landing() {
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [run, setRun] = useState<PipelineRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  const loadDemo = async () => {
    setError(null);
    setBusy(true);
    try {
      const result = await api.runDemo(12);
      setRun(result);
      window.location.href = "/dashboard";
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  const modelReady = health?.llm?.ok && health.llm.model_pulled;

  return (
    <div className="relative min-h-screen overflow-hidden bg-warm-gradient">
      <div className="pointer-events-none absolute inset-0 grain opacity-30" />

      <div className="relative mx-auto max-w-7xl px-6 py-5">
        <Wordmark />
      </div>

      {/* Hero */}
      <section className="relative mx-auto grid max-w-7xl gap-10 px-6 pb-16 pt-6 lg:grid-cols-[1.05fr_1fr] lg:items-center lg:gap-14 lg:pt-12">
        <div>
          <motion.p
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4 }}
            className="mb-5 inline-flex items-center gap-2 rounded-full border border-brand-200 bg-white/70 px-3.5 py-1.5 text-xs font-medium text-brand-700 shadow-sm backdrop-blur"
          >
            <Lock className="h-3.5 w-3.5" aria-hidden="true" />
            Runs entirely on your machine
          </motion.p>

          <motion.h1
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.55, delay: 0.05 }}
            className="text-balance font-display text-4xl font-semibold leading-[1.08] tracking-tight text-ink md:text-6xl"
          >
            Where is my{" "}
            <span className="bg-gradient-to-r from-brand-600 to-amber-500 bg-clip-text text-transparent">
              money
            </span>{" "}
            going?
          </motion.h1>

          <motion.p
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.55, delay: 0.12 }}
            className="mt-5 max-w-xl text-lg leading-relaxed text-ink-muted"
          >
            I could never answer that from a bank statement. Twelve months of UPI
            transfers to people whose names mean nothing six weeks later, and no
            spreadsheet was going to fix it. So this reads the statements, labels
            every row, and tells you what the pattern actually is.
          </motion.p>

          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.55, delay: 0.2 }}
            className="mt-8 flex flex-col gap-4 sm:flex-row sm:items-center"
          >
            <Button size="xl" variant="glow" onClick={loadDemo} disabled={busy} data-testid="start-button">
              {busy ? "Building the demo…" : "See it on demo data"}
              {!busy && <ArrowRight className="ml-1.5 h-4 w-4" aria-hidden="true" />}
            </Button>
            <Link
              href="/ingest"
              className="inline-flex items-center gap-1.5 text-sm font-medium text-ink underline-offset-4 hover:underline"
            >
              Or use your own statements
              <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
            </Link>
          </motion.div>

          {/* Demo data is synthetic and says so. */}
          <p className="mt-4 flex items-start gap-1.5 text-xs text-ink-muted">
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            The demo generates a year of fictional transactions. No real
            person&apos;s data is included, and nothing is uploaded anywhere.
          </p>

          {/* Model status. Explicit, because the app's behaviour depends on it. */}
          <div className="mt-6 inline-flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-xl border border-brand-100 bg-white/70 px-4 py-2.5 text-xs backdrop-blur">
            <span className="flex items-center gap-1.5 font-medium">
              <span
                className={`inline-block h-2 w-2 rounded-full ${
                  modelReady ? "animate-pulse-soft bg-emerald-500" : "bg-amber-500"
                }`}
                aria-hidden="true"
              />
              {health === null
                ? "Checking the backend…"
                : !health.llm.ok
                  ? "Local model offline"
                  : !health.llm.model_pulled
                    ? "Model not downloaded"
                    : "Local model ready"}
            </span>
            {health?.llm?.model && (
              <code className="rounded bg-brand-50 px-1.5 py-0.5 font-mono text-[11px] text-brand-700">
                {health.llm.model}
              </code>
            )}
            {health?.llm?.ok && !health.llm.model_pulled && (
              <span className="text-ink-muted">
                run <code className="font-mono">ollama pull {health.llm.model}</code>
              </span>
            )}
            {health && !health.llm.ok && (
              <span className="text-ink-muted">
                Statements still parse; categorising needs the model.
              </span>
            )}
          </div>

          {error && (
            <p role="alert" className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700">
              {error}
            </p>
          )}
          {run && (
            <p className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm text-emerald-800">
              Run #{run.id} finished — {run.transactions_processed} transactions. Opening the dashboard…
            </p>
          )}
        </div>

        {/* Hero animation */}
        <motion.div
          initial={{ opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.7, delay: 0.15 }}
          className="relative"
        >
          <div className="rounded-3xl border border-brand-100 bg-white/60 p-4 shadow-depth-lg backdrop-blur">
            <MoneyFlowCanvas className="h-[300px] w-full sm:h-[380px]" />
          </div>
          <p className="mt-3 text-center text-xs text-ink-muted">
            One salary in, six directions out. The app&apos;s job is to name them.
          </p>
        </motion.div>
      </section>

      {/* Capabilities */}
      <section className="relative mx-auto max-w-7xl px-6 pb-20">
        <h2 className="mb-8 font-display text-2xl font-semibold tracking-tight text-ink">
          What it actually does
        </h2>
        <div className="grid gap-5 md:grid-cols-2">
          {CAPABILITIES.map((item, index) => (
            <TiltCard key={item.title} delay={0.05 * index}>
              <div className="mb-4 inline-flex h-10 w-10 items-center justify-center rounded-xl bg-brand-100 text-brand-600">
                <item.icon className="h-5 w-5" aria-hidden="true" />
              </div>
              <h3 className="mb-2 font-semibold text-ink">{item.title}</h3>
              <p className="text-sm leading-relaxed text-ink-muted">{item.body}</p>
            </TiltCard>
          ))}
        </div>

        <div className="mt-14 rounded-2xl border border-brand-100 bg-white/60 p-6 backdrop-blur sm:p-8">
          <h2 className="font-display text-xl font-semibold tracking-tight text-ink">
            How it is built
          </h2>
          <p className="mt-3 max-w-3xl text-sm leading-relaxed text-ink-muted">
            A LangGraph pipeline does the work: parse or generate, store with a
            deterministic id so re-ingesting a statement is a no-op, categorise with
            a local model under schema-constrained decoding, then two agents refine —
            one finds people, one re-checks the tags the first pass was unsure about.
            FastAPI and SQLite behind it, Next.js in front, and one PDF report at the
            end.
          </p>
          <div className="mt-5 flex flex-wrap gap-x-5 gap-y-2 text-sm">
            <Link href="/pipeline" className="font-medium text-brand-700 underline-offset-4 hover:underline">
              See the pipeline →
            </Link>
            <Link href="/dashboard" className="font-medium text-brand-700 underline-offset-4 hover:underline">
              Open the dashboard →
            </Link>
          </div>
        </div>
      </section>
    </div>
  );
}
