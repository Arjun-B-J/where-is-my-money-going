"""HTTP surface tests."""
from __future__ import annotations

import io

from app.models import TagSource, Transaction

CSV_STATEMENT = """\
Transaction Details:
"Date","Sr.No.","Transaction Details","Reward Points","Intl.Amount","Amount(in Rs)","Sign"
"03-APR-26","1","POS EXAMPLE SUPERMARKET","12","","1,299.00","1,299.00"
"05-APR-26","2","POS EXAMPLE COFFEE","0","","240.00","240.00"
"""


def test_root(client):
    body = client.get("/").json()
    assert body["name"] == "Where Is My Money Going?"
    assert "kharcha" not in str(body).lower()


def test_health_reports_model_state(client):
    body = client.get("/system/health").json()
    assert body["ok"] is True
    assert body["llm"]["ok"] is True
    assert body["config"]["thinking_enabled"] is False


def test_openapi_builds(client):
    """Catches malformed response models across every route at once."""
    assert client.get("/openapi.json").status_code == 200


def test_pipeline_run_then_dashboard(client):
    run = client.post("/pipeline/run?months=3&seed=7").json()
    assert run["status"] == "ok"
    assert run["llm_available"] is True

    summary = client.get("/dashboard/summary").json()
    assert summary["transaction_count"] > 50
    assert summary["total_debit"] > 0
    assert summary["by_category"]


def test_pipeline_topology_describes_both_modes(client):
    topology = client.get("/pipeline/topology").json()
    node_ids = {node["id"] for node in topology["nodes"]}
    assert {"generate", "load_files", "llm_tag", "validator"} <= node_ids
    assert any(edge.get("conditional") for edge in topology["edges"])


def test_transactions_filters(client):
    client.post("/pipeline/run?months=3&seed=7")

    everything = client.get("/transactions?limit=200").json()
    assert everything

    debits = client.get("/transactions?direction=debit&limit=200").json()
    assert all(row["direction"] == "debit" for row in debits)

    flagged = client.get("/transactions?needs_review=true&limit=200").json()
    assert all(row["needs_review"] for row in flagged)


def test_manual_tag_wins(client):
    client.post("/pipeline/run?months=2&seed=7")
    txn_id = client.get("/transactions?limit=1").json()[0]["id"]

    updated = client.patch(
        f"/transactions/{txn_id}/tag",
        json={"category": "investments", "subcategory": "index fund"},
    ).json()

    assert updated["category"] == "investments"
    assert updated["tag_source"] == "user"
    assert updated["tag_confidence"] == 1.0
    assert updated["needs_review"] is False


def test_bulk_tag(client):
    client.post("/pipeline/run?months=3&seed=7")
    ids = [row["id"] for row in client.get("/transactions?limit=5").json()]

    result = client.post(
        "/transactions/bulk-tag", json={"transaction_ids": ids, "category": "food"}
    ).json()

    assert result["updated"] == len(ids)


def test_bulk_tag_rejects_empty_list(client):
    assert client.post(
        "/transactions/bulk-tag", json={"transaction_ids": [], "category": "food"}
    ).status_code == 422


def test_insights_are_labelled_with_their_origin(client):
    client.post("/pipeline/run?months=3&seed=7")
    cards = client.get("/insights").json()

    assert cards
    assert all(card["generated_by"] in {"model", "computed"} for card in cards)


