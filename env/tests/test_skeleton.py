from decimal import Decimal

from scar_env.log import JsonFormatter


def test_health(client):
    assert client.get("/health").json() == {"ok": True}


def test_companies_filter_by_split(client, ledger):
    all_rows = client.get("/companies").json()
    assert [r["company_id"] for r in all_rows] == ["company-901", "company-902", "company-903"]
    train = client.get("/companies", params={"split": "train"}).json()
    assert {r["split"] for r in train} == {"train"} and len(train) == 2
    assert client.get("/companies", params={"split": "bogus"}).json()["error"] == "invalid_input"


def test_synthetic_ledger_shape(ledger):
    for cid, *_ in ledger.companies:
        txns = ledger.company_txns(cid)
        assert len(txns) > 60
        assert len({t.txn_id for t in txns}) == len(txns)
        assert any(t.amount < 0 for t in txns)
        assert any(t.vendor_id is None for t in txns)
    freight = [t.amount for t in ledger.company_txns("company-901") if t.account_id.endswith("acct-freight")]
    median = sorted(freight)[len(freight) // 2]
    assert Decimal("2950.37") > 3 * median


def test_json_log_line():
    import json
    import logging

    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "hello", None, None)
    rec.run_id = "run_1"
    out = json.loads(JsonFormatter().format(rec))
    assert out["service"] == "env" and out["msg"] == "hello" and out["run_id"] == "run_1"


def test_unknown_route_uses_spec_error_body(client):
    r = client.get("/nope")
    assert r.status_code == 404 and r.json() == {"error": "not_found", "detail": "Not Found"}
