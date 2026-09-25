import { useEffect, useMemo, useRef, useState } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer, useMap } from 'react-leaflet'
import { backendEnabled, createAdminUser, createCheckpoint, createVisitorSession, downloadAdminEvents, getAdminAnalytics, getAdminConfig, getAdminEvents, getAdminPublicUrl, saveAdminPublicUrl, getAdminSite, getAdminTariff, getDestination, getParkingAvailability, getPublicLiveAvailability, getPublicCheckpoint, getPublicEntry, getPublicTariff, getSecurityLot, getSecurityQueue, getSessionCharges, getStoredVisitorToken, getVisitorLayout, getVisitorMe, joinVisitorQueue, listAdminUsers, listCheckpoints, requestVisitorExit, saveAdminConfig, saveAdminSite, saveAdminTariff, simulateParkingEvent, storeVisitorToken, verifyVisitorArrival, recordManualPayment, confirmVehicleExit, visitorSocket, getPublicFacility, subscribeNewsletter, unsubscribeNewsletter, getLiveBays, assignOpenBay, assignVisitorBay, assignAnonymousBay, assignOpenVisitorBay, reportDemoBay, getFacilityConfig, type LiveBay, type AdminUser, type QueueRecord, type SiteSettings, type TariffSettings, type VisitorInvoice, type SystemConfig } from './api'
import {
  ArrowDownRight, ArrowRight, Bell, CarFront, Check, ChevronDown, CircleHelp, Clock3,
  Grid2x2, LayoutDashboard, ListFilter, LogOut, MapPin, Menu, MoreHorizontal, Navigation,
  Search, Settings, ShieldCheck, Sparkles, Users, X, Zap,
} from 'lucide-react'
import './footer.css'
import './lot.css'

type Status = 'available' | 'reserved' | 'assigned' | 'occupied' | 'unavailable' | 'out-of-service'
type Space = { id: string; status: Status; visitor?: string; vehicle?: string; since?: string }
type Activity = { time: string; event: string; detail: string; tone: 'green' | 'blue' | 'orange' }
type CampusDestination = { configured: boolean; display_name: string; address: string; logo_data_uri?: string; latitude: number | null; longitude: number | null; google_maps_url: string; waypoints: { name: string; latitude: number; longitude: number }[] }
type DriverScreen = 'home' | 'welcome' | 'availability' | 'navigate' | 'my-space' | 'session' | 'payment' | 'receipt' | 'scan' | 'install'

const driverScreens = new Set<DriverScreen>(['welcome', 'availability', 'navigate', 'my-space', 'session', 'payment', 'receipt'])

function screenForPath(path: string): DriverScreen {
  if (path.startsWith('/enter/')) return 'navigate'
  if (path.startsWith('/scan/')) return 'scan'
  if (path === '/navigate') return 'navigate'
  if (path === '/parking') return 'availability'
  if (path === '/my-session') return 'session'
  if (path === '/install') return 'install'
  const segment = path.split('/').filter(Boolean)[1] || ''
  if (path === '/' || path === '/visit' || path === '/visit/') return 'navigate'
  if (segment === 'parking') return 'availability'
  return driverScreens.has(segment as DriverScreen) ? segment as DriverScreen : path.startsWith('/visit/') ? 'navigate' : 'home'
}

function pathForScreen(screen: DriverScreen, siteId = 'default') {
  return screen === 'home' || screen === 'navigate' ? `/visit?facility=${encodeURIComponent(siteId)}` : screen === 'scan' ? `/scan/${encodeURIComponent(siteId)}` : screen === 'availability' ? '/visit/parking' : screen === 'install' ? '/install' : `/visit/${screen}?facility=${encodeURIComponent(siteId)}`
}

function staffViewForPath(path: string) {
  if (path === '/admin/spaces' || path === '/admin/parking') return 'Parking status'
  if (path === '/admin/bays') return 'Live bays'
  if (path === '/admin/pricing') return 'Pricing'
  if (path === '/admin/payments') return 'Payments'
  if (path === '/admin/staff') return 'User management'
  if (path === '/admin/simulator') return 'Simulator'
  if (path === '/security' || path === '/guard/arrivals' || path === '/guard/sessions') return 'Visitor queue'
  if (path === '/security/checkpoints' || path === '/guard/checkpoint') return 'Checkpoint QR'
  if (path === '/reports') return 'Reports'
  if (path === '/admin/settings') return 'Settings'
  if (path === '/admin/settings/facility') return 'Settings'
  if (path === '/admin/settings/sensors') return 'Sensor settings'
  return 'Live view'
}

const initialSpaces: Space[] = [
  { id: 'L1', status: 'available' },
  { id: 'L2', status: 'available' },
  { id: 'L3', status: 'available' },
  { id: 'L4', status: 'available' },
]

const initialActivity: Activity[] = [
  { time: '10:24 AM', event: 'Vehicle parked', detail: 'L1 is now occupied', tone: 'green' },
  { time: '10:22 AM', event: 'Vehicle detected', detail: 'Approaching vehicle detected', tone: 'blue' },
  { time: '10:21 AM', event: 'Space assigned', detail: 'L4 assigned to Visitor #103', tone: 'orange' },
]

const statusLabel: Record<Status, string> = { available: 'Available', reserved: 'Reserved', assigned: 'Reserved for you', occupied: 'Occupied', unavailable: 'Temporarily unavailable', 'out-of-service': 'Out of service' }

function currentSiteId(path = window.location.pathname) {
  const parts = path.split('/').filter(Boolean)
  if (parts[0] === 'scan' && parts[1]) return decodeURIComponent(parts[1])
  if (parts[0] === 'visit' && parts[1]) return decodeURIComponent(driverScreens.has(parts[1] as DriverScreen) || parts[1] === 'parking' ? new URLSearchParams(window.location.search).get('facility') || new URLSearchParams(window.location.search).get('site') || 'default' : parts[1])
  return new URLSearchParams(window.location.search).get('facility') || new URLSearchParams(window.location.search).get('site') || 'default'
}

function getDemoSiteSetup(): SiteSettings | null {
  if (backendEnabled) return null
  try { const raw = window.localStorage.getItem('smartpark.site.setup'); return raw ? JSON.parse(raw) as SiteSettings : null } catch { return null }
}

