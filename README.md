# SmartPark

Smart Park is a single-facility, mobile-first parking PWA operated by ParkTech. An administrator configures the facility name, logo, entrance coordinates, rates, staff, public HTTPS URL, and entrance QR. Drivers scan one permanent QR to open the map-first visitor flow without an account, app install, or arrival code.

## Project map

```text
src/                    React/Vite visitor PWA and security dashboard
	App.tsx               Visitor, GPS, QR scanner, admin login, and dashboards
	api.ts                Typed REST/WebSocket client
	styles.css            Responsive visual system
backend/app/main.py    FastAPI routes, SQLite models, auth, QR generation, WebSockets
backend/tests/          API and validation tests
iot/                   Raspberry Pi hardware agent, simulator, and tests
public/                 PWA manifest, service worker, icons, printable poster
docker-compose.yml      Local API container and optional PostgreSQL service
```

More detail is available in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/API.md](docs/API.md).
The implemented scope, migration limits, and API demonstration flow are in [docs/MIGRATION-PLAN.md](docs/MIGRATION-PLAN.md).

## Quick start

### Frontend only

The frontend runs in demo mode when `VITE_API_URL` is unset.

```powershell
npm install
npm run dev -- --host 0.0.0.0
```

Open `http://localhost:5173/visit?facility=default` or the port printed by Vite. The QR route opens the live map first. ParkTech staff can update the displayed parking lot name and location under **Parking lot setup**.

### Frontend with FastAPI

Create a Python environment and install the backend dependencies:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
cd ..
```

Run the API in one terminal:

```powershell
python -m uvicorn app.main:app --app-dir backend --reload --host 0.0.0.0 --port 8000
```

Copy the root frontend environment file and restart Vite:

```powershell
Copy-Item .env.example .env
npm run dev -- --host 0.0.0.0
```

The browser only receives `VITE_API_URL`; never put the device secret into a `VITE_*` variable. The `/admin` workspace opens directly without staff credentials. Set facility GPS coordinates in **Settings**.

Useful URLs:

- Visitor PWA: `http://localhost:5173/visit?facility=default`
- Admin portal: `http://localhost:5173/admin`
- QR display: `http://localhost:5173/qr` (uses the active signed checkpoint URL)
- Checkpoint QR setup and print: Admin **Settings** or Security **Checkpoint QR**
- Printable poster: `http://localhost:5173/poster.html`
- FastAPI docs: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`

### Tests and production build

```powershell
npm run build
python -m pytest backend/tests -q
```

The test suite covers allocation order, duplicate sensor events, privacy boundaries, public QR safety, invalid sites, admin account creation, login sessions, and field validation.

## Visitor flow

1. Scan the permanent main entrance QR. It opens `/enter/{signed-checkpoint-token}` in the phone browser; no in-app scanner or app install is required.
2. Each scan creates an isolated anonymous session, with a six-digit arrival code shown only to that visitor.
3. The welcome screen shows live availability, public space states, facility directions, posted GH₵ rates, and parking guidance. It does not promise a bay before assignment.
4. The driver checks in at the entrance. Security asks to see the arrival code in person and verifies it in **Visitor queue**.
5. The backend transaction assigns the first available bay in order (L1→L4); if full, the verified visitor waits for the next confirmed vacancy.
6. A private WebSocket update shows only that driver's assignment. The map pulses their reserved bay; the public layout hides other drivers' identities.
7. The visitor explicitly starts GPS navigation. The route is to the facility entrance, followed by the simple in-app bay diagram. GPS never identifies an indoor bay.
8. A configured bay sensor or authorized simulator confirms parking and starts the server-side timer. Exit billing, demo payment, staff authorization, and confirmed vacancy remain separate steps.

The 5 cm entrance sensor setting detects proximity only. It cannot identify a phone, visitor, or bay. Security verifies the driver's arrival code before allocation. A physical Pi is not considered validated until tested with the actual deployment hardware.

## Admin and account access

The staff workspace is currently configured for direct access with no login. All users who can access `/admin` can manage the facility. Staff account creation and role-based access are not used in this mode.

- Alphabetic name characters plus spaces, apostrophes, or hyphens
- Valid email address
- Ghana Card format `GHA-123456789-0`
- Exactly 10 phone digits
- Password of at least 12 characters
- Role: `SECURITY`, `ADMIN`, or `MANAGER`

The backend stores PBKDF2 password hashes and a SHA-256 Ghana Card hash plus last four digits. It never returns passwords or full card numbers. The bootstrap token is for local setup only and must be replaced in production.

## QR, PWA, and phone testing

Set `SMARTPARK_PUBLIC_ENTRY_URL` to the permanent HTTPS base URL for deployment (the local default is `http://localhost:5173`). On a phone, use an HTTPS tunnel or LAN-reachable HTTPS address rather than localhost. The admin creates a signed checkpoint QR from **Settings**; replacing it requires a deliberate confirmation and invalidates the old QR. The QR does not identify the driver:

