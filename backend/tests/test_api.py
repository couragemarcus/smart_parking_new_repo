import os
import sys
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

test_database = Path(__file__).parent / 'test.db'
if test_database.exists():
    test_database.unlink()
os.environ["SMARTPARK_DATABASE_URL"] = f"sqlite:///{test_database}"
os.environ["SMARTPARK_SIMULATOR_ENABLED"] = "true"
os.environ["SMARTPARK_OPEN_STAFF_INTERFACE"] = "false"
os.environ["SMARTPARK_PUBLIC_ENTRY_URL"] = "https://parking.example.test"
sys.path.insert(0, str(Path(__file__).parents[1]))

from fastapi.testclient import TestClient
from app.main import ADMIN_TOKEN, SETUP_TOKEN, app, connect, fee_quote, init_db
import app.main as main_module
from datetime import timezone

_server_now = main_module.now
main_module.now = lambda: datetime.now(timezone.utc).isoformat()

init_db()
with connect() as db:
    db.execute("UPDATE sites SET name = 'Test Parking Lot', address = 'Test address', latitude = 5.5, longitude = -0.2, setup_complete = 1 WHERE id = 'default'")
    db.execute("UPDATE tariffs SET free_minutes = 30, block_minutes = 10, block_price_minor = 200 WHERE site_id = 'default'")
client = TestClient(app)


def arrive_and_verify(session):
    headers = {"x-visitor-token": session["visitor_token"]}
    checked_in = client.post("/api/v1/visitor/arrivals", headers=headers).json()
    assert checked_in["status"] == "WAITING_CONFIRMATION"
    return client.post(f"/api/v1/security/arrivals/{session['session_id']}/match", headers={"authorization": f"Bearer {ADMIN_TOKEN}"}, json={})


def reset_default_lot():
    with connect() as db:
        active_ids = [row["id"] for row in db.execute("SELECT id FROM visitor_sessions WHERE site_id = 'default'").fetchall()]
        if active_ids:
            db.executemany("DELETE FROM reservations WHERE session_id = ?", [(session_id,) for session_id in active_ids])
            db.executemany("DELETE FROM invoices WHERE session_id = ?", [(session_id,) for session_id in active_ids])
            db.executemany("DELETE FROM visitor_sessions WHERE id = ?", [(session_id,) for session_id in active_ids])
        db.execute("UPDATE parking_spaces SET status = 'AVAILABLE', assigned_session = NULL, vehicle_id = NULL WHERE site_id = 'default'")
        db.execute("DELETE FROM iot_events")
        db.execute("UPDATE bay_sensor_state SET physical_state = 'UNKNOWN', device_id = NULL, last_seen = NULL, updated_at = ?", (datetime.now(timezone.utc).isoformat(),))
        db.execute("DELETE FROM demo_bay_assignments")
        db.execute("DELETE FROM reservations")
        db.execute("DELETE FROM visitor_sessions WHERE site_id = 'default'")


def test_first_parktech_admin_can_create_account_once():
    assert client.get("/api/v1/admin/bootstrap/status").json() == {"available": True}
    payload = {"setup_token": SETUP_TOKEN, "display_name": "Ama Mensah", "email": "first-admin@parktech.test", "password": "strong-password-123"}
    assert client.post("/api/v1/admin/bootstrap", json={**payload, "setup_token": "wrong-setup-token"}).status_code == 401
    response = client.post("/api/v1/admin/bootstrap", json=payload)
    assert response.status_code == 201
    assert response.json()["user"]["role"] == "ADMIN"
    token = response.json()["access_token"]
    headers = {"authorization": f"Bearer {token}"}
    assert client.patch("/api/v1/admin/public-url", json={"base_url": "http://localhost:5173"}, headers=headers).status_code == 422
    assert client.patch("/api/v1/admin/public-url", json={"base_url": "https://parking.example.test"}, headers=headers).status_code == 200
    assert client.get("/api/v1/admin/users", headers=headers).status_code == 200
    profile = {"name": "Test Parking Lot", "address": "Test address", "contact_email": "help@example.test", "contact_phone": "0240000000", "social_links": {"instagram": "https://instagram.com/test"}}
    assert client.patch("/api/v1/admin/site", json=profile, headers=headers).status_code == 200
    public_profile = client.get("/api/public/facility").json()
    assert public_profile["contact_email"] == "help@example.test"
    assert public_profile["social_links"]["instagram"] == "https://instagram.com/test"
    assert client.patch("/api/v1/admin/site", json={**profile, "social_links": {"x": "http://unsafe.example"}}, headers=headers).status_code == 422
    assert client.get("/api/v1/admin/bootstrap/status").json() == {"available": False}
    assert client.post("/api/v1/admin/bootstrap", json=payload).status_code == 409


