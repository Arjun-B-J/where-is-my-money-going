import { Navbar } from "./Navbar";

export function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative min-h-screen">
      {/* Background gradient */}
      <div className="pointer-events-none fixed inset-0 -z-10 bg-warm-gradient" />
      <div className="pointer-events-none fixed inset-0 -z-10 opacity-30 grain" />
      <Navbar />
      <main className="mx-auto max-w-7xl px-6 py-8">{children}</main>
    </div>
  );
}
