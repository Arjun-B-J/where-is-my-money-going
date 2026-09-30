"""Command-line interface.

Replaces seven one-off scripts (`ingest_real.py`, `llm_tag_only.py`,
`agent_pass.py`, `generate_report.py`, `generate_spend_analysis.py`,
`show_patterns.py`, `finalize_demo_state.py`) that had drifted apart — each had
its own copy of the tagging loop, so fixing a bug in the pipeline did not fix it
for whoever ran the script.

    wimmg status                    # is the model reachable, what is in the DB
    wimmg demo                      # load the synthetic dataset and categorise it
    wimmg ingest ./statements       # parse real statements through the pipeline
    wimmg tag                       # categorise anything still untagged
    wimmg agents                    # detect people, re-check low-confidence tags
    wimmg patterns                  # print what the detectors found
    wimmg report -o out.pdf         # write the Spend Analysis PDF
    wimmg reset                     # delete transactions and run history
    wimmg eval                      # score the categoriser against labelled data
    wimmg eval-chat                 # check chat answers against computed figures
    wimmg mcp                       # serve the read-only query tools over MCP (stdio)

`finalize_demo_state.py` has no replacement on purpose. It marked untagged rows
as model-tagged with zero confidence and wrote invented node timings onto a run
record so screenshots would look complete. Fabricating the audit trail is not a
feature.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from app.clock import utc_now
from app.config import APP_NAME, APP_VERSION, get_settings
from app.console import use_utf8_streams
from app.db import SessionLocal, init_db
from app.llm.client import get_llm
from app.models import Person, PipelineRun, Transaction, TxnSource
from app.reports.labels import known_person_names, redact_payee
from app.reports.theme import rupees

logger = logging.getLogger("wimmg")


UNICODE_OK = True


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s" if not verbose else "%(levelname)-7s %(name)s :: %(message)s",
    )
    # These are chatty at DEBUG and never what you are debugging here.
    for noisy in ("httpx", "httpcore", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _heading(text: str) -> None:
    rule = "─" if UNICODE_OK else "-"
    print(f"\n{text}\n{rule * len(text)}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def cmd_status(_args: argparse.Namespace) -> int:
    settings = get_settings()
    health = await get_llm().health()

    _heading(f"{APP_NAME} v{APP_VERSION}")
    print(f"  Database   {settings.database_url}")
    print(f"  Model      {settings.llm_model} at {settings.llm_host}")
    if health["ok"]:
        pulled = "pulled" if health["model_pulled"] else "NOT PULLED"
        print(f"  Status     reachable, model {pulled}")
        if not health["model_pulled"]:
            print(f"             run: ollama pull {settings.llm_model}")
    else:
        print(f"  Status     unreachable: {health.get('error')}")
        print("             start Ollama, then re-run this command")

    with SessionLocal() as db:
        total = db.query(Transaction).count()
        untagged = db.query(Transaction).filter(Transaction.category.is_(None)).count()
        review = db.query(Transaction).filter(Transaction.needs_review.is_(True)).count()
        people = db.query(Person).count()
        runs = db.query(PipelineRun).count()

    _heading("Data")
    print(f"  {total:>6} transactions")
    print(f"  {untagged:>6} not categorised yet")
    print(f"  {review:>6} flagged for review")
    print(f"  {people:>6} people detected")
    print(f"  {runs:>6} pipeline runs")
    if untagged:
        print("\n  Run `wimmg tag` to categorise the remaining rows.")
    return 0


async def cmd_demo(args: argparse.Namespace) -> int:
    from app.pipeline.graph import run_pipeline

    print(f"Generating {args.months} months of synthetic data (seed {args.seed})…")
    with SessionLocal() as db:
        run = await run_pipeline(db, mode="demo", seed=args.seed, months=args.months)
        _report_run(run)
    return 0


async def cmd_ingest(args: argparse.Namespace) -> int:
    from app.pipeline.graph import run_pipeline

    target = Path(args.path).expanduser()
    if not target.exists():
        print(f"error: {target} does not exist", file=sys.stderr)
        return 1

    source = TxnSource(args.source) if args.source else None
    with SessionLocal() as db:
        if target.is_file():
            # A single file still goes through the loader, but there is no point
            # spinning up the graph's demo branch for it.
            from app.ingest import load_file
            from app.llm.client import get_llm as _llm
            from app.pipeline.nodes import PipelineState, node_llm_tag, node_rule_tag
            from app.seed import seed_all

            seed_all(db)
            result = load_file(db, target, source)
            print(f"{result.file}: {result.inserted} new, {result.duplicates} duplicate "
                  f"({result.parser})")
            if result.inserted_ids:
                state: PipelineState = {"inserted_ids": result.inserted_ids, "timings_ms": {}}
                state.update(node_rule_tag(state, db))
                state.update(await node_llm_tag(state, db, _llm()))
                print(f"Categorised {state.get('llm_tagged', 0)}, "
                      f"{state.get('needs_review', 0)} need review")
            return 0

        print(f"Ingesting statements from {target}…")
        run = await run_pipeline(db, mode="files", directory=str(target))
        _report_run(run)
    return 0


async def cmd_tag(_args: argparse.Namespace) -> int:
    """Categorise rows nothing has categorised yet.

    This is the recovery path for a run that happened while the model was down:
    those rows were deliberately left untagged rather than written as
    zero-confidence model output, so they are still here to pick up.
    """
    from app.llm.client import get_llm as _llm
    from app.pipeline.nodes import PipelineState, node_llm_tag

    with SessionLocal() as db:
        pending = [
            row.id for row in
            db.query(Transaction.id).filter(Transaction.category.is_(None)).all()
        ]
        if not pending:
            print("Everything is already categorised.")
            return 0

        print(f"Categorising {len(pending)} transactions…")
        state: PipelineState = {"inserted_ids": pending, "timings_ms": {}}
        state.update(await node_llm_tag(state, db, _llm()))

        print(f"  categorised    {state.get('llm_tagged', 0)}")
        print(f"  need review    {state.get('needs_review', 0)}")
        if state.get("llm_failed"):
            print(f"  still untagged {state['llm_failed']} (the model was unreachable)")
            return 1
    return 0


async def cmd_agents(_args: argparse.Namespace) -> int:
    from app.pipeline.validator import classify_relationships, revalidate_low_confidence
    from app.services.friend_detector import detect_friends, link_detected_friends

    settings = get_settings()
    llm = get_llm()
    with SessionLocal() as db:
        detected = detect_friends(db)
        stats = link_detected_friends(
            db, detected, min_confidence=settings.friend_min_confidence
        )
        _heading("People")
        print(f"  {len(detected)} candidates, {stats['people_created']} created, "
              f"{stats['transactions_linked']} transactions linked")

        decisions = await revalidate_low_confidence(
            db, llm=llm, threshold=settings.confidence_threshold,
            limit=settings.validator_max_per_run,
        )
        _heading("Validator")
        confirmed = sum(1 for d in decisions if d.agreed)
        print(f"  {len(decisions)} reviewed · {confirmed} confirmed · "
              f"{len(decisions) - confirmed} overridden")

        relationships = await classify_relationships(db, llm=llm)
        if relationships:
            _heading("Relationships")
            for row in relationships:
                print(f"  {redact_payee(row['name'], known_person_names(db)):<14} {row['kind']:<14} "
                      f"{rupees(row['net_to_user']):>12}")
    return 0


async def cmd_patterns(_args: argparse.Namespace) -> int:
    """Print what the detectors found, with individuals' names reduced to initials."""
    from app.services.anomaly_hunter import all_anomalies
    from app.services.emi_detector import summarize_emi_plans
    from app.services.patterns import detect_friend_loops, detect_recurring
    from app.services.subscriptions import detect_subscriptions

    with SessionLocal() as db:
        people = known_person_names(db)
        recurring = detect_recurring(db)
        if recurring:
            _heading("Recurring payments")
            for row in recurring[:15]:
                print(f"  {redact_payee(row.counterparty, people):<24} {rupees(row.amount):>12}  "
                      f"{row.frequency:<10} {row.likely_purpose}")

        subs = detect_subscriptions(db)
        if subs:
            _heading("Subscriptions")
            for sub in subs:
                print(f"  {sub.service:<24} {rupees(sub.median_amount):>12}  "
                      f"{sub.cadence:<10} ~{rupees(sub.annual_estimate)}/year")

        plans = summarize_emi_plans(db)
        if plans:
            _heading("Instalment plans")
            for plan in plans:
                print(f"  {plan.merchant[:24]:<24} {rupees(plan.monthly_amount):>12}/mo  "
                      f"{plan.progress_label}")

        loops = detect_friend_loops(db)
        if loops:
            _heading("Two-way flows")
            for loop in loops[:12]:
                side = "owed to you" if loop.net > 0 else "owed by you"
                print(f"  {redact_payee(loop.counterparty, people):<24} "
                      f"{rupees(abs(loop.net)):>12}  {side}")

        anomalies = all_anomalies(db)
        if anomalies:
            _heading("Worth checking")
            for hit in anomalies[:12]:
                print(f"  {hit.posted_at:%d %b %y}  {hit.title}")

        if not any([recurring, subs, plans, loops, anomalies]):
            print("Nothing detected yet. Ingest some statements first.")
    return 0