def test_first_arrival_gets_l1_and_second_gets_l2():
    reset_default_lot()
    first = client.post("/api/v1/visitor/sessions").json()
    second = client.post("/api/v1/visitor/sessions").json()
    one = arrive_and_verify(first)
    two = arrive_and_verify(second)
    assert one.json()["space_id"] == "L1"
    assert two.json()["space_id"] == "L2"


def test_duplicate_iot_events_are_idempotent():
    payload = {"event_id": "event-unique-1", "device_id": "pi-1", "sensor_id": "L1", "event_type": "space_occupied", "space_id": "L1"}
    first = client.post("/api/v1/iot/events", headers={"x-device-token": "change-device-token-in-production"}, json=payload)
    second = client.post("/api/v1/iot/events", headers={"x-device-token": "change-device-token-in-production"}, json=payload)
    assert first.status_code == 200
    assert second.json()["status"] == "duplicate"


def test_live_two_bay_sensor_events_assignment_and_health():
    reset_default_lot()
    headers = {"x-device-token": "change-device-token-in-production"}
    assert client.get("/api/bays").status_code == 200
    initial = client.get("/api/bays").json()["bays"]
    assert [bay["id"] for bay in initial] == ["L1", "L2"]
    assert all(bay["display_state"] == "WAITING_FOR_SENSOR" for bay in initial)
    assert client.post("/api/bays/L1/assignment", json={"assigned": True}).status_code == 409
    free = {"event_id": "live-bay-free-001", "device_id": "pi-live", "sensor_id": "L1", "space_id": "L1", "event_type": "bay_free"}
    assert client.post("/api/iot/events", headers=headers, json=free).json()["status"] == "processed"
    assert client.post("/api/iot/events", headers=headers, json=free).json()["status"] == "duplicate"
    assert client.post("/api/bays/L1/assignment", json={"assigned": True}).status_code == 200
    assert client.post("/api/bays/L1/assignment", json={"assigned": False}).status_code == 200
    occupied = {**free, "event_id": "live-bay-occupied-001", "event_type": "bay_occupied"}
    assert client.post("/api/iot/events", headers=headers, json=occupied).status_code == 200
    l1 = next(bay for bay in client.get("/api/bays").json()["bays"] if bay["id"] == "L1")
    assert l1["display_state"] == "OCCUPIED"
    assert client.post("/api/iot/events", headers=headers, json={**free, "event_id": "live-bad-space", "space_id": "L3"}).status_code == 422
    assert client.post("/api/iot/demo-events", json={**free, "event_id": "demo-event-0001"}).status_code == 200


def test_live_bay_assignment_requires_free_sensor_state():
    reset_default_lot()
    headers = {"x-device-token": "change-device-token-in-production"}
    payload = {"event_id": "live-bay-occupied-002", "device_id": "pi-live", "sensor_id": "L2", "space_id": "L2", "event_type": "bay_occupied"}
    assert client.post("/api/iot/events", headers=headers, json=payload).status_code == 200
    assert client.post("/api/bays/L2/assignment", json={"assigned": True}).status_code == 409


