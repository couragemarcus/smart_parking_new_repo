export type BackendSession = {
  session_id: string
  visitor_token: string
  site_id: string
  site_name: string
  expires_at: string
}

const apiUrl = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '')

export const backendEnabled = Boolean(apiUrl)
export const visitorTokenKey = 'smartpark.visitor.token'

export function subscribeNewsletter(payload: { email: string; consent: boolean; source?: string; website?: string }) {
  return request<{ message: string; unsubscribe_token?: string }>('/api/public/newsletter/subscriptions', { method: 'POST', body: JSON.stringify(payload) })
}

export function unsubscribeNewsletter(token: string) {
  return request<{ message: string }>('/api/public/newsletter/unsubscribe', { method: 'POST', body: JSON.stringify({ token }) })
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (!apiUrl) throw new Error('SmartPark API is not configured')
  const response = await fetch(`${apiUrl}${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } })
  if (!response.ok) {
    const body = await response.text()
    let message = body || `SmartPark API error: ${response.status}`
    try { const parsed = JSON.parse(body) as { detail?: string }; if (parsed.detail) message = parsed.detail } catch { /* use response text */ }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

export function createVisitorSession(siteId = 'default', checkpointToken?: string) {
  const params = new URLSearchParams({ site_id: siteId })
  if (checkpointToken) params.set('checkpoint_token', checkpointToken)
  return request<BackendSession>(`/api/v1/visitor/sessions?${params}`, { method: 'POST' })
}

export function getPublicCheckpoint(token: string) {
  return request<{ checkpoint_id: string; checkpoint_name: string; site_id: string; site_name: string; address: string; logo_data_uri: string; latitude: number | null; longitude: number | null; configured: boolean; spaces: { id: string; status: string }[] }>(`/api/v1/public/checkpoints/${encodeURIComponent(token)}`)
}

export function getPublicEntry(siteId = 'default') {
  return request<{ site_id: string; platform_name: string; site_name: string; configured: boolean; entry_url: string; https_required: boolean }>(`/api/v1/public/sites/${encodeURIComponent(siteId)}/entry`)
}

export function listCheckpoints(token?: string) {
  return request<{ id: string; name: string; active: boolean; created_at: string; entry_url: string }[]>('/api/v1/admin/checkpoints', { headers: adminHeaders(token) })
}

export function createCheckpoint(name: string, confirm_revoke_previous: boolean, token?: string) {
  return request<{ id: string; name: string; active: boolean; entry_url: string }>('/api/v1/admin/checkpoints', { method: 'POST', headers: adminHeaders(token), body: JSON.stringify({ name, confirm_revoke_previous }) })
}

export function verifyVisitorArrival(sessionId: string, token?: string) {
  return request<{ session_id: string; status: string; space_id: string }>(`/api/v1/security/arrivals/${encodeURIComponent(sessionId)}/match`, { method: 'POST', headers: adminHeaders(token), body: JSON.stringify({}) })
}

export function recordManualPayment(sessionId: string, method: 'CASH' | 'MOBILE_MONEY_MANUAL' | 'OTHER_MANUAL', reference = '', token?: string) {
  return request<{ status: string; method: string; amount_minor: number }>(`/api/v1/security/sessions/${encodeURIComponent(sessionId)}/payment`, { method: 'POST', headers: adminHeaders(token), body: JSON.stringify({ method, reference }) })
}
export function confirmVehicleExit(sessionId: string, token?: string) {
  return request<{ status: string; space_id: string }>(`/api/v1/security/sessions/${encodeURIComponent(sessionId)}/exit`, { method: 'POST', headers: adminHeaders(token), body: JSON.stringify({}) })
}

export function getStoredVisitorToken() {
  const token = window.localStorage.getItem(visitorTokenKey)
  const expiresAt = window.localStorage.getItem(`${visitorTokenKey}.expires`)
  if (expiresAt && Date.parse(expiresAt) <= Date.now()) {
    window.localStorage.removeItem(visitorTokenKey)
    window.localStorage.removeItem(`${visitorTokenKey}.expires`)
    return null
  }
  return token
}

export function storeVisitorToken(token: string, expiresAt?: string) {
  window.localStorage.setItem(visitorTokenKey, token)
  if (expiresAt) window.localStorage.setItem(`${visitorTokenKey}.expires`, expiresAt)
}

export function storeArrivalCode(code: string) { window.localStorage.setItem('smartpark.visitor.arrival_code', code) }
export function getArrivalCode() { return window.localStorage.getItem('smartpark.visitor.arrival_code') || '' }

export function getParkingAvailability(siteId = 'default') {
  return request<{ site_id: string; total: number; available: number; occupied: number; unavailable: number; out_of_service: number; spaces: { id: string; status: string }[]; configured: boolean; updated_at: string }>(`/api/v1/sites/${encodeURIComponent(siteId)}/availability`)
}

export function getPublicLiveAvailability() {
  return request<{ site_id: string; total: number; available: number; occupied: number; unavailable: number; spaces: { id: string; status: string }[]; configured: boolean; updated_at: string }>('/api/public/live-availability')
}

export function getPublicTariff(siteId = 'default') {
  return request<Omit<TariffSettings, 'demo_assumption'>>(`/api/v1/sites/${encodeURIComponent(siteId)}/tariff`)
}

export function getVisitorLayout(token: string) {
  return request<{ site_id: string; spaces: { id: string; status: string; is_mine: boolean }[] }>('/api/v1/visitor/parking-layout', { headers: { 'X-Visitor-Token': token } })
}

export function getVisitorMe(token: string) {
  return request<{ session_id: string; status: string; assignment?: { id: string; space_id: string; status: string; created_at: string; occupied_at: string | null }; invoice?: VisitorInvoice | null }>('/api/v1/visitor/me', { headers: { 'X-Visitor-Token': token } })
}

export function getSessionCharges(sessionId: string, token: string) {
  return request<{ invoice: VisitorInvoice | null; estimate: { duration_minutes: number; amount_minor: number; currency: string; demo_tariff: boolean; tariff: { free_minutes: number; block_minutes: number; block_price_minor: number } } | null }>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/charges`, { headers: { 'X-Visitor-Token': token } })
}