function App() {
  const [pathname, setPathname] = useState(window.location.pathname)
  const [checkpointToken, setCheckpointToken] = useState(() => window.location.pathname.startsWith('/enter/') ? decodeURIComponent(window.location.pathname.slice('/enter/'.length)) : new URLSearchParams(window.location.search).get('checkpoint') || '')
  const [visitorTariff, setVisitorTariff] = useState<TariffSettings | null>(null)
  const [checkpointError, setCheckpointError] = useState('')
  const [mode, setMode] = useState<'driver' | 'admin'>(() => (/^\/(admin|security|guard|reports|login)(\/|$)/.test(window.location.pathname) ? 'admin' : 'driver'))
  const siteId = currentSiteId(pathname)
  const [spaces, setSpaces] = useState<Space[]>(() => backendEnabled ? initialSpaces.map((space) => ({ ...space, status: 'unavailable' })) : getDemoSiteSetup()?.setup_complete ? initialSpaces : [])
  const [activity, setActivity] = useState<Activity[]>(initialActivity)
  const [visitorStarted, setVisitorStarted] = useState(false)
  const [anonymousBay, setAnonymousBay] = useState<string | null>(null)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [adminView, setAdminView] = useState(() => staffViewForPath(window.location.pathname))
  const [visitorToken, setVisitorToken] = useState<string | null>(null)
  const [siteName, setSiteName] = useState(() => getDemoSiteSetup()?.name || 'Your Parking Lot')
  const [siteLogo, setSiteLogo] = useState(() => getDemoSiteSetup()?.logo_data_uri || '')
  const [lotConfigured, setLotConfigured] = useState(() => Boolean(getDemoSiteSetup()?.setup_complete))
  const [installPrompt, setInstallPrompt] = useState<BeforeInstallPromptEvent | null>(null)
  const [adminUsers, setAdminUsers] = useState<AdminUser[]>([])
  const [adminToken, setAdminToken] = useState<string | null>('open-interface')
  const [adminRole, setAdminRole] = useState<AdminUser['role'] | null>('ADMIN')
  const [visitorSessionId, setVisitorSessionId] = useState<string | null>(null)
  const [visitorInvoice, setVisitorInvoice] = useState<VisitorInvoice | null>(null)
  const [visitorAssignment, setVisitorAssignment] = useState<{ id: string; space_id: string; status: string; created_at: string; occupied_at: string | null } | null>(null)
  const [chargeEstimate, setChargeEstimate] = useState<{ duration_minutes: number; amount_minor: number; currency: string; tariff: { free_minutes: number; block_minutes: number; block_price_minor: number } } | null>(null)
  const [billingMessage, setBillingMessage] = useState('')
  const [reservationMessage, setReservationMessage] = useState('')
  const [availabilityState, setAvailabilityState] = useState<'demo' | 'loading' | 'live' | 'error'>(backendEnabled ? 'loading' : 'demo')
  const adminPage = /^\/(admin|security|guard|reports|login)(\/|$)/.test(pathname)
  const driverScreen = screenForPath(pathname)

  const navigate = (path: string, replace = false) => {
    if (replace) window.history.replaceState(null, '', path)
    else window.history.pushState(null, '', path)
    setPathname(window.location.pathname)
    if (/^\/(admin|security|guard|reports|login)(\/|$)/.test(window.location.pathname)) setMode('admin')
    else setMode('driver')
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  useEffect(() => {
    const onPopState = () => { setPathname(window.location.pathname); setAdminView(staffViewForPath(window.location.pathname)); setMode(/^\/(admin|security|guard|reports|login)(\/|$)/.test(window.location.pathname) ? 'admin' : 'driver') }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  useEffect(() => {
    if (pathname.startsWith('/scan/')) navigate(pathForScreen('welcome', currentSiteId(pathname)), true)
    if (/^\/(security|guard|reports|login)(\/|$)/.test(pathname) && !adminPage) {
      setMode('admin')
      return
    }
    if (pathname.startsWith('/enter/')) {
      setCheckpointToken('')
      navigate(`/visit?facility=${encodeURIComponent(siteId)}`, true)
    }
  }, [pathname])

  const [destination, setDestination] = useState<CampusDestination | null>(() => {
    const site = getDemoSiteSetup()
    if (!site?.setup_complete || (!site.latitude && !site.longitude)) return null
    return { configured: true, display_name: site.name, address: site.address, latitude: site.latitude, longitude: site.longitude, google_maps_url: `https://www.google.com/maps/dir/?api=1&destination=${site.latitude},${site.longitude}`, waypoints: [{ name: site.name, latitude: site.latitude, longitude: site.longitude }] }
  })

  useEffect(() => {
    const onInstallPrompt = (event: Event) => {
      event.preventDefault()
      setInstallPrompt(event as BeforeInstallPromptEvent)
    }
    window.addEventListener('beforeinstallprompt', onInstallPrompt)
    return () => window.removeEventListener('beforeinstallprompt', onInstallPrompt)
  }, [])

  useEffect(() => {
    const onSiteConfigured = (event: Event) => {
      const site = (event as CustomEvent<SiteSettings>).detail
      if (!site) return
      setSiteName(site.name)
      setSiteLogo(site.logo_data_uri || '')
      setLotConfigured(site.setup_complete)
      if (site.setup_complete && site.latitude && site.longitude) setDestination({ configured: true, display_name: site.name, address: site.address, latitude: site.latitude, longitude: site.longitude, google_maps_url: `https://www.google.com/maps/dir/?api=1&destination=${site.latitude},${site.longitude}`, waypoints: [{ name: site.name, latitude: site.latitude, longitude: site.longitude }] })
      if (!backendEnabled) setSpaces(initialSpaces)
      else {
        void getPublicLiveAvailability().then((availability) => { setSpaces(availability.spaces.map((space) => ({ id: space.id, status: toUiStatus(space.status) }))); setAvailabilityState('live') }).catch(() => setAvailabilityState('error'))
        void getDestination(siteId).then((destinationInfo) => setDestination(destinationInfo.latitude !== null && destinationInfo.longitude !== null ? destinationInfo : null)).catch(() => undefined)
      }
      setReservationMessage('')
      setBillingMessage('')
    }
    window.addEventListener('smartpark:site-configured', onSiteConfigured)
    return () => window.removeEventListener('smartpark:site-configured', onSiteConfigured)
  }, [siteId])

  useEffect(() => {
    const onCoordinates = (event: Event) => {
      const point = (event as CustomEvent<{ latitude: number; longitude: number }>).detail
      if (point?.latitude && point.longitude) setDestination((current) => current ? { ...current, latitude: point.latitude, longitude: point.longitude } : { configured: true, display_name: siteName, address: '', latitude: point.latitude, longitude: point.longitude, google_maps_url: `https://www.google.com/maps/dir/?api=1&destination=${point.latitude},${point.longitude}`, waypoints: [] })
    }
    window.addEventListener('smartpark:facility-coordinates', onCoordinates)
    return () => window.removeEventListener('smartpark:facility-coordinates', onCoordinates)
  }, [siteName])

  useEffect(() => {
    if (!backendEnabled || adminPage) return
    let active = true
    const refresh = async () => {
      try {
        const data = await getPublicLiveAvailability()
        if (!active) return
        setSpaces(data.spaces.map((space) => ({ id: space.id, status: toUiStatus(space.status) })))
        setLotConfigured(data.configured)
        setAvailabilityState('live')
      } catch {
        if (active) setAvailabilityState('error')
      }
    }
    void refresh()
    const timer = window.setInterval(refresh, 15000)
    return () => { active = false; window.clearInterval(timer) }
  }, [siteId, adminPage])

  useEffect(() => {
    if (!backendEnabled || adminPage) return
    let socket: WebSocket | null = null
    const bootstrap = async () => {
      void getDestination(siteId).then((site) => { setSiteName(site.display_name); setSiteLogo(site.logo_data_uri || ''); setDestination(site.configured && site.latitude !== null && site.longitude !== null ? site : null) }).catch(() => undefined)
      let entrySiteId = siteId
      if (checkpointToken) {
        const checkpoint = await getPublicCheckpoint(checkpointToken)
        entrySiteId = checkpoint.site_id
        setSiteName(checkpoint.site_name); setSiteLogo(checkpoint.logo_data_uri || '')
        setSpaces(checkpoint.spaces.map((space) => ({ id: space.id, status: toUiStatus(space.status) })))
        if (checkpoint.latitude !== null && checkpoint.longitude !== null) setDestination({ configured: true, display_name: checkpoint.site_name, address: checkpoint.address, latitude: checkpoint.latitude, longitude: checkpoint.longitude, google_maps_url: `https://www.google.com/maps/dir/?api=1&destination=${checkpoint.latitude},${checkpoint.longitude}`, waypoints: [{ name: checkpoint.site_name, latitude: checkpoint.latitude, longitude: checkpoint.longitude }] })
      }
      void getPublicTariff(entrySiteId).then(setVisitorTariff).catch(() => setVisitorTariff(null))
      // Passwordless visitor session preserves private assignment, timer, billing, and exit flow.
      let token = getStoredVisitorToken()
      if (token) {
        try { await refreshBackendState(token) }
        catch { window.localStorage.removeItem('smartpark.visitor.token'); window.localStorage.removeItem('smartpark.visitor.token.expires'); token = null }
      }
      if (!token && backendEnabled) {
        try {
          const session = await createVisitorSession(entrySiteId, checkpointToken || undefined)
          token = session.visitor_token
          storeVisitorToken(token, session.expires_at)
          setVisitorToken(token)
          setVisitorSessionId(session.session_id)
          socket = visitorSocket(token)
          if (socket) socket.onmessage = () => { void refreshBackendState(token!) }
        } catch { setVisitorToken(null) }
      }
      if (token) {
        setVisitorToken(token)
        socket = visitorSocket(token)
        if (socket) socket.onmessage = () => { void refreshBackendState(token!) }
      } else setVisitorToken(null)
    }
    void bootstrap().catch((error) => setCheckpointError(error instanceof Error ? `This entrance QR is invalid or revoked. Ask ParkTech staff for the current checkpoint QR. (${error.message})` : 'This entrance QR is invalid or revoked. Ask ParkTech staff for the current checkpoint QR.'))
    return () => socket?.close()
  }, [adminPage, adminToken, lotConfigured, siteId, checkpointToken])

  useEffect(() => {
    if (!backendEnabled || anonymousBay || !visitorToken || !visitorSessionId || !visitorAssignment?.occupied_at) return
    let active = true
    const refresh = async () => {
      try {
        const charges = await getSessionCharges(visitorSessionId, visitorToken)
        if (!active) return
        setChargeEstimate(charges.estimate)
        if (charges.invoice) setVisitorInvoice(charges.invoice)
      } catch { /* session screen keeps its last known server estimate when offline */ }
    }
    void refresh()
    const timer = window.setInterval(refresh, 15000)
    return () => { active = false; window.clearInterval(timer) }
  }, [visitorToken, visitorSessionId, visitorAssignment?.occupied_at, anonymousBay])

  const refreshBackendState = async (token: string) => {
    const [data, layout] = await Promise.all([getVisitorMe(token), getVisitorLayout(token)])
    setVisitorSessionId(data.session_id)
    setVisitorInvoice(data.invoice || null)
    setVisitorAssignment(data.assignment || null)
    setSpaces(layout.spaces.map((space) => ({ id: space.id, status: toUiStatus(space.status, space.is_mine), ...(space.is_mine ? { visitor: 'You · Guest', vehicle: 'Your vehicle', since: 'Live' } : {}) })))
    setVisitorStarted(Boolean(data.assignment) || ['WAITING_CONFIRMATION', 'WAITING_VERIFIED', 'WAITING'].includes(data.status))
  }

  const refreshAvailability = async () => {
    if (!backendEnabled) return
    try {
      const data = await getPublicLiveAvailability()
      setLotConfigured(data.configured)
      setSpaces((current) => current.map((space) => {
        const live = data.spaces.find((candidate) => candidate.id === space.id)
        if (!live) return space
        const mine = space.visitor === 'You · Guest'
        return { ...space, status: mine && live.status === 'UNAVAILABLE' ? 'assigned' : toUiStatus(live.status, mine) }
      }))
      setAvailabilityState('live')
    } catch { setAvailabilityState('error') }
  }

  useEffect(() => {
    if (!backendEnabled || anonymousBay || !visitorToken) return
    let active = true
    const poll = async () => { try { await refreshBackendState(visitorToken) } catch { /* keep the last known private state while offline */ } }
    void poll()
    const timer = window.setInterval(() => { if (active) void poll() }, 5000)
    return () => { active = false; window.clearInterval(timer) }
  }, [visitorToken, anonymousBay])

  useEffect(() => {
    if (!visitorAssignment || (pathname !== '/' && pathname !== '/visit')) return
    window.history.replaceState(null, '', '/visit/parking')
    setPathname('/visit/parking')
  }, [visitorAssignment, pathname])

  const stats = useMemo(() => ({
    occupied: spaces.filter((space) => space.status === 'occupied').length,
    available: spaces.filter((space) => space.status === 'available').length,
    assigned: spaces.filter((space) => space.status === 'assigned' || space.status === 'reserved').length,
  }), [spaces])

  const assignNext = async () => {
    const freeBay = spaces.find((space) => space.status === 'available')
    if (backendEnabled && freeBay) {
      try {
        const result = visitorToken
          ? await assignVisitorBay(visitorToken, freeBay.id)
          : await assignOpenVisitorBay(freeBay.id)
        const assignedVisitorToken = result.visitor_token || visitorToken
        if (result.visitor_token) { storeVisitorToken(result.visitor_token); setVisitorToken(result.visitor_token) }
        setVisitorSessionId(result.session_id)
        setAnonymousBay(null)
        setReservationMessage(`Bay ${result.space_id} assigned. Use directions to reach the facility.`)
        setVisitorStarted(true)
        setSpaces((current) => current.map((space) => space.id === result.space_id ? { ...space, status: 'assigned', visitor: 'You · Guest' } : space))
        if (assignedVisitorToken) void refreshBackendState(assignedVisitorToken)
        return
      } catch (error) { setReservationMessage(error instanceof Error ? error.message : 'Could not assign a bay.') }
    } else if (!backendEnabled && freeBay) {
      setAnonymousBay(freeBay.id)
      setVisitorStarted(true)
      setSpaces((current) => current.map((space) => space.id === freeBay.id ? { ...space, status: 'assigned', visitor: 'You · Guest' } : space))
      setReservationMessage(`Demo bay ${freeBay.id} assigned.`)
    } else setReservationMessage('No sensor-confirmed free bay is currently available.')
  }

  const occupyAssigned = () => {
    if (anonymousBay) { setBillingMessage('This local-only preview does not create a parking timer.') ; return }
    setSpaces((current) => current.map((space) => space.visitor === 'You · Guest'
      ? { ...space, status: 'occupied', since: 'Just now' }
      : space))
    setActivity((current) => [{ time: 'Just now', event: 'Vehicle parked', detail: 'Your assigned space is now occupied', tone: 'green' }, ...current])
  }

  const exitRequest = async () => {
    if (!backendEnabled || anonymousBay || !visitorToken || !visitorSessionId) {
      setBillingMessage('Ask staff to record cash payment and confirm your exit. The bay sensor will mark the bay free when your vehicle leaves.')
      return
    }
    try {
      const result = await requestVisitorExit(visitorSessionId, visitorToken)
      setVisitorInvoice(result.invoice)
      setBillingMessage(result.invoice.amount_minor ? 'Invoice calculated. Pay at the security checkpoint; staff will record payment.' : 'No fee is due. Ask security to confirm your departure.')
      navigate(pathForScreen(result.invoice.amount_minor ? 'payment' : 'receipt', siteId))
    } catch (error) { setBillingMessage(error instanceof Error ? error.message : 'Could not calculate the exit charge.') }
  }

  const payInvoice = async () => {
    setBillingMessage('Pay cash at the security checkpoint. Staff will confirm your exit; the sensor reports when the bay becomes free.')
  }

  if (pathname === '/qr') return <QrDisplay />
  if (driverScreen === 'install') return <InstallScreen onBack={() => navigate(pathForScreen('home', siteId))} />
  if (['/help', '/faq', '/contact', '/privacy', '/terms', '/accessibility', '/unsubscribe'].includes(pathname)) return <PublicInfoPage path={pathname} onHome={() => navigate(pathForScreen('home', siteId))} />

  if (mode === 'admin') {
    return <AdminDashboard spaces={spaces} stats={stats} activity={activity} onActivity={setActivity} view={adminView} setView={(view) => { setAdminView(view); navigate(pathForStaffView(view)) }} role={adminRole || 'SECURITY'} onMode={() => { navigate(pathForScreen('home', siteId), true); setAdminToken('open-interface'); setAdminRole('ADMIN'); setMode('driver') }} onSpaces={setSpaces} mobileOpen={mobileOpen} setMobileOpen={setMobileOpen} users={adminUsers} setUsers={setAdminUsers} adminToken={adminToken} siteId={siteId} />
  }

  return <DriverHome screen={driverScreen} onNavigate={(screen) => { const target = pathForScreen(screen, siteId); navigate(`${target}${checkpointToken ? `${target.includes('?') ? '&' : '?'}checkpoint=${encodeURIComponent(checkpointToken)}` : ''}`) }} spaces={spaces} visitorStarted={visitorStarted} visitorTariff={visitorTariff} checkpointError={checkpointError} onGuest={assignNext} reservationMessage={reservationMessage} canReserve={!backendEnabled || availabilityState === 'live'} onOccupy={occupyAssigned} onRequestExit={exitRequest} onPayDemo={payInvoice} invoice={visitorInvoice} billingMessage={billingMessage} availabilityState={availabilityState} mobileOpen={mobileOpen} setMobileOpen={setMobileOpen} siteName={siteName} siteLogo={siteLogo} installPrompt={installPrompt} destination={destination} lotConfigured={lotConfigured} assignment={visitorAssignment} estimate={chargeEstimate} />
}

function pathForStaffView(view: string) {
  if (view === 'Live bays') return '/admin/bays'
  if (view === 'Parking status') return '/admin/spaces'
  if (view === 'Pricing') return '/admin/pricing'
  if (view === 'Payments') return '/admin/payments'
  if (view === 'User management') return '/admin/staff'
  if (view === 'Simulator') return '/admin/simulator'
  if (view === 'Visitor queue') return '/security'
  if (view === 'Reports' || view === 'Activity logs') return '/reports'
  if (view === 'Settings') return '/admin/settings'
  if (view === 'Sensor settings') return '/admin/settings/sensors'
  if (view === 'Checkpoint QR') return '/security/checkpoints'
  return '/admin'
}

function toUiStatus(status: string, mine = false): Status {
  if (status === 'OCCUPIED') return 'occupied'
  if (status === 'RESERVED' && mine) return 'assigned'
  if (status === 'RESERVED') return 'reserved'
  if (status === 'OUT_OF_SERVICE') return 'out-of-service'
  if (status === 'UNAVAILABLE' || status === 'UNKNOWN') return 'unavailable'
  return 'available'
}

function AvailabilitySummary({ spaces, state }: { spaces: Space[]; state: 'demo' | 'loading' | 'live' | 'error' }) {
  const available = spaces.filter((space) => space.status === 'available').length
  const full = state !== 'loading' && available === 0
  return <section className="availability-summary" aria-live="polite" aria-label="Parking availability">
    <div><span>{state === 'live' ? 'Live availability' : state === 'demo' ? 'Demo availability' : state === 'loading' ? 'Checking availability' : 'Availability unavailable'}</span><strong>{state === 'loading' || state === 'error' ? '—' : `${available} / ${spaces.length}`}</strong><small>spaces available</small></div>
    <p role={full ? 'alert' : 'status'}>{state === 'error' ? 'Could not connect to the parking service. Try again when you are online.' : full ? 'Parking is full. No space will be assigned until one is confirmed available.' : 'Choose a space when you are ready. Other drivers’ assignments remain private.'}</p>
  </section>
}

function QrDisplay() {
  const [entryUrl, setEntryUrl] = useState(`${window.location.origin}/scan/${currentSiteId()}`)
  useEffect(() => { if (backendEnabled) void getPublicEntry(currentSiteId()).then((entry) => setEntryUrl(entry.entry_url)).catch(() => undefined) }, [])
  return <div className="qr-display-page"><div className="qr-display-card"><Brand /><div className="eyebrow">Main entrance checkpoint</div><h1>Scan to find<br /><em>your parking space</em></h1><p>Mount this code at the facility entrance.<br />No app or login required.</p><div className="qr-display-code"><QRCodeSVG value={entryUrl} size={320} level="H" includeMargin fgColor="#064e3b" bgColor="#ffffff" /></div><strong className="qr-display-url">{entryUrl}</strong><span className="qr-display-note">SmartPark visitor entry · map opens instantly</span></div></div>
}

type BeforeInstallPromptEvent = Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }> }

function Brand({ compact = false, logoUrl }: { compact?: boolean; logoUrl?: string }) {
  return <div className={`brand ${compact ? 'brand-compact' : ''}`}>{logoUrl ? <img className="brand-logo-image" src={logoUrl} alt="Facility logo" /> : <span className="brand-mark"><CarFront size={compact ? 17 : 20} strokeWidth={2.5} /></span>}<span>Smart <span>Park</span></span></div>
}

function DriverHome({ screen, onNavigate, spaces, visitorStarted, visitorTariff, checkpointError, onGuest, reservationMessage, canReserve, onOccupy, onRequestExit, onPayDemo, invoice, billingMessage, availabilityState, mobileOpen, setMobileOpen, siteName, siteLogo, installPrompt, destination, lotConfigured, assignment, estimate }: { screen: DriverScreen; onNavigate: (screen: DriverScreen) => void; spaces: Space[]; visitorStarted: boolean; visitorTariff: TariffSettings | null; checkpointError: string; onGuest: () => void; reservationMessage: string; canReserve: boolean; onOccupy: () => void; onRequestExit: () => void; onPayDemo: () => void; invoice: VisitorInvoice | null; billingMessage: string; availabilityState: 'demo' | 'loading' | 'live' | 'error'; mobileOpen: boolean; setMobileOpen: (value: boolean) => void; siteName: string; siteLogo: string; installPrompt: BeforeInstallPromptEvent | null; destination: CampusDestination | null; lotConfigured: boolean; assignment: { id: string; space_id: string; status: string; created_at: string; occupied_at: string | null } | null; estimate: { duration_minutes: number; amount_minor: number; currency: string; tariff: { free_minutes: number; block_minutes: number; block_price_minor: number } } | null }) {
  const assigned = spaces.find((space) => space.visitor === 'You · Guest')
  const full = lotConfigured && availabilityState !== 'loading' && availabilityState !== 'error' && !spaces.some((space) => space.status === 'available') && !assigned
  const install = async () => { if (!installPrompt) return; await installPrompt.prompt() }
  if (screen !== 'home') return <DriverRoute screen={screen} onNavigate={onNavigate} spaces={spaces} assigned={assigned} full={full} visitorStarted={visitorStarted} checkpointError={checkpointError} onGuest={onGuest} reservationMessage={reservationMessage} canReserve={canReserve} onOccupy={onOccupy} onRequestExit={onRequestExit} onPayDemo={onPayDemo} invoice={invoice} billingMessage={billingMessage} availabilityState={availabilityState} siteName={siteName} siteLogo={siteLogo} destination={destination} lotConfigured={lotConfigured} assignment={assignment} estimate={estimate} visitorTariff={visitorTariff} />
  return <div className="driver-shell">
    <header className="site-nav"><Brand logoUrl={siteLogo} /><nav className={mobileOpen ? 'nav-links mobile-nav-open' : 'nav-links'}><a href="#how" onClick={() => setMobileOpen(false)}>How it works</a><a href="#features" onClick={() => setMobileOpen(false)}>Features</a><a href="#about" onClick={() => setMobileOpen(false)}>About us</a><a href="/install" onClick={() => setMobileOpen(false)}>Install app</a></nav><div className="nav-actions"><button className="icon-button mobile-menu" onClick={() => setMobileOpen(!mobileOpen)} aria-label="Toggle navigation">{mobileOpen ? <X size={21} /> : <Menu size={21} />}</button></div></header>
    <main>{installPrompt && !visitorStarted && <div className="install-notice"><Sparkles size={15} /><span>SmartPark works in your browser. Installation is optional.</span><button onClick={install}>Add to Home Screen</button></div>}
      <section className="hero-grid">
        <div className="hero-copy fade-up"><div className="eyebrow"><span className="pulse-dot" /> Parking made simple</div><h1>Welcome to<br /><em>SmartPark.</em></h1><p className="hero-subtitle">Move smart. <strong>Park easy.</strong></p><p className="hero-description">Check availability, get directions to the entrance, and check in with security. Your bay is assigned only after your arrival is verified.</p><div className="hero-actions"><button className="button button-primary" onClick={() => onNavigate('navigate')}>Get directions <ArrowRight size={17} /></button></div>{reservationMessage && <p className="login-error" role="status">{reservationMessage} {!lotConfigured && <a href="/admin">ParkTech staff setup</a>}</p>}<div className="hero-note"><ShieldCheck size={16} /> No account required · location stays on your device</div></div>
        <div className="hero-visual fade-up delay-1"><div className="hero-image"><div className="image-glow" /><div className="parking-pill"><span className="live-dot" /> {!lotConfigured ? 'Waiting for lot setup' : availabilityState === 'live' ? 'Live availability' : availabilityState === 'loading' ? 'Checking spaces' : availabilityState === 'error' ? 'Availability offline' : 'Demo availability'} <strong>{!lotConfigured || availabilityState === 'loading' || availabilityState === 'error' ? '—' : `${spaces.filter((s) => s.status === 'available').length} / ${spaces.length}`}</strong></div><div className="hero-car"><CarFront size={98} strokeWidth={1.2} /></div><div className="hero-road-line line-one" /><div className="hero-road-line line-two" /><div className="image-tag"><MapPin size={15} /> {siteName}</div></div><div className="visual-caption"><span><strong>01</strong> Find your spot</span><span className="caption-line" /><span>Real-time guidance <ArrowDownRight size={16} /></span></div></div>
      </section>
      <div className="journey-strip" aria-label="Parking journey"><div className="journey-step active"><span>01</span><strong>Navigate</strong></div><div className="journey-connector" /><div className="journey-step"><span>02</span><strong>Arrive</strong></div><div className="journey-connector" /><div className="journey-step"><span>03</span><strong>Park</strong></div></div>
      {!lotConfigured && <section className="availability-summary"><div><span>Facility setup</span><strong>Not ready</strong></div><p>ParkTech has not configured this facility yet. Live availability and driver reservations appear after the ParkTech administrator completes facility setup.</p></section>}
      {lotConfigured && <AvailabilitySummary spaces={spaces} state={availabilityState} />}
      <CampusMapCard destination={destination} />
      {!lotConfigured ? <div className="empty-admin"><h3>This lot is ready for setup.</h3><p>Open the staff interface to configure facility details.</p><a className="button button-primary" href="/admin">Open staff interface</a></div> : visitorStarted || full ? <DriverFlow spaces={spaces} assigned={assigned} full={full} siteName={siteName} onOccupy={onOccupy} onRequestExit={onRequestExit} onPayDemo={onPayDemo} invoice={invoice} billingMessage={billingMessage} /> : <ParkingMap spaces={spaces} highlight={assigned?.id} />}
      <section className="feature-strip" id="features"><Feature icon={<Zap />} number="01" title="Real-time assignment" copy="No circling, no guesswork. Your spot is ready when you are." /><Feature icon={<Navigation />} number="02" title="Easy navigation" copy="Follow clear directions from the entrance right to your space." /><Feature icon={<ShieldCheck />} number="03" title="Safe & organized" copy="A calmer, cleaner parking experience for every visitor." /></section>
      <section className="how-section" id="how"><div><div className="eyebrow">How SmartPark works</div><h2>Parking, without<br /><em>the friction.</em></h2><p className="campus-context">{siteName} · parking lot</p></div><div className="how-steps"><div><span>01</span><p>Scan the QR code at the entrance.</p></div><div><span>02</span><p>Get assigned a spot in seconds.</p></div><div><span>03</span><p>Follow the guide and park easy.</p></div></div></section>
      <section className="about-section" id="about"><div className="eyebrow">About ParkTech</div><h2>Parking made easier for <em>everyone.</em></h2><p>ParkTech operates a simple, reliable parking service for organizations and individual drivers. Check live space availability, reserve a bay, and follow directions to the lot. Visitors do not need to create an account.</p></section>
    </main><Footer siteName={siteName} siteLogo={siteLogo} />
  </div>
}

function Footer({ siteName, siteLogo, compact = false }: { siteName: string; siteLogo: string; compact?: boolean }) {
  const [facility, setFacility] = useState<{ address: string; contact_email: string; contact_phone: string; social_links: Record<string, string> } | null>(null)
  const [email, setEmail] = useState('')
  const [consent, setConsent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [unsubscribeToken, setUnsubscribeToken] = useState('')
  useEffect(() => { if (backendEnabled) void getPublicFacility().then(setFacility).catch(() => setFacility(null)) }, [])
  const submit = async (event: { preventDefault(): void }) => {
    event.preventDefault(); setMessage(''); setBusy(true)
    try {
      const result = await subscribeNewsletter({ email: email.trim().toLowerCase(), consent, source: 'footer', website: (document.getElementById('footer-website') as HTMLInputElement | null)?.value || '' })
      setMessage(result.message); setEmail(''); setConsent(false); setUnsubscribeToken(result.unsubscribe_token || '')
    } catch (error) { const reason = error instanceof Error ? error.message : ''; setMessage(reason.includes('Failed to fetch') || reason.includes('not configured') ? 'Could not connect. Please try again when online.' : reason.includes('429') ? 'Too many attempts. Please try again later.' : 'Please check your email and consent, then try again.') }
    finally { setBusy(false) }
  }
  const quick = [['Home', '/'], ['Find Parking', '/visit/welcome'], ['Staff dashboard', '/admin'], ['How It Works', '/#how'], ['Parking Rules', '/help'], ['Help Center', '/help'], ['FAQs', '/faq']]
  const legal = [['Contact Us', '/contact'], ['Privacy Policy', '/privacy'], ['Terms of Service', '/terms'], ['Accessibility', '/accessibility']]
  return <footer className={`public-footer ${compact ? 'public-footer-compact' : ''}`}><div className="footer-grid"><section className="footer-brand"><Brand compact logoUrl={siteLogo} /><h2>{siteName || 'Smart Park'}</h2><p>Simple parking. Clear directions. Real-time availability.</p>{facility?.address && <p>{facility.address}</p>}{facility?.contact_email && <a href={`mailto:${facility.contact_email}`}>{facility.contact_email}</a>}{facility?.contact_phone && <a href={`tel:${facility.contact_phone}`}>{facility.contact_phone}</a>}</section><nav aria-label="Quick links"><h3>Quick Links</h3>{quick.map(([label, href]) => <a key={label} href={href}>{label}</a>)}</nav><nav aria-label="Support and legal"><h3>Support &amp; Legal</h3>{legal.map(([label, href]) => <a key={label} href={href}>{label}</a>)}</nav>{!compact && <section className="footer-newsletter"><h3>Stay Updated</h3><p>Get occasional parking service updates.</p><form onSubmit={submit}><label className="footer-honey" aria-hidden="true" htmlFor="footer-website">Leave this field empty<input id="footer-website" name="website" type="text" tabIndex={-1} autoComplete="off" /></label><label className="sr-only" htmlFor="footer-email">Email address</label><input id="footer-email" type="email" required maxLength={160} autoComplete="email" placeholder="you@example.com" value={email} onChange={(event) => setEmail(event.target.value)} /><label className="footer-consent"><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} required /> I agree to receive updates. I can unsubscribe anytime.</label><button type="submit" disabled={busy}>{busy ? 'Submitting…' : 'Subscribe'}</button></form>{message && <p className="footer-message" role="status">{message}{unsubscribeToken && <> <button type="button" className="footer-inline" onClick={async () => { try { await unsubscribeNewsletter(unsubscribeToken); setMessage('You have been unsubscribed.'); setUnsubscribeToken('') } catch { setMessage('Could not unsubscribe while offline. Please try again.') } }}>Unsubscribe</button></>}</p>}{facility && Object.entries(facility.social_links || {}).filter(([, url]) => url.startsWith("https://")).map(([network, url]) => <a className="footer-social" key={network} href={url} target="_blank" rel="noopener noreferrer">{network}</a>)}</section>}</div><div className="footer-bottom"><span>© {new Date().getFullYear()} {siteName || 'Smart Park'}. All rights reserved.</span><span>Ghana · Africa/Accra · GHS</span></div></footer>
}

function PublicInfoPage({ path, onHome }: { path: string; onHome: () => void }) {
  const [message, setMessage] = useState('')
  const title: Record<string, string> = { '/help': 'Help Center', '/faq': 'Frequently asked questions', '/contact': 'Contact ParkTech', '/privacy': 'Privacy Policy', '/terms': 'Terms of Service', '/accessibility': 'Accessibility', '/unsubscribe': 'Unsubscribe' }
  useEffect(() => { if (path !== '/unsubscribe') return; const token = new URLSearchParams(window.location.search).get('token'); if (!token) { setMessage('An unsubscribe token is missing. Use the link from your subscription confirmation.'); return } void unsubscribeNewsletter(token).then((result) => setMessage(result.message)).catch(() => setMessage('Could not complete the request while offline. Please try again.')) }, [path])
  const copy: Record<string, string> = { '/help': 'Open the live parking map, check bay availability, and use GPS directions to reach the facility. Bay sensors report occupied or free; they do not identify drivers.', '/faq': 'Do I need an account? No. Visitors can use the QR visitor portal without registration. How is arrival matched? A close-range sensor matches one waiting visitor, or security selects the right visitor when there is more than one. How do I pay? Pay the attendant by cash or another accepted offline method. A bay is released after security confirms the vehicle has exited.', '/contact': 'For parking assistance, speak with the attendant at the facility entrance. Owner contact details can be added when configured.', '/privacy': 'Smart Park uses an anonymous visitor session to match a driver with a verified arrival. Location is requested only when you choose navigation and is not stored as a travel history. Newsletter email is stored only after explicit consent and can be unsubscribed.', '/terms': 'Follow posted facility signs and security instructions. Park only in your assigned bay. Availability depends on facility connectivity and sensor state. Payment is collected manually at the security checkpoint; security confirms exit before a bay becomes available again.', '/accessibility': 'The visitor portal supports keyboard access, labeled forms, visible focus indicators, and reduced-motion preferences. Contact the parking attendant if you need assistance using the service.' }
  return <div className="public-info-shell"><header><button className="brand-button" onClick={onHome}><Brand /></button><a className="button button-secondary" href="/admin">Staff dashboard</a></header><main><span className="eyebrow">ParkTech visitor information</span><h1>{title[path]}</h1>{path === '/unsubscribe' ? <p role="status">{message}</p> : <p>{copy[path]}</p>}</main><Footer siteName="Smart Park" siteLogo="" compact /></div>
}

function DriverRoute({ screen, onNavigate, spaces, assigned, full, visitorStarted, checkpointError, onGuest, reservationMessage, canReserve, onOccupy, onRequestExit, onPayDemo, invoice, billingMessage, availabilityState, siteName, siteLogo, destination, lotConfigured, assignment, estimate, visitorTariff }: { screen: DriverScreen; onNavigate: (screen: DriverScreen) => void; spaces: Space[]; assigned?: Space; full: boolean; visitorStarted: boolean; checkpointError: string; onGuest: () => void; reservationMessage: string; canReserve: boolean; onOccupy: () => void; onRequestExit: () => void; onPayDemo: () => void; invoice: VisitorInvoice | null; billingMessage: string; availabilityState: 'demo' | 'loading' | 'live' | 'error'; siteName: string; siteLogo: string; destination: CampusDestination | null; lotConfigured: boolean; assignment: { id: string; space_id: string; status: string; created_at: string; occupied_at: string | null } | null; estimate: { duration_minutes: number; amount_minor: number; currency: string; tariff: { free_minutes: number; block_minutes: number; block_price_minor: number } } | null; visitorTariff: TariffSettings | null }) {
  const heading: Record<DriverScreen, string> = { home: 'Parking', welcome: 'Welcome', availability: 'Live availability', navigate: 'Directions', 'my-space': 'My parking space', session: 'Active session', payment: 'Demo payment', receipt: 'Receipt', scan: 'Checkpoint', install: 'Install SmartPark' }
  return <div className="driver-shell driver-route-shell">
    <header className="site-nav route-nav"><button className="brand-button" onClick={() => onNavigate('home')} aria-label="Smart Park home"><Brand logoUrl={siteLogo} /></button><span className="route-site-name">{siteName}</span><a className="route-help" href="/install">Install</a></header>
    <main className="driver-route-main">
      <div className="route-breadcrumb"><button onClick={() => onNavigate('home')}>Home</button><span>/</span><strong>{heading[screen]}</strong></div>
      {checkpointError && <p className="login-error" role="alert">{checkpointError}</p>}
      {screen === 'welcome' && <section className="route-card welcome-card"><span className="eyebrow">ParkTech parking · {availabilityState === 'live' ? 'live' : 'offline'}</span><h1>Welcome to<br /><em>{siteName}</em></h1><p>Spaces available: <strong>{spaces.filter((space) => space.status === 'available').length} of {spaces.length}</strong>. Check bay sensors, choose an available space, and follow GPS directions to the lot.</p><div className="availability-legend"><span><i className="legend-dot available-dot" /> Available</span><span><i className="legend-dot assigned-dot" /> Reserved</span><span><i className="legend-dot occupied-dot" /> Occupied</span><span><i className="legend-dot unavailable-dot" /> Unknown</span></div>{visitorTariff && <p className="route-copy">Parking charges: first {visitorTariff.free_minutes} minutes free, then GH₵{(visitorTariff.block_price_minor / 100).toFixed(2)} per {visitorTariff.block_minutes} minutes. Times use Africa/Accra.</p>}<div className="privacy-note"><ShieldCheck size={17} /> No driver account required. GPS stays on your device unless you choose live navigation.</div><div className="route-actions"><button className="button button-primary" onClick={() => onNavigate('navigate')}>Get directions <ArrowRight size={16} /></button><button className="button button-secondary" onClick={() => onNavigate('availability')}>Check availability</button></div><p className="form-help">Park only in the space assigned to you by staff and follow posted signs.</p></section>}
      {screen === 'availability' && <section className="route-panel"><div className="route-panel-heading"><div><span className="eyebrow">{availabilityState === 'live' ? 'Live status' : availabilityState === 'error' ? 'Connection problem' : 'Checking spaces'}</span><h1>Choose an available space</h1></div><span className="availability-count">{spaces.filter((space) => space.status === 'available').length} / {spaces.length} available</span></div><p className="route-copy">Availability is updated from the two live bay sensors. You can request an assignment for any confirmed-free bay.</p><ParkingMap spaces={spaces} highlight={assigned?.id} /><div className="route-actions"><button className="button button-primary" disabled={!assigned} onClick={() => onNavigate('my-space')}>VIEW MY BAY <ArrowRight size={16} /></button><p className="route-warning">Choose a confirmed-free bay and use GPS directions to reach the facility.</p></div>{reservationMessage && <p className="gps-message" role="alert">{reservationMessage}</p>}{full && <p className="gps-message" role="status">No bays are free right now. You can join the queue and Smart Park will check again.</p>}</section>}
      {screen === 'navigate' && <section className="route-panel"><div className="route-panel-heading"><div><span className="eyebrow">Waiting for parking entrance detection</span><h1>{assigned ? `DRIVE TO BAY ${bayLabel(assigned.id)}` : 'Find your parking'}</h1></div><span className="status-chip">{destination?.latitude !== null && destination?.latitude !== undefined ? 'Directions ready' : 'Address not set'}</span></div>{!assigned && <p className="route-warning">Use GPS directions to reach the facility entrance, then confirm your bay with staff.</p>}{reservationMessage && <p className="gps-message" role="status">{reservationMessage}</p>}<CampusMapCard destination={destination} /><div className="route-actions"><button className="button button-primary" onClick={() => onNavigate('my-space')}>View my parking space <ArrowRight size={16} /></button><button className="button button-secondary" onClick={() => onNavigate('availability')}>Availability</button></div></section>}
      {screen === 'my-space' && <section className="route-panel"><div className="route-panel-heading"><div><span className="eyebrow">Final approach</span><h1>Your assigned parking space</h1></div></div>{assigned || assignment ? <DriverFlow spaces={spaces} assigned={assigned} full={full} siteName={siteName} onOccupy={onOccupy} onRequestExit={onRequestExit} onPayDemo={onPayDemo} invoice={invoice} billingMessage={billingMessage} /> : <div className="route-empty"><h2>You have not reserved a bay yet.</h2><p>Check availability and reserve when you arrive at the checkpoint.</p><button className="button button-primary" onClick={() => onNavigate('availability')}>Check availability</button></div>}<div className="route-actions"><button className="button button-secondary" onClick={() => onNavigate(assignment?.occupied_at ? 'session' : 'navigate')}>{assignment?.occupied_at ? 'View active session' : 'View directions'}</button></div></section>}
      {screen === 'session' && <ParkingSessionScreen assignment={assignment} estimate={estimate} siteName={siteName} onRequestExit={onRequestExit} invoice={invoice} message={billingMessage} />}
      {screen === 'payment' && <PaymentScreen invoice={invoice} estimate={estimate} message={billingMessage} onPay={onPayDemo} onRequestExit={onRequestExit} onReceipt={() => onNavigate('receipt')} />}
      {screen === 'receipt' && <ReceiptScreen invoice={invoice} assignment={assignment} siteName={siteName} message={billingMessage} onReturn={() => onNavigate('home')} />}
      {screen === 'scan' && <section className="route-card"><span className="eyebrow">Checkpoint entry</span><h1>Welcome to {siteName}</h1><p>Continue to see current availability and join the arrival queue. No account is needed.</p><button className="button button-primary" onClick={() => onNavigate('welcome')}>Continue <ArrowRight size={16} /></button></section>}
      {!lotConfigured && <p className="route-warning">Facility details are not configured yet. You can still view the live sensor bays and use the staff interface.</p>}
    </main>
    <Footer siteName={siteName} siteLogo={siteLogo} compact />
    <nav className="visitor-bottom-nav" aria-label="Visitor navigation"><button className={screen === 'welcome' ? 'active' : ''} onClick={() => onNavigate('welcome')}><CarFront size={17} /><span>Home</span></button><button className={screen === 'availability' || screen === 'my-space' ? 'active' : ''} onClick={() => onNavigate('availability')}><Grid2x2 size={17} /><span>Parking</span></button><button className={screen === 'session' || screen === 'payment' || screen === 'receipt' ? 'active' : ''} onClick={() => onNavigate('session')}><Clock3 size={17} /><span>Session</span></button></nav>
  </div>
}

function ParkingSessionScreen({ assignment, estimate, siteName, onRequestExit, invoice, message }: { assignment: { space_id: string; status: string; created_at: string; occupied_at: string | null } | null; estimate: { duration_minutes: number; amount_minor: number; currency: string; tariff: { free_minutes: number; block_minutes: number; block_price_minor: number } } | null; siteName: string; onRequestExit: () => void; invoice: VisitorInvoice | null; message: string }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer) }, [])
  const minutes = assignment?.occupied_at ? Math.max(0, Math.floor((now - Date.parse(assignment.occupied_at)) / 60000)) : 0
  const elapsed = `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`
  const freeMinutes = estimate?.tariff.free_minutes ?? invoice?.tariff_snapshot.free_minutes ?? 30
  const amount = invoice?.amount_minor ?? estimate?.amount_minor ?? 0
  const ghc = new Intl.NumberFormat('en-GH', { style: 'currency', currency: 'GHS' }).format(amount / 100)
  return <section className="route-panel session-panel"><div className="route-panel-heading"><div><span className="eyebrow">{siteName}</span><h1>Parking session</h1></div><span className={`status-chip ${assignment?.occupied_at ? 'status-active' : ''}`}>{assignment?.occupied_at ? 'You are parked' : 'Waiting for occupancy'}</span></div><div className="session-space"><span>Assigned bay</span><strong>{assignment?.space_id || '—'}</strong><small>{assignment?.occupied_at ? 'Occupancy confirmed by sensor or staff' : 'The timer starts after occupancy is confirmed.'}</small></div><div className="session-stats"><div><span>Elapsed</span><strong>{assignment?.occupied_at ? elapsed : '—'}</strong></div><div><span>Free time</span><strong>{assignment?.occupied_at ? `${Math.max(0, freeMinutes - minutes)} min` : `${freeMinutes} min`}</strong></div><div><span>Estimated charge</span><strong>{ghc}</strong></div></div><p className="route-copy">Prototype tariff: first {freeMinutes} minutes free, then GH₵{((estimate?.tariff.block_price_minor ?? 200) / 100).toFixed(2)} per started {estimate?.tariff.block_minutes ?? 10} minute block. Final amount is calculated by the server.</p><div className="route-actions"><button className="button button-primary" onClick={onRequestExit} disabled={!assignment?.occupied_at}>VIEW AMOUNT TO PAY</button></div>{message && <p className="gps-message" role="status">{message}</p>}</section>
}

