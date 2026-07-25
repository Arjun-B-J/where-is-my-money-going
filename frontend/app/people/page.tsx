"use client";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { ArrowDownLeft, ArrowUpRight, CheckCircle2 } from "lucide-react";
import { Shell } from "@/components/Shell";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { rupees } from "@/lib/utils";
import { api, type PersonBalance } from "@/lib/api";

export default function PeoplePage() {
  const [people, setPeople] = useState<PersonBalance[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.people().then((p) => {
      setPeople(p);
      setLoading(false);
    });
  }, []);

  const youOwe = people.filter((p) => p.they_owe_you < 0);
  const theyOwe = people.filter((p) => p.they_owe_you > 0);
  const settled = people.filter((p) => p.they_owe_you === 0);

  return (
    <Shell>
      <div className="mb-8">
        <h1 className="font-display text-3xl font-semibold tracking-tight">People</h1>
        <p className="text-sm text-muted-foreground">
          Track loans, repayments and shared bills across friends and vendors.
        </p>
      </div>

      <div className="grid gap-6 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-emerald-700">
              <ArrowDownLeft className="h-4 w-4" />
              They owe you
            </CardTitle>
            <CardDescription>
              Net positive. You sent more than you received from these people.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {theyOwe.length === 0 && <Empty />}
            {theyOwe.map((p, i) => (
              <PersonRow key={p.person.id} p={p} i={i} positive />
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-amber-700">
              <ArrowUpRight className="h-4 w-4" />
              You owe them
            </CardTitle>
            <CardDescription>You received more than you&apos;ve paid back.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {youOwe.length === 0 && <Empty />}
            {youOwe.map((p, i) => (
              <PersonRow key={p.person.id} p={p} i={i} positive={false} />
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-slate-600">
              <CheckCircle2 className="h-4 w-4" />
              Settled
            </CardTitle>
            <CardDescription>Net zero across the period.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {settled.length === 0 && <Empty />}
            {settled.map((p, i) => (
              <PersonRow key={p.person.id} p={p} i={i} positive={null} />
            ))}
          </CardContent>
        </Card>
      </div>

      {!loading && people.length === 0 && (
        <div className="mt-8 rounded-xl border border-brand-100 bg-white/70 p-8 text-center text-muted-foreground">
          No people yet. Run the pipeline to populate.
        </div>
      )}
    </Shell>
  );
}

function Empty() {
  return <p className="text-sm text-muted-foreground">nothing here yet</p>;
}

function PersonRow({ p, i, positive }: { p: PersonBalance; i: number; positive: boolean | null }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: i * 0.05 }}
      className="rounded-lg border border-brand-100 bg-white p-3"
    >
      <div className="flex items-baseline justify-between">
        <p className="font-medium">{p.person.name}</p>
        <span
          className={
            positive === true
              ? "text-emerald-700"
              : positive === false
              ? "text-amber-700"
              : "text-slate-500"
          }
        >
          {p.they_owe_you > 0 ? "+" : ""}
          {rupees(p.they_owe_you, { compact: true })}
        </span>
      </div>
      <div className="mt-1 flex items-center justify-between gap-2">
        <Badge tone="muted">{p.person.relationship_type}</Badge>
        <span className="text-xs text-muted-foreground">{p.transaction_count} txns</span>
      </div>
      {p.person.notes && (
        <p className="mt-2 text-xs text-muted-foreground">{p.person.notes}</p>
      )}
    </motion.div>
  );
}