export function joinVisitorQueue(token: string) {
  return request<{ session_id: string; status: string; assignment?: string }>('/api/v1/visitor/arrivals', { method: 'POST', headers: { 'X-Visitor-Token': token } })
}

export function getDestination(siteId: string) {
  return request<{ configured: boolean; display_name: string; address: string; logo_data_uri?: string; latitude: number | null; longitude: number | null; google_maps_url: string; waypoints: { name: string; latitude: number; longitude: number }[] }>(`/api/v1/sites/${encodeURIComponent(siteId)}/destination`)
}

export type SiteSettings = { id: string; name: string; address: string; latitude: number; longitude: number; setup_complete: boolean; logo_data_uri?: string; contact_email?: string; contact_phone?: string; social_links?: Record<string, string> }

export function getAdminSite(token?: string) {
  return request<SiteSettings>('/api/v1/admin/site', { headers: adminHeaders(token) })
}

export function saveAdminSite(payload: { name: string; address: string; latitude?: number; longitude?: number; logo_data_uri?: string; contact_email?: string; contact_phone?: string; social_links?: Record<string, string> }, token?: string) {
  return request<SiteSettings>('/api/v1/admin/site', { method: 'PATCH', headers: adminHeaders(token), body: JSON.stringify(payload) })
}

export function getPublicFacility() {
  return request<{ display_name: string; address: string; logo_url: string; contact_email: string; contact_phone: string; social_links: Record<string, string>; description: string; currency: string; timezone: string }>('/api/public/facility')
}

export function getAdminPublicUrl(token?: string) { return request<{ base_url: string }>('/api/v1/admin/public-url', { headers: adminHeaders(token) }) }
export function saveAdminPublicUrl(base_url: string, token?: string) { return request<{ base_url: string }>('/api/v1/admin/public-url', { method: 'PATCH', headers: adminHeaders(token), body: JSON.stringify({ base_url }) }) }