def test_sensor_free_after_assignment_keeps_marker_and_occupied_overrides():
    reset_default_lot()
    headers = {"x-device-token": "change-device-token-in-production"}
    free = {"event_id": "assigned-free-event-01", "device_id": "pi-assignment", "sensor_id": "L1", "space_id": "L1", "event_type": "bay_free"}
    assert client.post("/api/iot/events", headers=headers, json=free).status_code == 200
    assert client.post("/api/bays/L1/assignment", json={"assigned": True}).status_code == 200
    assert client.post("/api/iot/events", headers=headers, json={**free, "event_id": "assigned-free-event-02"}).status_code == 200
    l1 = next(bay for bay in client.get("/api/bays").json()["bays"] if bay["id"] == "L1")
    assert l1["display_state"] == "ASSIGNED"
    assert client.post("/api/iot/demo-events", json={**free, "event_id": "assigned-occupied-event-01", "event_type": "bay_occupied"}).status_code == 200
    l1 = next(bay for bay in client.get("/api/bays").json()["bays"] if bay["id"] == "L1")
    assert l1["display_state"] == "OCCUPIED"


def test_public_passwordless_assignment_creates_billable_visitor_session():
    reset_default_lot()
    headers = {"x-device-token": "change-device-token-in-production"}
    free = {"event_id": "public-assign-free-001", "device_id": "pi-public-assign", "sensor_id": "L1", "space_id": "L1", "event_type": "bay_free"}
    assert client.post("/api/iot/events", headers=headers, json=free).status_code == 200
    result = client.post("/api/public/assign-visitor-bay", json={"space_id": "L1"})
    assert result.status_code == 200
    assert result.json()["status"] == "ASSIGNED"
    visitor_headers = {"x-visitor-token": result.json()["visitor_token"]}
    assert client.get("/api/v1/visitor/me", headers=visitor_headers).json()["assignment"]["space_id"] == "L1"
    occupied = {**free, "event_id": "public-assign-occupied-001", "event_type": "bay_occupied"}
    assert client.post("/api/iot/events", headers=headers, json=occupied).status_code == 200
    assert client.get("/api/v1/visitor/me", headers=visitor_headers).json()["status"] == "PARKED"


def test_open_staff_interface_no_login_and_iot_still_requires_device_token():
    import app.main as main_module
    main_module.OPEN_STAFF_INTERFACE = True
    assert client.get("/api/v1/security/lot").status_code == 200
    assert client.get("/api/v1/admin/analytics").status_code == 200
    assert client.post("/api/iot/events", json={"event_id": "device-auth-required", "device_id": "forged", "sensor_id": "L1", "space_id": "L1", "event_type": "bay_occupied"}).status_code == 401
    main_module.OPEN_STAFF_INTERFACE = False


def test_open_staff_routes_skip_login_but_device_event_auth_remains():
    import app.main as main_module
    main_module.OPEN_STAFF_INTERFACE = True
    assert client.get("/api/v1/security/lot").status_code == 200
    assert client.get("/api/v1/admin/analytics").status_code == 200
    assert client.post("/api/iot/events", json={"event_id": "no-device-token-01", "device_id": "forged", "sensor_id": "L1", "space_id": "L1", "event_type": "bay_occupied"}).status_code == 401
    main_module.OPEN_STAFF_INTERFACE = False


def test_direct_staff_interface_and_public_sensor_availability():
    import app.main as main_module
    main_module.OPEN_STAFF_INTERFACE = True
    assert client.get("/api/open/interface").json() == {"enabled": True, "role": "ADMIN"}
    initial = client.get("/api/public/live-availability")
    assert initial.status_code == 200
    assert [space["id"] for space in initial.json()["spaces"]] == ["L1", "L2"]
    assert client.get("/api/v1/admin/site").status_code == 200
    assert client.get("/api/v1/admin/config").status_code == 200
    assert client.get("/api/v1/admin/tariff").status_code == 200
    assert client.post("/api/v1/admin/login", json={"email": "anyone@example.test", "password": "unused"}).json()["access_token"] == "open-interface"
    main_module.OPEN_STAFF_INTERFACE = False


