from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import base64
import csv
import hmac
from io import BytesIO
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import qrcode
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.getenv("SMARTPARK_DATABASE_URL", f"sqlite:///{ROOT / 'smartpark.db'}")
DATABASE_PATH = Path(DATABASE_URL.removeprefix("sqlite:///"))
if not DATABASE_PATH.is_absolute():
    DATABASE_PATH = ROOT / DATABASE_PATH
ADMIN_TOKEN = os.getenv("SMARTPARK_ADMIN_TOKEN", "change-me-in-production")
SETUP_TOKEN = os.getenv("SMARTPARK_SETUP_TOKEN", ADMIN_TOKEN)
CHECKPOINT_SECRET = os.getenv("SMARTPARK_CHECKPOINT_SECRET", SETUP_TOKEN)
DEVICE_TOKEN = os.getenv("SMARTPARK_DEVICE_TOKEN", "change-device-token-in-production")
SITE_ID = os.getenv("SMARTPARK_SITE_ID", "default")
PUBLIC_ENTRY_URL = os.getenv("SMARTPARK_PUBLIC_ENTRY_URL", "")
PLATFORM_NAME = "SmartPark"
SIMULATOR_ENABLED = os.getenv("SMARTPARK_SIMULATOR_ENABLED", "true").lower() == "true"
RESERVATION_MINUTES = int(os.getenv("SMARTPARK_RESERVATION_MINUTES", "5"))
ADMIN_SESSION_MINUTES = int(os.getenv("SMARTPARK_ADMIN_SESSION_MINUTES", "480"))
VISITOR_SESSION_MINUTES = int(os.getenv("SMARTPARK_VISITOR_SESSION_MINUTES", "240"))
DB_LOCK = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def resolve_site_id(site_id: str) -> str:
    # `default` is the global entry slug and follows the facility selected by deployment config.
    return SITE_ID if site_id == "default" else site_id


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"pbkdf2_sha256$310000${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def connect() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS sites (id TEXT PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL, latitude REAL NOT NULL, longitude REAL NOT NULL, logo_data_uri TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS parking_spaces (id TEXT PRIMARY KEY, site_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'AVAILABLE', assigned_session TEXT, vehicle_id TEXT, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bay_sensor_state (space_id TEXT PRIMARY KEY, physical_state TEXT NOT NULL DEFAULT 'UNKNOWN', device_id TEXT, last_seen TEXT, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS demo_bay_assignments (id TEXT PRIMARY KEY, space_id TEXT NOT NULL, created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS visitor_sessions (id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, site_id TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, arrived_at TEXT, expires_at TEXT, checkpoint_id TEXT);
        CREATE TABLE IF NOT EXISTS checkpoints (id TEXT PRIMARY KEY, site_id TEXT NOT NULL, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, revoked_at TEXT);
        CREATE TABLE IF NOT EXISTS reservations (id TEXT PRIMARY KEY, space_id TEXT NOT NULL, session_id TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, occupied_at TEXT, released_at TEXT, UNIQUE(space_id, status), UNIQUE(session_id, status));
        CREATE TABLE IF NOT EXISTS iot_events (event_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, sensor_id TEXT NOT NULL, event_type TEXT NOT NULL, space_id TEXT, observed_at TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit_events (id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, space_id TEXT, session_id TEXT, description TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, name TEXT NOT NULL, online INTEGER NOT NULL DEFAULT 0, last_seen TEXT, token_hash TEXT, active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS admin_users (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS admin_sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tariffs (site_id TEXT PRIMARY KEY, free_minutes INTEGER NOT NULL DEFAULT 30, block_minutes INTEGER NOT NULL DEFAULT 10, block_price_minor INTEGER NOT NULL DEFAULT 200, currency TEXT NOT NULL DEFAULT 'GHS', grace_minutes INTEGER NOT NULL DEFAULT 0, daily_cap_minor INTEGER, timezone TEXT NOT NULL DEFAULT 'Africa/Accra');
        CREATE TABLE IF NOT EXISTS invoices (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, reservation_id TEXT NOT NULL, status TEXT NOT NULL, currency TEXT NOT NULL, amount_minor INTEGER NOT NULL, duration_minutes INTEGER NOT NULL, occupied_at TEXT NOT NULL, calculated_at TEXT NOT NULL, tariff_snapshot TEXT NOT NULL, UNIQUE(session_id));
        CREATE TABLE IF NOT EXISTS demo_payments (id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL UNIQUE, idempotency_key TEXT NOT NULL UNIQUE, amount_minor INTEGER NOT NULL, currency TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'DEMO_PAID', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS gate_actions (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS app_config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS newsletter_subscriptions (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, consent_at TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'footer', unsubscribe_hash TEXT NOT NULL UNIQUE, unsubscribed_at TEXT);
        CREATE TABLE IF NOT EXISTS manual_payments (id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE, amount_minor INTEGER NOT NULL, method TEXT NOT NULL, reference TEXT NOT NULL DEFAULT '', recorded_by TEXT NOT NULL, recorded_at TEXT NOT NULL);
        """)
        columns = {row[1] for row in db.execute("PRAGMA table_info(admin_users)").fetchall()}
        if "ghana_card_hash" not in columns:
            db.execute("ALTER TABLE admin_users ADD COLUMN ghana_card_hash TEXT")
        if "ghana_card_last4" not in columns:
            db.execute("ALTER TABLE admin_users ADD COLUMN ghana_card_last4 TEXT")
        if "phone_number" not in columns:
            db.execute("ALTER TABLE admin_users ADD COLUMN phone_number TEXT")
        site_columns = {row[1] for row in db.execute("PRAGMA table_info(sites)").fetchall()}
        for column, declaration in (("contact_email", "TEXT NOT NULL DEFAULT ''"), ("contact_phone", "TEXT NOT NULL DEFAULT ''"), ("social_links", "TEXT NOT NULL DEFAULT '{}'")):
            if column not in site_columns:
                db.execute(f"ALTER TABLE sites ADD COLUMN {column} {declaration}")
        if "setup_complete" not in site_columns:
            db.execute("ALTER TABLE sites ADD COLUMN setup_complete INTEGER NOT NULL DEFAULT 0")
        if "logo_data_uri" not in site_columns:
            db.execute("ALTER TABLE sites ADD COLUMN logo_data_uri TEXT NOT NULL DEFAULT ''")
        device_columns = {row[1] for row in db.execute("PRAGMA table_info(devices)").fetchall()}
        if "token_hash" not in device_columns:
            db.execute("ALTER TABLE devices ADD COLUMN token_hash TEXT")
        if "active" not in device_columns:
            db.execute("ALTER TABLE devices ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
        session_columns = {row[1] for row in db.execute("PRAGMA table_info(visitor_sessions)").fetchall()}
        if "expires_at" not in session_columns:
            db.execute("ALTER TABLE visitor_sessions ADD COLUMN expires_at TEXT")
        if "checkpoint_id" not in session_columns:
            db.execute("ALTER TABLE visitor_sessions ADD COLUMN checkpoint_id TEXT")
        if "arrival_code_hash" not in session_columns:
            db.execute("ALTER TABLE visitor_sessions ADD COLUMN arrival_code_hash TEXT")
        for legacy in db.execute("SELECT id, created_at FROM visitor_sessions WHERE expires_at IS NULL").fetchall():
            created = datetime.fromisoformat(legacy["created_at"].replace("Z", "+00:00"))
            db.execute("UPDATE visitor_sessions SET expires_at = ? WHERE id = ?", ((created + timedelta(minutes=VISITOR_SESSION_MINUTES)).isoformat(), legacy["id"]))
        db.execute("INSERT OR IGNORE INTO sites(id, name, address, latitude, longitude, setup_complete) VALUES (?, 'ParkTech Park', '', 0, 0, 1)", (SITE_ID,))
        if SITE_ID == "default":
            db.execute("UPDATE sites SET name = 'ParkTech Park', setup_complete = 1 WHERE id = ? AND setup_complete = 0 AND name = 'Your Parking Lot'", (SITE_ID,))
        # Remove the former demo facility's institutional branding and coordinates while preserving operational records.
        db.execute("UPDATE sites SET name = 'Your Parking Lot', address = '', latitude = 0, longitude = 0, setup_complete = 0 WHERE setup_complete = 0 AND (lower(name) LIKE '%parliament house%' OR lower(name) LIKE '%ho technical university%')")
        if SITE_ID == "default":
            legacy = db.execute("SELECT id FROM sites WHERE id = 'htu-main'").fetchone()
            if legacy:
                db.execute("UPDATE parking_spaces SET site_id = ? WHERE site_id = ?", (SITE_ID, legacy["id"]))
                db.execute("UPDATE visitor_sessions SET site_id = ? WHERE site_id = ?", (SITE_ID, legacy["id"]))
                db.execute("INSERT OR IGNORE INTO tariffs(site_id, free_minutes, block_minutes, block_price_minor, currency, grace_minutes, daily_cap_minor, timezone) SELECT ?, free_minutes, block_minutes, block_price_minor, currency, grace_minutes, daily_cap_minor, timezone FROM tariffs WHERE site_id = ?", (SITE_ID, legacy["id"]))
                db.execute("DELETE FROM tariffs WHERE site_id = ?", (legacy["id"],))
                db.execute("DELETE FROM sites WHERE id = ?", (legacy["id"],))
        for space_id in ("L1", "L2", "L3", "L4"):
            db.execute("INSERT OR IGNORE INTO parking_spaces VALUES (?, ?, 'AVAILABLE', NULL, NULL, ?)", (space_id, SITE_ID, now()))
        for space_id in ("L1", "L2"):
            db.execute("INSERT OR IGNORE INTO bay_sensor_state(space_id, physical_state, updated_at) VALUES (?, 'UNKNOWN', ?)", (space_id, now()))
        db.execute("INSERT OR IGNORE INTO tariffs(site_id, free_minutes, block_minutes, block_price_minor) VALUES (?, 0, 1, 100)", (SITE_ID,))
        db.execute("UPDATE tariffs SET free_minutes = 0, block_minutes = 1, block_price_minor = 100 WHERE site_id = ? AND free_minutes = 30 AND block_minutes = 10 AND block_price_minor = 200", (SITE_ID,))
        db.execute("INSERT OR IGNORE INTO app_config(key, value) VALUES ('entrance_threshold_cm', '70'), ('reservation_minutes', ?)", (str(RESERVATION_MINUTES),))
        db.execute("INSERT OR IGNORE INTO checkpoints(id, site_id, name, active, created_at) VALUES ('main-entrance', ?, 'Main entrance', 1, ?)", (SITE_ID, now()))


class SessionOut(BaseModel):
    session_id: str
    visitor_token: str
    site_id: str
    site_name: str
    expires_at: str


class CheckpointCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    confirm_revoke_previous: bool = False


class ArrivalVerification(BaseModel):
    pass


class ArrivalOut(BaseModel):
    session_id: str
    status: str
    assignment: str | None = None


class SensorEvent(BaseModel):
    event_id: str = Field(min_length=8)
    device_id: str
    sensor_id: str = "simulator"
    event_type: str
    distance_cm: float | None = Field(default=None, ge=0, le=10000)
    space_id: str | None = None
    observed_at: str | None = None
    facility_id: str | None = None
    measured_at: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class SimulatorEvent(BaseModel):
    event_type: str
    space_id: str | None = None
    session_id: str | None = None
    distance_cm: float | None = Field(default=None, ge=0, le=10000)


class BayAssignment(BaseModel):
    assigned: bool = True


class VisitorBayAssignment(BaseModel):
    space_id: str = Field(pattern=r"^L[12]$")


class ConfigPatch(BaseModel):
    entrance_threshold_cm: float | None = Field(default=None, ge=5, le=300)
    reservation_minutes: int | None = Field(default=None, ge=1, le=60)


class TariffPatch(BaseModel):
    free_minutes: int = Field(ge=0, le=1440)
    block_minutes: int = Field(ge=1, le=1440)
    block_price_minor: int = Field(ge=0, le=10000000)
    grace_minutes: int = Field(default=0, ge=0, le=1440)
    daily_cap_minor: int | None = Field(default=None, ge=0, le=100000000)
    timezone: str = Field(default="Africa/Accra", min_length=3, max_length=64)


class DemoPayment(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=128)


class VehicleConfirm(BaseModel):
    plate: str = Field(min_length=2, max_length=16, pattern=r"^[A-Za-z0-9 -]+$")


class ExitAuthorize(BaseModel):
    exempt: bool = False


class AdminUserCreate(BaseModel):
    email: str = Field(min_length=5, max_length=160)
    display_name: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z]+(?:[ '-][A-Za-z]+)*$")
    password: str = Field(min_length=12, max_length=128)
    role: str = Field(default="SECURITY", pattern="^(SECURITY|ADMIN|MANAGER)$")
    ghana_card_number: str = Field(pattern=r"^GHA-[0-9]{9}-[0-9]$")
    phone_number: str = Field(pattern=r"^[0-9]{10}$")


class AdminLogin(BaseModel):
    email: str
    password: str


class AdminBootstrap(BaseModel):
    setup_token: str = Field(min_length=8, max_length=256)
    display_name: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z]+(?:[ '-][A-Za-z]+)*$")
    email: str = Field(min_length=5, max_length=160)
    password: str = Field(min_length=12, max_length=128)


class DeviceCreate(BaseModel):
    device_id: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=2, max_length=120)


class SiteSetup(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    address: str = Field(default="", max_length=240)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    logo_data_uri: str | None = Field(default=None, max_length=500_000)
    contact_email: str = Field(default="", max_length=160)
    contact_phone: str = Field(default="", max_length=40)
    social_links: dict[str, str] = Field(default_factory=dict)


class NewsletterSubscribe(BaseModel):
    email: str = Field(min_length=5, max_length=160)
    consent: bool
    source: str = Field(default="footer", max_length=40)
    website: str = Field(default="", max_length=200)


class NewsletterUnsubscribe(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class PublicURLSetup(BaseModel):
    base_url: str = Field(min_length=9, max_length=240)


class ManualPayment(BaseModel):
    method: str = Field(pattern="^(CASH|MOBILE_MONEY_MANUAL|OTHER_MANUAL)$")
    reference: str = Field(default="", max_length=120)


class AdminLoginOut(BaseModel):
    access_token: str
    user: dict[str, Any]


class ConnectionManager:
    def __init__(self) -> None:
        self.operations: set[WebSocket] = set()
        self.visitors: dict[str, set[WebSocket]] = {}

    async def broadcast(self, event: dict[str, Any], session_id: str | None = None) -> None:
        targets = set(self.operations) if session_id is None else set(self.visitors.get(session_id, set()))
        if session_id is None:
            targets.update(self.visitors.get(session_id or "", set()))
        for socket in targets:
            try:
                await socket.send_json(event)
            except Exception:
                self.operations.discard(socket)
                for visitor_sockets in self.visitors.values():
                    visitor_sockets.discard(socket)


manager = ConnectionManager()


def audit(db: sqlite3.Connection, event_type: str, description: str, space_id: str | None = None, session_id: str | None = None) -> None:
    db.execute("INSERT INTO audit_events(event_type, space_id, session_id, description, created_at) VALUES (?, ?, ?, ?, ?)", (event_type, space_id, session_id, description, now()))


def session_from_token(db: sqlite3.Connection, token: str | None) -> sqlite3.Row:
    if not token:
        raise HTTPException(401, "visitor_token_required")
    session = db.execute("SELECT * FROM visitor_sessions WHERE token_hash = ?", (token_hash(token),)).fetchone()
    if not session:
        raise HTTPException(401, "invalid_visitor_token")
    if session["expires_at"] and session["expires_at"] <= now():
        raise HTTPException(401, "visitor_session_expired")
    return session


def admin_required(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if OPEN_STAFF_INTERFACE:
        return {"id": "open-interface", "role": "ADMIN", "email": "open-interface"}
    if authorization == f"Bearer {ADMIN_TOKEN}":
        return {"id": "bootstrap", "role": "ADMIN", "email": "bootstrap"}
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "admin_authentication_required")
    access_token = authorization.removeprefix("Bearer ")
    with connect() as db:
        session = db.execute("SELECT admin_users.id, admin_users.email, admin_users.display_name, admin_users.role FROM admin_sessions JOIN admin_users ON admin_users.id = admin_sessions.user_id WHERE admin_sessions.token_hash = ? AND admin_sessions.expires_at > ? AND admin_users.active = 1", (token_hash(access_token), now())).fetchone()
    if not session:
        raise HTTPException(401, "admin_session_expired")
    return dict(session)


def require_staff_if_closed(authorization: str | None = None) -> None:
    if not OPEN_STAFF_INTERFACE:
        admin_required(authorization)


OPEN_STAFF_INTERFACE = os.getenv("SMARTPARK_OPEN_STAFF_INTERFACE", "true").lower() == "true"


def roles_required(*roles: str):
    def dependency(user: dict[str, Any] = Depends(admin_required)) -> dict[str, Any]:
        if OPEN_STAFF_INTERFACE:
            return {"id": "open-interface", "role": "ADMIN", "email": "open-interface"}
        if user["role"] not in roles:
            raise HTTPException(403, "insufficient_role")
        return user
    return dependency


def open_staff_user() -> dict[str, Any]:
    return {"id": "open-interface", "role": "ADMIN", "email": "open-interface"}


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, iterations, salt_text, digest_text = encoded.split("$")
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(digest_text.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
        return secrets.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def device_required(x_device_token: str | None = Header(default=None), x_device_id: str | None = Header(default=None)) -> str | None:
    if not x_device_token:
        raise HTTPException(401, "device_authentication_required")
    # The shared token remains a local bootstrap credential for legacy installations.
    if secrets.compare_digest(x_device_token, DEVICE_TOKEN):
        return None
    with connect() as db:
        device = db.execute("SELECT id FROM devices WHERE token_hash = ? AND active = 1", (token_hash(x_device_token),)).fetchone()
    if not device or (x_device_id and x_device_id != device["id"]):
        raise HTTPException(401, "device_authentication_required")
    return device["id"]


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="SmartPark API", version="1.0.0", lifespan=lifespan)
origins = [origin.strip() for origin in os.getenv("SMARTPARK_CORS_ORIGINS", "http://localhost:5173,http://localhost:5174,http://localhost:5175").split(",")]
app.add_middleware(CORSMiddleware, allow_origins=origins if origins != ["*"] else ["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "smartpark-api"}


@app.post("/api/v1/admin/login", response_model=AdminLoginOut)
def admin_login(credentials: AdminLogin) -> AdminLoginOut:
    if OPEN_STAFF_INTERFACE:
        return AdminLoginOut(access_token="open-interface", user={"id": "open-interface", "email": credentials.email, "display_name": "Staff", "role": "ADMIN"})
    with connect() as db:
        user = db.execute("SELECT id, email, display_name, password_hash, role, active FROM admin_users WHERE email = ?", (credentials.email.strip().lower(),)).fetchone()
        if not user or not user["active"] or not verify_password(credentials.password, user["password_hash"]):
            raise HTTPException(401, "invalid_admin_credentials")
        access_token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc).timestamp() + ADMIN_SESSION_MINUTES * 60
        expiry = datetime.fromtimestamp(expires_at, timezone.utc).isoformat()
        db.execute("INSERT INTO admin_sessions VALUES (?, ?, ?, ?)", (token_hash(access_token), user["id"], expiry, now()))
    return AdminLoginOut(access_token=access_token, user={"id": user["id"], "email": user["email"], "display_name": user["display_name"], "role": user["role"]})


@app.get("/api/open/interface")
def open_interface() -> dict[str, Any]:
    return {"enabled": OPEN_STAFF_INTERFACE, "role": "ADMIN" if OPEN_STAFF_INTERFACE else None}


@app.get("/api/v1/admin/bootstrap/status")
def admin_bootstrap_status() -> dict[str, bool]:
    if OPEN_STAFF_INTERFACE:
        return {"available": False}
    with connect() as db:
        available = db.execute("SELECT 1 FROM admin_users LIMIT 1").fetchone() is None
    return {"available": available}


@app.post("/api/v1/admin/bootstrap", response_model=AdminLoginOut, status_code=201)
def bootstrap_admin(registration: AdminBootstrap) -> AdminLoginOut:
    email = registration.email.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(422, "valid_email_required")
    if not SETUP_TOKEN or not secrets.compare_digest(registration.setup_token, SETUP_TOKEN):
        raise HTTPException(401, "invalid_setup_code")
    with DB_LOCK, connect() as db:
        if db.execute("SELECT 1 FROM admin_users LIMIT 1").fetchone():
            raise HTTPException(409, "first_admin_already_created")
        user_id = secrets.token_urlsafe(12)
        db.execute("INSERT INTO admin_users(id, email, display_name, password_hash, role, active, created_at) VALUES (?, ?, ?, ?, 'ADMIN', 1, ?)", (user_id, email, registration.display_name.strip(), password_hash(registration.password), now()))
        audit(db, "first_admin_created", f"Created initial ParkTech administrator {email}")
        access_token = secrets.token_urlsafe(32)
        expiry = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + ADMIN_SESSION_MINUTES * 60, timezone.utc).isoformat()
        db.execute("INSERT INTO admin_sessions VALUES (?, ?, ?, ?)", (token_hash(access_token), user_id, expiry, now()))
    return AdminLoginOut(access_token=access_token, user={"id": user_id, "email": email, "display_name": registration.display_name.strip(), "role": "ADMIN"})


def public_base_url() -> str:
    with connect() as db:
        setting = db.execute("SELECT value FROM app_config WHERE key = 'public_base_url'").fetchone()
    base = (setting["value"] if setting else PUBLIC_ENTRY_URL).rstrip("/")
    if not base or not re.match(r"^https?://", base) or re.search(r"localhost|127\.0\.0\.1", base, re.I):
        return ""
    if base.endswith("/visit") or base.endswith("/scan") or base.endswith("/enter"):
        base = base.rsplit("/", 1)[0]
    return base


def checkpoint_entry_url(checkpoint_id: str) -> str:
    base = public_base_url()
    return f"{base}/visit?facility={SITE_ID}" if base else f"http://localhost:5173/visit?facility={SITE_ID}"


def entry_url(site_id: str) -> str:
    base = public_base_url()
    return f"{base}/visit?facility={site_id}" if base else f"http://localhost:5173/visit?facility={site_id}"


def checkpoint_token(checkpoint_id: str) -> str:
    signature = hmac.new(CHECKPOINT_SECRET.encode(), checkpoint_id.encode(), hashlib.sha256).hexdigest()
    return f"{checkpoint_id}.{signature}"


def active_checkpoint(token: str, db: sqlite3.Connection) -> sqlite3.Row:
    try:
        checkpoint_id, signature = token.rsplit(".", 1)
    except ValueError:
        raise HTTPException(404, "checkpoint_not_found")
    expected = hmac.new(CHECKPOINT_SECRET.encode(), checkpoint_id.encode(), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(signature, expected):
        raise HTTPException(404, "checkpoint_not_found")
    checkpoint = db.execute("SELECT * FROM checkpoints WHERE id = ? AND active = 1", (checkpoint_id,)).fetchone()
    if not checkpoint:
        raise HTTPException(404, "checkpoint_inactive")
    return checkpoint


SESSION_REQUESTS: dict[str, list[float]] = {}
SESSION_REQUESTS_LOCK = threading.Lock()
NEWSLETTER_REQUESTS: dict[str, list[float]] = {}


def enforce_newsletter_rate_limit(client_id: str) -> None:
    moment = datetime.now(timezone.utc).timestamp()
    with SESSION_REQUESTS_LOCK:
        recent = [stamp for stamp in NEWSLETTER_REQUESTS.get(client_id, []) if moment - stamp < 3600]
        if len(recent) >= 5:
            raise HTTPException(429, "newsletter_rate_limited")
        recent.append(moment)
        NEWSLETTER_REQUESTS[client_id] = recent


def enforce_session_rate_limit(client_id: str) -> None:
    moment = datetime.now(timezone.utc).timestamp()
    with SESSION_REQUESTS_LOCK:
        recent = [stamp for stamp in SESSION_REQUESTS.get(client_id, []) if moment - stamp < 60]
        if len(recent) >= 30:
            raise HTTPException(429, "visitor_session_rate_limited")
        recent.append(moment)
        SESSION_REQUESTS[client_id] = recent


@app.get("/api/v1/public/sites/{site_id}/entry")
@app.get("/api/public/sites/{site_id}/entry")
def public_entry(site_id: str) -> dict[str, Any]:
    site_id = resolve_site_id(site_id)
    with connect() as db:
        site = db.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
        if not site:
            raise HTTPException(404, "site_not_found")
    return {"site_id": site_id, "platform_name": PLATFORM_NAME, "site_name": site["name"], "configured": bool(site["setup_complete"]), "entry_url": entry_url(site_id), "https_required": True}


@app.get("/api/public/facility")
def public_facility() -> dict[str, Any]:
    with connect() as db:
        site = db.execute("SELECT name, address, logo_data_uri, setup_complete, contact_email, contact_phone, social_links FROM sites WHERE id = ?", (SITE_ID,)).fetchone()
    if not site:
        raise HTTPException(404, "site_not_found")
    # Contact details remain unset until the owner configures them; do not invent public business contacts.
    social = json.loads(site["social_links"] or "{}")
    return {"display_name": site["name"] if site["setup_complete"] else "Smart Park", "address": site["address"], "logo_url": site["logo_data_uri"], "contact_email": site["contact_email"], "contact_phone": site["contact_phone"], "social_links": social, "description": "Simple parking. Clear directions. Real-time availability.", "currency": "GHS", "timezone": "Africa/Accra"}


@app.post("/api/public/newsletter/subscriptions")
def subscribe_newsletter(subscription: NewsletterSubscribe, request: Request) -> dict[str, str]:
    email = subscription.email.strip().lower()
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+", email):
        raise HTTPException(422, "invalid_email")
    if not subscription.consent:
        raise HTTPException(422, "consent_required")
    if subscription.website.strip():
        return {"message": "Subscription received."}
    enforce_newsletter_rate_limit(request.client.host if request.client else "unknown")
    raw_token = secrets.token_urlsafe(32)
    with connect() as db:
        existing = db.execute("SELECT id, unsubscribed_at, unsubscribe_hash FROM newsletter_subscriptions WHERE email = ?", (email,)).fetchone()
        if existing and existing["unsubscribed_at"] is None:
            raw_token = ""
        else:
            if existing:
                db.execute("DELETE FROM newsletter_subscriptions WHERE id = ?", (existing["id"],))
            db.execute("INSERT INTO newsletter_subscriptions(id, email, consent_at, source, unsubscribe_hash) VALUES (?, ?, ?, ?, ?)", (secrets.token_urlsafe(16), email, now(), subscription.source, token_hash(raw_token)))
    # Duplicate submissions are intentionally indistinguishable. With no mail provider, expose a one-time
    # unsubscribe link only to the subscribing browser; no mailing is claimed or sent.
    result = {"message": "Subscription received."}
    if raw_token:
        result["unsubscribe_token"] = raw_token
    return result


@app.post("/api/public/newsletter/unsubscribe")
def unsubscribe_newsletter(payload: NewsletterUnsubscribe) -> dict[str, str]:
    with connect() as db:
        db.execute("UPDATE newsletter_subscriptions SET unsubscribed_at = ? WHERE unsubscribe_hash = ? AND unsubscribed_at IS NULL", (now(), token_hash(payload.token)))
    return {"message": "If the subscription was active, it has been unsubscribed."}


@app.get("/api/v1/public/checkpoints/{token}")
def public_checkpoint(token: str) -> dict[str, Any]:
    with connect() as db:
        checkpoint = active_checkpoint(token, db)
        site = db.execute("SELECT id, name, address, latitude, longitude, logo_data_uri, setup_complete FROM sites WHERE id = ?", (checkpoint["site_id"],)).fetchone()
        if not site or not site["setup_complete"]:
            raise HTTPException(404, "facility_not_ready")
        spaces = db.execute("SELECT id, status FROM parking_spaces WHERE site_id = ?", (site["id"],)).fetchall()
    return {"checkpoint_id": checkpoint["id"], "checkpoint_name": checkpoint["name"], "site_id": site["id"], "site_name": site["name"], "address": site["address"], "logo_data_uri": site["logo_data_uri"], "latitude": site["latitude"] or None, "longitude": site["longitude"] or None, "configured": True, "spaces": [{"id": row["id"], "status": row["status"] if row["status"] in ("AVAILABLE", "RESERVED", "OCCUPIED", "OUT_OF_SERVICE") else "UNKNOWN"} for row in spaces]}


@app.get("/api/v1/admin/checkpoints", dependencies=[Depends(roles_required("ADMIN", "MANAGER", "SECURITY"))])
def list_checkpoints() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("SELECT * FROM checkpoints WHERE site_id = ? ORDER BY created_at", (SITE_ID,)).fetchall()
    return [{"id": row["id"], "name": row["name"], "active": bool(row["active"]), "created_at": row["created_at"], "entry_url": checkpoint_entry_url(row["id"]) if row["active"] else ""} for row in rows]


@app.post("/api/v1/admin/checkpoints", dependencies=[Depends(roles_required("ADMIN"))], status_code=201)
def create_checkpoint(config: CheckpointCreate) -> dict[str, Any]:
    checkpoint_id = secrets.token_urlsafe(18)
    with DB_LOCK, connect() as db:
        active = db.execute("SELECT 1 FROM checkpoints WHERE site_id = ? AND active = 1 LIMIT 1", (SITE_ID,)).fetchone()
        if active and not config.confirm_revoke_previous:
            raise HTTPException(409, "confirm_checkpoint_replacement")
        if active:
            db.execute("UPDATE checkpoints SET active = 0, revoked_at = ? WHERE site_id = ? AND active = 1", (now(), SITE_ID))
        db.execute("INSERT INTO checkpoints(id, site_id, name, active, created_at) VALUES (?, ?, ?, 1, ?)", (checkpoint_id, SITE_ID, config.name.strip(), now()))
        audit(db, "checkpoint_created", f"Created entrance checkpoint {config.name.strip()}")
    return {"id": checkpoint_id, "name": config.name.strip(), "active": True, "entry_url": checkpoint_entry_url(checkpoint_id)}


@app.post("/api/v1/admin/checkpoints/{checkpoint_id}/revoke", dependencies=[Depends(roles_required("ADMIN"))])
def revoke_checkpoint(checkpoint_id: str) -> dict[str, str]:
    with connect() as db:
        changed = db.execute("UPDATE checkpoints SET active = 0, revoked_at = ? WHERE id = ? AND site_id = ? AND active = 1", (now(), checkpoint_id, SITE_ID)).rowcount
        if not changed:
            raise HTTPException(404, "active_checkpoint_not_found")
        audit(db, "checkpoint_revoked", f"Revoked entrance checkpoint {checkpoint_id}")
    return {"id": checkpoint_id, "status": "revoked"}


@app.post("/api/v1/visitor/sessions", response_model=SessionOut)
@app.post("/api/visitor/sessions", response_model=SessionOut)
@app.post("/api/public/visitors", response_model=SessionOut)
def create_session(request: Request, site_id: str = Query(default=SITE_ID), checkpoint: str | None = Query(default=None, alias="checkpoint_token")) -> SessionOut:
    enforce_session_rate_limit(request.client.host if request.client else "unknown")
    checkpoint_id = None
    if checkpoint:
        with connect() as db:
            checked = active_checkpoint(checkpoint, db)
            checkpoint_id = checked["id"]
            site_id = checked["site_id"]
    site_id = resolve_site_id(site_id)
    session_id, visitor_token = secrets.token_urlsafe(16), secrets.token_urlsafe(32)
    created_at = now()
    expires_at = (datetime.fromisoformat(created_at) + timedelta(minutes=VISITOR_SESSION_MINUTES)).isoformat()
    with connect() as db:
        site = db.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
        if not site:
            raise HTTPException(404, "site_not_found")
        if not site["setup_complete"]:
            raise HTTPException(409, "parking_lot_not_configured")
        db.execute("INSERT INTO visitor_sessions(id, token_hash, site_id, status, created_at, arrived_at, expires_at, checkpoint_id, arrival_code_hash) VALUES (?, ?, ?, 'WAITING_CONFIRMATION', ?, NULL, ?, ?, NULL)", (session_id, token_hash(visitor_token), site_id, created_at, expires_at, checkpoint_id))
    return SessionOut(session_id=session_id, visitor_token=visitor_token, site_id=site_id, site_name=site["name"], expires_at=expires_at)


@app.get("/api/v1/sites/{site_id}/availability")
def parking_availability(site_id: str) -> dict[str, Any]:
    site_id = resolve_site_id(site_id)
    with connect() as db:
        site = db.execute("SELECT id, setup_complete FROM sites WHERE id = ?", (site_id,)).fetchone()
        if not site:
            raise HTTPException(404, "site_not_found")
        if not site["setup_complete"]:
            return {"site_id": site_id, "total": 0, "available": 0, "occupied": 0, "unavailable": 0, "out_of_service": 0, "spaces": [], "configured": False, "updated_at": now()}
        rows = db.execute("SELECT id, status FROM parking_spaces WHERE site_id = ?", (site_id,)).fetchall()
    spaces = sorted(rows, key=lambda row: (int(re.search(r"\d+", row["id"]).group()) if re.search(r"\d+", row["id"]) else 0, row["id"]))
    public_spaces = [{"id": row["id"], "status": row["status"] if row["status"] in ("AVAILABLE", "RESERVED", "OCCUPIED", "OUT_OF_SERVICE") else "UNKNOWN"} for row in spaces]
    counts = {status.lower(): sum(space["status"] == status for space in public_spaces) for status in ("AVAILABLE", "RESERVED", "OCCUPIED", "UNKNOWN", "OUT_OF_SERVICE")}
    return {"site_id": site_id, "total": len(public_spaces), "available": counts["available"], "reserved": counts["reserved"], "occupied": counts["occupied"], "unavailable": counts["unknown"], "out_of_service": counts["out_of_service"], "spaces": public_spaces, "configured": True, "updated_at": now()}


@app.get("/api/parking/availability")
def parking_availability_compat(site_id: str = Query(default=SITE_ID)) -> dict[str, Any]:
    return parking_availability(site_id)


@app.get("/api/public/bays")
def public_bays() -> dict[str, Any]:
    return parking_availability(SITE_ID)


@app.get("/api/v1/visitor/me")
@app.get("/api/public/visitors/me")
@app.get("/api/public/sessions/me")
def visitor_me(x_visitor_token: str | None = Header(default=None)) -> dict[str, Any]:
    with connect() as db:
        session = session_from_token(db, x_visitor_token)
        reservation = db.execute("SELECT * FROM reservations WHERE session_id = ? AND status IN ('RESERVED', 'OCCUPIED', 'EXIT_REQUESTED', 'EXIT_AUTHORIZED') ORDER BY created_at DESC LIMIT 1", (session["id"],)).fetchone()
        invoice = db.execute("SELECT * FROM invoices WHERE session_id = ?", (session["id"],)).fetchone()
        return {"session_id": session["id"], "status": session["status"], "assignment": dict(reservation) if reservation else None, "invoice": invoice_payload(invoice) if invoice else None}


@app.post("/api/v1/visitor/arrivals", response_model=ArrivalOut)
@app.post("/api/visitor/arrived", response_model=ArrivalOut)
async def join_arrival_queue(x_visitor_token: str | None = Header(default=None)) -> ArrivalOut:
    with DB_LOCK, connect() as db:
        db.execute("BEGIN IMMEDIATE")
        session = session_from_token(db, x_visitor_token)
        existing = db.execute("SELECT * FROM reservations WHERE session_id = ? AND status IN ('RESERVED', 'OCCUPIED')", (session["id"],)).fetchone()
        if existing:
            return ArrivalOut(session_id=session["id"], status=existing["status"], assignment=existing["space_id"])
        timestamp = now()
        db.execute("UPDATE visitor_sessions SET status = 'WAITING_CONFIRMATION', arrived_at = COALESCE(arrived_at, ?) WHERE id = ?", (timestamp, session["id"]))
        audit(db, "arrival_checkin", "Visitor checked in and is waiting for attendant identity confirmation", session_id=session["id"])
        result = ArrivalOut(session_id=session["id"], status="WAITING_CONFIRMATION")
    await manager.broadcast({"type": "arrival_checkin", "session_id": result.session_id}, result.session_id)
    await manager.broadcast({"type": "arrival_checkin"})
    return result


@app.post("/api/public/visitor/assign")
async def assign_public_visitor_bay(assignment: VisitorBayAssignment, x_visitor_token: str | None = Header(default=None)) -> dict[str, Any]:
    with DB_LOCK, connect() as db:
        db.execute("BEGIN IMMEDIATE")
        visitor = session_from_token(db, x_visitor_token)
        sensor = db.execute("SELECT physical_state, last_seen FROM bay_sensor_state WHERE space_id = ?", (assignment.space_id,)).fetchone()
        if not sensor or not sensor["last_seen"] or sensor["last_seen"] < (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat() or sensor["physical_state"] != "FREE":
            raise HTTPException(409, "bay_not_confirmed_free")
        existing = db.execute("SELECT id, space_id FROM reservations WHERE session_id = ? AND status IN ('RESERVED','OCCUPIED')", (visitor["id"],)).fetchone()
        if existing:
            return {"session_id": visitor["id"], "status": "ASSIGNED", "space_id": existing["space_id"]}
        changed = db.execute("UPDATE parking_spaces SET status='RESERVED', assigned_session=?, updated_at=? WHERE id=? AND status='AVAILABLE'", (visitor["id"], now(), assignment.space_id))
        if changed.rowcount != 1:
            raise HTTPException(409, "bay_no_longer_available")
        timestamp = now()
        db.execute("UPDATE visitor_sessions SET status='ASSIGNED' WHERE id=?", (visitor["id"],))
        db.execute("INSERT INTO reservations VALUES (?, ?, ?, 'RESERVED', ?, NULL, NULL)", (secrets.token_urlsafe(12), assignment.space_id, visitor["id"], timestamp))
        audit(db, "visitor_bay_assigned", f"Visitor assigned confirmed-free bay {assignment.space_id}", assignment.space_id, visitor["id"])
    await manager.broadcast({"type": "space_reserved", "space_id": assignment.space_id}, visitor["id"])
    await manager.broadcast({"type": "bay_state_changed", "space_id": assignment.space_id})
    return {"session_id": visitor["id"], "status": "ASSIGNED", "space_id": assignment.space_id}


@app.post("/api/public/assign-bay")
async def assign_anonymous_bay(assignment: VisitorBayAssignment) -> dict[str, Any]:
    with DB_LOCK, connect() as db:
        db.execute("BEGIN IMMEDIATE")
        sensor = db.execute("SELECT physical_state, last_seen FROM bay_sensor_state WHERE space_id = ?", (assignment.space_id,)).fetchone()
        if not sensor or not sensor["last_seen"] or sensor["last_seen"] < (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat() or sensor["physical_state"] != "FREE":
            raise HTTPException(409, "bay_not_confirmed_free")
        bay = db.execute("SELECT status, assigned_session FROM parking_spaces WHERE id = ? AND site_id = ?", (assignment.space_id, SITE_ID)).fetchone()
        if not bay or bay["status"] == "OCCUPIED":
            raise HTTPException(409, "bay_not_available")
        if bay["assigned_session"] not in (None, "OPEN_DEMO", "DEMO_DRIVER"):
            raise HTTPException(409, "bay_already_assigned")
        timestamp = now()
        db.execute("UPDATE parking_spaces SET status='RESERVED', assigned_session='OPEN_DEMO', updated_at=? WHERE id=?", (timestamp, assignment.space_id))
        db.execute("UPDATE demo_bay_assignments SET active = 0 WHERE space_id = ? AND active = 1", (assignment.space_id,))
        db.execute("INSERT INTO demo_bay_assignments(id, space_id, created_at, active) VALUES (?, ?, ?, 1)", (secrets.token_urlsafe(10), assignment.space_id, timestamp))
        audit(db, "bay_assignment", f"{assignment.space_id} assigned through public arrival interface", assignment.space_id)
    await manager.broadcast({"type": "bay_state_changed", "space_id": assignment.space_id})
    return {"status": "ASSIGNED", "space_id": assignment.space_id}


@app.post("/api/guard/arrivals/{session_id}/match")
@app.post("/api/v1/security/arrivals/{session_id}/match")
@app.post("/api/v1/security/arrivals/{session_id}/verify", include_in_schema=False)
async def verify_arrival(session_id: str, verification: ArrivalVerification = ArrivalVerification(), authorization: str | None = Header(default=None), internal_call: bool = False) -> dict[str, Any]:
    if not internal_call:
        require_staff_if_closed(authorization)
    with DB_LOCK, connect() as db:
        db.execute("BEGIN IMMEDIATE")
        session = db.execute("SELECT * FROM visitor_sessions WHERE id = ? AND status = 'WAITING_CONFIRMATION' AND expires_at > ?", (session_id, now())).fetchone()
        if not session:
            raise HTTPException(404, "waiting_visitor_not_found")
        rows = db.execute("SELECT id FROM parking_spaces WHERE site_id = ? AND status = 'AVAILABLE'", (session["site_id"],)).fetchall()
        space = min(rows, key=lambda row: (int(re.search(r"\d+", row["id"]).group()) if re.search(r"\d+", row["id"]) else 0, row["id"])) if rows else None
        if not space:
            db.execute("UPDATE visitor_sessions SET status = 'WAITING_VERIFIED' WHERE id = ?", (session_id,))
            audit(db, "arrival_verified_waitlisted", "Security verified visitor; no bay was available", session_id=session_id)
            result = {"session_id": session_id, "status": "WAITLISTED", "space_id": ""}
            return result
        timestamp = now()
        changed = db.execute("UPDATE parking_spaces SET status = 'RESERVED', assigned_session = ?, updated_at = ? WHERE id = ? AND status = 'AVAILABLE'", (session_id, timestamp, space["id"]))
        if changed.rowcount != 1:
            raise HTTPException(409, "space_no_longer_available")
        db.execute("UPDATE visitor_sessions SET status = 'ASSIGNED' WHERE id = ?", (session_id,))
        db.execute("INSERT INTO reservations VALUES (?, ?, ?, 'RESERVED', ?, NULL, NULL)", (secrets.token_urlsafe(12), space["id"], session_id, timestamp))
        audit(db, "arrival_verified", "Security verified the visitor arrival code", space["id"], session_id)
        result = {"session_id": session_id, "status": "ASSIGNED", "space_id": space["id"]}
    await manager.broadcast({"type": "space_reserved", "space_id": result["space_id"], "session_id": session_id}, session_id)
    await manager.broadcast({"type": "space_reserved", "space_id": result["space_id"], "session_id": session_id})
    return result


@app.get("/api/v1/visitor/parking-layout")
def visitor_layout(x_visitor_token: str | None = Header(default=None)) -> dict[str, Any]:
    with connect() as db:
        session = session_from_token(db, x_visitor_token)
        rows = db.execute("SELECT id, status, assigned_session, updated_at FROM parking_spaces WHERE site_id = ?", (session["site_id"],)).fetchall()
        rows = sorted(rows, key=lambda row: (int(re.search(r"\d+", row["id"]).group()) if re.search(r"\d+", row["id"]) else 0, row["id"]))
        return {"site_id": session["site_id"], "spaces": [{"id": row["id"], "status": row["status"] if row["status"] in ("AVAILABLE", "RESERVED", "OCCUPIED", "OUT_OF_SERVICE") else "UNKNOWN", "is_mine": row["assigned_session"] == session["id"]} for row in rows]}


@app.get("/api/v1/visitor/assignment")
@app.get("/api/visitor/assignment")
def visitor_assignment(x_visitor_token: str | None = Header(default=None)) -> dict[str, Any]:
    with connect() as db:
        session = session_from_token(db, x_visitor_token)
        reservation = db.execute("SELECT * FROM reservations WHERE session_id = ? AND status IN ('RESERVED', 'OCCUPIED', 'EXIT_REQUESTED', 'EXIT_AUTHORIZED') ORDER BY created_at DESC LIMIT 1", (session["id"],)).fetchone()
        return {"assignment": dict(reservation) if reservation else None}


def tariff_for(db: sqlite3.Connection, site_id: str) -> sqlite3.Row:
    db.execute("INSERT OR IGNORE INTO tariffs(site_id) VALUES (?)", (site_id,))
    return db.execute("SELECT * FROM tariffs WHERE site_id = ?", (site_id,)).fetchone()


@app.get("/api/v1/sites/{site_id}/tariff")
def public_tariff(site_id: str) -> dict[str, Any]:
    site_id = resolve_site_id(site_id)
    with connect() as db:
        site = db.execute("SELECT setup_complete FROM sites WHERE id = ?", (site_id,)).fetchone()
        if not site:
            raise HTTPException(404, "site_not_found")
        if not site["setup_complete"]:
            raise HTTPException(409, "parking_lot_not_configured")
        tariff = tariff_for(db, site_id)
    return {key: tariff[key] for key in ("free_minutes", "block_minutes", "block_price_minor", "currency", "grace_minutes", "daily_cap_minor", "timezone")}


def fee_quote(occupied_at: str, at: str, tariff: sqlite3.Row) -> tuple[int, int]:
    start = datetime.fromisoformat(occupied_at.replace("Z", "+00:00"))
    finish = datetime.fromisoformat(at.replace("Z", "+00:00"))
    minutes = max(0, int((finish - start).total_seconds() // 60))
    billable = max(0, minutes - int(tariff["free_minutes"]) - int(tariff["grace_minutes"]))
    blocks = (billable + int(tariff["block_minutes"]) - 1) // int(tariff["block_minutes"])
    amount = blocks * int(tariff["block_price_minor"])
    if tariff["daily_cap_minor"] is not None:
        amount = min(amount, int(tariff["daily_cap_minor"]))
    return minutes, amount


def invoice_payload(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["tariff_snapshot"] = json.loads(result["tariff_snapshot"])
    result["demo_payment"] = True
    return result


@app.post("/api/v1/visitor/vehicle")
def confirm_vehicle(vehicle: VehicleConfirm, x_visitor_token: str | None = Header(default=None)) -> dict[str, str]:
    plate = re.sub(r"[^A-Z0-9]", "", vehicle.plate.upper())
    with connect() as db:
        session = session_from_token(db, x_visitor_token)
        # Keep the displayed identifier useful while minimizing retained plate data.
        db.execute("CREATE TABLE IF NOT EXISTS visitor_vehicles(session_id TEXT PRIMARY KEY, plate_hash TEXT NOT NULL, plate_last4 TEXT NOT NULL)")
        db.execute("INSERT INTO visitor_vehicles VALUES (?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET plate_hash=excluded.plate_hash, plate_last4=excluded.plate_last4", (session["id"], token_hash(plate), plate[-4:]))
        audit(db, "vehicle_confirmed", f"Vehicle ending {plate[-4:]} confirmed", session_id=session["id"])
    return {"session_id": session["id"], "plate_last4": plate[-4:], "identification_mode": "visitor_entered_demo"}


@app.get("/api/v1/sessions/{session_id}/charges")
def session_charges(session_id: str, x_visitor_token: str | None = Header(default=None)) -> dict[str, Any]:
    with connect() as db:
        session = session_from_token(db, x_visitor_token)
        if session["id"] != session_id:
            raise HTTPException(404, "session_not_found")
        invoice = db.execute("SELECT * FROM invoices WHERE session_id = ?", (session_id,)).fetchone()
        reservation = db.execute("SELECT * FROM reservations WHERE session_id = ? AND status IN ('OCCUPIED','EXIT_REQUESTED','EXIT_AUTHORIZED') ORDER BY created_at DESC LIMIT 1", (session_id,)).fetchone()
        if invoice:
            return {"invoice": invoice_payload(invoice)}
        if not reservation or not reservation["occupied_at"]:
            return {"invoice": None, "estimate": None}
        tariff = tariff_for(db, session["site_id"])
        duration, amount = fee_quote(reservation["occupied_at"], now(), tariff)
        return {"invoice": None, "estimate": {"duration_minutes": duration, "amount_minor": amount, "currency": tariff["currency"], "demo_tariff": True, "tariff": dict(tariff)}}


@app.post("/api/v1/sessions/{session_id}/request-exit")
def request_exit(session_id: str, x_visitor_token: str | None = Header(default=None)) -> dict[str, Any]:
    with DB_LOCK, connect() as db:
        session = session_from_token(db, x_visitor_token)
        if session["id"] != session_id:
            raise HTTPException(404, "session_not_found")
        existing = db.execute("SELECT * FROM invoices WHERE session_id = ?", (session_id,)).fetchone()
        if existing:
            return {"invoice": invoice_payload(existing)}
        reservation = db.execute("SELECT * FROM reservations WHERE session_id = ? AND status = 'OCCUPIED'", (session_id,)).fetchone()
        if not reservation or not reservation["occupied_at"]:
            raise HTTPException(409, "confirmed_occupancy_required")
        timestamp = now()
        tariff = tariff_for(db, session["site_id"])
        duration, amount = fee_quote(reservation["occupied_at"], timestamp, tariff)
        snapshot = {key: tariff[key] for key in ("free_minutes", "block_minutes", "block_price_minor", "currency", "grace_minutes", "daily_cap_minor", "timezone")}
        status = "PAYMENT_PENDING" if amount else "PAID"
        invoice_id = secrets.token_urlsafe(12)
        db.execute("INSERT INTO invoices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (invoice_id, session_id, reservation["id"], status, tariff["currency"], amount, duration, reservation["occupied_at"], timestamp, json.dumps(snapshot)))
        db.execute("UPDATE reservations SET status = 'EXIT_REQUESTED' WHERE id = ?", (reservation["id"],))
        db.execute("UPDATE visitor_sessions SET status = 'EXIT_REQUESTED' WHERE id = ?", (session_id,))
        audit(db, "exit_requested", f"Exit requested; amount {amount} {tariff['currency']} minor units", reservation["space_id"], session_id)
        row = db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    return {"invoice": invoice_payload(row)}


@app.post("/api/v1/invoices/{invoice_id}/demo-pay")
def demo_pay(invoice_id: str, payment: DemoPayment) -> dict[str, Any]:
    raise HTTPException(410, "online_payment_disabled_pay_at_security_checkpoint")


@app.post("/api/v1/sessions/{session_id}/authorize-exit")
def authorize_exit(session_id: str, decision: ExitAuthorize, user: dict[str, Any] = Depends(open_staff_user)) -> dict[str, str]:
    with DB_LOCK, connect() as db:
        session = db.execute("SELECT * FROM visitor_sessions WHERE id = ?", (session_id,)).fetchone()
        invoice = db.execute("SELECT * FROM invoices WHERE session_id = ?", (session_id,)).fetchone()
        if not session or not invoice:
            raise HTTPException(404, "session_or_invoice_not_found")
        if invoice["status"] != "PAID" and not (decision.exempt and (OPEN_STAFF_INTERFACE or user["role"] in ("ADMIN", "MANAGER"))):
            raise HTTPException(409, "payment_or_authorized_exemption_required")
        if decision.exempt and invoice["status"] != "PAID":
            db.execute("UPDATE invoices SET status = 'EXEMPT' WHERE id = ?", (invoice["id"],))
            audit(db, "fee_exempted", f"Fee exemption authorized by {user['role']}", session_id=session_id)
        reservation = db.execute("SELECT * FROM reservations WHERE id = ?", (invoice["reservation_id"],)).fetchone()
        if not reservation or reservation["status"] in ("EXIT_AUTHORIZED", "RELEASED"):
            raise HTTPException(409, "exit_already_authorized_or_completed")
        db.execute("UPDATE reservations SET status = 'EXIT_AUTHORIZED' WHERE id = ?", (reservation["id"],))
        db.execute("UPDATE visitor_sessions SET status = 'EXIT_AUTHORIZED' WHERE id = ?", (session_id,))
        action_id = secrets.token_urlsafe(12)
        db.execute("INSERT INTO gate_actions VALUES (?, ?, 'SIMULATED_EXIT_OPEN', 'AUTHORIZED', ?)", (action_id, session_id, now()))
        audit(db, "exit_authorized", "Simulated exit gate authorized; vacancy still required", reservation["space_id"], session_id)
    return {"session_id": session_id, "status": "EXIT_AUTHORIZED", "gate": "SIMULATED_OPEN", "space_released": "false"}


@app.post("/api/guard/sessions/{session_id}/payment")
@app.post("/api/v1/security/sessions/{session_id}/payment")
def record_manual_payment(session_id: str, payment: ManualPayment, user: dict[str, Any] = Depends(open_staff_user)) -> dict[str, Any]:
    with DB_LOCK, connect() as db:
        invoice = db.execute("SELECT * FROM invoices WHERE session_id = ?", (session_id,)).fetchone()
        if not invoice:
            raise HTTPException(404, "invoice_not_found")
        if invoice["status"] != "PAYMENT_PENDING":
            raise HTTPException(409, "invoice_not_payable")
        payment_id, stamp = secrets.token_urlsafe(12), now()
        db.execute("INSERT INTO manual_payments(id, session_id, amount_minor, method, reference, recorded_by, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (payment_id, session_id, invoice["amount_minor"], payment.method, payment.reference.strip(), user["id"], stamp))
        db.execute("UPDATE invoices SET status = 'PAID' WHERE id = ? AND status = 'PAYMENT_PENDING'", (invoice["id"],))
        audit(db, "manual_payment_recorded", f"Guard recorded {payment.method} payment of {invoice['amount_minor']} GHS minor units", session_id=session_id)
    return {"status": "PAID", "method": payment.method, "amount_minor": invoice["amount_minor"], "payment_id": payment_id}


@app.post("/api/guard/sessions/{session_id}/exit")
@app.post("/api/v1/security/sessions/{session_id}/exit")
async def confirm_physical_exit(session_id: str, user: dict[str, Any] = Depends(open_staff_user)) -> dict[str, Any]:
    authorize_exit(session_id, ExitAuthorize(), user)
    with connect() as db:
        reservation = db.execute("SELECT space_id FROM reservations WHERE session_id = ? AND status = 'EXIT_AUTHORIZED' ORDER BY created_at DESC LIMIT 1", (session_id,)).fetchone()
    if not reservation:
        raise HTTPException(409, "authorized_reservation_not_found")
    event = SensorEvent(event_id=secrets.token_urlsafe(18), device_id="guard-confirmation", sensor_id=reservation["space_id"], event_type="space_released", space_id=reservation["space_id"], facility_id=SITE_ID)
    result = await process_event(event)
    return {"status": "EXIT_CONFIRMED", "space_id": reservation["space_id"], "release": result}


@app.get("/api/v1/admin/tariff", dependencies=[Depends(roles_required("ADMIN", "MANAGER"))])
def get_tariff() -> dict[str, Any]:
    with connect() as db:
        return {**dict(tariff_for(db, SITE_ID)), "demo_assumption": True}


@app.patch("/api/v1/admin/tariff", dependencies=[Depends(roles_required("ADMIN"))])
def update_tariff(tariff: TariffPatch) -> dict[str, Any]:
    with connect() as db:
        db.execute("INSERT INTO tariffs(site_id, free_minutes, block_minutes, block_price_minor, grace_minutes, daily_cap_minor, timezone) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(site_id) DO UPDATE SET free_minutes=excluded.free_minutes, block_minutes=excluded.block_minutes, block_price_minor=excluded.block_price_minor, grace_minutes=excluded.grace_minutes, daily_cap_minor=excluded.daily_cap_minor, timezone=excluded.timezone", (SITE_ID, tariff.free_minutes, tariff.block_minutes, tariff.block_price_minor, tariff.grace_minutes, tariff.daily_cap_minor, tariff.timezone))
        audit(db, "tariff_updated", "Updated editable DEMO tariff")
        return {**dict(db.execute("SELECT * FROM tariffs WHERE site_id = ?", (SITE_ID,)).fetchone()), "demo_assumption": True}


@app.get("/api/v1/sites/{site_id}/destination")
def destination(site_id: str) -> dict[str, Any]:
    site_id = resolve_site_id(site_id)
    with connect() as db:
        site = db.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
        if not site:
            raise HTTPException(404, "site_not_found")
    located = bool(site["setup_complete"] and (site["latitude"] != 0 or site["longitude"] != 0))
    maps_url = f"https://www.google.com/maps/dir/?api=1&destination={site['latitude']},{site['longitude']}" if located else ""
    return {"site_id": site_id, "configured": bool(site["setup_complete"]), "display_name": site["name"], "address": site["address"], "logo_data_uri": site["logo_data_uri"], "latitude": site["latitude"] if located else None, "longitude": site["longitude"] if located else None, "waypoints": ([{"name": site["name"], "latitude": site["latitude"], "longitude": site["longitude"]}, {"name": "Parking entrance", "latitude": site["latitude"] + 0.00035, "longitude": site["longitude"] + 0.0001}] if located else []), "google_maps_url": maps_url}


@app.get("/api/v1/admin/site", dependencies=[Depends(roles_required("ADMIN", "MANAGER"))])
def admin_site() -> dict[str, Any]:
    with connect() as db:
        site = db.execute("SELECT id, name, address, latitude, longitude, logo_data_uri, setup_complete, contact_email, contact_phone, social_links FROM sites WHERE id = ?", (SITE_ID,)).fetchone()
        if not site:
            raise HTTPException(404, "site_not_found")
        result = {**dict(site), "setup_complete": bool(site["setup_complete"])}
        result["social_links"] = json.loads(result["social_links"] or "{}")
        return result


@app.patch("/api/v1/admin/site", dependencies=[Depends(roles_required("ADMIN", "MANAGER"))])
def configure_site(config: SiteSetup) -> dict[str, Any]:
    if (config.latitude is None) != (config.longitude is None):
        raise HTTPException(422, "latitude_and_longitude_must_be_provided_together")
    if config.logo_data_uri and not re.fullmatch(r"data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/]+=*", config.logo_data_uri):
        raise HTTPException(422, "logo_must_be_png_jpeg_or_webp_data")
    if config.contact_email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", config.contact_email):
        raise HTTPException(422, "invalid_contact_email")
    if set(config.social_links) - {"x", "facebook", "instagram", "linkedin"} or any(url and not re.fullmatch(r"https://[^\s]+", url) for url in config.social_links.values()):
        raise HTTPException(422, "social_links_must_be_https")
    with connect() as db:
        db.execute("UPDATE sites SET name = ?, address = ?, latitude = COALESCE(?, latitude), longitude = COALESCE(?, longitude), logo_data_uri = COALESCE(?, logo_data_uri), contact_email = ?, contact_phone = ?, social_links = ?, setup_complete = 1 WHERE id = ?", (config.name.strip(), config.address.strip(), config.latitude, config.longitude, config.logo_data_uri, config.contact_email.strip().lower(), config.contact_phone.strip(), json.dumps(config.social_links), SITE_ID))
        audit(db, "parking_lot_configured", f"Configured parking lot: {config.name.strip()}")
    return admin_site()


@app.get("/api/parking/destination")
def legacy_destination(site_id: str = Query(default=SITE_ID)) -> dict[str, Any]:
    return destination(site_id)


@app.get("/api/v1/security/lot")
def security_lot(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    require_staff_if_closed(authorization)
    bays = live_bays()
    with connect() as db:
        rows = db.execute("SELECT id, status, assigned_session, vehicle_id, updated_at FROM parking_spaces WHERE site_id = ? ORDER BY id", (SITE_ID,)).fetchall()
        rows = [row for row in rows if row["id"] in ("L1", "L2")]
        by_id = {bay["id"]: bay for bay in bays}
        spaces = []
        for row in rows:
            bay = by_id[row["id"]]
            state = bay["display_state"]
            spaces.append({"id": row["id"], "status": {"AVAILABLE": "AVAILABLE", "OCCUPIED": "OCCUPIED", "ASSIGNED": "RESERVED", "WAITING_FOR_SENSOR": "UNKNOWN"}[state], "assigned_session": row["assigned_session"], "vehicle_id": row["vehicle_id"], "updated_at": row["updated_at"]})
        return {"site_id": SITE_ID, "spaces": spaces, "stats": {status: sum(row["status"] == status for row in spaces) for status in ("AVAILABLE", "RESERVED", "OCCUPIED")}}


def live_bays() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("""SELECT p.id, COALESCE(s.physical_state, 'UNKNOWN') AS physical_state,
            s.device_id, s.last_seen, s.updated_at AS sensor_updated_at,
            CASE WHEN s.last_seen IS NULL OR s.last_seen < ? THEN 'WAITING_FOR_SENSOR'
                 WHEN p.status = 'OCCUPIED' THEN 'OCCUPIED'
                 WHEN p.assigned_session IS NOT NULL AND COALESCE(s.physical_state, 'UNKNOWN') = 'FREE' THEN 'ASSIGNED'
                 WHEN s.physical_state = 'OCCUPIED' THEN 'OCCUPIED'
                 ELSE 'AVAILABLE' END AS display_state,
            CASE WHEN p.assigned_session IS NOT NULL THEN 1 ELSE 0 END AS assigned
            FROM parking_spaces p LEFT JOIN bay_sensor_state s ON s.space_id = p.id
            WHERE p.site_id = ? AND p.id IN ('L1','L2') ORDER BY p.id""", ((datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(), SITE_ID)).fetchall()
        return [dict(row) for row in rows]


@app.get("/api/bays")
def get_live_bays() -> dict[str, Any]:
    return {"site_id": SITE_ID, "bays": live_bays()}


@app.get("/api/public/live-availability")
def public_live_availability() -> dict[str, Any]:
    bays = live_bays()
    spaces = [{"id": bay["id"], "status": "OCCUPIED" if bay["display_state"] == "OCCUPIED" else "RESERVED" if bay["display_state"] == "ASSIGNED" else "AVAILABLE" if bay["display_state"] == "AVAILABLE" else "UNKNOWN"} for bay in bays]
    return {"site_id": SITE_ID, "total": len(spaces), "available": sum(space["status"] == "AVAILABLE" for space in spaces), "occupied": sum(space["status"] == "OCCUPIED" for space in spaces), "unavailable": sum(space["status"] == "UNKNOWN" for space in spaces), "spaces": spaces, "configured": True, "updated_at": now()}


@app.get("/api/public/facility-config")
def public_facility_config() -> dict[str, Any]:
    with connect() as db:
        row = db.execute("SELECT latitude, longitude FROM sites WHERE id = ?", (SITE_ID,)).fetchone()
    if not row:
        raise HTTPException(404, "site_not_found")
    return {"site_id": SITE_ID, "latitude": row["latitude"], "longitude": row["longitude"]}


@app.post("/api/v1/security/bays/{space_id}/assignment")
async def set_bay_assignment(space_id: str, assignment: BayAssignment) -> dict[str, Any]:
    if space_id not in ("L1", "L2"):
        raise HTTPException(404, "bay_not_found")
    with DB_LOCK, connect() as db:
        sensor = db.execute("SELECT physical_state, last_seen FROM bay_sensor_state WHERE space_id = ?", (space_id,)).fetchone()
        if assignment.assigned:
            if not sensor or not sensor["last_seen"] or sensor["last_seen"] < (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat() or sensor["physical_state"] != "FREE":
                raise HTTPException(409, "bay_not_confirmed_free")
            existing = db.execute("SELECT assigned_session, status FROM parking_spaces WHERE id = ?", (space_id,)).fetchone()
            if existing["status"] == "OCCUPIED":
                raise HTTPException(409, "bay_occupied")
            if existing["assigned_session"] not in (None, "DEMO_DRIVER"):
                raise HTTPException(409, "bay_already_assigned")
            db.execute("UPDATE parking_spaces SET assigned_session = 'DEMO_DRIVER', status = 'RESERVED', updated_at = ? WHERE id = ?", (now(), space_id))
            db.execute("INSERT INTO demo_bay_assignments(id, space_id, created_at, active) VALUES (?, ?, ?, 1) ON CONFLICT(id) DO NOTHING", (secrets.token_urlsafe(10), space_id, now()))
        else:
            db.execute("UPDATE demo_bay_assignments SET active = 0 WHERE space_id = ? AND active = 1", (space_id,))
            db.execute("UPDATE parking_spaces SET assigned_session = NULL, status = CASE WHEN (SELECT physical_state FROM bay_sensor_state WHERE space_id = ?) = 'OCCUPIED' THEN 'OCCUPIED' ELSE 'AVAILABLE' END, updated_at = ? WHERE id = ?", (space_id, now(), space_id))
        audit(db, "bay_assignment", f"{space_id} {'assigned to demo driver' if assignment.assigned else 'assignment cleared'}", space_id)
    await manager.broadcast({"type": "bay_state_changed", "space_id": space_id})
    return next(bay for bay in live_bays() if bay["id"] == space_id)


@app.post("/api/bays/{space_id}/assignment")
async def set_public_demo_assignment(space_id: str, assignment: BayAssignment) -> dict[str, Any]:
    return await set_bay_assignment(space_id, assignment)


@app.get("/api/v1/security/arrivals", dependencies=[Depends(roles_required("SECURITY", "ADMIN", "MANAGER"))])
@app.get("/api/guard/arrivals", dependencies=[Depends(roles_required("SECURITY", "ADMIN", "MANAGER"))])
def security_arrivals() -> list[dict[str, Any]]:
    with connect() as db:
        return [dict(row) for row in db.execute("SELECT id, site_id, status, created_at, arrived_at FROM visitor_sessions WHERE status IN ('WAITING_CONFIRMATION', 'WAITING_VERIFIED', 'ASSIGNED') ORDER BY arrived_at").fetchall()]


@app.get("/api/guard/sessions", dependencies=[Depends(roles_required("SECURITY", "ADMIN", "MANAGER"))])
@app.get("/api/v1/security/queue", dependencies=[Depends(roles_required("SECURITY", "ADMIN", "MANAGER"))])
def security_queue() -> list[dict[str, Any]]:
    """Operational lifecycle data, excluding visitor tokens and full vehicle identifiers."""
    with connect() as db:
        rows = db.execute(
            """SELECT s.id AS session_id, s.status AS session_status, s.created_at, s.arrived_at,
                      r.space_id, r.status AS reservation_status, r.occupied_at,
                      i.id AS invoice_id, i.status AS invoice_status, i.amount_minor, i.currency
               FROM visitor_sessions s
               LEFT JOIN reservations r ON r.session_id = s.id
                 AND r.status IN ('RESERVED','OCCUPIED','EXIT_REQUESTED','EXIT_AUTHORIZED')
               LEFT JOIN invoices i ON i.session_id = s.id
               WHERE s.site_id = ? AND s.status IN ('WAITING_CONFIRMATION','WAITING_VERIFIED','ASSIGNED','PARKED','EXIT_REQUESTED','EXIT_AUTHORIZED')
               ORDER BY CASE s.status WHEN 'EXIT_REQUESTED' THEN 0 WHEN 'WAITING' THEN 1 WHEN 'ASSIGNED' THEN 2 ELSE 3 END,
                        COALESCE(s.arrived_at, s.created_at)""",
            (SITE_ID,),
        ).fetchall()
        return [dict(row) for row in rows]


@app.get("/api/v1/admin/events", dependencies=[Depends(roles_required("ADMIN", "MANAGER"))])
def admin_events(limit: int = Query(default=50, le=200)) -> list[dict[str, Any]]:
    with connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]


@app.get("/api/v1/admin/analytics", dependencies=[Depends(roles_required("ADMIN", "MANAGER"))])
def admin_analytics() -> dict[str, Any]:
    with connect() as db:
        statuses = {row["status"]: row["count"] for row in db.execute("SELECT status, COUNT(*) AS count FROM parking_spaces WHERE site_id = ? GROUP BY status", (SITE_ID,)).fetchall()}
        active = db.execute("SELECT COUNT(*) AS count FROM visitor_sessions WHERE site_id = ? AND status IN ('WAITING_CONFIRMATION','WAITING_VERIFIED','WAITING','ASSIGNED','PARKED','EXIT_REQUESTED','EXIT_AUTHORIZED')", (SITE_ID,)).fetchone()["count"]
        invoices = db.execute("SELECT status, COUNT(*) AS count, COALESCE(SUM(amount_minor),0) AS amount_minor FROM invoices GROUP BY status").fetchall()
        event_counts = db.execute("SELECT event_type, COUNT(*) AS count FROM audit_events WHERE created_at >= ? GROUP BY event_type ORDER BY count DESC", ((datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),)).fetchall()
    return {"site_id": SITE_ID, "period_days": 30, "spaces_by_status": statuses, "active_visitor_sessions": active,
            "invoices": [dict(row) for row in invoices], "audit_events_by_type": [dict(row) for row in event_counts],
            "demo_amounts_are_not_transfers": True}


@app.get("/api/v1/admin/events/export.csv", dependencies=[Depends(roles_required("ADMIN", "MANAGER"))])
def export_admin_events(limit: int = Query(default=5000, ge=1, le=20000)) -> Response:
    with connect() as db:
        rows = db.execute("SELECT id, event_type, space_id, session_id, description, created_at FROM audit_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    output = BytesIO()
    # CSV is UTF-8 and contains only the same operational fields as the admin event feed.
    import io
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(("id", "event_type", "space_id", "session_id", "description", "created_at"))
    writer.writerows(tuple(row) for row in rows)
    output.write(stream.getvalue().encode("utf-8-sig"))
    return Response(output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=smartpark-events.csv"})


@app.get("/api/v1/admin/config", dependencies=[Depends(roles_required("ADMIN", "MANAGER"))])
def admin_config() -> dict[str, Any]:
    with connect() as db:
        config = {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM app_config")}
    return {"site_id": SITE_ID, "entrance_threshold_cm": float(config.get("entrance_threshold_cm", 5)), "reservation_minutes": int(config.get("reservation_minutes", RESERVATION_MINUTES)), "simulator_enabled": SIMULATOR_ENABLED}


@app.get("/api/v1/admin/public-url", dependencies=[Depends(roles_required("ADMIN"))])
def admin_public_url() -> dict[str, str]:
    return {"base_url": public_base_url()}


@app.patch("/api/v1/admin/public-url", dependencies=[Depends(roles_required("ADMIN"))])
def save_admin_public_url(config: PublicURLSetup) -> dict[str, str]:
    base = config.base_url.rstrip("/")
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?", base) or re.search(r"localhost|127\.0\.0\.1", base, re.I):
        raise HTTPException(422, "public_url_must_be_reachable_https_origin")
    with connect() as db:
        db.execute("INSERT INTO app_config(key, value) VALUES ('public_base_url', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (base,))
        audit(db, "public_url_updated", "Updated the public HTTPS visitor URL")
    return {"base_url": base}


@app.patch("/api/v1/admin/config", dependencies=[Depends(roles_required("ADMIN"))])
def patch_admin_config(config: ConfigPatch) -> dict[str, Any]:
    with connect() as db:
        for key, value in config.model_dump(exclude_none=True).items():
            db.execute("INSERT INTO app_config(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    return admin_config()


@app.get("/api/v1/admin/users", dependencies=[Depends(roles_required("ADMIN"))])
def admin_users() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("SELECT id, email, display_name, role, active, created_at FROM admin_users ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]


@app.post("/api/v1/admin/devices", dependencies=[Depends(roles_required("ADMIN"))], status_code=201)
def provision_device(device: DeviceCreate) -> dict[str, str]:
    credential = secrets.token_urlsafe(32)
    with connect() as db:
        existing = db.execute("SELECT token_hash FROM devices WHERE id = ?", (device.device_id,)).fetchone()
        if existing and existing["token_hash"]:
            raise HTTPException(409, "device_id_already_registered")
        if existing:
            db.execute("UPDATE devices SET name = ?, token_hash = ?, active = 1 WHERE id = ?", (device.name.strip(), token_hash(credential), device.device_id))
        else:
            db.execute("INSERT INTO devices(id, name, online, last_seen, token_hash, active) VALUES (?, ?, 0, NULL, ?, 1)", (device.device_id, device.name.strip(), token_hash(credential)))
        audit(db, "device_provisioned", f"Provisioned IoT device {device.device_id}")
    return {"device_id": device.device_id, "device_token": credential, "note": "Store this token securely; it cannot be retrieved again."}


@app.post("/api/v1/admin/devices/{device_id}/revoke", dependencies=[Depends(roles_required("ADMIN"))])
def revoke_device(device_id: str) -> dict[str, str]:
    with connect() as db:
        changed = db.execute("UPDATE devices SET active = 0, online = 0 WHERE id = ? AND token_hash IS NOT NULL", (device_id,)).rowcount
        if not changed:
            raise HTTPException(404, "provisioned_device_not_found")
        audit(db, "device_revoked", f"Revoked IoT device {device_id}")
    return {"device_id": device_id, "status": "revoked"}


@app.get("/api/v1/admin/devices", dependencies=[Depends(roles_required("ADMIN"))])
def list_devices() -> list[dict[str, Any]]:
    with connect() as db:
        return [dict(row) for row in db.execute("SELECT id AS device_id, name, online, last_seen, active FROM devices ORDER BY name").fetchall()]


@app.post("/api/v1/admin/users", dependencies=[Depends(roles_required("ADMIN"))], status_code=201)
def create_admin_user(user: AdminUserCreate) -> dict[str, Any]:
    email = user.email.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(422, "valid_email_required")
    with DB_LOCK, connect() as db:
        if db.execute("SELECT 1 FROM admin_users WHERE email = ?", (email,)).fetchone():
            raise HTTPException(409, "email_already_registered")
        user_id = secrets.token_urlsafe(12)
        db.execute("INSERT INTO admin_users(id, email, display_name, password_hash, role, active, created_at, ghana_card_hash, ghana_card_last4, phone_number) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)", (user_id, email, user.display_name.strip(), password_hash(user.password), user.role, now(), token_hash(user.ghana_card_number), user.ghana_card_number[-4:], user.phone_number))
        audit(db, "admin_user_created", f"Created {user.role.lower()} account for {email}")
    return {"id": user_id, "email": email, "display_name": user.display_name.strip(), "role": user.role, "active": True}


@app.get("/api/v1/admin/sites/{site_id}/qr", dependencies=[Depends(roles_required("ADMIN", "MANAGER"))])
def admin_qr(site_id: str, format: str = Query(default="svg", pattern="^(svg|png)$")) -> Response:
    public = public_entry(site_id)
    if not public["entry_url"]:
        raise HTTPException(409, "configure_reachable_https_public_url_before_printing_qr")
    code = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=18, border=4)
    code.add_data(public["entry_url"])
    code.make(fit=True)
    if format == "png":
        image = code.make_image(fill_color="#203026", back_color="white")
        output = BytesIO()
        image.save(output, format="PNG")
        return Response(output.getvalue(), media_type="image/png", headers={"Content-Disposition": f'attachment; filename="smartpark-{site_id}.png"'})
    matrix = code.get_matrix()
    size = len(matrix)
    cells = "".join(f'<rect x="{x}" y="{y}" width="1" height="1"/>' for y, row in enumerate(matrix) for x, filled in enumerate(row) if filled)
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" shape-rendering="crispEdges"><rect width="100%" height="100%" fill="white"/><g fill="#203026">{cells}</g></svg>'
    return Response(svg, media_type="image/svg+xml", headers={"Content-Disposition": f'attachment; filename="smartpark-{site_id}.svg"'})


@app.get("/api/public/sites/{site_id}/qr")
def public_qr(site_id: str, format: str = Query(default="svg", pattern="^(svg|png)$")) -> Response:
    return admin_qr(site_id, format)


@app.get("/api/guard/qr-poster", dependencies=[Depends(roles_required("SECURITY", "ADMIN", "MANAGER"))])
def guard_qr_poster(format: str = Query(default="png", pattern="^(svg|png)$")) -> Response:
    return admin_qr(SITE_ID, format)


@app.post("/api/v1/iot/events")
async def iot_event(event: SensorEvent, authenticated_device: str | None = Depends(device_required)) -> dict[str, Any]:
    if authenticated_device and event.device_id != authenticated_device:
        raise HTTPException(403, "device_identity_mismatch")
    return await process_event(event)


@app.post("/api/iot/events")
async def live_iot_event(event: SensorEvent, authenticated_device: str | None = Depends(device_required)) -> dict[str, Any]:
    if authenticated_device and event.device_id != authenticated_device:
        raise HTTPException(403, "device_identity_mismatch")
    if event.space_id not in ("L1", "L2") or event.event_type.lower() not in ("bay_occupied", "bay_free", "bay_vacant", "space_vacant"):
        raise HTTPException(422, "invalid_two_bay_event")
    if event.facility_id and event.facility_id != SITE_ID:
        raise HTTPException(403, "device_facility_mismatch")
    state = "OCCUPIED" if event.event_type.lower() == "bay_occupied" else "FREE"
    measured_at = event.measured_at or event.observed_at or now()
    with DB_LOCK, connect() as db:
        if db.execute("SELECT 1 FROM iot_events WHERE event_id = ?", (event.event_id,)).fetchone():
            return {"status": "duplicate", "event_id": event.event_id}
        db.execute("INSERT INTO iot_events VALUES (?, ?, ?, ?, ?, ?, ?)", (event.event_id, event.device_id, event.sensor_id, "bay_" + state.lower(), event.space_id, measured_at, now()))
        db.execute("INSERT INTO bay_sensor_state(space_id, physical_state, device_id, last_seen, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(space_id) DO UPDATE SET physical_state=excluded.physical_state, device_id=excluded.device_id, last_seen=excluded.last_seen, updated_at=excluded.updated_at", (event.space_id, state, event.device_id, now(), now()))
        if state == "OCCUPIED":
            db.execute("UPDATE demo_bay_assignments SET active = 0 WHERE space_id = ? AND active = 1", (event.space_id,))
            reservation = db.execute("SELECT * FROM reservations WHERE space_id = ? AND status = 'RESERVED' ORDER BY created_at DESC LIMIT 1", (event.space_id,)).fetchone()
            if not reservation:
                db.execute("UPDATE demo_bay_assignments SET active = 0 WHERE space_id = ? AND active = 1", (event.space_id,))
                db.execute("UPDATE parking_spaces SET status = 'OCCUPIED', assigned_session = NULL, updated_at = ? WHERE id = ?", (measured_at, event.space_id))
            else:
                db.execute("UPDATE parking_spaces SET status = 'OCCUPIED', updated_at = ? WHERE id = ?", (measured_at, event.space_id))
            if reservation:
                db.execute("UPDATE reservations SET status = 'OCCUPIED', occupied_at = COALESCE(occupied_at, ?) WHERE id = ?", (measured_at, reservation["id"]))
                db.execute("UPDATE visitor_sessions SET status = 'PARKED' WHERE id = ?", (reservation["session_id"],))
        else:
            reservation = db.execute("SELECT * FROM reservations WHERE space_id = ? AND status = 'EXIT_AUTHORIZED' ORDER BY created_at DESC LIMIT 1", (event.space_id,)).fetchone()
            if reservation:
                db.execute("UPDATE bay_sensor_state SET physical_state='FREE', last_seen=?, updated_at=? WHERE space_id=?", (measured_at, measured_at, event.space_id))
                db.execute("UPDATE reservations SET status = 'RELEASED', released_at = ? WHERE id = ?", (measured_at, reservation["id"]))
                db.execute("UPDATE visitor_sessions SET status = 'CLOSED' WHERE id = ?", (reservation["session_id"],))
                db.execute("UPDATE parking_spaces SET status = 'AVAILABLE', assigned_session = NULL, vehicle_id = NULL, updated_at = ? WHERE id = ?", (measured_at, event.space_id))
            else:
                db.execute("UPDATE bay_sensor_state SET physical_state='FREE', last_seen=?, updated_at=? WHERE space_id=?", (measured_at, measured_at, event.space_id))
                db.execute("UPDATE demo_bay_assignments SET active = 0 WHERE space_id = ? AND active = 1", (event.space_id,))
                db.execute("UPDATE parking_spaces SET status = 'AVAILABLE', assigned_session = NULL WHERE id = ?", (event.space_id,))
                db.execute("UPDATE parking_spaces SET updated_at = ? WHERE id = ?", (measured_at, event.space_id))
        audit(db, "bay_" + state.lower(), f"{event.space_id} physical state is {state}", event.space_id)
    await manager.broadcast({"type": "bay_state_changed", "space_id": event.space_id, "physical_state": state})
    return {"status": "processed", "event_id": event.event_id}


@app.post("/api/iot/demo-events")
async def live_iot_demo_event(event: SensorEvent) -> dict[str, Any]:
    if event.space_id not in ("L1", "L2") or event.event_type.lower() not in ("bay_occupied", "bay_free"):
        raise HTTPException(422, "invalid_two_bay_event")
    state = "OCCUPIED" if event.event_type.lower() == "bay_occupied" else "FREE"
    measured_at = now()
    with DB_LOCK, connect() as db:
        if db.execute("SELECT 1 FROM iot_events WHERE event_id = ?", (event.event_id,)).fetchone():
            return {"status": "duplicate", "event_id": event.event_id}
        db.execute("INSERT INTO iot_events VALUES (?, 'demo', ?, ?, ?, ?, ?)", (event.event_id, event.sensor_id, "bay_" + state.lower(), event.space_id, measured_at, measured_at))
        db.execute("INSERT INTO bay_sensor_state(space_id, physical_state, device_id, last_seen, updated_at) VALUES (?, ?, 'demo', ?, ?) ON CONFLICT(space_id) DO UPDATE SET physical_state=excluded.physical_state, device_id='demo', last_seen=excluded.last_seen, updated_at=excluded.updated_at", (event.space_id, state, measured_at, measured_at))
        if state == "OCCUPIED":
            db.execute("UPDATE demo_bay_assignments SET active = 0 WHERE space_id = ? AND active = 1", (event.space_id,))
            reservation = db.execute("SELECT * FROM reservations WHERE space_id = ? AND status = 'RESERVED' ORDER BY created_at DESC LIMIT 1", (event.space_id,)).fetchone()
            db.execute("UPDATE parking_spaces SET status = 'OCCUPIED', updated_at = ? WHERE id = ?", (measured_at, event.space_id))
            if reservation:
                db.execute("UPDATE reservations SET status = 'OCCUPIED', occupied_at = COALESCE(occupied_at, ?) WHERE id = ?", (measured_at, reservation["id"]))
                db.execute("UPDATE visitor_sessions SET status = 'PARKED' WHERE id = ?", (reservation["session_id"],))
        else:
            reservation = db.execute("SELECT * FROM reservations WHERE space_id = ? AND status = 'EXIT_AUTHORIZED' ORDER BY created_at DESC LIMIT 1", (event.space_id,)).fetchone()
            if reservation:
                db.execute("UPDATE bay_sensor_state SET physical_state='FREE', last_seen=?, updated_at=? WHERE space_id=?", (measured_at, measured_at, event.space_id))
                db.execute("UPDATE reservations SET status = 'RELEASED', released_at = ? WHERE id = ?", (measured_at, reservation["id"]))
                db.execute("UPDATE visitor_sessions SET status = 'CLOSED' WHERE id = ?", (reservation["session_id"],))
                db.execute("UPDATE parking_spaces SET status = 'AVAILABLE', assigned_session = NULL, vehicle_id = NULL, updated_at = ? WHERE id = ?", (measured_at, event.space_id))
            else:
                db.execute("UPDATE demo_bay_assignments SET active = 0 WHERE space_id = ? AND active = 1", (event.space_id,))
                db.execute("UPDATE parking_spaces SET status = 'AVAILABLE', assigned_session = NULL, updated_at = ? WHERE id = ?", (measured_at, event.space_id))
        audit(db, "bay_" + state.lower(), f"Demo sensor: {event.space_id} is {state}", event.space_id)
    await manager.broadcast({"type": "bay_state_changed", "space_id": event.space_id, "physical_state": state})
    return {"status": "processed", "event_id": event.event_id}


@app.post("/api/v1/iot/heartbeat")
def iot_heartbeat(device_id: str, name: str = "Raspberry Pi sensor agent", authenticated_device: str | None = Depends(device_required)) -> dict[str, str]:
    if authenticated_device and device_id != authenticated_device:
        raise HTTPException(403, "device_identity_mismatch")
    with connect() as db:
        timestamp = now()
        db.execute("INSERT INTO devices(id, name, online, last_seen) VALUES (?, ?, 1, ?) ON CONFLICT(id) DO UPDATE SET online=1, last_seen=excluded.last_seen", (device_id, name, timestamp))
        # Keep known bay states fresh while this device is healthy; don't add state rows for uninitialized sensors.
        db.execute("UPDATE bay_sensor_state SET last_seen = ? WHERE device_id = ? AND physical_state IN ('FREE','OCCUPIED')", (timestamp, device_id))
    return {"device_id": device_id, "status": "online"}


async def process_event(event: SensorEvent) -> dict[str, Any]:
    event_type = event.event_type.lower()
    if event_type == "bay_occupied":
        event_type = "space_occupied"
    elif event_type in ("space_vacant", "bay_vacant"):
        event_type = "space_released"
    elif event_type == "vehicle_detected":
        event_type = "vehicle_approaching"
    visitor_notifications: list[tuple[str, str]] = []
    auto_match_id: str | None = None
    guard_match_count = 0
    with DB_LOCK, connect() as db:
        if db.execute("SELECT 1 FROM iot_events WHERE event_id = ?", (event.event_id,)).fetchone():
            return {"status": "duplicate", "event_id": event.event_id}
        if event.facility_id and event.facility_id != SITE_ID:
            raise HTTPException(403, "device_facility_mismatch")
        measured_at = event.measured_at or event.observed_at or now()
        db.execute("INSERT INTO iot_events VALUES (?, ?, ?, ?, ?, ?, ?)", (event.event_id, event.device_id, event.sensor_id, event_type, event.space_id, measured_at, now()))
        if event_type == "space_occupied" and event.space_id:
            reservation = db.execute("SELECT * FROM reservations WHERE space_id = ? AND status = 'RESERVED'", (event.space_id,)).fetchone()
            db.execute("UPDATE parking_spaces SET status = 'OCCUPIED', updated_at = ? WHERE id = ? AND status = 'RESERVED'", (measured_at, event.space_id))
            if reservation:
                visitor_notifications.append((reservation["session_id"], "space_occupied"))
                db.execute("UPDATE reservations SET status = 'OCCUPIED', occupied_at = ? WHERE id = ? AND occupied_at IS NULL", (measured_at, reservation["id"]))
                db.execute("UPDATE visitor_sessions SET status = 'PARKED' WHERE id = ?", (reservation["session_id"],))
            audit(db, "space_occupied", f"{event.space_id} confirmed occupied", event.space_id, reservation["session_id"] if reservation else None)
        elif event_type == "space_released" and event.space_id:
            reservation = db.execute("SELECT * FROM reservations WHERE space_id = ? AND status = 'EXIT_AUTHORIZED'", (event.space_id,)).fetchone()
            if reservation:
                visitor_notifications.append((reservation["session_id"], "space_released"))
                db.execute("UPDATE reservations SET status = 'RELEASED', released_at = ? WHERE id = ?", (measured_at, reservation["id"]))
                db.execute("UPDATE visitor_sessions SET status = 'CLOSED' WHERE id = ?", (reservation["session_id"],))
                space = db.execute("SELECT site_id FROM parking_spaces WHERE id = ?", (event.space_id,)).fetchone()
                waiting = db.execute("SELECT id FROM visitor_sessions WHERE site_id = ? AND status = 'WAITING_VERIFIED' AND expires_at > ? ORDER BY arrived_at, created_at LIMIT 1", (space["site_id"], now())).fetchone()
                if waiting:
                    timestamp = now()
                    reservation_id = secrets.token_urlsafe(12)
                    db.execute("UPDATE parking_spaces SET status = 'RESERVED', assigned_session = ?, vehicle_id = NULL, updated_at = ? WHERE id = ?", (waiting["id"], timestamp, event.space_id))
                    db.execute("UPDATE visitor_sessions SET status = 'ASSIGNED' WHERE id = ?", (waiting["id"],))
                    db.execute("INSERT INTO reservations VALUES (?, ?, ?, 'RESERVED', ?, NULL, NULL)", (reservation_id, event.space_id, waiting["id"], timestamp))
                    visitor_notifications.append((waiting["id"], "space_reserved"))
                    audit(db, "space_reserved", f"{event.space_id} assigned to the next waiting visitor", event.space_id, waiting["id"])
                else:
                    db.execute("UPDATE bay_sensor_state SET physical_state='FREE', last_seen=?, updated_at=? WHERE space_id=?", (measured_at, measured_at, event.space_id))
                    db.execute("UPDATE demo_bay_assignments SET active = 0 WHERE space_id = ? AND active = 1", (event.space_id,))
                    db.execute("UPDATE parking_spaces SET status = 'AVAILABLE', assigned_session = NULL, vehicle_id = NULL, updated_at = ? WHERE id = ?", (measured_at, event.space_id))
                audit(db, "space_released", f"{event.space_id} released after confirmed departure", event.space_id, reservation["session_id"])
            else:
                audit(db, "vacancy_unmatched", f"Vacancy reported for {event.space_id} without authorized exit", event.space_id)
        elif event_type in ("device_heartbeat", "device_online"):
            db.execute("INSERT INTO devices(id, name, online, last_seen) VALUES (?, ?, 1, ?) ON CONFLICT(id) DO UPDATE SET online=1, last_seen=excluded.last_seen", (event.device_id, event.device_id, measured_at))
            db.execute("UPDATE bay_sensor_state SET last_seen = ? WHERE device_id = ? AND physical_state IN ('FREE','OCCUPIED')", (measured_at, event.device_id))
        elif event_type in ("device_offline", "sensor_error"):
            db.execute("INSERT INTO devices(id, name, online, last_seen) VALUES (?, ?, 0, ?) ON CONFLICT(id) DO UPDATE SET online=0, last_seen=excluded.last_seen", (event.device_id, event.device_id, measured_at))
            audit(db, event_type, f"Device {event.device_id}: {event.payload}", event.space_id)
        elif event_type in ("vehicle_approaching", "arrival_proximity"):
            config = {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM app_config").fetchall()}
            threshold = float(config.get("entrance_threshold_cm", "70"))
            if event.distance_cm is None or event.distance_cm > threshold:
                return {"status": "outside_threshold", "threshold_cm": threshold, "event_id": event.event_id}
            candidates = db.execute("SELECT id FROM visitor_sessions WHERE site_id = ? AND status = 'WAITING_CONFIRMATION' AND expires_at > ? ORDER BY created_at", (SITE_ID, now())).fetchall()
            if len(candidates) == 1:
                auto_match_id = candidates[0]["id"]
            elif len(candidates) > 1:
                guard_match_count = len(candidates)
                audit(db, "arrival_ambiguous", f"Sensor arrival requires guard selection among {guard_match_count} pending visitors")
            else:
                return {"status": "no_pending_visitors", "event_id": event.event_id}
    await manager.broadcast({"type": event_type, "space_id": event.space_id})
    if auto_match_id:
        matched = await verify_arrival(auto_match_id, ArrivalVerification(), internal_call=True)
        return {"status": "auto_matched", "event_id": event.event_id, "match": matched}
    if guard_match_count:
        return {"status": "guard_match_required", "pending_count": guard_match_count, "event_id": event.event_id}
    for session_id, private_type in visitor_notifications:
        await manager.broadcast({"type": private_type, "space_id": event.space_id}, session_id)
    return {"status": "processed", "event_id": event.event_id}


@app.post("/api/v1/simulator/events")
@app.post("/api/admin/simulator/events")
async def simulator_event(event: SimulatorEvent) -> dict[str, Any]:
    if not SIMULATOR_ENABLED:
        raise HTTPException(404, "simulator_disabled")
    sensor = SensorEvent(event_id=secrets.token_urlsafe(12), device_id="simulator", sensor_id="simulator", event_type=event.event_type, space_id=event.space_id, distance_cm=event.distance_cm if event.distance_cm is not None else (5 if event.event_type == "vehicle_approaching" else None))
    return await process_event(sensor)


@app.post("/api/admin/simulator/arrival")
async def simulator_arrival() -> dict[str, Any]:
    if not SIMULATOR_ENABLED:
        raise HTTPException(404, "simulator_disabled")
    event = SensorEvent(event_id=secrets.token_urlsafe(18), device_id="simulator", sensor_id="entrance", event_type="vehicle_approaching", distance_cm=5, facility_id=SITE_ID)
    return await process_event(event)


@app.post("/api/admin/simulator/occupancy")
async def simulator_occupancy(event: SimulatorEvent) -> dict[str, Any]:
    if not SIMULATOR_ENABLED or not event.space_id:
        raise HTTPException(400, "simulator_disabled_or_space_required")
    sensor = SensorEvent(event_id=secrets.token_urlsafe(18), device_id="simulator", sensor_id=event.space_id, event_type="space_occupied", space_id=event.space_id, facility_id=SITE_ID)
    return await process_event(sensor)


@app.post("/api/admin/simulator/departure")
async def simulator_departure(event: SimulatorEvent) -> dict[str, Any]:
    if not SIMULATOR_ENABLED or not event.space_id:
        raise HTTPException(400, "simulator_disabled_or_space_required")
    sensor = SensorEvent(event_id=secrets.token_urlsafe(18), device_id="simulator", sensor_id=event.space_id, event_type="space_released", space_id=event.space_id, facility_id=SITE_ID)
    return await process_event(sensor)


@app.websocket("/api/v1/ws/visitor")
async def visitor_socket(websocket: WebSocket, token: str = Query(...)) -> None:
    try:
        with connect() as db:
            session = session_from_token(db, token)
    except HTTPException:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    manager.visitors.setdefault(session["id"], set()).add(websocket)
    try:
        await websocket.send_json({"type": "connected", "session_id": session["id"]})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.visitors.get(session["id"], set()).discard(websocket)


@app.websocket("/api/v1/ws/operations")
async def operations_socket(websocket: WebSocket, authorization: str = Query(...)) -> None:
    try:
        with connect() as db:
            user = admin_required(f"Bearer {authorization}")
    except HTTPException:
        await websocket.close(code=1008)
        return
    if user["role"] not in ("SECURITY", "ADMIN", "MANAGER"):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    manager.operations.add(websocket)
    try:
        await websocket.send_json({"type": "connected", "site_id": SITE_ID})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.operations.discard(websocket)
