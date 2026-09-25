"""Software-only tests for the Raspberry Pi agent; no GPIO imports required."""
from sensor_agent import EventClient, GateStateMachine, StableClassifier, standalone_servo_test, Settings


def test_classifier_debounces_and_hysteresis():
    classifier = StableClassifier(70, confirm_samples=3, hysteresis_cm=5)
    assert [classifier.update(value)[0] for value in (60, 60, 60)] == [None, None, True]
    assert [classifier.update(value)[0] for value in (73, 73, 73)] == [None, None, None]
    assert [classifier.update(value)[0] for value in (100, 100, 100)] == [None, None, False]


def test_gate_requires_multiple_clear_samples_and_tolerates_missing_echo():
    events = []
    gate = GateStateMachine(events.append, minimum_open_seconds=0)
    gate.update(True, 50, now=0)
    gate.update(None, 100, now=1)
    gate.update(None, None, now=1)
    assert events == ["gate_opened"]
    for _ in range(4):
        gate.update(None, 90, now=2)
    assert events == ["gate_opened"]
    gate.update(None, 90, now=2)
    assert events == ["gate_opened", "gate_closed"]


def test_gate_does_not_close_if_car_is_still_in_clearance_zone():
    events = []
    gate = GateStateMachine(events.append, minimum_open_seconds=0)
    gate.update(True, 50, now=0)
    for _ in range(7):
        gate.update(None, 78, now=10)
    assert events == ["gate_opened"]


def test_standalone_test_does_not_touch_gpio():
    standalone_servo_test()


def test_event_client_retries_same_idempotency_key(tmp_path):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "processed"}

    class Session:
        def __init__(self):
            self.headers = {}
            self.calls = []

        def post(self, url, json=None, timeout=5):
            self.calls.append((url, json.copy() if json else None, self.headers.copy()))
            if len(self.calls) == 1:
                raise OSError("temporary network failure")
            return Response()

    import sensor_agent
    original_sleep = sensor_agent.time.sleep
    sensor_agent.time.sleep = lambda _: None
    session = Session()
    try:
        client = EventClient(Settings(device_token="test", queue_path=str(tmp_path / "outbox.jsonl"), retry_seconds=0), session)
        response = client.event("bay_free", "L1", "L1")
        assert response["status"] == "queued"
        client.flush()
        assert session.calls[0][1]["event_id"] == session.calls[1][1]["event_id"]
        assert not (tmp_path / "outbox.jsonl").read_text(encoding="utf-8")

        session.calls.clear()
        client.heartbeat({"L1": False, "L2": True})
        heartbeat_url, heartbeat_body, heartbeat_headers = session.calls[-1]
        assert heartbeat_url.endswith("/api/v1/iot/heartbeat?device_id=pi-main-gate")
        assert heartbeat_body == {"sensors": {"L1": False, "L2": True}}
        assert heartbeat_headers["x-device-token"] == "test"
    finally:
        sensor_agent.time.sleep = original_sleep