def test_visitor_cannot_read_admin_lot():
    import app.main as main_module
    main_module.OPEN_STAFF_INTERFACE = False
    assert client.get("/api/v1/security/lot").status_code == 401


def test_public_entry_and_qr_are_safe_but_admin_qr_is_protected():
    import app.main as main_module
    main_module.OPEN_STAFF_INTERFACE = False
    entry = client.get("/api/public/sites/default/entry")
    assert entry.status_code == 200
    assert entry.json()["entry_url"] == "https://parking.example.test/visit?facility=default"
    assert "token=" not in entry.json()["entry_url"]
    assert client.get("/api/public/sites/default/qr?format=svg").headers["content-type"].startswith("image/svg+xml")
    assert client.get("/api/v1/admin/sites/default/qr?format=png").status_code == 401


def test_newsletter_requires_explicit_consent_normalizes_and_unsubscribes():
    assert client.get("/api/public/facility").json()["timezone"] == "Africa/Accra"
    no_consent = client.post("/api/public/newsletter/subscriptions", json={"email": "reader@example.test", "consent": False})
    assert no_consent.status_code == 422
    invalid = client.post("/api/public/newsletter/subscriptions", json={"email": "not-an-email", "consent": True})
    assert invalid.status_code == 422
    result = client.post("/api/public/newsletter/subscriptions", json={"email": " Reader@Example.test ", "consent": True, "source": "footer"})
    assert result.status_code == 200
    assert result.json()["message"] == "Subscription received."
    token = result.json()["unsubscribe_token"]
    duplicate = client.post("/api/public/newsletter/subscriptions", json={"email": "reader@example.test", "consent": True})
    assert duplicate.status_code == 200
    assert duplicate.json()["message"] == result.json()["message"]
    assert "unsubscribe_token" not in duplicate.json()
    assert client.post("/api/public/newsletter/unsubscribe", json={"token": token}).status_code == 200
    with connect() as db:
        row = db.execute("SELECT email, unsubscribed_at FROM newsletter_subscriptions").fetchone()
    assert row["email"] == "reader@example.test"
    assert row["unsubscribed_at"] is not None


def test_unknown_site_cannot_create_session():
    assert client.post("/api/visitor/sessions?site_id=unknown").status_code == 404


def test_public_facility_qr_opens_map_route_without_driver_code():
    entry = client.get("/api/public/sites/default/entry").json()["entry_url"]
    assert entry.endswith("/visit?facility=default")
    one = client.post("/api/v1/visitor/sessions?site_id=default").json()
    two = client.post("/api/v1/visitor/sessions?site_id=default").json()
    assert one["session_id"] != two["session_id"]
    assert "arrival_code" not in one
    with connect() as db:
        row = db.execute("SELECT checkpoint_id FROM visitor_sessions WHERE id = ?", (one["session_id"],)).fetchone()
    assert row["checkpoint_id"] is None


def test_checkpoint_replacement_requires_confirmation_and_revokes_old_qr():
    headers = {"authorization": f"Bearer {ADMIN_TOKEN}"}
    previous = client.get("/api/public/sites/default/entry").json()["entry_url"]
    blocked = client.post("/api/v1/admin/checkpoints", headers=headers, json={"name": "North gate"})
    assert blocked.status_code == 409
    created = client.post("/api/v1/admin/checkpoints", headers=headers, json={"name": "North gate", "confirm_revoke_previous": True})
    assert created.status_code == 201
    assert client.get("/api/public/sites/default/entry").json()["entry_url"] == previous
    assert created.json()["entry_url"] == previous