function PaymentScreen({ invoice, estimate, message, onPay, onRequestExit, onReceipt }: { invoice: VisitorInvoice | null; estimate: { duration_minutes: number; amount_minor: number; currency: string } | null; message: string; onPay: () => void; onRequestExit: () => void; onReceipt: () => void }) {
  const amount = invoice?.amount_minor ?? estimate?.amount_minor ?? 0
  const currency = invoice?.currency ?? estimate?.currency ?? 'GHS'
  const label = new Intl.NumberFormat('en-GH', { style: 'currency', currency }).format(amount / 100)
  return <section className="route-card payment-card"><span className="eyebrow">Manual payment only</span><h1>Pay at security checkpoint</h1>{invoice ? <><div className="payment-amount"><span>{invoice.status === 'PAYMENT_PENDING' ? 'Amount to pay' : 'Payment status'}</span><strong>{invoice.status === 'PAYMENT_PENDING' ? label : invoice.status.replaceAll('_', ' ')}</strong></div><dl><div><dt>Parking duration</dt><dd>{invoice.duration_minutes} minutes</dd></div><div><dt>Free allowance</dt><dd>{invoice.tariff_snapshot.free_minutes} minutes</dd></div></dl><p className="route-warning">PAY AT SECURITY CHECKPOINT. The guard records cash, mobile money, or other manual payment. Payment alone does not release the bay.</p></> : estimate ? <><div className="payment-amount"><span>Current estimated fee</span><strong>{label}</strong></div><p>{estimate.duration_minutes} minutes parked. Request the final amount when leaving.</p><button className="button button-primary" onClick={onRequestExit}>VIEW AMOUNT TO PAY</button></> : <p>Your final amount appears after confirmed occupancy and an exit request.</p>}{message && <p className="gps-message" role="status">{message}</p>}</section>
}
function ReceiptScreen({ invoice, assignment, siteName, message, onReturn }: { invoice: VisitorInvoice | null; assignment: { space_id: string; occupied_at: string | null } | null; siteName: string; message: string; onReturn: () => void }) {
  const amount = invoice?.amount_minor ?? 0
  const label = new Intl.NumberFormat('en-GH', { style: 'currency', currency: invoice?.currency ?? 'GHS' }).format(amount / 100)
  return <section className="route-card receipt-card"><div className="receipt-check"><Check size={25} /></div><span className="eyebrow">ParkTech · payment receipt</span><h1>{invoice?.status === 'DEMO_PAID' || invoice?.status === 'PAID' ? 'Payment complete' : 'Exit receipt'}</h1><p className="receipt-demo">SIMULATED · No real payment was processed</p><dl><div><dt>Receipt</dt><dd>{invoice?.id || 'No charge due'}</dd></div><div><dt>Facility</dt><dd>{siteName}</dd></div><div><dt>Space</dt><dd>{assignment?.space_id || '—'}</dd></div><div><dt>Entry time</dt><dd>{assignment?.occupied_at ? new Date(assignment.occupied_at).toLocaleString('en-GH', { timeZone: 'Africa/Accra' }) : '—'}</dd></div><div><dt>Duration</dt><dd>{invoice ? `${invoice.duration_minutes} minutes` : 'No billed parking'}</dd></div><div><dt>Total</dt><dd>{label}</dd></div><div><dt>Status</dt><dd>{invoice?.status || 'No charge'}</dd></div></dl><p className="route-copy">Payment does not release the parking bay. ParkTech confirms departure separately.</p>{message && <p className="gps-message" role="status">{message}</p>}<button className="button button-primary" onClick={onReturn}>Return to parking</button></section>
}

