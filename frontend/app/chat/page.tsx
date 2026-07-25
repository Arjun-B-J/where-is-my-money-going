"use client";
import { useEffect, useRef, useState } from "react";
import { Send, Loader2, Sparkles } from "lucide-react";
import { Shell } from "@/components/Shell";
import { Card, CardContent } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { api } from "@/lib/api";

interface Msg { role: "user" | "assistant"; content: string }

const SUGGESTIONS = [
  "How much did I spend on food this year?",
  "Who owes me the most money?",
  "Where am I overspending?",
  "What's my biggest recurring expense?",
];

export default function ChatPage() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const send = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || streaming) return;
    const next: Msg[] = [...messages, { role: "user", content: trimmed }];
    setMessages(next);
    setInput("");
    setStreaming(true);

    try {
      const r = await fetch(api.chatStreamUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: next }),
      });
      if (!r.body) throw new Error("no stream");

      // Append placeholder assistant message
      setMessages((m) => [...m, { role: "assistant", content: "" }]);

      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const events = buf.split("\n\n");
        buf = events.pop() || "";
        for (const ev of events) {
          if (!ev.startsWith("data: ")) continue;
          try {
            const obj = JSON.parse(ev.slice(6));
            if (obj.delta) {
              setMessages((m) => {
                const copy = [...m];
                copy[copy.length - 1] = {
                  role: "assistant",
                  content: copy[copy.length - 1].content + obj.delta,
                };
                return copy;
              });
            }
            if (obj.done) break;
            if (obj.error) {
              setMessages((m) => [
                ...m.slice(0, -1),
                { role: "assistant", content: `(error) ${obj.error}` },
              ]);
            }
          } catch {
            /* swallow */
          }
        }
      }
    } catch (e) {
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: `Couldn't reach the agent. Make sure backend + Ollama are running.\n${e}`,
        },
      ]);
    } finally {
      setStreaming(false);
    }
  };

  return (
    <Shell>
      <div className="mb-6">
        <h1 className="font-display text-3xl font-semibold tracking-tight">Chat with your finances</h1>
        <p className="text-sm text-muted-foreground">
          Ask Gemma 4 anything about your spending. Your data never leaves this machine.
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
            {messages.map((m, i) => (
              <div
                key={i}
                className={
                  m.role === "user"
                    ? "ml-auto max-w-[80%] rounded-2xl rounded-tr-sm bg-brand-500 px-4 py-2.5 text-sm text-white"
                    : "max-w-[85%] rounded-2xl rounded-tl-sm border border-brand-100 bg-white px-4 py-2.5 text-sm"
                }
              >
                {m.content || (streaming && i === messages.length - 1 ? "▍" : "")}
              </div>
            ))}
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