def test_guard_matches_arrival_without_driver_code_before_private_space_allocation():
    import app.main as main_module
    main_module.OPEN_STAFF_INTERFACE = False
    reset_default_lot()
    session = client.post("/api/v1/visitor/sessions").json()
    checked_in = client.post("/api/v1/visitor/arrivals", headers={"x-visitor-token": session["visitor_token"]})
    assert checked_in.json()["status"] == "WAITING_CONFIRMATION"
    assert "arrival_code" not in session
    assert client.post(f"/api/v1/security/arrivals/{session['session_id']}/match", json={}).status_code == 401
    with connect() as db:
        assert db.execute("SELECT COUNT(*) AS n FROM reservations WHERE session_id = ?", (session["session_id"],)).fetchone()["n"] == 0
    verified = client.post(f"/api/v1/security/arrivals/{session['session_id']}/match", headers={"authorization": f"Bearer {ADMIN_TOKEN}"}, json={})
    assert verified.status_code == 200
    assert verified.json()["space_id"] == "L1"


def test_single_sensor_arrival_auto_matches_at_five_centimeters_and_deduplicates():
    reset_default_lot()
    visitor = client.post("/api/v1/visitor/sessions").json()
    payload = {"event_id": "approach-single-0001", "device_id": "pi-test", "sensor_id": "entrance", "event_type": "vehicle_approaching", "distance_cm": 5, "facility_id": "default"}
    headers = {"x-device-token": "change-device-token-in-production"}
    matched = client.post("/api/v1/iot/events", headers=headers, json=payload)
    assert matched.status_code == 200
    assert matched.json()["status"] == "auto_matched"
    assert matched.json()["match"]["session_id"] == visitor["session_id"]
    assert client.post("/api/v1/iot/events", headers=headers, json=payload).json()["status"] == "duplicate"


def test_ambiguous_sensor_arrival_requires_guard_selection():
    reset_default_lot()
    first = client.post("/api/v1/visitor/sessions").json()
    second = client.post("/api/v1/visitor/sessions").json()
    payload = {"event_id": "approach-ambiguous-0001", "device_id": "pi-test", "sensor_id": "entrance", "event_type": "vehicle_approaching", "distance_cm": 4, "facility_id": "default"}
    response = client.post("/api/v1/iot/events", headers={"x-device-token": "change-device-token-in-production"}, json=payload)
    assert response.json()["status"] == "guard_match_required"
    headers = {"authorization": f"Bearer {ADMIN_TOKEN}"}
    assert client.post(f"/api/v1/security/arrivals/{first['session_id']}/match", headers=headers, json={}).json()["status"] == "ASSIGNED"
    assert client.post(f"/api/v1/security/arrivals/{second['session_id']}/match", headers=headers, json={}).json()["status"] == "ASSIGNED"
    assert client.get("/api/v1/visitor/parking-layout", headers={"x-visitor-token": first["visitor_token"]}).json()["spaces"] != client.get("/api/v1/visitor/parking-layout", headers={"x-visitor-token": second["visitor_token"]}).json()["spaces"]


def test_free_time_billing_boundaries_use_started_ten_minute_blocks():
    with connect() as db:
        tariff = db.execute("SELECT * FROM tariffs WHERE site_id = 'default'").fetchone()
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    amounts = {}
    for duration in (29, 31, 40, 41):
        finish = start + timedelta(minutes=duration)
        minutes, amount = fee_quote(start.isoformat(), finish.isoformat(), tariff)
        assert minutes == duration
        amounts[duration] = amount
    assert amounts == {29: 0, 31: 200, 40: 200, 41: 400}


