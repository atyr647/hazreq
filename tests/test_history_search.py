"""Browse + search past requests, including across line content."""

from __future__ import annotations


def _seed_with_lines(client, session, *, requestor: str, lines: list[dict]):
    from app.models import RequestLine

    r = client.get("/requests/new")
    rid = int(r.headers["location"].rstrip("/").rsplit("/", 1)[-1])
    client.patch(f"/requests/{rid}", data={"requestor_name": requestor})
    for i, ln in enumerate(lines):
        session.add(
            RequestLine(
                request_id=rid,
                sort_order=(i + 1) * 10,
                spmig_code=ln.get("spmig"),
                nomenclature=ln.get("nomenclature"),
                niin=ln.get("niin"),
                qty=ln.get("qty", 1),
            )
        )
    session.commit()
    return rid


def test_history_lists_recent_requests(client, session):
    rid1 = _seed_with_lines(client, session, requestor="Alice",
                            lines=[{"nomenclature": "Oil A"}])
    rid2 = _seed_with_lines(client, session, requestor="Bob",
                            lines=[{"nomenclature": "Solvent B"}])

    resp = client.get("/requests/")
    assert resp.status_code == 200
    assert "Alice" in resp.text
    assert "Bob" in resp.text


def test_search_by_header_field(client, session):
    _seed_with_lines(client, session, requestor="Alice", lines=[])
    _seed_with_lines(client, session, requestor="Bob", lines=[])

    resp = client.get("/requests/", params={"q": "Alice"})
    assert resp.status_code == 200
    assert "Alice" in resp.text
    assert "Bob" not in resp.text


def test_search_by_line_content(client, session):
    _seed_with_lines(client, session, requestor="Alice",
                     lines=[{"nomenclature": "Lubricant Oil 30W", "niin": "123"}])
    _seed_with_lines(client, session, requestor="Bob",
                     lines=[{"nomenclature": "Antifreeze", "niin": "999"}])

    # Search by item nomenclature
    resp = client.get("/requests/", params={"q": "Lubricant"})
    assert resp.status_code == 200
    assert "Alice" in resp.text
    assert "Bob" not in resp.text

    # Search by NIIN
    resp = client.get("/requests/", params={"q": "999"})
    assert resp.status_code == 200
    assert "Bob" in resp.text
    assert "Alice" not in resp.text


def test_filter_by_status(client, session):
    rid1 = _seed_with_lines(client, session, requestor="Alice",
                            lines=[{"nomenclature": "X", "qty": 1}])
    # Finalize one
    client.post(f"/requests/{rid1}/finalize")
    _seed_with_lines(client, session, requestor="Bob",
                     lines=[{"nomenclature": "Y", "qty": 1}])  # stays draft

    resp = client.get("/requests/", params={"status": "final"})
    assert "Alice" in resp.text
    assert "Bob" not in resp.text

    resp = client.get("/requests/", params={"status": "draft"})
    assert "Bob" in resp.text
    assert "Alice" not in resp.text