export type SystemConfig = { site_id: string; entrance_threshold_cm: number; reservation_minutes: number; simulator_enabled: boolean }
export function getAdminConfig(token?: string) { return request<SystemConfig>('/api/v1/admin/config', { headers: adminHeaders(token) }) }
export function saveAdminConfig(payload: { entrance_threshold_cm: number; reservation_minutes: number }, token?: string) { return request<SystemConfig>('/api/v1/admin/config', { method: 'PATCH', headers: adminHeaders(token), body: JSON.stringify(payload) }) }

export function visitorSocket(token: string) {
  if (!apiUrl) return null
  const url = new URL(`${apiUrl.replace(/^http/, 'ws')}/api/v1/ws/visitor`)
  url.searchParams.set('token', token)
  return new WebSocket(url)
}

export type AdminUser = { id: string; email: string; display_name: string; role: 'SECURITY' | 'ADMIN' | 'MANAGER'; active: boolean; created_at?: string }

export function adminLogin(email: string, password: string) {
  return request<{ access_token: string; user: AdminUser }>('/api/v1/admin/login', { method: 'POST', body: JSON.stringify({ email, password }) })
}

export function adminHeaders(token?: string): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export function getAdminBootstrapStatus() {
  return request<{ available: boolean }>('/api/v1/admin/bootstrap/status')
}

export function bootstrapAdmin(payload: { setup_token: string; display_name: string; email: string; password: string }) {
  return request<{ access_token: string; user: AdminUser }>('/api/v1/admin/bootstrap', { method: 'POST', body: JSON.stringify(payload) })
}

export function listAdminUsers(token?: string) {
  return request<AdminUser[]>('/api/v1/admin/users', { headers: adminHeaders(token) })
}

export type QueueRecord = { session_id: string; session_status: string; created_at: string; arrived_at: string | null; space_id: string | null; reservation_status: string | null; occupied_at: string | null; invoice_id: string | null; invoice_status: string | null; amount_minor: number | null; currency: string | null }

export function getSecurityQueue(token?: string) {
  return request<QueueRecord[]>('/api/v1/security/queue', { headers: adminHeaders(token) })
}

export function getSecurityLot(token?: string) {
  return request<{ site_id: string; spaces: { id: string; status: string; assigned_session: string | null; vehicle_id: string | null; updated_at: string }[]; stats: Record<string, number> }>('/api/v1/security/lot', { headers: adminHeaders(token) })
}

export type LiveBay = { id: string; physical_state: 'UNKNOWN' | 'FREE' | 'OCCUPIED'; last_seen: string | null; sensor_updated_at: string | null; display_state: 'WAITING_FOR_SENSOR' | 'AVAILABLE' | 'OCCUPIED' | 'ASSIGNED'; assigned: number }
export function getLiveBays() { return request<{ site_id: string; bays: LiveBay[] }>('/api/bays') }
export function getFacilityConfig() { return request<{ site_id: string; latitude: number | null; longitude: number | null }>('/api/public/facility-config') }
export function assignLiveBay(spaceId: string, assigned: boolean, token?: string) {
  return request<LiveBay>(`/api/v1/security/bays/${encodeURIComponent(spaceId)}/assignment`, { method: 'POST', headers: adminHeaders(token), body: JSON.stringify({ assigned }) })
}
export function assignOpenBay(spaceId: string, assigned: boolean) {
  return request<LiveBay>(`/api/bays/${encodeURIComponent(spaceId)}/assignment`, { method: 'POST', body: JSON.stringify({ assigned }) })
}
export function assignVisitorBay(token: string, spaceId: string) {
  return request<{ session_id: string; status: string; space_id: string; visitor_token?: string }>('/api/public/visitor/assign', { method: 'POST', headers: { 'X-Visitor-Token': token }, body: JSON.stringify({ space_id: spaceId }) })
}
export function assignAnonymousBay(spaceId: string) {
  return request<{ status: string; space_id: string }>('/api/public/assign-bay', { method: 'POST', body: JSON.stringify({ space_id: spaceId }) })
}
export function assignOpenVisitorBay(spaceId: string) {
  return request<{ session_id: string; status: string; space_id: string; visitor_token: string }>('/api/public/assign-visitor-bay', { method: 'POST', body: JSON.stringify({ space_id: spaceId }) })
}