async def cmd_report(args: argparse.Namespace) -> int:
    from app.reports import render_spend_analysis

    output = Path(args.output or f"spend-analysis-{utc_now():%Y%m%d-%H%M}.pdf")
    print("Generating the report. Several model passes, expect a minute or two…")
    with SessionLocal() as db:
        pdf = await render_spend_analysis(db)
    output.write_bytes(pdf)
    print(f"Wrote {output}  ({len(pdf) / 1024:.0f} KB)")
    return 0


async def cmd_reset(args: argparse.Namespace) -> int:
    from app.db import Base, engine

    if args.all:
        # Destructive and irreversible, so it asks — unless told not to.
        if not args.yes:
            answer = input("Drop every table, including rules and people? [y/N] ")
            if answer.strip().lower() not in {"y", "yes"}:
                print("Cancelled.")
                return 1
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        print("All tables dropped and recreated.")
        return 0

    with SessionLocal() as db:
        transactions = db.query(Transaction).delete()
        runs = db.query(PipelineRun).delete()
        db.commit()
    print(f"Deleted {transactions} transactions and {runs} run records.")
    print("Rules, categories, payee notes and people were kept "
          "(use --all to drop those too).")
    return 0


async def cmd_eval(args: argparse.Namespace) -> int:
    """Score one or more arms and models against the labelled set.

    Reads nothing from the user's database and writes nothing to it. Each run is
    saved as JSON under `--out`, with every prediction, so a number in a table
    can always be traced to the rows behind it.
    """
    from app.evals.dataset import (
        GOLDEN_PATH,
        PERSONAL_PATH,
        PERSONAL_SLICES,
        SLICES,
        dataset_fingerprint,
        load_golden,
        load_personal,
    )
    from app.evals.metrics import score
    from app.evals.report import build_record, comparison_table, save_record, slice_table
    from app.evals.runner import EvalAbortedError, run_arm
    from app.llm.client import LLMClient

    settings = get_settings()
    slices: tuple[str, ...]
    if args.suite == "personal":
        # Learning from corrections: history rows stand in for your own labels.
        history, rows = load_personal()
        dataset_path, slices = PERSONAL_PATH, PERSONAL_SLICES
        default_arms = ["lookup", "llm", "llm+memory"]
    else:
        history, rows = [], load_golden()
        dataset_path, slices = GOLDEN_PATH, SLICES
        default_arms = ["llm"]
    if args.slice:
        rows = [row for row in rows if row.slice in args.slice]
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        print("No rows selected.", file=sys.stderr)
        return 1

    models = args.model or [settings.llm_model]
    plan: list[tuple[str, str | None]] = []
    for arm in args.arm or default_arms:
        no_model = arm in ("rules", "lookup")
        plan.extend([(arm, None)] if no_model else [(arm, model) for model in models])

    records: list[dict] = []
    for arm, model in plan:
        print(f"Scoring {len(rows)} rows: {arm} / {model or 'no model'}…")
        try:
            result = await run_arm(
                rows, arm=arm,
                llm=LLMClient(model=model) if model else None,
                concurrency=args.concurrency,
                history=history,
            )
        except (EvalAbortedError, ValueError) as e:
            print(f"  skipped: {e}", file=sys.stderr)
            continue
        scores = score(result.predictions, threshold=settings.confidence_threshold)
        record = build_record(
            result, scores, dataset=dataset_path.name,
            fingerprint=dataset_fingerprint(dataset_path),
        )
        path = save_record(record, Path(args.out))
        print(f"  accuracy {scores['accuracy'] * 100:.1f}%, wrote {path}")
        records.append(record)

    if not records:
        return 1
    _heading("Comparison")
    print(comparison_table(records))
    _heading("Accuracy by slice")
    print(slice_table(records, slices))
    return 0


