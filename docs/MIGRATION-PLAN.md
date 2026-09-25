# SmartPark scope and migration plan

## Existing code inventory

The product is a single ParkTech-operated facility with a React/Vite visitor PWA, Leaflet map and Google Maps fallback, signed entrance checkpoint QR, isolated visitor sessions, visitor arrival codes, guard-verified ordered bay allocation, staff roles, audit events, WebSockets, and a Pi sensor prototype. The current database is `backend/smartpark.db`; startup applies additive SQLite migrations. This is not an Alembic/PostgreSQL migration.

## Implemented in this continuation

- Signed, permanent main-entrance checkpoint URLs at `/enter/{id}.{signature}`; the QR contains no visitor identity and each scan starts a new session. Replacing the QR requires explicit confirmation and revokes the old signature.
- Public checkpoint welcome screen with live space states, current facility tariff, parking guidance, and a direction action.
- Six-digit arrival code visible only in the visitor session. Check-in waits for a security operator to verify the code before any bay is allocated; verified full-lot visitors wait for a vacancy.
- Staff can print the active checkpoint QR from Settings or Security → Checkpoint QR.
- GPS remains opt-in and local to the Smart Park backend. Navigation uses configurable OSRM-compatible road routing, distance/ETA when available, external Google Maps fallback, and stops location watch on exit.
- A configurable, persisted 5 cm entrance threshold setting and matching IoT agent default. Proximity still does not identify a visitor or parking space.

- Editable per-site demo tariff, seeded at 30 free minutes then GHS 2 per started 10-minute block (200 pesewas).
- Server-side UTC duration/charge calculation and tariff snapshot on the invoice.
- Private visitor-entered plate record stored as a hash and last four characters.
- Idempotent DEMO payment records, staff exit authorization, and confirmed-vacancy release.
- IoT event envelope accepts facility ID, measured time, event type and payload; duplicate event IDs are ignored. Vacancy no longer frees a bay before exit authorization.
- End-to-end API regression test for billing, payment, exit authorization and vacancy.
- Staff-only operational queue API and admin queue screen for waiting, assigned, parked, and exit-requested sessions; eligible exit requests can be authorized from the queue. The feed omits visitor tokens and full vehicle identifiers and refreshes every 10 seconds.
- Visitor session recovery now returns the visitor's private invoice with their authenticated session state, so a refresh retains payment and exit status.
- Per-device IoT credentials can be provisioned once and revoked by an administrator; event identity is checked against the credential. Basic 30-day operational counts and CSV audit export are available to administrators and managers.
- The default product entry is organization-neutral. Administrators can name the active parking lot and optionally provide its address and coordinates; public availability and visitor reservations remain unavailable until setup is complete. The former Parliament House demo location is cleared while its operational records are retained.

## Remaining stages and known scope gaps

This is a single-facility prototype. The API uses SQLite/sqlite3 with additive startup schema setup. The router's default OSRM demo endpoint is not sized or guaranteed for production; configure a production routing provider. The 5 cm threshold is a prototype value and must be calibrated against the installed hardware. Raspberry Pi hardware, sensors, gate control, production payments, and actual device-to-server deployment have not been field-tested. The admin page stores threshold settings in the API; Pi deployments must set the same `SMARTPARK_ENTRANCE_THRESHOLD_CM` value on their agent. Multi-worker WebSocket fan-out needs Redis/pub-sub before scaling. The shared IoT compatibility credential should be removed once every device uses its own provisioned token.

## Demonstration: ENTER → ASSIGN → PARK → TRACK → PAY → EXIT

Run FastAPI with the local bootstrap token and simulator enabled. Create a visitor session, then use its returned token below. The user-facing driver screen currently demonstrates ENTER/ASSIGN/PARK in its UI; the following API calls demonstrate the authoritative billing and safe exit lifecycle.

```powershell
$base = 'http://localhost:8000'
$session = Invoke-RestMethod -Method Post "$base/api/v1/visitor/sessions?site_id=default"
$visitor = @{ 'X-Visitor-Token' = $session.visitor_token }
Invoke-RestMethod -Method Post "$base/api/v1/visitor/arrivals" -Headers $visitor
# Security verifies the code shown on the visitor's phone, then the first free bay is assigned.
$verification = @{ arrival_code = $session.arrival_code } | ConvertTo-Json
$allocation = Invoke-RestMethod -Method Post "$base/api/v1/security/arrivals/$($session.session_id)/verify" -Headers @{ Authorization = 'Bearer change-me-in-production' } -ContentType 'application/json' -Body $verification
$space = $allocation.space_id
Invoke-RestMethod -Method Post "$base/api/v1/simulator/events" -Headers @{ Authorization = 'Bearer change-me-in-production' } -ContentType 'application/json' -Body (@{ event_type = 'space_occupied'; space_id = $space } | ConvertTo-Json)
# TRACK: private estimate (starts from the event's server timestamp).
Invoke-RestMethod -Method Get "$base/api/v1/sessions/$($session.session_id)/charges" -Headers $visitor
# PAY/EXIT: request the final snapshot, pay only through the clearly labelled demo endpoint.
$invoice = Invoke-RestMethod -Method Post "$base/api/v1/sessions/$($session.session_id)/request-exit" -Headers $visitor
if ($invoice.invoice.amount_minor -gt 0) {
  Invoke-RestMethod -Method Post "$base/api/v1/invoices/$($invoice.invoice.id)/demo-pay" -Headers $visitor -ContentType 'application/json' -Body '{"idempotency_key":"demo-payment-0001"}'
}
Invoke-RestMethod -Method Post "$base/api/v1/sessions/$($session.session_id)/authorize-exit" -Headers @{ Authorization = 'Bearer change-me-in-production' } -ContentType 'application/json' -Body '{}'
# Release only after vacancy confirmation from an authenticated IoT event.
$vacancy = @{ event_id = [guid]::NewGuid().ToString(); facility_id = 'default'; device_id = 'demo-device'; space_id = $space; measured_at = (Get-Date).ToUniversalTime().ToString('o'); event_type = 'SPACE_VACANT'; payload = @{ simulated = $true } } | ConvertTo-Json
Invoke-RestMethod -Method Post "$base/api/v1/iot/events" -Headers @{ 'X-Device-Token' = 'change-device-token-in-production' } -ContentType 'application/json' -Body $vacancy
```

For a no-wait billing test, run `python -m pytest backend/tests -q`; the billing lifecycle test moves the occupied timestamp back 41 minutes in its isolated test database. Never interpret a `DEMO_PAID` record as a real transfer.
