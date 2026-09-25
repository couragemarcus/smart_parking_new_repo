# Current Smart Park scope

## Active interface

- The existing React/Vite visitor app and FastAPI backend are used; no parallel website is introduced.
- `/admin` opens directly without staff login when `SMARTPARK_OPEN_STAFF_INTERFACE=true` (the default). Keep this development configuration on a trusted LAN.
- The visitor QR points directly to `/visit?facility=default`. A browser gets a private passwordless visitor session; no signed QR credential or account is needed for the active visitor journey.
- Visitors see public live availability, can request a sensor-confirmed free bay, get opt-in GPS directions to the configured facility, and see their assigned bay and backend-calculated parking charges.
- Staff can assign or clear a demo assignment for L1/L2 only when the sensor reports a fresh free state. Physical occupancy overrides assignment.
- Staff can view the active session queue, record manual cash/offline payment, and confirm vehicle exit. The backend owns session timing, billing, and release authorization.
- The default tariff is GH₵1 per minute (100 pesewas per one-minute block, zero free minutes); staff can edit it in Pricing.

## Active Raspberry Pi MVP

`iot/sensor_agent.py` currently configures only the two bay HC-SR04 sensors: L1 TRIG BCM18/ECHO BCM17 and L2 TRIG BCM21/ECHO BCM20. It reads them sequentially, applies median filtering, debounce and hysteresis, reconciles startup state, reports authenticated idempotent transitions, and includes per-sensor health in device heartbeats. Failed network requests retain the original event ID in the local outbox.

The current two-bay loop intentionally leaves entrance TRIG6/ECHO13, servo GPIO5, LEDs, and buzzers untouched. Gate mechanics can be tested with the standalone software state-machine check, but the running two-bay Pi agent does not actuate the gate. No physical Raspberry Pi, sensor, servo, wiring, or LAN-to-hardware setup has been tested here.

Protect each HC-SR04 5 V ECHO output with a resistor divider or 3.3 V level shifter before connecting it to Pi GPIO. Do not power servos from Pi GPIO or its 3.3 V rail. Any later servo work needs an appropriate external regulated supply and shared ground.

## Verification

From the repository root on Windows:

```powershell
npm.cmd run build
.\.venv\Scripts\python.exe -m pytest backend/tests iot -q -p no:cacheprovider
.\.venv\Scripts\python.exe iot/sensor_agent.py --standalone-test
```

These checks exercise the frontend build, backend API tests, software sensor simulator tests, event retry logic, and gate state-machine logic without GPIO. They do not constitute physical hardware testing.