async def cmd_eval_chat(args: argparse.Namespace) -> int:
    """Ask the chat questions and check each answer against a computed figure.

    Builds its own throwaway database from the synthetic generator, so it reads
    nothing from the user's database and writes nothing to it. Each run is saved
    as JSON under `--out` with every question, answer and tool trace.
    """
    from app.evals.chat import (
        build_chat_record,
        chat_scores,
        chat_table,
        load_questions,
        run_chat_eval,
        save_chat_record,
    )
    from app.evals.runner import EvalAbortedError
    from app.llm.client import LLMClient

    questions = load_questions()
    if args.limit:
        questions = questions[:args.limit]
    if not questions:
        print("No questions selected.", file=sys.stderr)
        return 1

    model = args.model or get_settings().llm_model
    records: list[dict] = []
    for arm in args.arm or ["agent"]:
        print(f"Asking {len(questions)} questions: {arm} / {model}…")
        try:
            result = await run_chat_eval(questions, arm=arm, llm=LLMClient(model=model))
        except EvalAbortedError as e:
            print(f"  skipped: {e}", file=sys.stderr)
            continue
        scores = chat_scores(result.answers)
        record = build_chat_record(result, scores)
        path = save_chat_record(record, Path(args.out))
        print(f"  accuracy {scores['accuracy'] * 100:.1f}%, "
              f"{scores['failures']} unanswered, wrote {path}")
        records.append(record)

    if not records:
        return 1
    _heading("Chat comparison")
    print(chat_table(records))
    return 0