def test_upload_csv(client):
    response = client.post(
        "/ingest/file",
        files={"file": ("export.csv", io.BytesIO(CSV_STATEMENT.encode()), "text/csv")},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["parser"] == "hdfc_credit_csv"
    assert body["inserted"] == 2
    assert body["llm_available"] is True


def test_uploading_the_same_file_twice_inserts_nothing(client):
    for _ in range(2):
        response = client.post(
            "/ingest/file",
            files={"file": ("export.csv", io.BytesIO(CSV_STATEMENT.encode()), "text/csv")},
        )
    assert response.json()["inserted"] == 0
    assert response.json()["duplicates"] == 2


def test_upload_rejects_traversal_in_filename(client):
    """A crafted filename must not decide where anything is written."""
    response = client.post(
        "/ingest/file",
        files={
            "file": (
                "../../../../etc/passwd.csv",
                io.BytesIO(CSV_STATEMENT.encode()),
                "text/csv",
            )
        },
    )
    assert response.status_code == 200
    assert "/" not in response.json()["file"]
    assert ".." not in response.json()["file"]


def test_upload_rejects_wrong_type(client):
    response = client.post(
        "/ingest/file",
        files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert response.status_code == 415


def test_upload_rejects_empty_file(client):
    response = client.post(
        "/ingest/file", files={"file": ("empty.csv", io.BytesIO(b""), "text/csv")}
    )
    assert response.status_code == 400


def test_upload_rejects_unrecognised_content(client):
    response = client.post(
        "/ingest/file",
        files={"file": ("junk.csv", io.BytesIO(b"not,a,statement\n1,2,3\n"), "text/csv")},
    )
    assert response.status_code == 422


def test_supported_formats_listed(client):
    formats = client.get("/ingest/formats").json()
    assert formats
    assert all({"name", "label", "extensions"} <= set(f) for f in formats)


def test_patterns_and_budget_endpoints(client):
    client.post("/pipeline/run?months=6&seed=7")

    assert client.get("/patterns").status_code == 200
    assert client.get("/budget/envelopes").status_code == 200
    assert client.get("/budget/weekday-pattern").status_code == 200
    assert client.get("/trends?granularity=monthly").status_code == 200
    assert client.get("/trends/forecast").status_code == 200
    assert client.get("/cross-source/card-reconcile").status_code == 200
    assert client.get("/cross-source/duplicates").status_code == 200
    assert client.get("/review/groups").status_code == 200


def test_people_appear_after_detection(client):
    client.post("/pipeline/run?months=12&seed=7")
    people = client.get("/people").json()
    assert people, "friend detection should find the two-way flows in demo data"


def test_merchant_notes_round_trip(client):
    created = client.put(
        "/merchant-notes", json={"pattern": "example fuel", "note": "Car running cost"}
    ).json()
    assert created["pattern"] == "EXAMPLE FUEL"

    # Upsert rather than duplicate.
    client.put("/merchant-notes", json={"pattern": "example fuel", "note": "Updated"})
    matching = [n for n in client.get("/merchant-notes").json()
                if n["pattern"] == "EXAMPLE FUEL"]
    assert len(matching) == 1
    assert matching[0]["note"] == "Updated"

    assert client.delete(f"/merchant-notes/{created['id']}").status_code == 200


def test_chat_returns_a_reply(client):
    client.post("/pipeline/run?months=2&seed=7")
    body = client.post(
        "/chat", json={"messages": [{"role": "user", "content": "How much on food?"}]}
    ).json()
    assert body["ok"] is True
    assert body["reply"]


def test_chat_rejects_empty_message(client):
    assert client.post("/chat", json={"messages": []}).status_code == 422


def test_receipt_scan(client):
    response = client.post(
        "/receipt/scan",
        files={"file": ("receipt.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 64), "image/png")},
    )
    body = response.json()
    assert body["ok"] is True
    assert body["transaction"]["merchant_normalized"] == "FAKE CAFE"


def test_receipt_rejects_non_image(client):
    response = client.post(
        "/receipt/scan",
        files={"file": ("statement.pdf", io.BytesIO(b"%PDF"), "application/pdf")},
    )
    assert response.status_code == 415


def test_agents_endpoints(client):
    client.post("/pipeline/run?months=12&seed=7")

    assert client.get("/agents/friend-detect").status_code == 200
    applied = client.post("/agents/friend-detect").json()
    assert applied["candidates"] >= 0

    validated = client.post("/agents/validate?limit=5").json()
    assert "reviewed" in validated


def test_reset_keeps_configuration(client):
    client.post("/pipeline/run?months=2&seed=7")
    assert client.get("/categories").json()

    client.post("/system/reset")

    assert client.get("/transactions").json() == []
    assert client.get("/categories").json(), "categories are config and must survive"


def test_report_generates_a_pdf(client):
    client.post("/pipeline/run?months=3&seed=7")
    response = client.get("/report/spend-analysis")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert len(response.content) > 20_000


def test_report_on_empty_database_still_renders(client):
    response = client.get("/report/spend-analysis")
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


def test_offline_model_is_visible_through_the_api(client, monkeypatch, db):
    """A run made while the model is down must be reported honestly."""
    from tests.conftest import FakeLLM

    offline = FakeLLM(available=False)
    import app.pipeline.graph as graph_module

    monkeypatch.setattr(graph_module, "get_llm", lambda: offline)

    run = client.post("/pipeline/run?months=2&seed=7").json()
    assert run["llm_available"] is False
    assert run["llm_failed"] > 0

    rows = client.get("/transactions?limit=5").json()
    assert all(row["tag_source"] is None for row in rows)
    assert all(row["category"] is None for row in rows)


def test_untagged_filter(client, monkeypatch):
    import app.pipeline.graph as graph_module
    from tests.conftest import FakeLLM

    monkeypatch.setattr(graph_module, "get_llm", lambda: FakeLLM(available=False))
    client.post("/pipeline/run?months=2&seed=7")

    untagged = client.get("/transactions?untagged=true&limit=10").json()
    assert untagged
    assert all(row["category"] is None for row in untagged)


def test_tagged_rows_record_provenance(client, db):
    client.post("/pipeline/run?months=2&seed=7")
    sources = {
        txn.tag_source for txn in db.query(Transaction).all() if txn.tag_source
    }
    assert sources <= {TagSource.LLM, TagSource.RULE, TagSource.VALIDATOR}