```text
https://your-domain/enter/<checkpoint-id>.<signature>
```

Public QR and metadata routes:

```text
GET /api/public/sites/default/entry
GET /api/public/sites/default/qr?format=svg
GET /api/public/sites/default/qr?format=png
GET /api/v1/public/checkpoints/{signed-token}
POST /api/v1/visitor/sessions?checkpoint_token={signed-token}
POST /api/v1/visitor/arrivals
POST /api/v1/security/arrivals/{session_id}/verify
```

For phone testing, set `VITE_API_URL` to an HTTPS API URL reachable by the phone and add the exact PWA origin to `SMARTPARK_CORS_ORIGINS`. Geolocation requires HTTPS (except localhost). The visitor page requests location only after the driver taps **Start live navigation**. GPS fixes stay in browser memory and are not sent to FastAPI. While navigation is active, `VITE_ROUTING_URL` (default OSRM demo router) receives the current and destination coordinates to calculate the road route, distance, and ETA; use a production routing provider for deployment. OpenStreetMap tile requests also go to their tile service. Provider failure falls back to the static destination and Google Maps link. Navigation stops when the visitor leaves the map or taps **Stop navigation**. Phone GPS cannot identify an individual bay.

The PWA manifest is `/manifest.webmanifest`; `/sw.js` caches only public shell assets. API responses, assignments, and occupancy data are never cached as current truth.

For a phone on the same network, bind both services to `0.0.0.0` and replace `localhost` with the computer's LAN IP. For camera and geolocation features, use HTTPS in production or an HTTPS tunnel during testing. A phone cannot reach the computer's `localhost`.

## IoT hardware module (Raspberry Pi 4)

### Two-bay live occupancy MVP

The open staff dashboard is at `/admin`; no staff or visitor login is required. **Live bays** shows L1 and L2 as **AVAILABLE**, **OCCUPIED**, **ASSIGNED TO DRIVER**, or **WAITING FOR SENSOR**. It polls `GET /api/bays` every second. Assignment requires a recent confirmed-free sensor reading. A physical occupied reading overrides an assignment. This marker does not identify a driver. Open staff mode grants administrative actions to anyone who can reach the app, so keep it on a trusted LAN or disable it before public deployment.

The attached `smartpark_live_mvp.zip` reference files were not present in this workspace, so the implementation uses the current FastAPI/React stack. Device events post to `POST /api/iot/events`; `GET /api/bays` returns the live two-bay state. Sensor transitions are debounced, readings are sequential, boot reconciles initial sensor state, and failed event posts are queued/retried with the original event ID. This phase only initializes L1/L2 sensors. Existing entrance, servo, LEDs, and buzzers are not configured by the MVP loop.

For local simulation, start FastAPI and set a provisioned (or local development) `SMARTPARK_DEVICE_TOKEN`, then run:

```powershell
$env:SMARTPARK_API_URL = 'http://127.0.0.1:8000'
$env:SMARTPARK_DEVICE_TOKEN = 'change-device-token-in-production'
$env:SMARTPARK_DEVICE_ID = 'pi-two-bay-demo'
python iot/sensor_agent.py --simulator
```

Enter distances at the L1/L2 prompts. `60` represents occupied and `100` represents free. Open the staff app at `http://localhost:5173/admin/bays` directly. The web simulator controls can also mark the sensor state through `POST /api/iot/demo-events`; use the Pi simulator to exercise authenticated device events.

For a Pi and development computer on the same LAN:

1. Find the computer's LAN IPv4 address with `ipconfig` (Windows) or `ip addr` (Linux), such as `192.168.1.20`.
2. Start the backend bound to the LAN interface: `python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000`.
3. Set the web app's `VITE_API_URL=http://192.168.1.20:8000` in the root `.env.local`, then restart Vite with `npm run dev -- --host 0.0.0.0`.
4. Allow inbound TCP port 8000 on the computer's private LAN firewall profile. Ensure both devices are on the same non-guest network and client isolation is disabled.
5. On the Pi, set `SMARTPARK_API_URL=http://192.168.1.20:8000`, `SMARTPARK_DEVICE_ID`, and `SMARTPARK_DEVICE_TOKEN`. Install `requests` and `RPi.GPIO`, then run `python iot/sensor_agent.py`.

The direct staff web interface intentionally has no login. Keep the development app and API on a trusted network; switch `SMARTPARK_OPEN_STAFF_INTERFACE=false` and deploy the existing account flow before exposing the service to an untrusted/public network. The Pi still uses a device token so public visitors cannot forge physical readings.

The example uses plain HTTP for a trusted local demonstration network only. Use HTTPS and a reachable protected API for deployment beyond that network. Protect each 5 V HC-SR04 ECHO line with a resistor divider or 3.3 V level shifter. No physical Pi was tested for this implementation.

