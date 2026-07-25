import Link from "next/link";

/**
 * The product wordmark: the question itself, since that is the name.
 *
 * Collapses to "WIMMG" below `sm` — the full question does not fit a phone
 * navbar without wrapping, and a wrapped wordmark looks broken.
 */
export function Wordmark({ href = "/" }: { href?: string }) {
  return (
    <Link href={href} className="group flex items-center gap-2.5" aria-label="Where Is My Money Going?">
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-ink text-sm font-bold text-brand-300 shadow-depth transition-transform group-hover:-translate-y-px">
        ₹
      </span>
      <span className="hidden text-[15px] font-semibold leading-none tracking-tight text-ink sm:inline">
        where is my <span className="text-brand-600">money</span> going?
      </span>
      <span className="text-sm font-bold tracking-tight text-ink sm:hidden">WIMMG</span>
    </Link>
  );
}
