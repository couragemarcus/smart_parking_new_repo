# SmartPark API reference

FastAPI generates the complete interactive schema at `/docs` and `/openapi.json`.

## Headers

Visitor routes use:

```text
X-Visitor-Token: <opaque visitor token>
```

Admin/security routes use either the local bootstrap token or an expiring login token:

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
| `GET` | `/api/v1/public/checkpoints/{signed_token}` | Validate an active checkpoint and show public facility/space details |
| `GET` | `/api/v1/sites/{site_id}/destination` | Configured coordinates, waypoints, Google Maps link |
| `GET` | `/api/v1/sites/{site_id}/availability` | Public totals and generic available/reserved/occupied states; no visitor identifiers |
| `GET` | `/api/v1/sites/{site_id}/tariff` | Publicly displayed facility rate |
| `GET` | `/api/parking/availability?site_id=default` | Compatibility availability route |

## Visitor routes

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/v1/visitor/sessions?checkpoint_token=...` | Create a new isolated session with random token and visitor-only arrival code; rate limited |
| `GET` | `/api/v1/visitor/me` | Read own session, reservation, and invoice for refresh recovery |
| `POST` | `/api/v1/visitor/arrivals` | Check in; does not allocate a bay until security verifies the visitor code |
| `GET` | `/api/v1/visitor/parking-layout` | Privacy-safe layout with `is_mine` marker |
| `GET` | `/api/v1/visitor/assignment` | Read current private assignment |
| `POST` | `/api/v1/visitor/vehicle` | Store visitor-entered plate as hash plus last four characters |
| `GET` | `/api/v1/sessions/{id}/charges` | Private current estimate or final invoice |
| `POST` | `/api/v1/sessions/{id}/request-exit` | Snapshot tariff and calculate itemized session charge |
| `POST` | `/api/v1/invoices/{id}/demo-pay` | Idempotent simulated payment; never transfers money |
| `POST` | `/api/v1/sessions/{id}/authorize-exit` | Staff authorization after payment or manager/admin exemption |
| `WS` | `/api/v1/ws/visitor?token=...` | Private visitor events |

Compatibility aliases exist for `/api/visitor/sessions`, `/api/visitor/arrived`, and `/api/parking/destination`.
The printed QR opens `/enter/{checkpoint-id}.{signature}`. Tokens are signed using `SMARTPARK_CHECKPOINT_SECRET`; replacing a checkpoint requires explicit confirmation and revokes the previous link. Each scan creates a distinct visitor session. Legacy `/scan/{site_id}` and `/visit/{site_id}` routes remain for local compatibility. Private visitor state requires an unexpired visitor token.

## Admin and security routes

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/v1/admin/login` | Create an expiring admin session |
| `GET` | `/api/v1/admin/bootstrap/status` | Report whether the one-time first admin setup is available |
| `POST` | `/api/v1/admin/bootstrap` | Create first ParkTech admin using server setup code |
| `GET` | `/api/v1/security/lot` | Full live lot state and counts |
| `GET` | `/api/v1/security/arrivals` | Waiting/assigned visitor queue |
| `GET` | `/api/v1/security/queue` | Active arrival, parking, and exit lifecycle queue |
| `POST` | `/api/v1/security/arrivals/{session_id}/verify` | Verify the visitor's six-digit code; only then assign the first available bay |
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

Role permissions are checked server-side: `SECURITY` can view operations/arrivals and checkpoint QR, verify visitor codes, use the simulator, and authorize exits; `MANAGER` can view operations, arrivals, checkpoints, and reports; `ADMIN` can manage users, checkpoints, facility settings, tariffs, devices, and configuration. Requests without a valid login/bootstrap token receive `401`; authenticated users missing the required role receive `403`.

## IoT and simulator routes

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/v1/iot/events` | Process idempotent sensor event |
| `POST` | `/api/v1/iot/heartbeat` | Mark device online |
| `POST` | `/api/v1/simulator/events` | Development-only event using same event logic |
| `POST` | `/api/iot/events` | Authenticated, idempotent L1/L2 occupancy event for the two-bay live MVP |
| `GET` | `/api/bays` | Public latest L1/L2 physical/sensor-health state |
| `POST` | `/api/v1/security/bays/{space_id}/assignment` | Staff-only demo assignment for a confirmed-free bay |

The IoT endpoint accepts the versioned event envelope `{event_id, facility_id, device_id, space_id, measured_at, event_type, payload}` and retains compatibility with the original `sensor_id` and `observed_at` fields. `bay_occupied` (or compatibility alias `space_occupied`) starts the backend session timer once. `bay_vacant` (or `space_vacant`) releases a space only after an authorized exit. `vehicle_detected`, `gate_opened`, `gate_closed`, and `device_heartbeat` are accepted as device events; sensor events never select a visitor or bay assignment. Events are deduplicated by `event_id`. Administrators provision per-device tokens with `POST /api/v1/admin/devices`; the returned token is shown once and must be stored securely. The configured shared token remains as a transition credential for existing deployments and should be unset after devices migrate. This is a single-facility deployment; multi-tenant isolation is out of scope.

## DEMO tariff and lifecycle

The editable seed assumption is 30 free minutes, then GHS 2 per started 10-minute block (200 pesewas), with zero grace and no daily cap. `PATCH /api/v1/admin/tariff` changes future invoices; each invoice stores a tariff snapshot. The `amount_minor` field is in the currency's minor unit. Payments are `DEMO_PAID` records only. Visitors may request the invoice and make the demo payment; staff authorize the simulated exit. The space remains occupied until a vacancy event arrives.

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