function InstallScreen({ onBack }: { onBack: () => void }) {
  return <div className="install-screen"><section className="route-card"><span className="eyebrow">Smart Park · installable PWA</span><h1>Park from your phone</h1><p>Add Smart Park to your home screen for quick access to availability, directions, and your active parking session.</p><ol><li>Open the browser menu.</li><li>Choose <strong>Install app</strong> or <strong>Add to Home Screen</strong>.</li><li>Open Smart Park from the new icon.</li></ol><p className="route-copy">Live availability and payments need an internet connection. The offline shell does not reserve spaces or process payments.</p><button className="button button-primary" onClick={onBack}>Back to parking</button></section></div>
}

function Feature({ icon, number, title, copy }: { icon: React.ReactNode; number: string; title: string; copy: string }) { return <div className="feature-card"><div className="feature-top"><span className="feature-icon">{icon}</span><span className="feature-number">{number}</span></div><h3>{title}</h3><p>{copy}</p><ArrowRight className="feature-arrow" size={18} /></div> }

function MapRecenter({ position }: { position: [number, number] }) {
  const map = useMap()
  useEffect(() => {
    map.panTo(position, { animate: !window.matchMedia('(prefers-reduced-motion: reduce)').matches, duration: 0.5 })
  }, [map, position])
  return null
}

