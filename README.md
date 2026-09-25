# SmartPark

Smart Park is a single-facility, mobile-first parking PWA operated by ParkTech. An administrator configures the facility name, logo, entrance coordinates, rates, staff, public HTTPS URL, and entrance QR. Drivers scan one permanent QR to open the map-first visitor flow without an account, app install, or arrival code.

## Project map

```text
src/                    React/Vite visitor PWA and security dashboard
	App.tsx               Visitor, GPS, QR scanner, open staff workspace, and dashboards
	api.ts                Typed REST/WebSocket client
	styles.css            Responsive visual system
	backend/app/main.py    FastAPI routes, SQLite persistence, QR generation, WebSockets, Pi-hosted web bundle
backend/tests/          API and validation tests
iot/                   Raspberry Pi hardware agent, simulator, and tests
public/                 PWA manifest, service worker, icons, printable poster
docker-compose.yml      Local API container and optional PostgreSQL service
```

More detail is available in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/API.md](docs/API.md).
The implemented scope, migration limits, and API demonstration flow are in [docs/MIGRATION-PLAN.md](docs/MIGRATION-PLAN.md).

## Quick start

### Frontend only

The development frontend uses demo mode when `VITE_API_URL` is unset.

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
- QR display: `http://localhost:5173/qr` (visitor entry, no sign-in required)
- Checkpoint QR setup and print: Admin **Settings** or Security **Checkpoint QR**
- Printable poster: `http://localhost:5173/poster.html`
- FastAPI docs: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`

### Tests and production build

```powershell
npm run build
python -m pytest backend/tests -q
```

The test suite covers allocation order, duplicate sensor events, privacy boundaries, public QR safety, direct staff access, two-bay assignment, passwordless visitor sessions, invalid sites, and field validation.

## Visitor flow

1. Open the visitor link or scan the entrance QR. The QR is an optional way to open the interface, not a sign-in requirement.
2. The browser creates a private anonymous session token; no username/password form is shown. The token ties one driver's assignment to the live parking timer and exit flow.
3. The visitor screen shows live L1/L2 sensor state, GPS directions to the configured facility, and the posted tariff.
4. A visitor can request a bay only after the backend confirms a recent physical-free state. Staff may also assign a confirmed-free bay from the Live bays dashboard.
5. The L1/L2 sensor reports occupancy to the backend, which starts the server-side timer for an assigned session and shows the bay as occupied.
6. At exit, the server calculates the charge. Staff records cash or other manual payment and confirms vehicle exit; a confirmed free sensor reading releases the bay.

GPS is requested only when the driver starts navigation. It cannot identify a driver or indoor bay. A physical Pi is not considered validated until tested with the actual deployment hardware.

## QR, PWA, and phone testing

Set `SMARTPARK_PUBLIC_ENTRY_URL` to the permanent HTTPS base URL for deployment (the local default is `http://localhost:5173`). The visitor QR points directly to `/visit?facility=default`; it is a public entry URL and does not use signed checkpoint tokens or a login. On a phone, use an HTTPS tunnel or LAN-reachable HTTPS address rather than localhost.

```text
https://your-domain/visit?facility=default
```

Public QR and metadata routes:

```text
GET /api/public/sites/default/entry
GET /api/public/sites/default/qr?format=svg
GET /api/public/sites/default/qr?format=png
GET /api/public/live-availability
GET /api/public/facility-config
POST /api/v1/visitor/sessions?site_id=default
POST /api/public/assign-visitor-bay
POST /api/v1/visitor/arrivals
```

For phone testing, set `VITE_API_URL` to an HTTPS API URL reachable by the phone and add the exact PWA origin to `SMARTPARK_CORS_ORIGINS`. Geolocation requires HTTPS (except localhost). The visitor page requests location only after the driver taps **Start live navigation**. GPS fixes stay in browser memory and are not sent to FastAPI. While navigation is active, `VITE_ROUTING_URL` (default OSRM demo router) receives the current and destination coordinates to calculate the road route, distance, and ETA; use a production routing provider for deployment. OpenStreetMap tile requests also go to their tile service. Provider failure falls back to the static destination and Google Maps link. Navigation stops when the visitor leaves the map or taps **Stop navigation**. Phone GPS cannot identify an individual bay.

The PWA manifest is `/manifest.webmanifest`; `/sw.js` caches only public shell assets. API responses, assignments, and occupancy data are never cached as current truth.

In Pi production mode, the built browser app uses same-origin API requests automatically. No computer-hosted Vite process or CORS setting is needed. For local development with a separate API set `VITE_API_URL=http://localhost:8000` in `.env.local`. For camera/geolocation from phones, use HTTPS or an HTTPS tunnel; a phone cannot reach a computer's `localhost`.

## IoT hardware module (Raspberry Pi 4)

### Raspberry Pi all-in-one deployment

The Pi runs the FastAPI backend, SQLite database, built React web app, and GPIO sensor agent. Staff and visitors open the same Pi URL from the local network; the browser uses same-origin API requests. `/admin` is direct-access/no-login as requested. Keep the Pi on a trusted LAN because every reachable staff action is open.