async def cmd_eval_summary(args: argparse.Namespace) -> int:
    """Render saved run records as the markdown tables in docs/EVALS.md.

    The tables in the docs are generated from the run files, never typed: a number
    in EVALS.md can always be traced to the JSON record, and from there to every
    prediction behind it.
    """
    import json

    from app.evals.chat import chat_table
    from app.evals.dataset import GOLDEN_PATH, PERSONAL_PATH, PERSONAL_SLICES, SLICES
    from app.evals.report import comparison_table, slice_table

    records = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.records]
    glance = _glance_table(records, GOLDEN_PATH.name, PERSONAL_PATH.name)
    if glance:
        print("\n### Models at a glance\n")
        print(glance)
    groups = {
        "Categorising: labelled stress set": [r for r in records if r.get("dataset") == GOLDEN_PATH.name],
        "Learning from corrections: personal payees": [
            r for r in records if r.get("dataset") == PERSONAL_PATH.name],
        "Chat: questions with computed answers": [r for r in records if r.get("suite") == "chat"],
    }
    for title, members in groups.items():
        if not members:
            continue
        print(f"\n### {title}\n")
        if title.startswith("Chat"):
            print(chat_table(members))
        else:
            print(comparison_table(members))
            print()
            print(slice_table(members, PERSONAL_SLICES if "personal" in title else SLICES))
        print("\nRuns:")
        for r in members:
            meta = r.get("meta") or {}
            print(f"- {r['arm']} / {r.get('model') or 'no model'}: run {r['run_at']}, dataset "
                  f"{r.get('dataset')} sha1 {r.get('dataset_sha1')}, model digest "
                  f"{meta.get('digest', 'n/a')}, Ollama {meta.get('ollama_version', 'n/a')}")
    return 0