function CampusMapCard({ destination }: { destination: CampusDestination | null }) {
  const [position, setPosition] = useState<[number, number] | null>(null)
  const [accuracy, setAccuracy] = useState<number | null>(null)
  const [locationState, setLocationState] = useState<'idle' | 'loading' | 'ready' | 'denied' | 'offline'>('idle')
  const [roadRoute, setRoadRoute] = useState<[number, number][] | null>(null)
  const [routeStats, setRouteStats] = useState<{ distance: number; duration: number } | null>(null)
  const [routeError, setRouteError] = useState(false)
  const watchId = useRef<number | null>(null)
  const lastRouteAt = useRef(0)
  const [online, setOnline] = useState(navigator.onLine)
  const destinationPosition: [number, number] = [destination?.latitude ?? 0, destination?.longitude ?? 0]
  useEffect(() => {
    if (!backendEnabled || destination?.latitude) return
    void getFacilityConfig().then((site) => {
      if (site.latitude !== null && site.longitude !== null) window.dispatchEvent(new CustomEvent('smartpark:facility-coordinates', { detail: site }))
    }).catch(() => undefined)
  }, [destination?.latitude])
  const internalWaypoints: [number, number][] = destination?.waypoints?.map((point) => [point.latitude, point.longitude]) || []
  const routePoints: [number, number][] = roadRoute || (position ? [position, ...internalWaypoints, destinationPosition] : (internalWaypoints.length ? internalWaypoints : [destinationPosition]))
  useEffect(() => {
    const updateOnline = () => { setOnline(navigator.onLine); if (!navigator.onLine) setLocationState('offline') }
    window.addEventListener('online', updateOnline)
    window.addEventListener('offline', updateOnline)
    return () => {
      window.removeEventListener('online', updateOnline)
      window.removeEventListener('offline', updateOnline)
      if (watchId.current !== null) navigator.geolocation?.clearWatch(watchId.current)
    }
  }, [])
  useEffect(() => {
    if (!position || !destination || !online) return
    const now = Date.now()
    if (now - lastRouteAt.current < 10_000) return
    const controller = new AbortController()
    const timer = window.setTimeout(async () => {
      lastRouteAt.current = Date.now()
      const base = (import.meta.env.VITE_ROUTING_URL as string | undefined)?.replace(/\/$/, '') || 'https://router.project-osrm.org'
      const url = `${base}/route/v1/driving/${position[1]},${position[0]};${destinationPosition[1]},${destinationPosition[0]}?overview=full&geometries=geojson`
      try {
        const response = await fetch(url, { signal: controller.signal })
        if (!response.ok) throw new Error('route provider unavailable')
        const payload = await response.json() as { routes?: { geometry?: { coordinates?: [number, number][] }; distance?: number; duration?: number }[] }
        const route = payload.routes?.[0]
        if (!route?.geometry?.coordinates?.length) throw new Error('no route')
        setRoadRoute(route.geometry.coordinates.map(([lng, lat]) => [lat, lng]))
        setRouteStats({ distance: route.distance || 0, duration: route.duration || 0 })
        setRouteError(false)
      } catch (error) { if (!controller.signal.aborted) { setRouteError(true); setRoadRoute(null); setRouteStats(null) } }
    }, 300)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [position, destination?.latitude, destination?.longitude, online])
    const locate = () => {
    if (!navigator.geolocation) { setLocationState('denied'); return }
    if (watchId.current !== null) navigator.geolocation.clearWatch(watchId.current)
    setLocationState('loading')
    watchId.current = navigator.geolocation.watchPosition((result) => {
      setPosition([result.coords.latitude, result.coords.longitude])
      setAccuracy(result.coords.accuracy)
      setLocationState(navigator.onLine ? 'ready' : 'offline')
    }, (error) => setLocationState(error.code === error.PERMISSION_DENIED ? 'denied' : navigator.onLine ? 'denied' : 'offline'), { enableHighAccuracy: true, timeout: 12000, maximumAge: 5000 })
  }
  const mapsUrl = new URL(destination?.google_maps_url || 'https://www.google.com/maps/dir/?api=1')
  if (position) mapsUrl.searchParams.set('origin', `${position[0]},${position[1]}`)
  if (!destination || destination.latitude === null || destination.longitude === null) return <section className="gps-card"><div className="gps-card-head"><div><div className="eyebrow"><MapPin size={13} /> Parking directions</div><h3>Set the facility entrance</h3><p>Staff can add the parking entrance in Facility setup.</p></div></div><p className="gps-route-note">Your live GPS can still be read on this device. Set the facility coordinates to show directions to the lot.</p><div className="gps-actions"><button className="button button-primary" onClick={locate} disabled={!online}><Navigation size={16} /> {locationState === 'loading' ? 'Waiting for GPS…' : locationState === 'ready' ? 'Update live position' : 'GET MY GPS'}</button><a className="button button-secondary" href="/admin/settings">Facility setup</a></div>{locationState === 'ready' && position && <p className="gps-message" role="status">Current device location: {position[0].toFixed(6)}, {position[1].toFixed(6)} (±{accuracy ? Math.round(accuracy) : '?'} m). Location remains on this device.</p>}{locationState === 'denied' && <p className="gps-message" role="status">Location permission was denied or GPS is unavailable. Browser GPS generally requires HTTPS or localhost.</p>}</section>
  return <section className="gps-card"><div className="gps-card-head"><div><div className="eyebrow"><MapPin size={13} /> Parking directions · facility entrance</div><h3>{destination?.display_name || 'Parking lot'}</h3><p>{destination?.address || 'Loading destination coordinates...'}</p></div><span className="gps-badge"><span className="live-dot" /> {locationState === 'ready' ? 'Live position' : locationState === 'loading' ? 'Waiting for GPS' : online ? 'Destination ready' : 'Offline'}</span></div><p className="gps-privacy">Live navigation starts only when you choose it. Smart Park does not store or send your location to its backend. For road routing, the configured routing provider receives your current position and this facility entrance; OpenStreetMap also loads map tiles. Google Maps receives your origin only if you open its directions link. Your GPS cannot identify an individual bay.</p><div className="gps-map-wrap"><MapContainer center={destinationPosition} zoom={16} scrollWheelZoom={false} className="gps-map"><TileLayer attribution='&copy; OpenStreetMap contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" /><Polyline positions={routePoints} pathOptions={{ color: '#2563eb', weight: 5 }} /><CircleMarker center={destinationPosition} radius={10} pathOptions={{ color: '#064e3b', fillColor: '#087f5b', fillOpacity: 1 }}><Popup>{destination?.display_name ?? "Parking lot entrance"}</Popup></CircleMarker>{position && <><MapRecenter position={position} /><CircleMarker center={position} radius={8} pathOptions={{ color: '#fff', weight: 3, fillColor: '#f59e0b', fillOpacity: 1 }}><Popup>Your current position · ±{accuracy ? Math.round(accuracy) : '?'} m</Popup></CircleMarker></>}</MapContainer><div className="map-label destination-label"><MapPin size={14} /> {destination?.display_name ?? "Parking lot"}{routeStats && ` · ${(routeStats.distance / 1000).toFixed(1)} km · ${Math.ceil(routeStats.duration / 60)} min`}</div></div><p className="gps-route-note">{routeStats ? `Road route · about ${(routeStats.distance / 1000).toFixed(1)} km and ${Math.ceil(routeStats.duration / 60)} minutes. Follow signs for the final approach.` : routeError ? 'The routing provider is unavailable. The straight-line guide and external directions link are still available.' : 'The line is a guide to the facility entrance. Individual indoor bays are shown separately in the parking diagram.'}</p><div className="gps-actions"><button className="button button-primary" onClick={locate} disabled={!online}><Navigation size={16} /> {locationState === 'loading' ? 'Waiting for GPS…' : locationState === 'ready' ? 'Update live position' : 'START DIRECTIONS'}</button>{locationState === 'ready' && <button className="button button-secondary" onClick={() => { if (watchId.current !== null) navigator.geolocation.clearWatch(watchId.current); watchId.current = null; setLocationState('idle'); setPosition(null); setRoadRoute(null); setRouteStats(null) }}>Stop navigation</button>}<a className="button button-secondary" href={mapsUrl.toString()} target="_blank" rel="noreferrer"><ArrowRight size={16} /> OPEN IN GOOGLE MAPS</a></div>{locationState === 'denied' && <p className="gps-message" role="status">Location permission was denied or GPS is unavailable. The static destination and external directions link still work.</p>}{!online && <p className="gps-message" role="status">You are offline. The saved destination remains visible; road directions need a network connection.</p>}{accuracy !== null && accuracy > 100 && <p className="gps-message" role="status">GPS accuracy is about {Math.round(accuracy)} m. Use the map only for general guidance, not to identify adjacent parking spaces.</p>}</section>
}

function DriverFlow({ spaces, assigned, full, siteName, onOccupy, onRequestExit, onPayDemo, invoice, billingMessage }: { spaces: Space[]; assigned?: Space; full: boolean; siteName: string; onOccupy: () => void; onRequestExit: () => void; onPayDemo: () => void; invoice: VisitorInvoice | null; billingMessage: string }) {
  const userSpace = assigned || spaces.find((space) => space.visitor === 'You · Guest')
  return <section className="flow-section"><div className="flow-heading"><div><div className="eyebrow"><span className="pulse-dot" /> Visitor mode · {siteName}</div><h2>{full ? 'Parking is full.' : userSpace?.status === 'occupied' ? 'Vehicle parked successfully.' : userSpace ? 'Your space is reserved.' : 'Choose a space when you arrive.'}</h2></div><div className="flow-status"><span className="live-dot" /> Live updates on</div></div>{full && <div className="empty-state"><div className="empty-icon"><CarFront /></div><h3>Please wait for a space to become available.</h3><p>All spaces are currently in use. We will keep checking in real time.</p></div>}{userSpace && !full && <div className="assignment-layout"><div className="assignment-card"><span className="mini-label">{userSpace.status === 'occupied' ? 'Your parking space' : 'Assigned space'}</span><div className="assignment-space">{userSpace.id}</div><div className={`assignment-status ${userSpace.status}`}><span /> {statusLabel[userSpace.status]}</div><p>{userSpace.status === 'occupied' ? 'Your vehicle is parked. Have a great day!' : 'Follow the directions to your reserved bay. Phone GPS cannot distinguish adjacent spaces.'}</p>{userSpace.status !== 'occupied' && !backendEnabled && <button className="button button-primary button-full" onClick={onOccupy}><Check size={17} /> Simulate parking (demo)</button>}{userSpace.status !== 'occupied' && backendEnabled && <p className="gps-message" role="status">Waiting for security or an authorized occupancy sensor to confirm parking.</p>}{userSpace.status === 'occupied' && <button className="button button-secondary button-full" onClick={onRequestExit}>VIEW AMOUNT TO PAY</button>}{invoice && <div className="invoice-card"><strong>Parking invoice · {invoice.demo_payment ? 'DEMO tariff' : ''}</strong><span>{invoice.duration_minutes} minutes · {invoice.tariff_snapshot.free_minutes} min free</span><span>{invoice.status} · {(invoice.amount_minor / 100).toFixed(2)} {invoice.currency}</span>{invoice.status === 'PAYMENT_PENDING' && <p className="route-warning">PAY AT SECURITY CHECKPOINT</p>}<small>Pay at the security checkpoint. Only the guard-confirmed exit releases the bay.</small></div>}{billingMessage && <p className="form-message" role="status">{billingMessage}</p>}</div><ParkingMap spaces={spaces} highlight={userSpace.id} /></div>}{(full || !userSpace) && <ParkingMap spaces={spaces} />}<div className="legend"><span><i className="legend-dot available-dot" /> Available</span><span><i className="legend-dot assigned-dot" /> Reserved for you</span><span><i className="legend-dot occupied-dot" /> Occupied</span><span>Space colors show status; driver details stay private.</span></div></section>
}

function ParkingMap({ spaces, highlight, campus = false }: { spaces: Space[]; highlight?: string; campus?: boolean }) { return <div className={`parking-map ${campus ? 'campus-map' : ''}`}><div className="map-top"><span><MapPin size={15} /> {campus ? 'Parking lot' : 'Parking lane'}</span><span className="map-direction"><Navigation size={15} /> {campus ? 'Route to your bay' : 'Access road → bays'}</span></div>{campus ? <div className="campus-canvas"><div className="campus-label campus-gate">Main gate</div><div className="campus-label campus-library">Other buildings</div><div className="campus-label campus-lab">Parking entrance</div><div className="campus-road road-horizontal" /><div className="campus-road road-vertical" /><div className="campus-route"><span>YOU ARE HERE</span><ArrowRight /><ArrowDownRight /><ArrowRight /></div><div className="campus-destination"><MapPin size={15} /><strong>{highlight || 'L2'}</strong><small>Assigned bay</small></div><div className="campus-bays">{spaces.map((space) => <div key={space.id} className={`campus-bay ${space.status} ${highlight === space.id ? 'is-highlighted' : ''}`}><b>{space.id}</b><span>{statusLabel[space.status]}</span></div>)}</div></div> : <div className="parking-schematic"><div className="access-road"><span>ACCESS ROAD</span><ArrowRight /></div><div className="space-stack">{spaces.map((space) => <div key={space.id} className={`parking-space ${space.status} ${highlight === space.id ? 'is-highlighted' : ''}`}><span className="space-id">{bayLabel(space.id)}</span><CarFront size={23} aria-hidden="true" /><span>{highlight === space.id ? 'YOUR BAY' : statusLabel[space.status]}</span></div>)}</div></div>}</div> }

function bayLabel(id: string) { return ({ L1: 'A1', L2: 'A2', L3: 'B1', L4: 'B2' } as Record<string, string>)[id] || id }

function LiveBaysView() {
  const [bays, setBays] = useState<LiveBay[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    const refresh = () => void getLiveBays().then((result) => { if (active) { setBays(result.bays); setError('') } }).catch(() => { if (active) setError('Live bay service is unavailable.') })
    refresh()
    const timer = window.setInterval(refresh, 1000)
    return () => { active = false; window.clearInterval(timer) }
  }, [])
  const assign = async (bay: LiveBay) => {
    setBusy(bay.id); setError('')
    try { const result = await assignOpenBay(bay.id, bay.assigned !== 1); setBays((current) => current.map((item) => item.id === bay.id ? result : item)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not change bay assignment.') }
    finally { setBusy(null) }
  }
  const stateLabel: Record<LiveBay['display_state'], string> = { WAITING_FOR_SENSOR: 'WAITING FOR SENSOR', AVAILABLE: 'AVAILABLE', OCCUPIED: 'OCCUPIED', ASSIGNED: 'ASSIGNED TO DRIVER' }
  const setSensorState = async (bay: LiveBay, occupied: boolean) => {
    setBusy(bay.id); setError('')
    try { await reportDemoBay({ event_id: crypto.randomUUID(), device_id: 'web-demo', sensor_id: bay.id, space_id: bay.id, event_type: occupied ? 'bay_occupied' : 'bay_free' }); const result = await getLiveBays(); setBays(result.bays) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Simulator event failed.') }
    finally { setBusy(null) }
  }
  return <main className="dashboard-content live-bays-page"><div className="dashboard-intro"><div><p className="muted">Two bay sensor MVP</p><h2>Live occupancy</h2></div><span className="status-chip status-active">Polling every second</span></div><div className="live-bay-grid">{(['L1', 'L2'] as const).map((id) => { const bay = bays.find((item) => item.id === id); const state = bay?.display_state || 'WAITING_FOR_SENSOR'; return <article key={id} className={`live-bay-card ${state.toLowerCase()}`}><div><span className="panel-kicker">Parking bay</span><h3>{id}</h3></div><strong>{stateLabel[state]}</strong><small>{state === 'WAITING_FOR_SENSOR' ? 'Initial reading pending or sensor health is stale.' : bay?.last_seen ? `Last sensor update ${new Date(bay.last_seen).toLocaleTimeString()}` : 'Waiting for sensor update.'}</small><button className="button button-secondary" disabled={!bay || busy === id || (state !== 'ASSIGNED' && state !== 'AVAILABLE')} onClick={() => bay && void assign(bay)}>{busy === id ? 'Updating…' : bay?.assigned === 1 ? 'Clear assignment' : `Assign ${id}`}</button><div className="live-bay-demo-controls"><button disabled={!bay || busy === id} onClick={() => bay && void setSensorState(bay, false)}>Simulate free</button><button disabled={!bay || busy === id} onClick={() => bay && void setSensorState(bay, true)}>Simulate occupied</button></div></article> })}</div><p className="form-help">Assignment is a staff demonstration marker. Sensors detect occupancy only and do not identify drivers. Occupancy always overrides assignment.</p>{error && <p className="login-error" role="alert">{error}</p>}</main>
}

function OpenStaffLogin() {
  useEffect(() => {
    window.history.replaceState(null, '', '/admin')
    window.dispatchEvent(new PopStateEvent('popstate'))
  }, [])
  return <main className="dashboard-content"><p>Opening staff dashboard…</p></main>
}

function AdminDashboard({ spaces, stats, activity, onActivity, view, setView, role, onMode, onSpaces, mobileOpen, setMobileOpen, users, setUsers, adminToken, siteId }: { spaces: Space[]; stats: { occupied: number; available: number; assigned: number }; activity: Activity[]; onActivity: React.Dispatch<React.SetStateAction<Activity[]>>; view: string; setView: (view: string) => void; role: AdminUser['role']; onMode: () => void; onSpaces: React.Dispatch<React.SetStateAction<Space[]>>; mobileOpen: boolean; setMobileOpen: (value: boolean) => void; users: AdminUser[]; setUsers: React.Dispatch<React.SetStateAction<AdminUser[]>>; adminToken: string | null; siteId: string }) {
  const navItems = [{ name: 'Live view', icon: <LayoutDashboard size={18} /> }, { name: 'Live bays', icon: <Grid2x2 size={18} /> }, { name: 'Parking status', icon: <Grid2x2 size={18} /> }, { name: 'Visitor queue', icon: <Users size={18} /> }, { name: 'Payments', icon: <Check size={18} /> }, { name: 'Pricing', icon: <Clock3 size={18} /> }, { name: 'Reports', icon: <Zap size={18} /> }, { name: 'Activity logs', icon: <Bell size={18} /> }, { name: 'Simulator', icon: <Sparkles size={18} /> }, { name: 'Checkpoint QR', icon: <MapPin size={18} /> }, { name: 'Sensor settings', icon: <ShieldCheck size={18} /> }, { name: 'Settings', icon: <Settings size={18} /> }]
  const allowedViews: Record<AdminUser['role'], string[]> = { SECURITY: navItems.map((item) => item.name), MANAGER: navItems.map((item) => item.name), ADMIN: navItems.map((item) => item.name) }
  const visibleNav = navItems.filter((item) => allowedViews[role].includes(item.name))
  const [activeSessions, setActiveSessions] = useState(0)
  useEffect(() => {
    if (!backendEnabled || !adminToken) return
    let active = true
    const refresh = async () => {
      try {
        const [result, queue] = await Promise.all([getSecurityLot(adminToken), getSecurityQueue(adminToken)])
        if (active) onSpaces(result.spaces.map((space) => ({ id: space.id, status: toUiStatus(space.status), ...(space.assigned_session === 'OPEN_DEMO' ? { visitor: 'Assigned driver' } : space.assigned_session ? { visitor: 'Active session', vehicle: space.vehicle_id || undefined } : {}) })))
        if (active) {
          try {
            const events = await getAdminEvents(adminToken)
            if (active) onActivity(events.slice(0, 8).map((event) => ({ time: new Date(event.created_at).toLocaleTimeString('en-GH', { timeZone: 'Africa/Accra', hour: '2-digit', minute: '2-digit' }), event: event.event_type.replaceAll('_', ' '), detail: event.description, tone: /occupied|paid|released/i.test(event.event_type) ? 'green' : /sensor|device/i.test(event.event_type) ? 'blue' : 'orange' })))
          } catch {
            if (active) onActivity(queue.slice(0, 8).map((row) => ({ time: new Date(row.created_at).toLocaleTimeString('en-GH', { timeZone: 'Africa/Accra', hour: '2-digit', minute: '2-digit' }), event: row.session_status.replaceAll('_', ' '), detail: row.space_id ? `Visitor · ${row.space_id}` : 'Visitor waiting for a bay', tone: row.session_status === 'PARKED' ? 'green' : 'blue' })))
          }
          setActiveSessions(queue.length)
        }
      } catch { /* existing view remains readable while temporarily offline */ }
    }
    void refresh()
    const timer = window.setInterval(refresh, 8000)
    return () => { active = false; window.clearInterval(timer) }
  }, [adminToken, onActivity, onSpaces])
  return <div className="admin-shell"><aside className={mobileOpen ? 'sidebar sidebar-open' : 'sidebar'}><div className="sidebar-brand"><Brand compact /><button className="icon-button close-sidebar" onClick={() => setMobileOpen(false)}><X size={19} /></button></div><div className="sidebar-label">ParkTech workspace · {role}</div><nav className="side-nav">{visibleNav.map((item) => <button key={item.name} className={view === item.name ? 'active' : ''} onClick={() => { setView(item.name); window.history.pushState(null, '', pathForStaffView(item.name)); setMobileOpen(false) }}>{item.icon}<span>{item.name}</span></button>)}</nav><div className="sidebar-bottom"><div className="security-card"><ShieldCheck size={19} /><div><strong>ParkTech operations</strong><span>Open staff interface</span></div></div><button className="logout-button" onClick={onMode}><ArrowRight size={17} /> Visitor interface</button></div></aside><div className="admin-main"><header className="admin-topbar"><div className="topbar-left"><button className="icon-button admin-menu" onClick={() => setMobileOpen(true)}><Menu /></button><div><span className="top-eyebrow">{role === 'SECURITY' ? 'Security workspace' : role === 'MANAGER' ? 'Facility manager workspace' : 'Owner dashboard'}</span><h1>{view}</h1></div></div><div className="topbar-right"><div className="date-label"><Clock3 size={15} /> {new Date().toLocaleDateString('en-GH', { timeZone: 'Africa/Accra' })}</div><div className="profile"><div className="avatar">{role.slice(0, 2)}</div><div><strong>Staff interface</strong><span>Open access</span></div></div></div></header>{view === 'Live view' ? <DashboardHome spaces={spaces} stats={stats} activity={activity} activeSessions={activeSessions} onOpenSimulator={() => { setView('Simulator'); window.history.pushState(null, '', '/admin/simulator') }} /> : view === 'Live bays' ? <LiveBaysView /> : <AdminPlaceholder view={view} spaces={spaces} stats={stats} users={users} setUsers={setUsers} adminToken={adminToken} siteId={siteId} role={role} />}</div></div>
}

function DashboardHomeOld({ spaces, stats, activity, onToggle }: { spaces: Space[]; stats: { occupied: number; available: number; assigned: number }; activity: Activity[]; onToggle: (id: string) => void }) { return <main className="dashboard-content"><div className="dashboard-intro"><div><p className="muted">Good morning, Jordan <span className="wave">✦</span></p><h2>Here’s your lot at a glance.</h2></div><button className="button button-primary"><MapPin size={16} /> Your parking lot <ChevronDown size={14} /></button></div><div className="stats-row"><StatCard label="Occupied" value={`${stats.occupied} / ${spaces.length}`} detail="spaces in use" icon={<CarFront />} tone="dark" /><StatCard label="Available" value={stats.available.toString()} detail="spaces ready" icon={<Grid2x2 />} tone="green" /><StatCard label="Today's visitors" value="24" detail="+12% from yesterday" icon={<Users />} tone="cream" /><StatCard label="Last activity" value="10:24" detail="minutes ago" icon={<Clock3 />} tone="white" /></div><div className="dashboard-grid"><section className="panel lot-panel"><div className="panel-heading"><div><span className="panel-kicker"><span className="live-dot" /> Live monitor</span><h3>Parking lot overview</h3></div><button className="more-button"><MoreHorizontal /></button></div><ParkingMap spaces={spaces} /><div className="panel-foot"><span><i className="legend-dot available-dot" /> Available <b>{stats.available}</b></span><span><i className="legend-dot assigned-dot" /> Assigned <b>{stats.assigned}</b></span><span><i className="legend-dot occupied-dot" /> Occupied <b>{stats.occupied}</b></span><span className="sensor-note"><span className="live-dot" /> Sensors connected</span></div></section><section className="panel activity-panel"><div className="panel-heading"><div><span className="panel-kicker">System feed</span><h3>Recent activity</h3></div><button className="view-all">View all <ArrowRight size={15} /></button></div><div className="activity-list">{activity.slice(0, 4).map((item, index) => <div className="activity-item" key={`${item.time}-${index}`}><div className={`activity-icon ${item.tone}`}>{item.tone === 'green' ? <Check size={15} /> : item.tone === 'blue' ? <CarFront size={15} /> : <Zap size={15} />}</div><div className="activity-copy"><strong>{item.event}</strong><span>{item.detail}</span></div><time>{item.time}</time></div>)}</div></section></div><p className="demo-hint"><Sparkles size={15} /> Demo controls: click any space to simulate its sensor state.</p><div className="quick-controls">{spaces.map((space) => <button key={space.id} onClick={() => onToggle(space.id)} className={`quick-space ${space.status}`}><span>{space.id}</span><small>{statusLabel[space.status]}</small></button>)}</div></main> }

function DashboardHome({ spaces, stats, activity, activeSessions, onOpenSimulator }: { spaces: Space[]; stats: { occupied: number; available: number; assigned: number }; activity: Activity[]; activeSessions: number; onOpenSimulator: () => void }) {
  return <main className="dashboard-content"><div className="dashboard-intro"><div><p className="muted">ParkTech operations <span className="wave">✦</span></p><h2>Parking at a glance.</h2></div><span className="status-chip status-active">Live lot monitor</span></div><div className="stats-row"><StatCard label="Spaces" value={String(spaces.length)} detail="configured bays" icon={<Grid2x2 />} tone="white" /><StatCard label="Available" value={String(stats.available)} detail="ready to reserve" icon={<Check />} tone="green" /><StatCard label="Occupied" value={String(stats.occupied)} detail="confirmed parking" icon={<CarFront />} tone="dark" /><StatCard label="Active sessions" value={String(activeSessions)} detail="visitor journeys" icon={<Users />} tone="cream" /></div><div className="dashboard-grid"><section className="panel lot-panel"><div className="panel-heading"><div><span className="panel-kicker"><span className="live-dot" /> Live monitor</span><h3>Parking lot overview</h3></div><span className="panel-kicker">Access road ← · bays →</span></div><ParkingMap spaces={spaces} /><div className="panel-foot"><span><i className="legend-dot available-dot" /> Available <b>{stats.available}</b></span><span><i className="legend-dot assigned-dot" /> Reserved <b>{stats.assigned}</b></span><span><i className="legend-dot occupied-dot" /> Occupied <b>{stats.occupied}</b></span><span className="sensor-note">Space labels remain visible in every status.</span></div></section><section className="panel activity-panel"><div className="panel-heading"><div><span className="panel-kicker">System feed</span><h3>Recent activity</h3></div></div><div className="activity-list">{activity.slice(0, 6).map((item, index) => <div className="activity-item" key={`${item.time}-${index}`}><div className={`activity-icon ${item.tone}`}>{item.tone === 'green' ? <Check size={15} /> : item.tone === 'blue' ? <CarFront size={15} /> : <Zap size={15} />}</div><div className="activity-copy"><strong>{item.event}</strong><span>{item.detail}</span></div><time>{item.time}</time></div>)}{!activity.length && <p className="account-empty">No activity recorded yet.</p>}</div></section></div><div className="dashboard-quick-actions"><div><strong>Need to verify a workflow?</strong><span>Run a test arrival, occupancy, demo payment, authorization and vacancy event.</span></div><button className="button button-blue" onClick={onOpenSimulator}><Zap size={16} /> Open simulator</button></div></main>
}

function StatCard({ label, value, detail, icon, tone }: { label: string; value: string; detail: string; icon: React.ReactNode; tone: string }) { return <div className={`stat-card ${tone}`}><div className="stat-icon">{icon}</div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div> }

function SiteSetup({ siteId, adminToken, role }: { siteId: string; adminToken: string | null; role: AdminUser['role'] }) {
  const [name, setName] = useState('')
  const [address, setAddress] = useState('')
  const [contactEmail, setContactEmail] = useState('')
  const [contactPhone, setContactPhone] = useState('')
  const [socialLinks, setSocialLinks] = useState<Record<string, string>>({ x: '', facebook: '', instagram: '', linkedin: '' })
  const [latitude, setLatitude] = useState('')
  const [longitude, setLongitude] = useState('')
  const [logoDataUri, setLogoDataUri] = useState('')
  const [configured, setConfigured] = useState(false)
  const [message, setMessage] = useState('')
  useEffect(() => {
    let active = true
    if (!backendEnabled) {
      const saved = window.localStorage.getItem('smartpark.site.setup')
      if (saved && active) { const site = JSON.parse(saved) as SiteSettings; setName(site.name); setAddress(site.address); setContactEmail(site.contact_email || ''); setContactPhone(site.contact_phone || ''); setSocialLinks({ x: '', facebook: '', instagram: '', linkedin: '', ...site.social_links }); setLatitude(site.latitude ? String(site.latitude) : ''); setLongitude(site.longitude ? String(site.longitude) : ''); setLogoDataUri(site.logo_data_uri || ''); setConfigured(site.setup_complete) }
    } else void getAdminSite(adminToken || undefined).then((site) => { if (active) { setName(site.name); setAddress(site.address); setContactEmail(site.contact_email || ''); setContactPhone(site.contact_phone || ''); setSocialLinks({ x: '', facebook: '', instagram: '', linkedin: '', ...site.social_links }); setLatitude(site.latitude ? String(site.latitude) : ''); setLongitude(site.longitude ? String(site.longitude) : ''); setLogoDataUri(site.logo_data_uri || ''); setConfigured(site.setup_complete) } }).catch((error) => { if (active) setMessage(error instanceof Error ? error.message : 'Could not load lot settings.') })
    return () => { active = false }
  }, [adminToken])
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setMessage('')
    const payload = { name: name.trim(), address: address.trim(), contact_email: contactEmail.trim(), contact_phone: contactPhone.trim(), social_links: socialLinks, logo_data_uri: logoDataUri, ...(latitude && longitude ? { latitude: Number(latitude), longitude: Number(longitude) } : {}) }
    try {
      if (backendEnabled) { const saved = await saveAdminSite(payload, adminToken || undefined); setConfigured(saved.setup_complete); setName(saved.name); window.dispatchEvent(new CustomEvent('smartpark:site-configured', { detail: saved })); setMessage('Parking lot settings saved. Visitor access is now enabled.') }
      else { const saved: SiteSettings = { id: siteId, ...payload, latitude: Number(latitude) || 0, longitude: Number(longitude) || 0, setup_complete: true }; window.localStorage.setItem('smartpark.site.setup', JSON.stringify(saved)); setConfigured(true); window.dispatchEvent(new CustomEvent('smartpark:site-configured', { detail: saved })); setMessage('Demo parking lot settings saved on this browser.') }
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Could not save parking lot settings.') }
  }
  const selectLogo = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 350_000) { setMessage('Choose a PNG, JPEG, or WebP logo under 350 KB.'); return }
    const reader = new FileReader()
    reader.onload = () => { if (typeof reader.result === 'string') { setLogoDataUri(reader.result); setMessage('') } }
    reader.readAsDataURL(file)
  }
  return <><main className="dashboard-content placeholder-content"><section className="panel account-form-panel"><div className="panel-heading"><div><span className="panel-kicker"><MapPin size={13} /> Owner setup · Africa/Accra</span><h3>Facility profile and entrance</h3></div></div><p className="form-help">Set the name and directions that drivers see. This installation is managed by ParkTech; drivers use it without creating accounts.</p><form className="account-form" onSubmit={submit}><label>Facility name<input required minLength={2} maxLength={120} value={name} onChange={(event) => setName(event.target.value)} placeholder="ParkTech Park" /></label><label>Facility logo<input type="file" accept="image/png,image/jpeg,image/webp" onChange={selectLogo} />{logoDataUri && <span className="logo-preview"><img src={logoDataUri} alt="Facility logo preview" /><button type="button" className="text-button" onClick={() => setLogoDataUri('')}>Remove logo</button></span>}</label><label>Physical address<input maxLength={240} value={address} onChange={(event) => setAddress(event.target.value)} placeholder="Street, city, country" /></label><label>Public contact email<input type="email" maxLength={160} value={contactEmail} onChange={(event) => setContactEmail(event.target.value)} placeholder="Optional" /></label><label>Public contact phone<input type="tel" maxLength={40} value={contactPhone} onChange={(event) => setContactPhone(event.target.value)} placeholder="Optional" /></label>{Object.keys(socialLinks).map((network) => <label key={network}>{network} profile URL<input type="url" value={socialLinks[network]} onChange={(event) => setSocialLinks({ ...socialLinks, [network]: event.target.value })} placeholder="https:// (optional)" /></label>)}<label>Entrance latitude<input type="number" step="any" min="-90" max="90" value={latitude} onChange={(event) => setLatitude(event.target.value)} placeholder="Optional" /></label><label>Entrance longitude<input type="number" step="any" min="-180" max="180" value={longitude} onChange={(event) => setLongitude(event.target.value)} placeholder="Optional" /></label><div className="timezone-note">Timezone: <strong>Africa/Accra · GHS</strong></div><button className="button button-primary" type="submit">{configured ? 'Save facility profile' : 'Set up facility'}</button></form>{message && <p className="form-message" role="status">{message}</p>}</section></main><PublicUrlSettings adminToken={adminToken} role={role} /><SensorSettings adminToken={adminToken} role={role} /><CheckpointSettings adminToken={adminToken} role={role} /></>
}

function PublicUrlSettings({ adminToken, role }: { adminToken: string | null; role: AdminUser['role'] }) {
  const [url, setUrl] = useState('')
  const [message, setMessage] = useState('')
  useEffect(() => { void getAdminPublicUrl(adminToken || undefined).then((result) => setUrl(result.base_url)).catch(() => setMessage('Could not load public URL.')) }, [adminToken])
  const save = async (event: React.FormEvent) => { event.preventDefault(); setMessage(''); try { const result = await saveAdminPublicUrl(url, adminToken || undefined); setUrl(result.base_url); setMessage('Public HTTPS visitor URL saved. You can now print the facility QR poster.') } catch (error) { setMessage(error instanceof Error ? error.message : 'Could not save URL. Use a reachable HTTPS origin.') } }
  return <section className="panel account-form-panel checkpoint-settings"><div className="panel-heading"><div><span className="panel-kicker">QR poster · public access</span><h3>Public visitor URL</h3></div></div><p className="form-help">Set the reachable HTTPS site or tunnel address used by the facility QR. Localhost links are not printed.</p><form className="account-form" onSubmit={save}><label>HTTPS base URL<input type="url" required placeholder="https://parking.example.com" value={url} disabled={role !== 'ADMIN'} onChange={(event) => setUrl(event.target.value)} /></label>{role === 'ADMIN' && <button className="button button-primary" type="submit">Save public URL</button>}</form>{message && <p className="form-message" role="status">{message}</p>}</section>
}

function SensorSettings({ adminToken, role }: { adminToken: string | null; role: AdminUser['role'] }) {
  const [config, setConfig] = useState<SystemConfig | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  useEffect(() => { void getAdminConfig(adminToken || undefined).then(setConfig).catch((reason) => setError(reason instanceof Error ? reason.message : 'Could not load sensor settings.')) }, [adminToken])
  const save = async (event: React.FormEvent) => { event.preventDefault(); if (!config) return; try { const updated = await saveAdminConfig({ entrance_threshold_cm: config.entrance_threshold_cm, reservation_minutes: config.reservation_minutes }, adminToken || undefined); setConfig(updated); setMessage('Prototype sensor threshold saved. Apply the same threshold to the Pi agent environment.'); setError('') } catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save sensor settings.') } }
  return <section className="panel account-form-panel checkpoint-settings"><div className="panel-heading"><div><span className="panel-kicker">IoT prototype</span><h3>Entrance sensor settings</h3></div></div><p className="form-help">A close-range event auto-matches one waiting visitor. Security selects the visitor when multiple arrivals are waiting. The sensor cannot identify a person or bay by itself.</p>{config && <form className="account-form" onSubmit={save}><label>Arrival threshold (cm)<input type="number" min="5" max="300" step="1" value={config.entrance_threshold_cm} disabled={role !== 'ADMIN'} onChange={(event) => setConfig({ ...config, entrance_threshold_cm: Number(event.target.value) })} /></label><label>Reservation expiry (minutes)<input type="number" min="1" max="60" value={config.reservation_minutes} disabled={role !== 'ADMIN'} onChange={(event) => setConfig({ ...config, reservation_minutes: Number(event.target.value) })} /></label>{role === 'ADMIN' && <button className="button button-primary" type="submit">Save sensor settings</button>}</form>}{message && <p className="form-message" role="status">{message}</p>}{error && <p className="login-error" role="alert">{error}</p>}</section>
}

function CheckpointSettings({ adminToken, role = 'ADMIN' }: { adminToken: string | null; role?: AdminUser['role'] }) {
  const [checkpoints, setCheckpoints] = useState<{ id: string; name: string; active: boolean; created_at: string; entry_url: string }[]>([])
  const [name, setName] = useState('Main entrance')
  const [confirmReplace, setConfirmReplace] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const refresh = () => listCheckpoints(adminToken || undefined).then(setCheckpoints).catch((e) => setError(e instanceof Error ? e.message : 'Could not load checkpoint.'))
  useEffect(() => { void refresh() }, [adminToken])
  const create = async (event: React.FormEvent) => { event.preventDefault(); setMessage(''); setError(''); try { await createCheckpoint(name, confirmReplace, adminToken || undefined); setConfirmReplace(false); setMessage('Checkpoint QR created. Print and mount it at the main entrance.'); await refresh() } catch (e) { setError(e instanceof Error ? e.message : 'Could not create checkpoint QR.') } }
  const active = checkpoints.find((checkpoint) => checkpoint.active)
  return <section className="panel account-form-panel checkpoint-settings"><div className="panel-heading"><div><span className="panel-kicker">One QR · main facility entrance</span><h3>Security checkpoint QR</h3></div></div><p className="form-help">Print this permanent QR and mount it at the facility entrance. It opens the visitor PWA and identifies the checkpoint only; each scan creates its own anonymous session.</p>{active && !active.entry_url && <div className="route-warning">Set a reachable HTTPS public URL before making a visitor QR. <a href="/admin/settings">SET PUBLIC URL</a></div>}{active?.entry_url && <div className="checkpoint-poster"><h2>SMART PARK</h2><h3>SCAN TO FIND YOUR PARKING</h3><p>Point your phone camera at this code. Your parking map opens instantly.</p><QRCodeSVG value={active.entry_url} size={260} level="H" includeMargin /><strong>{active.name}</strong><span>{active.entry_url}</span><button className="button button-secondary no-print" onClick={() => window.print()}>PRINT QR POSTER</button></div>}{role === 'ADMIN' && <form className="account-form" onSubmit={create}><label>Checkpoint name<input required minLength={2} value={name} onChange={(event) => setName(event.target.value)} /></label><label className="checkbox-label"><input type="checkbox" checked={confirmReplace} onChange={(event) => setConfirmReplace(event.target.checked)} /> I understand creating a replacement revokes the current QR.</label><button className="button button-primary" type="submit">{active ? 'Create replacement QR' : 'Create checkpoint QR'}</button></form>}{message && <p className="form-message" role="status">{message}</p>}{error && <p className="login-error" role="alert">{error}</p>}</section>
}

function AdminPlaceholder({ view, spaces, stats, users, setUsers, adminToken, siteId, role }: { view: string; spaces: Space[]; stats: { occupied: number; available: number; assigned: number }; users: AdminUser[]; setUsers: React.Dispatch<React.SetStateAction<AdminUser[]>>; adminToken: string | null; siteId: string; role: AdminUser['role'] }) {
  if (view === 'Settings') return <SiteSetup siteId={siteId} adminToken={adminToken} role={role} />
  if (view === 'Checkpoint QR') return <CheckpointSettings adminToken={adminToken} role={role} />
  if (view === 'Sensor settings') return <SensorSettings adminToken={adminToken} role={role} />
  return <main className="dashboard-content placeholder-content"><div className="placeholder-head"><div><p className="muted">ParkTech operations</p><h2>{view}</h2></div></div>{view === 'Parking status' ? <div className="panel table-panel"><div className="table-header"><span>Space</span><span>Status</span><span>Assigned visitor</span><span>Vehicle</span><span>Updated</span></div>{spaces.map((space) => <div className="table-row" key={space.id}><strong>{space.id}</strong><span className={`table-status ${space.status}`}><i /> {statusLabel[space.status]}</span><span>{space.visitor || '—'}</span><span>{space.vehicle || '—'}</span><span>{space.since || 'Live'}</span></div>)}</div> : view === 'User management' ? <UserManagement users={users} setUsers={setUsers} adminToken={adminToken} /> : view === 'Visitor queue' ? <SecurityQueue adminToken={adminToken} /> : view === 'Pricing' ? <PricingSettings role={role} adminToken={adminToken} /> : view === 'Reports' || view === 'Activity logs' ? <ReportsView adminToken={adminToken} /> : view === 'Payments' ? <PaymentsView adminToken={adminToken} /> : view === 'Simulator' ? <SimulatorView siteId={siteId} adminToken={adminToken} /> : <div className="empty-admin"><div className="empty-icon"><Users /></div><h3>{view} is ready for live data.</h3><p>Connected events will appear here as the system receives them.</p></div>}</main>
}

function PricingSettings({ role, adminToken }: { role: AdminUser['role']; adminToken: string | null }) {
  const [tariff, setTariff] = useState<TariffSettings | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  useEffect(() => { void getAdminTariff(adminToken || undefined).then(setTariff).catch((reason) => setError(reason instanceof Error ? reason.message : 'Could not load pricing.')) }, [adminToken])
  const update = (field: keyof TariffSettings, value: string) => setTariff((current) => current ? { ...current, [field]: field === 'timezone' ? value : value === '' && field === 'daily_cap_minor' ? null : Number(value) } : current)
  const save = async (event: React.FormEvent) => {
    event.preventDefault(); if (!tariff) return
    setError(''); setMessage('')
    try { const saved = await saveAdminTariff({ free_minutes: tariff.free_minutes, block_minutes: tariff.block_minutes, block_price_minor: tariff.block_price_minor, grace_minutes: tariff.grace_minutes, daily_cap_minor: tariff.daily_cap_minor, timezone: tariff.timezone }, adminToken || undefined); setTariff(saved); setMessage('Demo tariff saved. New invoices use these settings.') }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save pricing.') }
  }
  if (!tariff) return <section className="panel setup-panel"><p>{error || 'Loading pricing settings…'}</p></section>
  return <section className="panel setup-panel"><div className="panel-heading"><div><span className="panel-kicker">Owner controlled · GH₵</span><h3>Parking pricing</h3></div><span className="status-chip">Demo tariff</span></div><p className="form-help">Charges are stored in pesewas and calculated from the server occupancy time. Demo payments do not transfer money.</p><form className="account-form pricing-form" onSubmit={save}><label>Free minutes<input type="number" min="0" max="1440" value={tariff.free_minutes} disabled={role !== 'ADMIN'} onChange={(event) => update('free_minutes', event.target.value)} /></label><label>Billing block minutes<input type="number" min="1" max="1440" value={tariff.block_minutes} disabled={role !== 'ADMIN'} onChange={(event) => update('block_minutes', event.target.value)} /></label><label>Price per block (pesewas)<input type="number" min="0" max="10000000" value={tariff.block_price_minor} disabled={role !== 'ADMIN'} onChange={(event) => update('block_price_minor', event.target.value)} /></label><label>Grace minutes<input type="number" min="0" max="1440" value={tariff.grace_minutes} disabled={role !== 'ADMIN'} onChange={(event) => update('grace_minutes', event.target.value)} /></label><label>Daily maximum (pesewas)<input type="number" min="0" value={tariff.daily_cap_minor ?? ''} disabled={role !== 'ADMIN'} onChange={(event) => update('daily_cap_minor', event.target.value)} placeholder="No maximum" /></label><label>Local timezone<input value={tariff.timezone} disabled={role !== 'ADMIN'} onChange={(event) => update('timezone', event.target.value)} /></label>{role === 'ADMIN' && <button className="button button-primary" type="submit">Save pricing</button>}</form>{role !== 'ADMIN' && <p className="form-help">Only the owner account can change rates.</p>}{message && <p className="form-message" role="status">{message}</p>}{error && <p className="login-error" role="alert">{error}</p>}</section>
}

function ReportsView({ adminToken }: { adminToken: string | null }) {
  const [report, setReport] = useState<Awaited<ReturnType<typeof getAdminAnalytics>> | null>(null)
  const [events, setEvents] = useState<Awaited<ReturnType<typeof getAdminEvents>>>([])
  const [message, setMessage] = useState('')
  useEffect(() => { void Promise.all([getAdminAnalytics(adminToken || undefined), getAdminEvents(adminToken || undefined)]).then(([summary, audit]) => { setReport(summary); setEvents(audit) }).catch((error) => setMessage(error instanceof Error ? error.message : 'Could not load reports.')) }, [adminToken])
  const exportCsv = async () => {
    try { const file = await downloadAdminEvents(adminToken || undefined); const url = URL.createObjectURL(file); const link = document.createElement('a'); link.href = url; link.download = 'parktech-activity-report.csv'; link.click(); URL.revokeObjectURL(url) }
    catch (error) { setMessage(error instanceof Error ? error.message : 'CSV export failed.') }
  }
  const revenue = report?.invoices.reduce((sum, row) => sum + row.amount_minor, 0) ?? 0
  return <section className="reports-view"><div className="report-cards"><StatCard label="Spaces" value={String(Object.values(report?.spaces_by_status || {}).reduce((sum, n) => sum + n, 0))} detail="configured bays" icon={<Grid2x2 />} tone="white" /><StatCard label="Active sessions" value={String(report?.active_visitor_sessions ?? '—')} detail="current journeys" icon={<CarFront />} tone="green" /><StatCard label="Demo charges" value={new Intl.NumberFormat('en-GH', { style: 'currency', currency: 'GHS' }).format(revenue / 100)} detail="no money transferred" icon={<ShieldCheck />} tone="cream" /></div><div className="panel report-events"><div className="panel-heading"><div><span className="panel-kicker">Last 30 days · Africa/Accra</span><h3>Recent activity</h3></div><button className="button button-secondary" onClick={() => void exportCsv()}>Export CSV</button></div><div className="activity-list">{events.slice(0, 12).map((event) => <div className="activity-item" key={event.id}><div className="activity-icon blue"><Clock3 size={15} /></div><div className="activity-copy"><strong>{event.event_type.replaceAll('_', ' ')}</strong><span>{event.description}{event.space_id ? ` · ${event.space_id}` : ''}</span></div><time>{new Date(event.created_at).toLocaleString('en-GH', { timeZone: 'Africa/Accra' })}</time></div>)}</div>{!events.length && <p className="account-empty">No activity recorded yet.</p>}{message && <p className="login-error" role="alert">{message}</p>}</div></section>
}

function PaymentsView({ adminToken }: { adminToken: string | null }) {
  const [report, setReport] = useState<Awaited<ReturnType<typeof getAdminAnalytics>> | null>(null)
  const [error, setError] = useState('')
  useEffect(() => { void getAdminAnalytics(adminToken || undefined).then(setReport).catch((reason) => setError(reason instanceof Error ? reason.message : 'Could not load payments.')) }, [adminToken])
  return <section className="panel table-panel"><div className="panel-heading"><div><span className="panel-kicker">Ghana cedi · simulated only</span><h3>Payment ledger</h3></div></div><div className="table-header payment-table"><span>Status</span><span>Invoices</span><span>Amount</span></div>{(report?.invoices || []).map((row) => <div className="table-row payment-table" key={row.status}><strong>{row.status.replaceAll('_', ' ')}</strong><span>{row.count}</span><span>{new Intl.NumberFormat('en-GH', { style: 'currency', currency: 'GHS' }).format(row.amount_minor / 100)}</span></div>)}{!report?.invoices.length && !error && <p className="account-empty">No parking invoices yet.</p>}<p className="form-help">Payment records are demo-only. Smart Park does not collect card or bank details.</p>{error && <p className="login-error" role="alert">{error}</p>}</section>
}

function SimulatorView({ siteId, adminToken }: { siteId: string; adminToken: string | null }) {
  const [testSession, setTestSession] = useState<{ id: string; token: string; space: string } | null>(null)
  const [message, setMessage] = useState('Run an arrival to create a temporary visitor session and reserve the next free bay.')
  const [busy, setBusy] = useState(false)
  const arrival = async () => {
    setBusy(true); setMessage('Creating test arrival…')
    try {
      const session = await createVisitorSession(siteId)
      await joinVisitorQueue(session.visitor_token)
      const result = await verifyVisitorArrival(session.session_id, adminToken || undefined)
      setTestSession({ id: session.session_id, token: session.visitor_token, space: result.space_id })
      setMessage(`Simulated arrival matched and assigned ${result.space_id}.`)
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Could not create the test arrival.') }
    finally { setBusy(false) }
  }
  const park = async () => {
    if (!testSession) return
    setBusy(true)
    try { await simulateParkingEvent('space_occupied', testSession.space, adminToken || undefined); setMessage(`${testSession.space} is marked occupied. Server-side parking time has started.`) }
    catch (error) { setMessage(error instanceof Error ? error.message : 'Could not simulate parking.') }
    finally { setBusy(false) }
  }
  const exit = async () => {
    if (!testSession) return
    setBusy(true)
    try {
      const exitRequest = await requestVisitorExit(testSession.id, testSession.token)
      if (exitRequest.invoice.status === 'PAYMENT_PENDING') await recordManualPayment(testSession.id, 'CASH', 'SIMULATOR', adminToken || undefined)
      await confirmVehicleExit(testSession.id, adminToken || undefined)
      setMessage(`${testSession.space} test journey is complete. Manual payment and guard-confirmed exit released the bay.`)
      setTestSession(null)
    } catch (error) { setMessage(error instanceof Error ? error.message : 'The test exit could not be completed.') }
    finally { setBusy(false) }
  }
  return <section className="panel simulator-panel"><div className="panel-heading"><div><span className="panel-kicker">No hardware required</span><h3>Parking lifecycle simulator</h3></div><span className="status-chip">Simulated</span></div><p className="form-help">Exercise the same backend lifecycle used by visitors and IoT devices. The simulator creates a real test session in the live lot.</p><div className="simulator-steps"><div className={testSession ? 'done' : 'active'}><b>1</b><span>Arrival and bay assignment</span><button className="button button-secondary" disabled={busy || Boolean(testSession)} onClick={() => void arrival()}>Simulate arrival</button></div><div className={testSession ? 'active' : ''}><b>2</b><span>Occupancy event</span><button className="button button-secondary" disabled={busy || !testSession} onClick={() => void park()}>Confirm parked</button></div><div className={testSession ? 'active' : ''}><b>3</b><span>Manual payment and confirmed exit</span><button className="button button-primary" disabled={busy || !testSession} onClick={() => void exit()}>Simulate exit</button></div></div>{testSession && <p className="form-help">Test session {testSession.id.slice(0, 8)}… · assigned {testSession.space}</p>}<p className="form-message" role="status">{message}</p></section>
}

function SecurityQueue({ adminToken }: { adminToken: string | null }) {
  const [records, setRecords] = useState<QueueRecord[]>([])
  const [drafts, setDrafts] = useState<Record<string, { method: 'CASH' | 'MOBILE_MONEY_MANUAL' | 'OTHER_MANUAL'; reference: string }>>({})
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const refresh = async () => { try { setRecords(await getSecurityQueue(adminToken || undefined)); setError('') } catch (e) { setError(e instanceof Error ? e.message : 'Could not load arrivals.') } }
  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 5000); return () => window.clearInterval(timer) }, [adminToken])
  const match = async (sessionId: string) => { try { const result = await verifyVisitorArrival(sessionId, adminToken || undefined); setMessage(result.status === 'WAITLISTED' ? 'Parking full. Visitor added to the wait queue.' : `Arrival matched. Assigned bay ${bayLabel(result.space_id)}.`); await refresh() } catch (e) { setError(e instanceof Error ? e.message : 'Could not match arrival.') } }
  const payment = async (sessionId: string) => { const draft = drafts[sessionId] || { method: 'CASH' as const, reference: '' }; try { await recordManualPayment(sessionId, draft.method, draft.reference, adminToken || undefined); setMessage(`Recorded ${draft.method.replaceAll('_', ' ').toLowerCase()} payment received.`); await refresh() } catch (e) { setError(e instanceof Error ? e.message : 'Could not record payment.') } }
  const exit = async (sessionId: string) => { try { const result = await confirmVehicleExit(sessionId, adminToken || undefined); setMessage(`Vehicle exit confirmed. ${bayLabel(result.space_id)} is released.`); await refresh() } catch (e) { setError(e instanceof Error ? e.message : 'Exit confirmation failed.') } }
  return <div className="panel table-panel"><h2>Arrivals and active sessions</h2><p className="form-help">Select the arriving vehicle from this list. When several visitors are waiting, choose the one at the entrance; Smart Park will not guess.</p><div className="table-header"><span>Visitor session</span><span>Journey state</span><span>Bay</span><span>Amount</span><span>Guard action</span></div>{records.map((row) => { const draft = drafts[row.session_id] || { method: 'CASH' as const, reference: '' }; return <div className="table-row" key={row.session_id}><strong title={row.session_id}>{row.session_id.slice(0, 9)}…</strong><span>{row.session_status.replaceAll('_', ' ')}</span><span>{row.space_id ? bayLabel(row.space_id) : 'Waiting'}</span><span>{row.invoice_status ? `${row.invoice_status} · ${((row.amount_minor || 0) / 100).toFixed(2)} GHS` : '—'}</span><span>{row.session_status === 'WAITING_CONFIRMATION' ? <button className="button button-primary" onClick={() => void match(row.session_id)}>MATCH ARRIVING VEHICLE · ASSIGN BAY</button> : row.session_status === 'EXIT_REQUESTED' && row.invoice_status === 'PAYMENT_PENDING' ? <div className="arrival-verify"><select aria-label="Manual payment method" value={draft.method} onChange={(event) => setDrafts({ ...drafts, [row.session_id]: { ...draft, method: event.target.value as typeof draft.method } })}><option value="CASH">Cash</option><option value="MOBILE_MONEY_MANUAL">Mobile money manual</option><option value="OTHER_MANUAL">Other manual</option></select><input aria-label="Payment reference (optional)" placeholder="Transaction reference (optional)" value={draft.reference} onChange={(event) => setDrafts({ ...drafts, [row.session_id]: { ...draft, reference: event.target.value } })} /><button className="button button-primary" onClick={() => void payment(row.session_id)}>RECORD PAYMENT</button></div> : row.session_status === 'EXIT_REQUESTED' && ['PAID', 'EXEMPT'].includes(row.invoice_status || '') ? <button className="button button-primary" onClick={() => void exit(row.session_id)}>CONFIRM VEHICLE EXIT</button> : '—'}</span></div>})}{!records.length && !error && <p className="account-empty">No pending arrivals or active sessions.</p>}{error && <p className="login-error" role="alert">{error}</p>}{message && <p className="form-message" role="status">{message}</p>}<small className="form-help">The bay is released only after the guard confirms physical exit and vacancy.</small></div>
}
function UserManagement({ users, setUsers, adminToken }: { users: AdminUser[]; setUsers: React.Dispatch<React.SetStateAction<AdminUser[]>>; adminToken: string | null }) {
  const [form, setForm] = useState({ display_name: '', email: '', password: '', ghana_card_number: '', phone_number: '', role: 'SECURITY' as AdminUser['role'] })
  const [message, setMessage] = useState('')
  useEffect(() => { if (backendEnabled) listAdminUsers(adminToken || undefined).then(setUsers).catch(() => undefined) }, [adminToken, setUsers])
  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setMessage('')
    try {
      const created = backendEnabled ? await createAdminUser(form, adminToken || undefined) : { ...form, id: `demo-${Date.now()}`, active: true }
      setUsers((current) => [created, ...current])
      setForm({ display_name: '', email: '', password: '', ghana_card_number: '', phone_number: '', role: 'SECURITY' })
      setMessage('Account created successfully.')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to create account.')
    }
  }
  return <div className="user-management"><section className="panel account-form-panel"><div className="panel-heading"><div><span className="panel-kicker"><ShieldCheck size={13} /> Protected workspace</span><h3>Create an admin account</h3></div></div><p className="form-help">Invite staff with a verified Ghana Card. Card numbers are hashed before storage.</p><form className="account-form" onSubmit={submit}><label>Full name<input required pattern="[A-Za-z]+([ '-][A-Za-z]+)*" value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} placeholder="Ama Mensah" /></label><label>Work email<input required type="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} placeholder="ama@parktech.com" /></label><label>Ghana Card number<input required pattern="GHA-[0-9]{9}-[0-9]" value={form.ghana_card_number} onChange={(event) => setForm({ ...form, ghana_card_number: event.target.value.toUpperCase() })} placeholder="GHA-123456789-0" /></label><label>Phone number<input required inputMode="numeric" pattern="[0-9]{10}" maxLength={10} value={form.phone_number} onChange={(event) => setForm({ ...form, phone_number: event.target.value.replace(/\D/g, '').slice(0, 10) })} placeholder="0241234567" /></label><label>Temporary password<input required minLength={12} type="password" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} placeholder="At least 12 characters" /></label><label>Role<select value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value as AdminUser['role'] })}><option value="SECURITY">Security officer</option><option value="ADMIN">Administrator</option><option value="MANAGER">Facility manager</option></select></label><button className="button button-primary" type="submit"><Users size={16} /> Create account</button></form>{message && <p className="form-message" role="status">{message}</p>}</section><section className="panel accounts-panel"><div className="panel-heading"><div><span className="panel-kicker">Access roster</span><h3>Team accounts</h3></div><span className="account-count">{users.length}</span></div>{users.length === 0 ? <div className="account-empty">No additional accounts yet.</div> : <div className="account-list">{users.map((user) => <div className="account-row" key={user.id}><div className="avatar">{user.display_name.slice(0, 2).toUpperCase()}</div><div><strong>{user.display_name}</strong><span>{user.email}</span></div><b>{user.role}</b></div>)}</div>}</section></div>
}

export default App
