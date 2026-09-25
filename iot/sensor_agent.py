"""SmartPark Raspberry Pi sensor agent.

Software controls a hobby gate servo; it is not a safety-rated barrier. Use an
external regulated servo supply and join its ground to Pi ground. HC-SR04 ECHO
is 5 V and must pass through a divider/level shifter before 3.3 V GPIO.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import statistics
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable

@dataclass(frozen=True)
class Settings:
    api_url: str = os.getenv("SMARTPARK_API_URL", "http://localhost:8000")
    device_token: str = os.getenv("SMARTPARK_DEVICE_TOKEN", "")
    device_id: str = os.getenv("SMARTPARK_DEVICE_ID", "pi-main-gate")
    facility_id: str = os.getenv("SMARTPARK_SITE_ID", "default")
    gate_threshold_cm: float = 70.0
    bay_threshold_cm: float = 70.0
    warning_cm: float = 15.0
    too_close_cm: float = 5.0
    retry_seconds: float = 1.0
    queue_path: str = os.getenv("SMARTPARK_OUTBOX_PATH", "smartpark_iot_outbox.jsonl")


@dataclass(frozen=True)
class SensorPins:
    trigger: int
    echo: int


@dataclass(frozen=True)
class BayPins:
    sensor: SensorPins
    red: int
    green: int
    buzzer: int


ENTRANCE = SensorPins(6, 13)
SERVO_PIN = 5
BAYS = {"L1": BayPins(SensorPins(18, 17), 27, 22, 26),
        "L2": BayPins(SensorPins(21, 20), 23, 24, 19)}


class DistanceSensor:
    def read_cm(self) -> float | None:
        raise NotImplementedError


class SimulatorSensor(DistanceSensor):
    """Controllable deterministic input, also useful for --standalone-test."""
    def __init__(self, distance_cm: float = 100.0):
        self.distance_cm = distance_cm

    def read_cm(self) -> float:
        return self.distance_cm


class InteractiveSimulatorSensor(SimulatorSensor):
    def __init__(self, bay_id: str):
        super().__init__(100.0)
        self.bay_id = bay_id
        self.commands = collections.deque()
        threading.Thread(target=self._read_commands, daemon=True).start()

    def _read_commands(self) -> None:
        while True:
            command = input(f"{self.bay_id} distance cm (or q): ").strip().lower()
            if command == "q":
                return
            try:
                self.commands.append(float(command))
            except ValueError:
                print("Enter a distance in centimeters.")

    def read_cm(self) -> float:
        if self.commands:
            self.distance_cm = self.commands.popleft()
        return self.distance_cm


class SimulatorOutput:
    """Console output adapter for gate/indicator logic without GPIO."""
    def __init__(self):
        self.gate_open = False

    def gate(self, opened: bool) -> None:
        self.gate_open = opened
        print(f"GATE {'OPEN' if opened else 'CLOSED'} (simulated)")

    def bay(self, pins: BayPins, occupied: bool, distance: float | None) -> None:
        pass


class Hcsr04Sensor(DistanceSensor):
    def __init__(self, trigger_pin: int, echo_pin: int, gpio=None):
        if gpio is None:
            try:
                import RPi.GPIO as gpio
            except ImportError as error:
                raise RuntimeError("RPi.GPIO is required on Raspberry Pi OS") from error
        self.GPIO, self.trigger_pin, self.echo_pin = gpio, trigger_pin, echo_pin
        gpio.setmode(gpio.BCM)
        gpio.setup(trigger_pin, gpio.OUT, initial=gpio.LOW)
        gpio.setup(echo_pin, gpio.IN)

    def read_cm(self) -> float | None:
        gpio = self.GPIO
        gpio.output(self.trigger_pin, gpio.LOW)
        time.sleep(0.000002)
        gpio.output(self.trigger_pin, gpio.HIGH)
        time.sleep(0.00001)
        gpio.output(self.trigger_pin, gpio.LOW)
        deadline = time.monotonic() + 0.04
        while not gpio.input(self.echo_pin):
            if time.monotonic() >= deadline:
                return None
        start = time.monotonic()
        while gpio.input(self.echo_pin):
            if time.monotonic() >= deadline:
                return None
        return (time.monotonic() - start) * 17150


class EventClient:
    def __init__(self, settings: Settings, session=None):
        self.settings = settings
        if session is None:
            try:
                import requests
            except ImportError as error:
                raise RuntimeError("requests is required for backend connectivity") from error
            session = requests.Session()
        self.session = session
        self.session.headers.update({"x-device-token": settings.device_token, "x-device-id": settings.device_id})
        self.outbox = collections.deque()
        try:
            with open(settings.queue_path, encoding="utf-8") as queue_file:
                self.outbox.extend(json.loads(line) for line in queue_file if line.strip())
        except FileNotFoundError:
            pass
        self.last_retry_at = 0.0
        self.last_heartbeat_retry = 0.0

    def _save_outbox(self) -> None:
        path = os.path.abspath(self.settings.queue_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as queue_file:
            for queued_event in self.outbox:
                queue_file.write(json.dumps(queued_event) + "\n")

    def event(self, event_type: str, sensor_id: str, space_id: str | None = None,
              distance_cm: float | None = None, payload: dict | None = None) -> dict:
        self.enqueue(event_type, sensor_id, space_id, distance_cm, payload)
        event_id = self.outbox[-1]["event_id"]
        self.flush()
        return {"status": "queued", "event_id": event_id}

    def heartbeat(self, sensor_health: dict[str, bool] | None = None) -> None:
        """Report device liveness and which bay sensors returned valid readings."""
        if time.monotonic() - self.last_heartbeat_retry < self.settings.retry_seconds:
            return
        try:
            self.session.post(
                f"{self.settings.api_url}/api/v1/iot/heartbeat?device_id={self.settings.device_id}",
                json={"sensors": sensor_health or {}}, timeout=5,
            ).raise_for_status()
            self.last_heartbeat_retry = 0.0
        except Exception:
            # The persistent event outbox remains the reconnect path.
            self.last_heartbeat_retry = time.monotonic()
            return

    def enqueue(self, event_type: str, sensor_id: str, space_id: str | None = None,
                distance_cm: float | None = None, payload: dict | None = None) -> None:
        """Persist an event without blocking the sampling loop on a LAN outage."""
        event = {"event_id": str(uuid.uuid4()), "device_id": self.settings.device_id,
                 "sensor_id": sensor_id, "event_type": event_type,
                 "space_id": space_id, "distance_cm": distance_cm,
                 "facility_id": self.settings.facility_id, "payload": payload or {}}
        self.outbox.append(event)
        self._save_outbox()

    def flush(self) -> None:
        while self.outbox:
            if time.monotonic() - self.last_retry_at < self.settings.retry_seconds:
                return
            try:
                response = self.session.post(f"{self.settings.api_url}/api/iot/events", json=self.outbox[0], timeout=3)
                response.raise_for_status()
                self.outbox.popleft()
                self._save_outbox()
                self.last_retry_at = 0.0
            except Exception:
                self.last_retry_at = time.monotonic()
                return


class StableClassifier:
    """Median filtering, consecutive-sample debounce, and hysteresis."""
    def __init__(self, threshold_cm: float, confirm_samples: int = 3,
                 hysteresis_cm: float = 5, window: int = 5):
        self.threshold = threshold_cm
        self.confirm_samples = confirm_samples
        self.hysteresis = hysteresis_cm
        self.window = window
        self.samples: list[float] = []
        self.state: bool | None = None
        self.candidate: bool | None = None
        self.count = 0
        self.healthy = False

    def update(self, distance: float | None) -> tuple[bool | None, float | None]:
        if distance is None or distance < 0:
            self.healthy = False
            self.candidate = None
            self.count = 0
            self.samples = []
            return None, statistics.median(self.samples) if self.samples else None
        self.healthy = True
        if self.state is not None and self.candidate == self.state:
            self.samples = [distance]
        else:
            self.samples = (self.samples + [distance])[-self.window:]
        filtered = statistics.median(self.samples)
        if self.state is None:
            proposed = filtered <= self.threshold
        elif self.state is True:
            proposed = False if filtered > self.threshold + self.hysteresis else True
        else:
            proposed = filtered <= self.threshold
        if proposed != self.candidate:
            self.candidate, self.count = proposed, 1
        else:
            self.count += 1
        if self.count >= self.confirm_samples and self.state != proposed:
            self.state = proposed
            return self.state, filtered
        return None, filtered


class GateStateMachine:
    """Opens after confirmed approach and never closes from one bad reading."""
    def __init__(self, emit: Callable[[str], None], close_clear_samples: int = 5,
                 minimum_open_seconds: float = 3.0):
        self.emit = emit
        self.is_open = False
        self.clear_count = 0
        self.minimum_open_seconds = minimum_open_seconds
        self.opened_at = 0.0
        self.close_clear_samples = close_clear_samples

    def update(self, transition: bool | None, distance: float | None, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        if not self.is_open:
            if transition is True:
                self.is_open, self.opened_at, self.clear_count = True, moment, 0
                self.emit("gate_opened")
            return
        if distance is not None and distance > 80:
            self.clear_count += 1
        else:
            self.clear_count = 0
        if self.clear_count >= self.close_clear_samples and moment - self.opened_at >= self.minimum_open_seconds:
            self.is_open, self.clear_count = False, 0
            self.emit("gate_closed")


def standalone_servo_test() -> None:
    """Safe no-hardware test of configured end-stop pulses and gate transitions."""
    pulses = (1.0, 1.1, 1.2, 1.3, 1.4, 1.3, 1.2, 1.1, 1.0)
    assert all(0.8 <= pulse <= 2.0 for pulse in pulses)
    events: list[str] = []
    gate = GateStateMachine(events.append, minimum_open_seconds=0)
    classifier = StableClassifier(70, confirm_samples=3, window=1)
    for distance in (100, 100, 100, 60, 60, 60):
        transition, filtered = classifier.update(distance)
        gate.update(transition, filtered, now=1)
    assert events == ["gate_opened"]
    # A single clear sample and missing echoes must not close the gate.
    gate.update(None, 100, now=2)
    gate.update(None, None, now=2)
    assert events == ["gate_opened"]
    for _ in range(5):
        gate.update(None, 100, now=3)
    assert events == ["gate_opened", "gate_closed"]
    print("Standalone servo/gate logic passed (simulated only; no GPIO touched).")
    print("Pulse sweep candidate: 1.0–1.4 ms at 50 Hz; trim to the mechanism before loading.")


class PiOutputs:
    def __init__(self, gpio):
        self.GPIO = gpio
        gpio.setmode(gpio.BCM)
        for bay in BAYS.values():
            for pin in (bay.red, bay.green, bay.buzzer):
                gpio.setup(pin, gpio.OUT, initial=gpio.LOW)
        self.servo = gpio.PWM(SERVO_PIN, 50)
        self.servo.start(0)
        self.gate_open = False

    def gate(self, opened: bool) -> None:
        if self.gate_open == opened:
            return
        # Conservative pulses, never drive mechanical end stops without calibration.
        self.servo.ChangeDutyCycle(6.7 if opened else 5.3)
        time.sleep(0.35)
        self.servo.ChangeDutyCycle(0)
        self.gate_open = opened

    def bay(self, pins: BayPins, occupied: bool, distance: float | None) -> None:
        too_close = distance is not None and distance < 5
        blink = int(time.monotonic() * 4) % 2 == 0
        self.GPIO.output(pins.red, self.GPIO.HIGH if occupied and (not too_close or blink) else self.GPIO.LOW)
        self.GPIO.output(pins.green, self.GPIO.LOW if occupied else self.GPIO.HIGH)
        warning = distance is not None and distance < 15
        if too_close:
            buzzer = int(time.monotonic() * 8) % 2 == 0
        elif warning:
            buzzer = int(time.monotonic() * 2) % 2 == 0
        else:
            buzzer = False
        self.GPIO.output(pins.buzzer, self.GPIO.HIGH if buzzer else self.GPIO.LOW)


def run(settings: Settings, simulator: bool = False, poll_seconds: float = 0.2) -> None:
    if not settings.device_token:
        raise RuntimeError("Set SMARTPARK_DEVICE_TOKEN in the Pi backend and sensor service environment")
    client = EventClient(settings)
    if simulator:
        sensors = {"entrance": InteractiveSimulatorSensor("entrance"), **{key: InteractiveSimulatorSensor(key) for key in BAYS}}
        outputs = SimulatorOutput()
    else:
        import RPi.GPIO as GPIO
        sensors = {"entrance": Hcsr04Sensor(ENTRANCE.trigger, ENTRANCE.echo), **{key: Hcsr04Sensor(p.sensor.trigger, p.sensor.echo) for key, p in BAYS.items()}}
        outputs = PiOutputs(GPIO)
    classifiers = {key: StableClassifier(settings.bay_threshold_cm) for key in BAYS}
    entrance_classifier = StableClassifier(settings.gate_threshold_cm, hysteresis_cm=10)
    sensor_health = {"entrance": False, **{key: False for key in BAYS}}
    last_heartbeat = 0.0
    last_entrance_event = 0.0
    gate_machine = GateStateMachine(lambda event_type: (outputs.gate(event_type == "gate_opened"), client.enqueue(event_type, "entrance")), close_clear_samples=5, minimum_open_seconds=3.0)
    last_state: dict[str, bool | None] = {key: None for key in BAYS}
    try:
        # Reconcile startup after GPIO initialization and use persistent event IDs.
        for key, sensor in sensors.items():
            if key == "entrance":
                continue
            readings = []
            for _ in range(7):
                reading = sensor.read_cm()
                if reading is not None:
                    readings.append(reading)
                time.sleep(0.06)
            if len(readings) >= 3:
                sensor_health[key] = True
                state = statistics.median(readings[-5:]) <= settings.bay_threshold_cm
                classifiers[key].state = state
                classifiers[key].candidate = state
                classifiers[key].count = classifiers[key].confirm_samples
                classifiers[key].healthy = True
                last_state[key] = state
                client.enqueue("bay_occupied" if state else "bay_vacant", key, key)
        while True:
            now = time.monotonic()
            if now - last_heartbeat >= 30:
                sensor_health.update({key: classifiers[key].healthy for key in BAYS})
                client.enqueue("device_heartbeat", settings.device_id, payload={"sensors": sensor_health})
                client.heartbeat({key: classifiers[key].healthy for key in BAYS})
                last_heartbeat = now
            # Deliberately read sequentially to avoid HC-SR04 acoustic crosstalk.
            entrance_reading = sensors["entrance"].read_cm()
            sensor_health["entrance"] = entrance_reading is not None
            entrance_transition, entrance_filtered = entrance_classifier.update(entrance_reading)
            if entrance_transition is True and now - last_entrance_event >= 3:
                client.enqueue("vehicle_detected", "entrance", distance_cm=entrance_filtered)
                last_entrance_event = now
            gate_machine.update(entrance_transition, entrance_filtered, now)
            for name, sensor in sensors.items():
                if name == "entrance":
                    continue
                reading = sensor.read_cm()
                sensor_health[name] = reading is not None
                transition, filtered = classifiers[name].update(reading)
                if reading is None and classifiers[name].state is not None:
                    client.enqueue("sensor_error", name, name, payload={"reason": "echo_timeout"})
                    classifiers[name].state = None
                    client.flush()
                if transition is not None:
                    client.enqueue("bay_occupied" if transition else "bay_vacant", name, name, filtered)
                    if transition is False:
                        client.enqueue("space_vacant", name, name, filtered)
                    last_state[name] = transition
                bay_state = classifiers[name].state is True
                outputs.bay(BAYS[name], bay_state, filtered)
            client.flush()
            time.sleep(poll_seconds)
    finally:
        if not simulator:
            import RPi.GPIO as GPIO
            outputs.servo.stop()
            GPIO.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulator", action="store_true", help="run without GPIO hardware")
    parser.add_argument("--standalone-test", action="store_true", help="test servo/gate logic without GPIO")
    args = parser.parse_args()
    if args.standalone_test:
        standalone_servo_test()
    else:
        run(Settings(), simulator=args.simulator)


if __name__ == "__main__":
    main()