def _glance_table(records: list[dict], golden: str, personal: str) -> str:
    """One row per model across all three suites, from the same records as the detail.

    Headline cells only: accuracy on the labelled set with the model alone, how much of
    what clears the review threshold is right, per-call latency, the memory suite with
    and without corrections, and the chat agent. Empty when fewer than two models ran.
    """
    def find(dataset: str | None, arm: str, model: str, suite: str | None = None) -> dict | None:
        for r in records:
            if r.get("model") == model and r["arm"] == arm and (
                r.get("dataset") == dataset if suite is None else r.get("suite") == suite
            ):
                return r
        return None

    def pct(value: float | None) -> str:
        return "n/a" if value is None else f"{value * 100:.1f}%"

    models = sorted({r["model"] for r in records if r.get("model")})
    if len(models) < 2:
        return ""
    lines = [
        "| Model | Categorising | Auto-accepted rows correct (share of rows) | Median call "
        "| Rows/min | Memory: without, with | Chat agent |",
        "|---|---|---|---|---|---|---|",
    ]
    for model in models:
        cat = find(golden, "llm", model)
        alone, with_memory = find(personal, "llm", model), find(personal, "llm+memory", model)
        chat = find(None, "agent", model, suite="chat")
        s = cat["scores"] if cat else {}
        latency = s.get("latency_ms_p50")
        lines.append(
            f"| {model} | {pct(s.get('accuracy'))} "
            f"| {pct(s.get('auto_accept_precision'))} ({pct(s.get('auto_accept_coverage'))}) "
            f"| {'n/a' if latency is None else f'{latency / 1000:.2f}s'} "
            f"| {cat.get('rows_per_minute') if cat else 'n/a'} "
            f"| {pct(alone['scores']['accuracy']) if alone else 'n/a'}, "
            f"{pct(with_memory['scores']['accuracy']) if with_memory else 'n/a'} "
            f"| {pct(chat['scores']['accuracy']) if chat else 'n/a'} |"
        )
    return "\n".join(lines)


async def cmd_mcp(_args: argparse.Namespace) -> int:
    """Serve the chat agent's read-only tools to an MCP client over stdio.

    Blocks until the client closes stdin. Nothing else may print to stdout while
    it runs: stdout is the protocol channel, and logging already goes to stderr.
    """
    from app.mcp_server import serve

    serve()
    return 0