def test_admin_can_create_account_without_returning_password():
    payload = {"email": "officer@htu.edu", "display_name": "Kofi Mensah", "password": "strong-password-123", "role": "SECURITY", "ghana_card_number": "GHA-123456789-0", "phone_number": "0241234567"}
    response = client.post("/api/v1/admin/users", headers={"authorization": f"Bearer {ADMIN_TOKEN}"}, json=payload)
    assert response.status_code == 201
    assert response.json()["email"] == payload["email"]
    assert "password" not in response.json()
    assert "password_hash" not in response.json()
    assert client.post("/api/v1/admin/users", headers={"authorization": f"Bearer {ADMIN_TOKEN}"}, json=payload).status_code == 409
    assert client.get("/api/v1/admin/users", headers={"authorization": f"Bearer {ADMIN_TOKEN}"}).json()[0]["role"] == "SECURITY"


def test_admin_can_login_with_created_account():
    response = client.post("/api/v1/admin/login", json={"email": "officer@htu.edu", "password": "strong-password-123"})
    assert response.status_code == 200
    token = response.json()["access_token"]
    assert len(token) > 20
    assert client.get("/api/v1/admin/config", headers={"authorization": f"Bearer {token}"}).status_code == 403
    assert client.post("/api/v1/admin/login", json={"email": "officer@htu.edu", "password": "wrong-password-123"}).status_code == 401


def test_account_fields_reject_wrong_character_types():
    base = {"email": "typed@htu.edu", "display_name": "Kofi Mensah", "password": "strong-password-456", "role": "SECURITY", "ghana_card_number": "GHA-123456789-0", "phone_number": "0241234567"}
    invalid_card = {**base, "ghana_card_number": "123456789"}
    invalid_phone = {**base, "email": "typed2@htu.edu", "phone_number": "02412ABC67"}
    invalid_name = {**base, "email": "typed3@htu.edu", "display_name": "Kofi123"}
    headers = {"authorization": f"Bearer {ADMIN_TOKEN}"}
    assert client.post("/api/v1/admin/users", headers=headers, json=invalid_card).status_code == 422
    assert client.post("/api/v1/admin/users", headers=headers, json=invalid_phone).status_code == 422
    assert client.post("/api/v1/admin/users", headers=headers, json=invalid_name).status_code == 422


def test_guard_manual_payment_and_confirmed_exit_lifecycle():
    reset_default_lot()
    session = client.post("/api/v1/visitor/sessions").json()
    headers = {"x-visitor-token": session["visitor_token"]}
    allocation = arrive_and_verify(session).json()
    space_id = allocation["space_id"]
    assert client.post("/api/v1/simulator/events", headers={"authorization": f"Bearer {ADMIN_TOKEN}"}, json={"event_type": "space_occupied", "space_id": space_id}).status_code == 200
    # Put occupancy in the past to avoid a wall-clock wait in this workflow test.
    with connect() as db:
        occupied_at = (datetime.now(timezone.utc) - timedelta(minutes=41)).isoformat()
        db.execute("UPDATE reservations SET occupied_at = ? WHERE session_id = ?", (occupied_at, session["session_id"]))
    invoice = client.post(f"/api/v1/sessions/{session['session_id']}/request-exit", headers=headers).json()["invoice"]
    assert invoice["amount_minor"] > 0
    assert invoice["status"] == "PAYMENT_PENDING"
    security_headers = {"authorization": f"Bearer {ADMIN_TOKEN}"}
    assert client.post(f"/api/v1/security/sessions/{session['session_id']}/payment", headers=security_headers, json={"method": "CASH", "reference": "cash-test"}).json()["status"] == "PAID"
    with connect() as db:
        assert db.execute("SELECT status FROM parking_spaces WHERE id = ?", (space_id,)).fetchone()["status"] == "OCCUPIED"
    released = client.post(f"/api/v1/security/sessions/{session['session_id']}/exit", headers=security_headers, json={})
    assert released.status_code == 200
    with connect() as db:
        assert db.execute("SELECT status FROM parking_spaces WHERE id = ?", (space_id,)).fetchone()["status"] == "AVAILABLE"
        assert db.execute("SELECT status FROM visitor_sessions WHERE id = ?", (session["session_id"],)).fetchone()["status"] == "CLOSED"


