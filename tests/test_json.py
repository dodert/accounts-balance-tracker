import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def fill(client):
    a = client.post("/api/accounts", json={"name": "ING", "type": "savings", "institution": "ING Bank",
                                          "notes": "joint account", "available_from": "2027-01-01"}).json()
    b = client.post("/api/accounts", json={"name": "Old", "currency": "USD", "archived": True}).json()
    client.post("/api/accounts", json={"name": "Empty"})
    client.post(f"/api/accounts/{a['id']}/balances", json={"date": "2026-01-31", "amount": 100050, "note": "pay"})  # cents
    client.post(f"/api/accounts/{a['id']}/balances", json={"date": "2026-02-28", "amount": 110000})
    client.post(f"/api/accounts/{b['id']}/balances", json={"date": "2026-01-31", "amount": 700})
    client.post(f"/api/accounts/{a['id']}/milestones", json={"date": "2026-02-01", "amount": 30000, "description": "Car"})


def snapshot(client):
    """Everything the user sees, without ids or timestamps."""
    data = json.loads(client.get("/api/export/json").content)
    data.pop("exported_at")
    return data


def post(client, content: bytes, mode: str):
    return client.post("/api/import/json", data={"mode": mode},
                       files={"file": ("all.json", content, "application/json")})


def test_export_has_everything(client):
    """The JSON export has every account with all its fields, balances and milestones."""
    fill(client)
    res = client.get("/api/export/json")
    assert res.headers["content-disposition"].startswith("attachment") and ".json" in res.headers["content-disposition"]
    data = res.json()
    assert (data["format"], data["version"]) == ("accounts-balance-tracker", 1)
    accounts = {a["name"]: a for a in data["accounts"]}
    assert set(accounts) == {"ING", "Old", "Empty"}  # also an account without balances
    ing = accounts["ING"]
    assert (ing["notes"], ing["available_from"], ing["institution"], ing["type"]) == ("joint account", "2027-01-01", "ING Bank", "savings")
    assert ing["balances"] == [{"date": "2026-01-31", "amount": "1000.50", "note": "pay"},
                               {"date": "2026-02-28", "amount": "1100.00", "note": None}]
    assert ing["milestones"] == [{"date": "2026-02-01", "amount": "300.00", "description": "Car"}]
    assert accounts["Old"]["archived"] is True and accounts["Old"]["currency"] == "USD"


def test_replace_round_trip_gives_the_same_data(client, tmp_path):
    """A JSON import in replace mode gives the same data, after a backup."""
    fill(client)
    exported = client.get("/api/export/json").content
    other = TestClient(create_app(Settings(data_dir=tmp_path / "other")))
    other.post("/api/accounts", json={"name": "Will be deleted"})
    res = post(other, exported, "replace")
    assert res.status_code == 200, res.text
    assert res.json() == {"accounts_created": 3, "balances_created": 3, "milestones_created": 1,
                          "duplicates_count": 0, "duplicates": []}
    assert snapshot(other)["accounts"] == snapshot(client)["accounts"]
    # A backup of the database was made before replacing it
    assert len(list((tmp_path / "other").glob("balances.db.backup-before-import-*"))) == 1


def test_replace_keeps_the_newest_backups(client, tmp_path, monkeypatch):
    """Only the 3 newest backups made before an import are kept."""
    import app.migrate as migrate
    fill(client)
    exported = client.get("/api/export/json").content
    names = iter(f"2026010{i}-000000" for i in range(1, 6))

    class Clock:
        @staticmethod
        def now():
            class Stamp:
                def __format__(self, _spec):
                    return next(names)
            return Stamp()
    monkeypatch.setattr(migrate, "datetime", Clock)
    for _ in range(5):
        assert post(client, exported, "replace").status_code == 200
    kept = sorted(p.name for p in tmp_path.glob("balances.db.backup-before-import-*"))
    assert kept == [f"balances.db.backup-before-import-2026010{i}-000000" for i in (3, 4, 5)]


def test_merge_imports_only_what_is_not_there_and_lists_it(client, tmp_path):
    """A JSON import in merge mode adds only what is missing and lists what was already there."""
    fill(client)
    data = json.loads(client.get("/api/export/json").content)
    ing = next(a for a in data["accounts"] if a["name"] == "ING")
    ing["balances"].append({"date": "2026-03-31", "amount": "1200", "note": None})
    ing["balances"][0]["amount"] = "9999"  # same day as the one in the database: not imported
    ing["milestones"].append({"date": "2026-03-01", "amount": "50", "description": "Trip"})
    ing["notes"] = "changed"  # an existing account keeps its own fields
    data["accounts"].append({"name": "New", "balances": [{"date": "2026-01-31", "amount": "5"}]})
    res = post(client, json.dumps(data).encode(), "merge").json()
    assert (res["accounts_created"], res["balances_created"], res["milestones_created"]) == (1, 2, 1)
    assert res["duplicates_count"] == 4
    assert res["duplicates"] == ["ING (ING Bank): balance on 2026-01-31", "ING (ING Bank): balance on 2026-02-28",
                                 "ING (ING Bank): milestone 'Car' on 2026-02-01", "Old: balance on 2026-01-31"]
    after = {a["name"]: a for a in snapshot(client)["accounts"]}
    assert after["ING"]["balances"][0]["amount"] == "1000.50" and after["ING"]["notes"] == "joint account"
    assert len(after["ING"]["balances"]) == 3 and len(after["ING"]["milestones"]) == 2

    # Merging the same file again adds nothing
    again = post(client, json.dumps(data).encode(), "merge").json()
    assert (again["accounts_created"], again["balances_created"], again["milestones_created"]) == (0, 0, 0)
    # A merge needs no backup
    assert not list(tmp_path.glob("balances.db.backup-before-import-*"))


def test_an_invalid_file_changes_nothing(client):
    """An invalid JSON file is refused with the reason, and nothing changes."""
    fill(client)
    before = snapshot(client)
    data = json.loads(client.get("/api/export/json").content)
    next(a for a in data["accounts"] if a["balances"])["balances"][0]["amount"] = "not a number"
    for content, message in [(b"not json", "not JSON"), (b'{"format": "other"}', "not an export"),
                             (json.dumps({**data, "version": 99}).encode(), "newer version"),
                             (json.dumps(data).encode(), "Invalid file")]:
        res = post(client, content, "replace")
        assert res.status_code == 422 and message in res.json()["detail"], res.text
    assert post(client, b"{}", "other").status_code == 422
    assert snapshot(client) == before


def test_demo_can_export_and_import(client):
    """The demo can export and import JSON too."""
    client.post("/api/demo")
    exported = client.get("/api/export/json").content
    assert json.loads(exported)["accounts"]
    res = post(client, exported, "merge").json()
    assert res["accounts_created"] == 0 and res["balances_created"] == 0 and res["duplicates_count"] > 0
    assert post(client, exported, "replace").status_code == 200
