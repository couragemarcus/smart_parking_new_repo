# SmartPark API reference

FastAPI generates the complete interactive schema at `/docs` and `/openapi.json`.

## Headers

Visitor routes use:

```text
X-Visitor-Token: <opaque visitor token>
```

The staff interface is direct access by default (`SMARTPARK_OPEN_STAFF_INTERFACE=true`), so staff API routes do not require a login in this mode. If the flag is disabled, staff routes use the existing bearer-token account flow:

```text
Authorization: Bearer <admin token>
```

IoT routes use:

```text
X-Device-Token: <device token>
```

## Public routes

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/health` | Service health |
| `GET` | `/api/public/sites/{site_id}/entry` | Public QR entry metadata |
| `GET` | `/api/public/sites/{site_id}/qr?format=svg\|png` | Public reusable QR artifact |
| `GET` | `/api/v1/public/checkpoints/{signed_token}` | Legacy signed-checkpoint validation endpoint; the current public QR does not use it |
| `GET` | `/api/v1/sites/{site_id}/destination` | Configured coordinates, waypoints, Google Maps link |
| `GET` | `/api/v1/sites/{site_id}/availability` | Public totals and generic available/reserved/occupied states; no visitor identifiers |
| `GET` | `/api/public/live-availability` | Current two-bay sensor state for the visitor interface |
| `GET` | `/api/public/facility-config` | Public GPS destination coordinates |
| `GET` | `/api/v1/sites/{site_id}/tariff` | Publicly displayed facility rate |
| `GET` | `/api/parking/availability?site_id=default` | Compatibility availability route |

## Visitor routes

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/v1/visitor/sessions?site_id=default` | Create a private passwordless visitor session with an opaque token; rate limited |
| `GET` | `/api/v1/visitor/me` | Read own session, reservation, and invoice for refresh recovery |
| `POST` | `/api/public/visitor/assign` | Assign this visitor to a selected bay after recent sensor-confirmed vacancy |
| `POST` | `/api/public/assign-visitor-bay` | Create a passwordless session and reserve a sensor-confirmed free bay |
| `POST` | `/api/v1/visitor/arrivals` | Legacy arrival check-in route |
| `GET` | `/api/v1/visitor/parking-layout` | Privacy-safe layout with `is_mine` marker |
| `GET` | `/api/v1/visitor/assignment` | Read current private assignment |
| `POST` | `/api/v1/visitor/vehicle` | Store visitor-entered plate as hash plus last four characters |
| `GET` | `/api/v1/sessions/{id}/charges` | Private current estimate or final invoice |
| `POST` | `/api/v1/sessions/{id}/request-exit` | Snapshot tariff and calculate itemized session charge |
| `POST` | `/api/v1/invoices/{id}/demo-pay` | Idempotent simulated payment; never transfers money |
| `POST` | `/api/v1/sessions/{id}/authorize-exit` | Staff authorization after payment or manager/admin exemption |
| `WS` | `/api/v1/ws/visitor?token=...` | Private visitor events |

Compatibility aliases exist for `/api/visitor/sessions`, `/api/visitor/arrived`, and `/api/parking/destination`.
The current printed QR opens a plain `/visit?facility=default` URL. It does not require a signed checkpoint token. Opening the visitor app creates a private passwordless session; the opaque visitor token scopes assignment, timer, and billing state to that browser. Legacy signed-checkpoint routes remain available for compatibility but are not part of the normal visitor path.