def test_public_availability_exposes_only_status_and_total():
    response = client.get("/api/v1/sites/default/availability")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 4
    assert data["available"] + data["reserved"] + data["occupied"] + data["unavailable"] + data["out_of_service"] == 4
    assert all(set(space) == {"id", "status"} for space in data["spaces"])
    assert all("session" not in key and "visitor" not in key for space in data["spaces"] for key in space)
    assert client.get("/api/v1/sites/unknown/availability").status_code == 404


def test_concurrent_visitors_receive_distinct_left_to_right_spaces():
    site_id = "concurrent-acceptance"
    with connect() as db:
        db.execute("INSERT OR REPLACE INTO sites(id, name, address, latitude, longitude, setup_complete) VALUES (?, 'Test Facility', 'Test address', 5.5, -0.2, 1)", (site_id,))
        db.execute("DELETE FROM reservations WHERE space_id LIKE 'Q-%'")
        db.execute("DELETE FROM visitor_sessions WHERE site_id = ?", (site_id,))
        for space_id in ("Q-1", "Q-2", "Q-3", "Q-4"):
            db.execute("INSERT INTO parking_spaces(id, site_id, status, assigned_session, vehicle_id, updated_at) VALUES (?, ?, 'AVAILABLE', NULL, NULL, ?)", (space_id, site_id, datetime.now(timezone.utc).isoformat()))
    first = client.post(f"/api/v1/visitor/sessions?site_id={site_id}").json()
    second = client.post(f"/api/v1/visitor/sessions?site_id={site_id}").json()
    with ThreadPoolExecutor(max_workers=2) as executor:
        one, two = list(executor.map(arrive_and_verify, (first, second)))
    assert {one.json()["space_id"], two.json()["space_id"]} == {"Q-1", "Q-2"}


def test_visitor_layout_hides_other_visitors_reservation_identity():
    first = client.post("/api/v1/visitor/sessions").json()
    second = client.post("/api/v1/visitor/sessions").json()
    arrive_and_verify(first)
    arrive_and_verify(second)
    layout = client.get("/api/v1/visitor/parking-layout", headers={"x-visitor-token": first["visitor_token"]}).json()
    assert all("assigned_session" not in space for space in layout["spaces"])
    assert sum(space["is_mine"] for space in layout["spaces"]) == 1
    assert any(space["status"] == "RESERVED" and not space["is_mine"] for space in layout["spaces"])


def test_visitor_tokens_expire_and_roles_have_distinct_permissions():
    import app.main as main_module
    main_module.OPEN_STAFF_INTERFACE = False
    visitor = client.post("/api/v1/visitor/sessions").json()
    with connect() as db:
        db.execute("UPDATE visitor_sessions SET expires_at = ? WHERE id = ?", ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), visitor["session_id"]))
    assert client.get("/api/v1/visitor/me", headers={"x-visitor-token": visitor["visitor_token"]}).status_code == 401

    payload = {"email": "security-role@parliament.gh", "display_name": "Security Officer", "password": "secure-password-456", "role": "SECURITY", "ghana_card_number": "GHA-987654321-0", "phone_number": "0241234567"}
    created = client.post("/api/v1/admin/users", headers={"authorization": f"Bearer {ADMIN_TOKEN}"}, json=payload)
    assert created.status_code == 201
    login = client.post("/api/v1/admin/login", json={"email": payload["email"], "password": payload["password"]}).json()
    headers = {"authorization": f"Bearer {login['access_token']}"}
    assert client.get("/api/v1/security/lot", headers=headers).status_code == 200
    assert client.get("/api/v1/admin/users", headers=headers).status_code == 403
    assert client.patch("/api/v1/admin/config", headers=headers, json={"reservation_minutes": 3}).status_code == 403
    main_module.OPEN_STAFF_INTERFACE = True