def _report_run(run: PipelineRun) -> None:
    _heading(f"Run #{run.id}: {run.status}")
    print(f"  processed      {run.transactions_processed}")
    print(f"  rule-tagged    {run.rule_tagged}")
    print(f"  model-tagged   {run.llm_tagged}")
    print(f"  need review    {run.needs_review}")
    if not run.llm_available:
        print(f"  UNTAGGED       {run.llm_failed} (the model was unreachable)")
        print("                 Re-run `wimmg tag` once it is back.")
    if run.node_timings_ms:
        slowest = sorted(run.node_timings_ms.items(), key=lambda kv: -kv[1])
        timings = "  ".join(f"{name} {ms / 1000:.1f}s" for name, ms in slowest[:4])
        print(f"  timings        {timings}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wimmg",
        description=f"{APP_NAME}: local-first spending analysis.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="show model and database status")

    demo = subparsers.add_parser("demo", help="load the synthetic dataset")
    demo.add_argument("--months", type=int, default=12)
    demo.add_argument("--seed", type=int, default=42)

    ingest = subparsers.add_parser("ingest", help="parse real statements")
    ingest.add_argument("path", help="a statement file, or a directory of them")
    ingest.add_argument(
        "--source", choices=[s.value for s in TxnSource],
        help="which account these belong to (default: the parser's own guess)",
    )

    subparsers.add_parser("tag", help="categorise anything still untagged")
    subparsers.add_parser("agents", help="detect people and re-check weak tags")
    subparsers.add_parser("patterns", help="print detected patterns")

    report = subparsers.add_parser("report", help="write the Spend Analysis PDF")
    report.add_argument("-o", "--output", help="output path")

    reset = subparsers.add_parser("reset", help="delete data")
    reset.add_argument("--all", action="store_true",
                       help="also drop rules, categories, notes and people")
    reset.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    from app.evals.dataset import PERSONAL_SLICES, SLICES
    from app.evals.runner import ARMS

    evaluate = subparsers.add_parser(
        "eval", help="score the categoriser against the labelled set"
    )
    evaluate.add_argument("--suite", choices=("golden", "personal"), default="golden",
                          help="golden: the labelled stress set; personal: learning "
                               "from your corrections")
    evaluate.add_argument("--arm", action="append", choices=ARMS,
                          help="repeat for several (default: llm, or lookup + llm + "
                               "llm+memory for the personal suite)")
    evaluate.add_argument("--model", action="append",
                          help="model tag; repeat to compare several (default: LLM_MODEL)")
    evaluate.add_argument("--concurrency", type=int, default=get_settings().llm_concurrency)
    evaluate.add_argument("--slice", action="append", choices=SLICES + PERSONAL_SLICES,
                          help="only rows from this slice; repeatable")
    evaluate.add_argument("--limit", type=int, help="only the first N rows")
    evaluate.add_argument("--out", default="eval_results", help="where to write run records")

    from app.evals.chat import CHAT_ARMS

    chat_eval = subparsers.add_parser(
        "eval-chat", help="check chat answers against figures computed from the data"
    )
    chat_eval.add_argument("--model", help="model tag (default: LLM_MODEL)")
    chat_eval.add_argument("--arm", action="append", choices=CHAT_ARMS,
                           help="agent or summary; repeat for both (default: agent)")
    chat_eval.add_argument("--limit", type=int, help="only the first N questions")
    chat_eval.add_argument("--out", default="eval_results", help="where to write run records")

    summary = subparsers.add_parser(
        "eval-summary", help="render saved eval run records as markdown tables"
    )
    summary.add_argument("records", nargs="+", help="run record JSON files")

    subparsers.add_parser(
        "mcp", help="serve the read-only query tools to an MCP client over stdio"
    )

    return parser


COMMANDS = {
    "status": cmd_status,
    "demo": cmd_demo,
    "ingest": cmd_ingest,
    "tag": cmd_tag,
    "agents": cmd_agents,
    "patterns": cmd_patterns,
    "report": cmd_report,
    "reset": cmd_reset,
    "eval": cmd_eval,
    "eval-chat": cmd_eval_chat,
    "eval-summary": cmd_eval_summary,
    "mcp": cmd_mcp,
}


def main(argv: list[str] | None = None) -> int:
    global UNICODE_OK
    UNICODE_OK = use_utf8_streams()

    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    init_db()
    try:
        return asyncio.run(COMMANDS[args.command](args))
    except KeyboardInterrupt:
        print("\nInterrupted. Progress up to the last checkpoint was saved.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