## Admin and security routes

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/v1/admin/login` | Create an expiring admin session |
| `GET` | `/api/v1/admin/bootstrap/status` | Report whether the one-time first admin setup is available |
| `POST` | `/api/v1/admin/bootstrap` | Create first ParkTech admin using server setup code |
| `GET` | `/api/v1/security/lot` | Full live lot state and counts |
| `GET` | `/api/v1/security/arrivals` | Waiting/assigned visitor queue |
| `GET` | `/api/v1/security/queue` | Active arrival, parking, and exit lifecycle queue |
| `POST` | `/api/v1/security/arrivals/{session_id}/verify` | Legacy check-in matching route; current visitors request a sensor-confirmed bay directly |
| `GET` | `/api/v1/admin/checkpoints` | List checkpoint entry URLs for authorized staff |
| `POST` | `/api/v1/admin/checkpoints` | Create/replace the entrance QR; replacement needs explicit confirmation |
| `GET` | `/api/v1/admin/events` | Audit events |
| `GET/PATCH` | `/api/v1/admin/config` | Read/update configuration contract |
| `GET` | `/api/v1/admin/users` | List account roster without secrets |
| `POST` | `/api/v1/admin/users` | Create validated account |
| `POST` | `/api/v1/admin/devices` | Provision a one-time per-device credential |
| `GET` | `/api/v1/admin/devices` | List device status without credentials |
| `POST` | `/api/v1/admin/devices/{device_id}/revoke` | Revoke a device credential |
| `GET` | `/api/v1/admin/site` | Read the single facility's parking lot settings |
| `PATCH` | `/api/v1/admin/site` | Set facility name, logo, address, and location |
| `GET` | `/api/v1/admin/analytics` | 30-day operational summary |
| `GET` | `/api/v1/admin/events/export.csv` | Export audit events as CSV |
| `GET` | `/api/v1/admin/sites/{site_id}/qr` | Protected SVG/PNG QR download |
| `WS` | `/api/v1/ws/operations?authorization=...` | Authorized operations events |

With the default open staff setting, staff can use `/admin` and its API directly without a login. This grants administrative actions to anyone who can reach the service; keep it on a trusted LAN. Setting `SMARTPARK_OPEN_STAFF_INTERFACE=false` restores the existing login and role checks.

## IoT and simulator routes

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/v1/iot/events` | Process idempotent sensor event |
| `POST` | `/api/v1/iot/heartbeat` | Mark device online |
| `POST` | `/api/v1/simulator/events` | Development-only event using same event logic |
| `POST` | `/api/iot/events` | Authenticated, idempotent L1/L2 occupancy event for the two-bay live MVP |
| `GET` | `/api/bays` | Public latest L1/L2 physical/sensor-health state |
| `POST` | `/api/v1/iot/heartbeat?device_id=...` | Authenticated device heartbeat with per-sensor health map |
| `POST` | `/api/bays/{space_id}/assignment` | Direct staff assignment for a recently confirmed-free bay |
| `POST` | `/api/iot/demo-events` | Simulator-only bay occupancy transition |

The IoT endpoint accepts the versioned event envelope `{event_id, facility_id, device_id, space_id, measured_at, event_type, payload}` and retains compatibility with the original `sensor_id` and `observed_at` fields. `bay_occupied` (or compatibility alias `space_occupied`) starts the backend session timer once. `bay_vacant` (or `space_vacant`) releases a space only after an authorized exit. `vehicle_detected`, `gate_opened`, `gate_closed`, and `device_heartbeat` are accepted as device events; sensor events never select a visitor or bay assignment. Events are deduplicated by `event_id`. Administrators provision per-device tokens with `POST /api/v1/admin/devices`; the returned token is shown once and must be stored securely. The configured shared token remains as a transition credential for existing deployments and should be unset after devices migrate. This is a single-facility deployment; multi-tenant isolation is out of scope.

## DEMO tariff and lifecycle

The default tariff is GH₵1 per minute: zero free minutes, one-minute billing blocks, and 100 pesewas per block. Staff can edit rates at `/admin/pricing`; each invoice stores a tariff snapshot. Manual staff payment records support cash and other offline methods. The separately labelled demo-payment route is simulated and never transfers money. A bay stays occupied until staff authorizes exit and the sensor confirms vacancy.

Typical event types include `vehicle_detected`, `space_occupied`, `space_released`, `device_offline`, and `device_online`.

## Account payload

```json
{
  "email": "officer@example.com",
  "display_name": "Kofi Mensah",
  "password": "strong-password-123",
  "role": "SECURITY",
  "ghana_card_number": "GHA-123456789-0",
  "phone_number": "0241234567"
}
```

The Ghana Card number is hashed and only its last four digits are retained. Passwords are PBKDF2 hashed. Neither secret is returned by the API.

## WebSocket reconnect behavior

WebSocket events are notifications, not the authoritative database. After disconnect or reconnect, fetch `/visitor/me` or `/security/lot` and then resume event handling. Visitor events are scoped to the visitor session; operations events are role-protected.
