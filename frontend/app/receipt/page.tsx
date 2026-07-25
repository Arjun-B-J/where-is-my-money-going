"use client";
import { useState } from "react";
import { motion } from "framer-motion";
import { Upload, FileImage, Loader2, CheckCircle2 } from "lucide-react";
import { Shell } from "@/components/Shell";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { rupees } from "@/lib/utils";
import { api, type ReceiptExtraction } from "@/lib/api";

export default function ReceiptPage() {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [extracted, setExtracted] = useState<ReceiptExtraction | null>(null);
  const [scanning, setScanning] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const onPick = (f: File | null) => {
    setFile(f);
    setExtracted(null);
    setErr(null);
    if (f) setPreview(URL.createObjectURL(f));
    else setPreview(null);
  };

  const scan = async () => {
    if (!file) return;
    setScanning(true);
    setErr(null);
    try {
      const result = await api.scanReceipt(file, true);
      setExtracted(result.extracted);
      // The backend distinguishes "the model is down" from "that is not a
      // receipt". Surface both rather than showing an empty result.
      if (!result.ok) setErr(result.error ?? "The local vision model is not reachable.");
      else if (result.message) setErr(result.message);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setScanning(false);
    }
  };

  return (
    <Shell>
      <div className="mb-6">
        <h1 className="font-display text-3xl font-semibold tracking-tight">Receipt scan</h1>
        <p className="text-sm text-muted-foreground">
          Drop a photo of a receipt. The local vision model reads the merchant, total and
          date, and files it as a transaction. Unlike statement parsing, this step has
          no deterministic fallback: it either reads the image or tells you it could not.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>1. Upload</CardTitle>
            <CardDescription>PNG / JPEG, up to 5 MB</CardDescription>
          </CardHeader>
          <CardContent>
            <label
              htmlFor="receipt-file"
              className="flex h-64 cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed border-brand-200 bg-brand-50/40 transition-colors hover:border-brand-400 hover:bg-brand-50"
            >
              {preview ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={preview} alt="receipt preview" className="max-h-60 rounded-lg object-contain" />
              ) : (
                <>
                  <Upload className="mb-3 h-10 w-10 text-brand-400" />
                  <p className="font-medium">Drop a receipt or click to upload</p>
                  <p className="text-xs text-muted-foreground">Image will be processed locally by Gemma 4</p>
                </>
              )}
              <input
                id="receipt-file"
                type="file"
                accept="image/png,image/jpeg,image/webp"
                className="sr-only"
                onChange={(e) => onPick(e.target.files?.[0] ?? null)}
              />
            </label>

            <div className="mt-4 flex items-center gap-2">
              <Button onClick={scan} disabled={!file || scanning} variant="glow">
                {scanning ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Scanning…
                  </>
                ) : (
                  <>
                    <FileImage className="h-4 w-4" />
                    Scan with Gemma
                  </>
                )}
              </Button>
              {file && (
                <Button variant="ghost" size="sm" onClick={() => onPick(null)}>
                  Clear
                </Button>
              )}
            </div>

            {err && (
              <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                {err}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>2. Extracted</CardTitle>
            <CardDescription>What Gemma found in the image</CardDescription>
          </CardHeader>
          <CardContent>
            {!extracted && !scanning && (
              <p className="text-sm text-muted-foreground">
                Upload a receipt to see structured output.
              </p>
            )}
            {scanning && (
              <div className="space-y-2 animate-pulse">
                <div className="h-6 w-2/3 rounded bg-brand-100" />
                <div className="h-6 w-1/3 rounded bg-brand-100" />
                <div className="h-6 w-1/2 rounded bg-brand-100" />
              </div>
            )}
            {extracted && (
              <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-3">
                <Field label="Merchant" value={extracted.merchant ?? "(unknown)"} />
                <Field label="Amount" value={extracted.amount ? rupees(extracted.amount) : "—"} />
                <Field label="Date" value={extracted.date ?? "—"} />
                <Field
                  label="Category"
                  value={
                    <Badge tone="default">{extracted.category}</Badge>
                  }
                />
                <Field
                  label="Confidence"
                  value={`${Math.round((extracted.confidence ?? 0) * 100)}%`}
                />
                {extracted.items?.length > 0 && (
                  <div className="rounded-xl border border-brand-100 bg-white p-3">
                    <p className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                      Line items
                    </p>
                    <ul className="space-y-1 text-sm">
                      {extracted.items.map((it, i) => (
                        <li key={i}>• {it}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <div className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
                  <CheckCircle2 className="h-4 w-4" />
                  Saved as a transaction.
                </div>
              </motion.div>
            )}
          </CardContent>
        </Card>
      </div>
    </Shell>
  );
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-brand-50 pb-2">
      <span className="text-xs uppercase tracking-wider text-muted-foreground">{label}</span>
      <span className="text-sm font-medium">{value}</span>
    </div>
  );
}
