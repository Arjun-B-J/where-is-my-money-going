"use client";
import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Loader2, Send, Sparkles } from "lucide-react";
import { Shell } from "@/components/Shell";
import { Card, CardContent } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { api, type ChatMode, type ChatStreamEvent, type ChatToolStep } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Msg {
  role: "user" | "assistant";
  content: string;
  /** The queries the agent ran for this answer, in order. */
  steps?: ChatToolStep[];
  /** False when a figure in the answer matched nothing the queries returned. */
  grounded?: boolean;
  ungrounded?: string[];
  mode?: ChatMode;
  /** An error shown in place of an answer. Never sent back as conversation. */
  failed?: boolean;
}

const SUGGESTIONS = [
  "How much did I spend on food last month?",
  "Who owes me the most money?",
  "What was my largest expense this year?",
  "What are my recurring payments?",
];

const MONTH = /^\d{4}-\d{2}$/;

/** The last day of a YYYY-MM month, as YYYY-MM-DD. */
function monthEnd(month: string): string {
  const [year, index] = month.split("-").map(Number);
  const day = new Date(Date.UTC(year, index, 0)).getUTCDate();
  return `${month}-${String(day).padStart(2, "0")}`;
}

/** "2026-03" for a whole calendar month, otherwise the dates as the agent gave them. */
function periodLabel(start?: string, end?: string): string | null {
  const from = start && MONTH.test(start) ? `${start}-01` : start;
  const to = end && MONTH.test(end) ? monthEnd(end) : end;
  if (from && to) {
    const month = from.slice(0, 7);
    if (from === `${month}-01` && to === monthEnd(month)) return month;
    return `${from} to ${to}`;
  }
  if (from) return `from ${from}`;
  if (to) return `until ${to}`;
  return null;
}

/** A chip label such as "spending_summary · category · food · 2026-03". */
function stepLabel(step: ChatToolStep): string {
  const text = (key: string) => {
    const value = step.args?.[key];
    return typeof value === "string" && value.trim() ? value.trim() : undefined;
  };
  const parts = [step.name];
  for (const key of ["group_by", "category", "direction"]) {
    const value = text(key);
    if (value) parts.push(value);
  }
  const search = text("text");
  if (search) parts.push(`"${search}"`);
  const period = periodLabel(text("start_date"), text("end_date"));
  if (period) parts.push(period);
  return parts.join(" · ");
}