export function reportLiveBay(secureEvent: Record<string, unknown>) {
  return request<{ status: string; event_id: string }>('/api/iot/events', { method: 'POST', body: JSON.stringify(secureEvent) })
}

export function reportDemoBay(event: Record<string, unknown>) {
  return request<{ status: string; event_id: string }>('/api/iot/demo-events', { method: 'POST', body: JSON.stringify(event) })
}

export function simulateParkingEvent(event_type: string, space_id?: string, token?: string) {
  return request<{ status: string; event_id?: string }>('/api/v1/simulator/events', { method: 'POST', headers: adminHeaders(token), body: JSON.stringify({ event_type, space_id }) })
}

export type TariffSettings = { free_minutes: number; block_minutes: number; block_price_minor: number; grace_minutes: number; daily_cap_minor: number | null; timezone: string; currency: string; demo_assumption?: boolean }

export function getAdminTariff(token?: string) {
  return request<TariffSettings>('/api/v1/admin/tariff', { headers: adminHeaders(token) })
}

export function saveAdminTariff(tariff: Omit<TariffSettings, 'currency' | 'demo_assumption'>, token?: string) {
  return request<TariffSettings>('/api/v1/admin/tariff', { method: 'PATCH', headers: adminHeaders(token), body: JSON.stringify(tariff) })
}

export function getAdminAnalytics(token?: string) {
  return request<{ period_days: number; spaces_by_status: Record<string, number>; active_visitor_sessions: number; invoices: { status: string; count: number; amount_minor: number }[]; audit_events_by_type: { event_type: string; count: number }[]; demo_amounts_are_not_transfers: boolean }>('/api/v1/admin/analytics')
}

export function getAdminEvents(token?: string) {
  return request<{ id: number; event_type: string; space_id: string | null; description: string; created_at: string }[]>('/api/v1/admin/events')
}

export async function downloadAdminEvents(token?: string) {
  if (!apiUrl) throw new Error('SmartPark API is not configured')
  const response = await fetch(`${apiUrl}/api/v1/admin/events/export.csv`)
  if (!response.ok) throw new Error('Could not export the audit report.')
  return response.blob()
}

export function authorizeVisitorExit(sessionId: string, token?: string) {
  return request<{ status: string; gate: string }>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/authorize-exit`, { method: 'POST', headers: adminHeaders(token), body: JSON.stringify({}) })
}

export function createAdminUser(payload: { email: string; display_name: string; password: string; role: AdminUser['role']; ghana_card_number: string; phone_number: string }, token?: string) {
  return request<AdminUser>('/api/v1/admin/users', { method: 'POST', headers: adminHeaders(token), body: JSON.stringify(payload) })
}

export type VisitorInvoice = { id: string; status: string; amount_minor: number; currency: string; duration_minutes: number; tariff_snapshot: { free_minutes: number; block_minutes: number; block_price_minor: number }; demo_payment: true }

export function requestVisitorExit(sessionId: string, token: string) {
  return request<{ invoice: VisitorInvoice }>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/request-exit`, { method: 'POST', headers: { 'X-Visitor-Token': token } })
}

export function payDemoInvoice(invoiceId: string, token: string, idempotencyKey = crypto.randomUUID()) {
  return request<{ status: string; payment_id: string; label: string }>(`/api/v1/invoices/${encodeURIComponent(invoiceId)}/demo-pay`, { method: 'POST', headers: { 'X-Visitor-Token': token }, body: JSON.stringify({ idempotency_key: idempotencyKey }) })
}