The physical agent uses BCM pins entrance TRIG 6/ECHO 13, servo 5, L1 TRIG 18/ECHO 17/red 27/green 22/buzzer 26, and L2 TRIG 21/ECHO 20/red 23/green 24/buzzer 19. Sensors are sampled sequentially, classified at 70 cm after debounce and hysteresis. Warning patterns begin below 15 cm and intensify below 5 cm. Startup readings reconcile both bays; stale/failed sensor health displays WAITING FOR SENSOR. Events use a durable JSONL outbox and the same event ID is retained through retries. The backend owns anonymous visitor assignment, parking timers, and GH₵1/minute billing.

The gate opens on a stable entrance approach inside 70 cm and closes only after at least three seconds open plus five consecutive valid readings beyond 80 cm. Missing/ambiguous echoes never count as clear. The pulse values (1.34 ms open and 1.06 ms closed, 50 Hz) are conservative starting values only; calibrate with the servo mechanically disconnected. A software gate opening is not authorization for a public-road barrier or a safety-rated vehicle gate. No Pi hardware has been tested here.

#### Install on Raspberry Pi OS 64-bit

Connect Pi to the same Wi-Fi/Ethernet LAN as phones and staff devices. On the Pi:

```bash
sudo apt update
sudo apt install -y git python3-venv python3-pip python3-gpiozero nodejs npm
git clone <your-smartpark-repository-url> ~/smartpark
cd ~/smartpark
npm ci
python3 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt requests RPi.GPIO
cp backend/.env.example backend/.env
```

Set `SMARTPARK_DATABASE_URL=sqlite:////home/<pi-user>/smartpark-data/smartpark.db`, `SMARTPARK_OPEN_STAFF_INTERFACE=true`, `SMARTPARK_DEVICE_TOKEN` to a private random device secret, `SMARTPARK_SITE_ID=default`, and `SMARTPARK_SIMULATOR_ENABLED=true` in `backend/.env`. Keep this token out of browser variables and source control. Build and launch from the repository root:

```bash
VITE_API_URL= npm run build
set -a; . backend/.env; set +a
.venv/bin/uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000
```

Find the Pi LAN address with `hostname -I`, for example `192.168.1.42`. From any device on that same LAN, open `http://192.168.1.42:8000/` for visitors or `/admin` for staff. Use `/visit?facility=default` for QR entry. Configure the facility name/address/coordinates under **Settings** and tariff under **Pricing**. Browser GPS requires HTTPS or localhost; a plain HTTP Pi LAN URL may show destination directions without live browser GPS. Add a trusted local HTTPS certificate/reverse proxy for phone GPS.

#### Start the sensor agent

Use the same environment as the backend so its device token matches:

```bash
set -a; . backend/.env; set +a
export SMARTPARK_API_URL=http://127.0.0.1:8000
export SMARTPARK_DEVICE_ID=pi-main-gate
export SMARTPARK_OUTBOX_PATH=/home/$USER/smartpark-data/iot-outbox.jsonl
sudo -E .venv/bin/python iot/sensor_agent.py
```

`sudo` is used only for direct GPIO access. Both processes and the persistent SQLite/outbox data should start automatically after reboot using systemd services; do not run the agent and server as competing processes that claim the same pins. `GET /api/bays` shows exactly L1 and L2; public bay assignment and staff **Assign L1/Assign L2** require a recent confirmed-free state, and an occupied state overrides assignment. A sensor cannot identify a driver. Sensor vacancy never bypasses staff cash-payment confirmation or the backend’s exit authorization.

#### Wiring and physical test sequence

Each HC-SR04 ECHO output is 5 V; install a resistor divider (for example 1 kΩ from Echo to GPIO and 2 kΩ from GPIO to ground) or a 3.3 V logic shifter. Join sensor ground to Pi ground. Never connect 5 V ECHO directly to a Pi GPIO. Use an external regulated supply sized for the servo stall current; connect servo supply ground to Pi ground, and power the servo from that supply, never the Pi 3.3 V pin or GPIO. Fit a common ground for every sensor/output. Use flyback/driver hardware where the buzzer or indicator load exceeds GPIO current limits.

1. Run `python iot/sensor_agent.py --standalone-test`; it does not import or touch GPIO.
2. With power off, verify every BCM wire/pin and ECHO divider. Disconnect the servo horn/linkage and test its external supply separately.
3. Run the Pi backend and `python iot/sensor_agent.py --simulator` to verify the app/backend workflow before connecting real sensors.
4. Connect one bay sensor at a time, check live health/availability at `/admin/bays`, and test repeated occupied/free readings at distances on both sides of 70 cm.
5. Confirm 15 cm and 5 cm warning patterns, disconnect/reconnect a sensor and ensure the UI becomes waiting then recovers.
6. Only after mechanically unloaded pulse measurements and end-stop calibration, reconnect the servo linkage. Verify 5 clear echoes are needed to close and a missing echo never closes the gate.
7. Test QR entry, GPS from an HTTPS origin, anonymous assignment, server-side timer/billing, cash payment, authorized exit, and sensor-confirmed release.

The standalone checks and simulator are software-only; real GPIO, ultrasonic ranging, voltage levels, warning hardware, servo movement, camera/GPS permissions, and full physical entrance/exit behavior remain to be tested on the Pi.

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