The sensor agent supports a software simulator and HC-SR04 sensors on Raspberry Pi OS. It connects to the same FastAPI instance configured by `SMARTPARK_API_URL`; with `VITE_API_URL` set to that backend, accepted device events feed the existing web app availability and operations updates. BCM wiring is fixed in `iot/sensor_agent.py`: entrance TRIG 6/ECHO 13; servo signal GPIO 5; L1 TRIG 18/ECHO 17 with red 27, green 22, buzzer 26; L2 TRIG 21/ECHO 20 with red 23, green 24, buzzer 19. Detection thresholds are 70 cm, close warning below 15 cm, and too-close below 5 cm. Each HC-SR04 ECHO is commonly 5V; use a divider/level shifter before Pi GPIO. Power the servo from a separate regulated supply sized for its stall current, and connect that supply ground to Pi ground. Do not power a servo from a Pi GPIO pin or 3.3V rail. Remove the gate linkage during initial pulse calibration.

```powershell
$device = @{ device_id='pi-main-gate'; name='Main gate sensor' } | ConvertTo-Json
Invoke-RestMethod -Method Post http://localhost:8000/api/v1/admin/devices -Headers @{ Authorization='Bearer <admin-access-token>' } -ContentType 'application/json' -Body $device
```

Revoke a credential with `POST /api/v1/admin/devices/{device_id}/revoke`. Keep the legacy `SMARTPARK_DEVICE_TOKEN` compatibility credential unset in production after all agents have their own provisioned tokens.

```bash
python iot/sensor_agent.py --standalone-test
python iot/sensor_agent.py --simulator
```

The standalone check exercises the gate state machine without importing GPIO; the simulator currently reports sensor values and heartbeat to the backend. It does not validate hardware. For the real device install `requests` and `RPi.GPIO`, provision a unique device token, and set its environment:

```bash
python -m venv .venv
. .venv/bin/activate
pip install requests RPi.GPIO
export SMARTPARK_API_URL=https://your-api.example.com
export SMARTPARK_DEVICE_ID=pi-main-gate
export SMARTPARK_DEVICE_TOKEN=replace-with-provisioned-token
export SMARTPARK_SITE_ID=default
python iot/sensor_agent.py
```

Before attaching the linkage, run the standalone safe test. Then run the Pi agent with the servo arm disconnected, confirm the direction and limited travel, adjust the conservative 1.0–1.4 ms end-stop pulse candidates to the specific servo, and reattach only after the arm clears both stops. Keep people clear of the mechanism. Test each ultrasonic sensor independently, verify ECHO voltage reduction, and confirm the gate stays open through missing/isolated readings and closes only after sustained clear readings. Confirm backend assignment, occupied timer, authorized cash payment and exit through the existing QR → GPS → arrival → parking → cash payment → exit workflow. Raspberry Pi hardware has not been tested as part of this change.

```bash
Bay occupancy events only start a timer for a backend-reserved bay; vacancy events release bays only after the backend has authorized exit. The backend owns assignment and billing. The gate/servo code is not safety-rated and must not be the sole protection for a vehicle or pedestrian barrier.

## Configuration

Backend variables are documented in [backend/.env.example](backend/.env.example). Frontend variables are documented in [.env.example](.env.example). SQLite is the default local database. PostgreSQL can be selected through `SMARTPARK_DATABASE_URL`; multi-worker WebSocket fan-out should add Redis pub/sub before production scaling.

## Verification checklist

1. Open `/admin` directly and set the facility name, address, GPS coordinates, and tariff.
2. Start the sensor simulator and confirm L1/L2 move between unknown, available, assigned, and occupied.
3. Open the public visitor interface and use GPS directions to the configured facility entrance.
4. Scan `/qr` from iPhone or Android and confirm it opens the no-login visitor page.
5. Assign a bay only after the sensor reports that bay free. Confirm physical occupancy overrides its assignment marker.
6. Run the staff simulator to exercise arrival, occupancy, cash payment recording, and exit. Physical Pi behavior still requires hardware checks.

## Public footer and newsletter

The public visitor footer links to `/help`, `/faq`, `/contact`, `/privacy`, `/terms`, and `/accessibility`. The facility profile in the owner settings controls its public display name, address, contact email, phone, and HTTPS social profile URLs. Unconfigured social links are omitted. Schema additions are applied by `init_db()` on backend startup.

`POST /api/public/newsletter/subscriptions` stores a normalized email only with explicit consent, applies a per-IP rate limit and honeypot, and returns a neutral receipt. The local installation has no mailing provider, so it stores opt-ins but does not send email. `POST /api/public/newsletter/unsubscribe` accepts the opaque unsubscribe token returned to the subscribing browser. Configure a mail provider before promising delivery or sending campaigns; document retention and email verification when adding that integration.

Newsletter API and persistence tests are included in `backend/tests/test_api.py`. Local verification commands:

```powershell
npm.cmd run build
.\.venv\Scripts\python.exe -m pytest backend -p no:cacheprovider
```
