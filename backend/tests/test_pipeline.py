"""Pipeline behaviour, including what happens when the model is down."""
from __future__ import annotations

import pytest

from app.models import PipelineRun, TagSource, Transaction, TxnDirection
from app.pipeline.graph import run_pipeline


@pytest.mark.asyncio
async def test_demo_run_completes(db, llm):
    run = await run_pipeline(db, mode="demo", seed=7, months=3, llm=llm)

    assert run.status == "ok"
    assert run.trigger == "demo"
    assert run.transactions_processed > 50
    assert run.llm_tagged > 0
    assert run.llm_available is True
    assert run.llm_failed == 0
    assert run.finished_at is not None


@pytest.mark.asyncio
async def test_every_transaction_gets_a_category_when_model_is_up(db, llm):
    await run_pipeline(db, mode="demo", seed=7, months=3, llm=llm)
    txns = db.query(Transaction).all()

    assert txns
    assert all(txn.category is not None for txn in txns)
    assert all(txn.tag_source is not None for txn in txns)


@pytest.mark.asyncio
async def test_offline_model_leaves_rows_untagged_not_falsely_tagged(db, offline_llm):
    """The regression that matters most.

    When the model cannot be reached, rows must end up with no category and no
    provenance. Writing them as `tag_source=llm, confidence=0.0` is what made 285
    unclassified transactions look like genuine model output in the UI.
    """
    await run_pipeline(db, mode="demo", seed=7, months=2, llm=offline_llm)
    txns = db.query(Transaction).all()

    assert txns, "transactions should still be extracted and stored"
    assert all(txn.category is None for txn in txns)
    assert all(txn.tag_source is None for txn in txns)
    assert all(txn.tag_confidence is None for txn in txns)
    # And every one of them is surfaced for review rather than quietly dropped.
    assert all(txn.needs_review for txn in txns)


@pytest.mark.asyncio
async def test_run_record_admits_the_model_was_unavailable(db, offline_llm):
    run = await run_pipeline(db, mode="demo", seed=7, months=2, llm=offline_llm)

    assert run.llm_available is False
    assert run.llm_failed == run.transactions_processed
    assert run.llm_tagged == 0


@pytest.mark.asyncio
async def test_malformed_model_output_is_also_a_failure(db):
    """Valid HTTP, unparseable body: still no tag written."""
    from tests.conftest import FakeLLM

    run = await run_pipeline(
        db, mode="demo", seed=7, months=2, llm=FakeLLM(malformed=True)
    )
    assert run.llm_tagged == 0
    assert run.llm_failed > 0
    assert all(txn.category is None for txn in db.query(Transaction).all())


@pytest.mark.asyncio
async def test_rerunning_tag_recovers_untagged_rows(db, offline_llm, llm):
    """A run that happened while the model was down can be completed later."""
    from app.pipeline.nodes import node_llm_tag

    await run_pipeline(db, mode="demo", seed=7, months=2, llm=offline_llm)
    pending = [row.id for row in db.query(Transaction.id).all()]

    state = await node_llm_tag({"inserted_ids": pending, "timings_ms": {}}, db, llm)

    assert state["llm_tagged"] == len(pending)
    assert state["llm_failed"] == 0
    assert all(txn.category is not None for txn in db.query(Transaction).all())


@pytest.mark.asyncio
async def test_low_confidence_rows_are_flagged_for_review(db, llm):
    """The synthetic dataset contains opaque payees on purpose.

    If nothing needs review, either the data has no ambiguity or the confidence
    scores are not real — both mean the review queue is untested.
    """
    await run_pipeline(db, mode="demo", seed=7, months=6, llm=llm)

    flagged = db.query(Transaction).filter(Transaction.needs_review.is_(True)).all()
    assert flagged, "expected some genuinely ambiguous transactions"
    assert all(txn.tag_confidence < 0.70 for txn in flagged)


@pytest.mark.asyncio
async def test_reingesting_the_same_data_adds_nothing(db, llm):
    await run_pipeline(db, mode="demo", seed=7, months=2, llm=llm)
    first = db.query(Transaction).count()

    await run_pipeline(db, mode="demo", seed=7, months=2, llm=llm)
    assert db.query(Transaction).count() == first


@pytest.mark.asyncio
async def test_user_tags_survive_a_later_run(db, llm):
    """Pipeline runs skip rows that already have a category."""
    await run_pipeline(db, mode="demo", seed=7, months=2, llm=llm)
    txn = db.query(Transaction).first()
    txn.category = "investments"
    txn.tag_source = TagSource.USER
    txn.tag_confidence = 1.0
    db.commit()
    txn_id = txn.id

    await run_pipeline(db, mode="demo", seed=7, months=2, llm=llm)

    reloaded = db.get(Transaction, txn_id)
    assert reloaded.category == "investments"
    assert reloaded.tag_source == TagSource.USER


@pytest.mark.asyncio
async def test_timings_recorded_for_every_node(db, llm):
    run = await run_pipeline(db, mode="demo", seed=7, months=2, llm=llm)
    timings = run.node_timings_ms

    for node in ("seed", "generate", "store", "rule_tag", "llm_tag",
                 "friend_discover", "validator", "finalize"):
        assert node in timings, f"missing timing for {node}"
    assert all(isinstance(value, int) for value in timings.values())


@pytest.mark.asyncio
async def test_files_mode_reads_a_directory(db, llm, tmp_path):
    """`mode="files"` runs the same graph over real statements.

    An empty directory is a valid, if boring, case: the run must succeed with
    zero transactions rather than raise.
    """
    run = await run_pipeline(db, mode="files", directory=str(tmp_path), llm=llm)

    assert run.status == "ok"
    assert run.trigger == "files"
    assert run.transactions_processed == 0


@pytest.mark.asyncio
async def test_both_directions_present(db, llm):
    await run_pipeline(db, mode="demo", seed=7, months=3, llm=llm)

    debits = db.query(Transaction).filter(
        Transaction.direction == TxnDirection.DEBIT).count()
    credits = db.query(Transaction).filter(
        Transaction.direction == TxnDirection.CREDIT).count()
    assert debits > 0
    assert credits > 0


@pytest.mark.asyncio
async def test_failed_run_is_recorded_as_error(db, llm, monkeypatch):
    from app.pipeline import nodes

    def explode(state, db):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(nodes, "node_store", explode)

    with pytest.raises(RuntimeError):
        await run_pipeline(db, mode="demo", seed=7, months=1, llm=llm)

    run = db.query(PipelineRun).order_by(PipelineRun.id.desc()).first()
    assert run.status == "error"
    assert "simulated failure" in run.error_message