function AssistantMessage({ message, pending }: { message: Msg; pending: boolean }) {
  const steps = message.steps ?? [];
  return (
    <div className="max-w-[85%] space-y-1.5">
      {steps.length > 0 && (
        <div className="flex flex-wrap gap-1.5" aria-label="Queries run for this answer">
          {steps.map((step, i) => (
            <span
              key={i}
              title={
                step.ok
                  ? "A query run on your data"
                  : "This query was rejected, and the model was told why"
              }
              className={cn(
                "inline-flex items-center rounded-md border px-2 py-0.5 font-mono text-[11px]",
                step.ok
                  ? "border-slate-200 bg-slate-50 text-slate-500"
                  : "border-amber-200 bg-amber-50 text-amber-700",
              )}
            >
              {stepLabel(step)}
            </span>
          ))}
        </div>
      )}
      <div
        className={cn(
          "whitespace-pre-wrap rounded-2xl rounded-tl-sm border px-4 py-2.5 text-sm",
          message.failed ? "border-red-200 bg-red-50 text-red-800" : "border-brand-100 bg-white",
        )}
      >
        {message.content || (pending ? "▍" : "")}
      </div>
      {/* The figures were checked against what the queries returned. When one
          does not match, say so next to the answer rather than showing it as
          though it were computed. */}
      {message.grounded === false && (
        <p className="flex items-start gap-1.5 text-xs font-medium text-amber-700">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>
            Some figures could not be matched to your data
            {message.ungrounded?.length ? `: ${message.ungrounded.join(", ")}` : ""}. Treat them as
            unverified.
          </span>
        </p>
      )}
      {message.mode === "summary" && (
        <p className="text-[11px] text-muted-foreground">
          Answered from a fixed summary of your data, because this model cannot run queries.
          Figures the summary does not hold are not available.
        </p>
      )}
    </div>
  );
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  /** Apply a change to the reply being streamed, which is always the last message. */
  const updateReply = (change: (reply: Msg) => Msg) =>
    setMessages((all) => [...all.slice(0, -1), change(all[all.length - 1])]);

  const send = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || streaming) return;
    const next: Msg[] = [...messages, { role: "user", content: trimmed }];
    // The reply gets its place straight away, so the query chips have somewhere to appear.
    setMessages([...next, { role: "assistant", content: "", steps: [] }]);
    setInput("");
    setStreaming(true);

    // Errors are shown to the user, never replayed to the model as if it had said them.
    const history = next
      .filter((m) => !m.failed && m.content.trim())
      .map(({ role, content }) => ({ role, content }));

    let finished = false;
    try {
      const r = await fetch(api.chatStreamUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: history }),
      });
      if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);

      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const events = buf.split("\n\n");
        buf = events.pop() || "";
        for (const raw of events) {
          if (!raw.startsWith("data: ")) continue;
          let event: ChatStreamEvent;
          try {
            event = JSON.parse(raw.slice(6));
          } catch {
            continue;
          }
          if ("tool" in event) {
            const step = event.tool;
            updateReply((m) => ({ ...m, steps: [...(m.steps ?? []), step] }));
          } else if ("delta" in event) {
            const delta = event.delta;
            updateReply((m) => ({ ...m, content: m.content + delta }));
          } else if ("done" in event) {
            finished = true;
            const { grounded, ungrounded, mode } = event;
            updateReply((m) => ({ ...m, grounded, ungrounded, mode }));
          } else if ("error" in event) {
            finished = true;
            const message = event.error;
            updateReply((m) => ({ ...m, content: message, failed: true }));
          }
        }
      }
      if (!finished) {
        // A stream that stops without "done" or "error" delivered no checked
        // answer, whatever text may have arrived.
        updateReply((m) => ({
          ...m,
          content: "The answer was cut off before it finished. Try asking again.",
          failed: true,
        }));
      }
    } catch (e) {
      updateReply((m) => ({
        ...m,
        content: `Couldn't reach the backend. Make sure it and Ollama are running.\n${e}`,
        failed: true,
      }));
    } finally {
      setStreaming(false);
    }
  };

  return (
    <Shell>
      <div className="mb-6">
        <h1 className="font-display text-3xl font-semibold tracking-tight">Chat with your finances</h1>
        <p className="text-sm text-muted-foreground">
          Answers are worked out by querying your own transactions, on this machine. Your data
          never leaves it.
        </p>
      </div>

      <Card className="grid grid-rows-[1fr_auto] h-[70vh]">
        <CardContent className="overflow-y-auto p-5">
          {messages.length === 0 && (
            <div className="flex h-full flex-col items-center justify-center gap-6 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-100 text-brand-600">
                <Sparkles className="h-6 w-6" />
              </div>
              <div>
                <p className="font-medium">Try asking…</p>
                <p className="text-xs text-muted-foreground">click any to start</p>
              </div>
              <div className="flex max-w-xl flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="rounded-full border border-brand-200 bg-white px-4 py-1.5 text-sm hover:border-brand-400 hover:bg-brand-50"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="space-y-4">
            {messages.map((m, i) =>
              m.role === "user" ? (
                <div
                  key={i}
                  className="ml-auto max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-tr-sm bg-brand-500 px-4 py-2.5 text-sm text-white"
                >
                  {m.content}
                </div>
              ) : (
                <AssistantMessage
                  key={i}
                  message={m}
                  pending={streaming && i === messages.length - 1}
                />
              ),
            )}
            <div ref={endRef} />
          </div>
        </CardContent>

        <div className="border-t border-brand-100 p-3">
          <form
            className="flex items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              send(input);
            }}
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask anything about your money…"
              disabled={streaming}
              className="h-11 flex-1 rounded-xl border border-brand-200 bg-white px-4 text-sm focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-200"
            />
            <Button type="submit" disabled={!input.trim() || streaming}>
              {streaming ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            </Button>
          </form>
        </div>
      </Card>
    </Shell>
  );
}
