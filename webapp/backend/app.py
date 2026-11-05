"""
Servidor Web FastAPI para control de videoporteros Legrand / BTicino (Classe 100X).
Basado en ingeniería inversa del APK com.legrandgroup.c100x.
"""
import os
import re
import json
import logging
import uuid
import asyncio
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel
from cryptography import x509
from cryptography.x509.oid import NameOID

from eliot_client import EliotClient, CLIENT_ID, REDIRECT_URI, SCOPES
from sip_manager import SipManager
from camera_manager import CameraManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("doorentry_app")

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
SESSION_FILE = DATA_DIR / "session.json"
CONFIG_FILE = DATA_DIR / "config.json"

# Perfil de Hardware Preconfigurado (Classe 100X Autónomo)
DEFAULT_PLANT_ID = os.environ.get("PLANT_ID", "")
DEFAULT_GATEWAY_ID = os.environ.get("GATEWAY_ID", "")
DEFAULT_ENTRANCE_PANEL_GUID = os.environ.get("ENTRANCE_PANEL_GUID", "")
DEFAULT_SECONDARY_PANEL_GUID = os.environ.get("SECONDARY_PANEL_GUID", "")

AVAILABLE_CAMERAS = [
    {
        "index": 0,
        "name": "Placa Exterior Principal (PE0)",
        "address": DEFAULT_ENTRANCE_PANEL_GUID,
        "bus_address": "20",
        "description": "Entrada de Calle Principal (SCS Bus 2 Hilos)"
    },
    {
        "index": 1,
        "name": "Cámara Interior (PE1)",
        "address": DEFAULT_SECONDARY_PANEL_GUID,
        "bus_address": "21",
        "description": "Cámara Interior / Entrada Secundaria (SCS)"
    }
]
DEFAULT_SIP_USER = os.environ.get("SIP_USER", "")
DEFAULT_SIP_PASS = os.environ.get("SIP_PASSWORD", "")

eliot = EliotClient()
sip = SipManager(data_dir=DATA_DIR)
camera_mgr = CameraManager()

# Intentar restaurar sesión persistente al arrancar
if SESSION_FILE.exists():
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            sess_data = json.load(f)
            eliot.auth_token = sess_data.get("auth_token")
            eliot.refresh_token = sess_data.get("refresh_token")
            eliot.username = sess_data.get("username")
            eliot.user_data = sess_data.get("user_data", {})
            logger.info(f"Sesión restaurada para {eliot.username}")
    except Exception as e:
        logger.warning(f"No se pudo restaurar sesión previa: {e}")

# Helper para inspección de client.crt
def get_certificate_info(cert_path: Optional[Path] = None) -> Dict[str, Any]:
    target_path = cert_path or sip.client_cert_path
    if not target_path.exists():
        return {
            "exists": False,
            "valid": False,
            "subject": None,
            "issuer": None,
            "not_valid_before": None,
            "not_valid_after": None,
            "days_remaining": 0,
            "message": "client.crt no encontrado en el sistema"
        }
    try:
        data = target_path.read_bytes()
        cert = x509.load_pem_x509_certificate(data)
        now = datetime.now(timezone.utc)
        if hasattr(cert, "not_valid_before_utc"):
            nvb = cert.not_valid_before_utc
            nva = cert.not_valid_after_utc
        else:
            nvb = cert.not_valid_before.replace(tzinfo=timezone.utc)
            nva = cert.not_valid_after.replace(tzinfo=timezone.utc)

        is_valid = (nvb <= now <= nva)
        days_rem = max(0, (nva - now).days)
        subj_str = ", ".join(f"{attr.oid._name}={attr.value}" for attr in cert.subject)
        iss_str = ", ".join(f"{attr.oid._name}={attr.value}" for attr in cert.issuer)

        return {
            "exists": True,
            "valid": is_valid,
            "subject": subj_str,
            "issuer": iss_str,
            "not_valid_before": nvb.isoformat(),
            "not_valid_after": nva.isoformat(),
            "days_remaining": days_rem,
            "message": f"Certificado mTLS {'válido' if is_valid else 'caducado'} ({days_rem} días restantes)"
        }
    except Exception as e:
        return {
            "exists": True,
            "valid": False,
            "subject": None,
            "issuer": None,
            "not_valid_before": None,
            "not_valid_after": None,
            "days_remaining": 0,
            "message": f"Error leyendo certificado mTLS: {e}"
        }

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: iniciar demonio SIP de escucha pasiva para detección de timbre
    logger.info("Iniciando demonio SIP de escucha pasiva en segundo plano...")
    sip.start_listener_daemon()
    yield
    # Shutdown: parar demonio y liberar canales
    logger.info("Deteniendo demonio SIP y liberando recursos del videoportero...")
    sip.stop_listener_daemon()
    await sip.stop_video_session()
    camera_mgr.deactivate()

app = FastAPI(
    title="Door Entry Web Client - Classe 100X",
    description="Control de videoportero Legrand / BTicino Classe 100X mediante API Eliot y SIP",
    lifespan=lifespan
)

# Modelos Pydantic
class LoginRequest(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    code: Optional[str] = None
    token: Optional[str] = None
    remember: bool = True

class CertGenerateRequest(BaseModel):
    username: str
    password: str

class OAuthCallbackRequest(BaseModel):
    code: str
    remember: bool = True

class TokenImportRequest(BaseModel):
    token: str
    refresh_token: Optional[str] = None
    remember: bool = True

class DoorOpenRequest(BaseModel):
    gateway_id: str
    address: str = "20"
    plant_id: Optional[str] = None

class LightToggleRequest(BaseModel):
    gateway_id: str
    address: str = "114"
    plant_id: Optional[str] = None

class CustomCommandRequest(BaseModel):
    gateway_id: str
    command: str

class CameraActionRequest(BaseModel):
    gateway_id: Optional[str] = None
    camera_index: Optional[int] = None
    address: Optional[str] = None
    direction: str = "next"

# Health check
@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "doorentry-web",
        "app": "Door Entry Web Client (Classe 100X)",
        "authenticated": bool(eliot.auth_token)
    }

# API de Autenticación
@app.get("/api/auth/oauth/url")
async def get_oauth_url():
    """Devuelve la URL oficial de login OAuth2 para Classe 100X (Azure AD B2C)."""
    return {
        "url": eliot.get_authorize_url(),
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scopes": SCOPES
    }

@app.post("/api/auth/oauth/callback")
async def oauth_callback(req: OAuthCallbackRequest):
    """Canjea un código de autorización o URL de redirección por tokens de acceso."""
    try:
        res = await eliot.exchange_code(req.code)
        if req.remember:
            with open(SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "username": eliot.username,
                    "auth_token": eliot.auth_token,
                    "refresh_token": eliot.refresh_token,
                    "user_data": eliot.user_data
                }, f)
        return res
    except Exception as e:
        logger.error(f"Fallo en canje de código OAuth: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/auth/token")
async def import_token(req: TokenImportRequest):
    """Importa directamente un token JWT / Bearer."""
    try:
        res = await eliot.login(req.token, "")
        if req.remember:
            with open(SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "username": eliot.username,
                    "auth_token": eliot.auth_token,
                    "refresh_token": req.refresh_token or eliot.refresh_token,
                    "user_data": eliot.user_data
                }, f)
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/auth/login")
async def login(req: LoginRequest):
    """Inicia sesión con credenciales, código OAuth o token directo."""
    try:
        if req.code:
            res = await eliot.exchange_code(req.code)
        elif req.token:
            res = await eliot.login(req.token, "")
        else:
            if not req.username or not req.password:
                raise HTTPException(status_code=400, detail="Correo electrónico y contraseña requeridos")
            res = await eliot.login_b2c(req.username, req.password)

        if req.remember:
            with open(SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "username": eliot.username,
                    "auth_token": eliot.auth_token,
                    "refresh_token": eliot.refresh_token,
                    "user_data": eliot.user_data
                }, f)
        return res
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Fallo login: {e}")
        raise HTTPException(status_code=401, detail=str(e))

@app.post("/api/auth/b2c-login")
async def b2c_login(req: LoginRequest):
    """Inicia sesión programática directa en Azure AD B2C sin intervención del navegador."""
    if not req.username or not req.password:
        raise HTTPException(status_code=400, detail="Usuario y contraseña requeridos para B2C login")
    try:
        res = await eliot.login_b2c(req.username, req.password)
        if req.remember:
            with open(SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "username": eliot.username,
                    "auth_token": eliot.auth_token,
                    "refresh_token": eliot.refresh_token,
                    "user_data": eliot.user_data
                }, f)
        return res
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Fallo login B2C directo: {e}")
        raise HTTPException(status_code=401, detail=str(e))

@app.post("/api/auth/logout")
async def logout():
    await sip.stop_video_session()
    camera_mgr.deactivate()
    await eliot.logout()
    if SESSION_FILE.exists():
        try:
            SESSION_FILE.unlink()
        except Exception:
            pass
    return {"success": True}

@app.get("/api/auth/session")
async def get_session():
    return {
        "authenticated": True,
        "mode": "cloud" if bool(eliot.auth_token) else "autonomous",
        "username": eliot.username or os.environ.get("LEGRAND_USER", "Usuario Autenticado"),
        "token_snippet": f"{eliot.auth_token[:8]}..." if eliot.auth_token else "mTLS Client Cert",
        "oauth_url": eliot.get_authorize_url()
    }

# API de Instalación y Dispositivos (100% Autónoma con Fallback a Perfil de Hardware)
@app.get("/api/plants")
async def list_plants():
    if eliot.auth_token:
        try:
            plants = await eliot.get_plants()
            if plants:
                return {"plants": plants}
        except Exception as e:
            logger.warning(f"Error consultando plantas en la nube Eliot: {e}")
    return {
        "plants": [{
            "PlantId": DEFAULT_PLANT_ID,
            "id": DEFAULT_PLANT_ID,
            "name": os.environ.get("PLANT_NAME", "Videoportero (Classe 100X)")
        }]
    }

@app.get("/api/plants/{plant_id}/gateways")
async def list_gateways(plant_id: str):
    if eliot.auth_token:
        try:
            gws = await eliot.get_gateways(plant_id)
            if gws:
                return {"gateways": gws}
        except Exception as e:
            logger.warning(f"Error consultando pasarelas en la nube Eliot: {e}")
    return {
        "gateways": [{
            "GatewayId": DEFAULT_GATEWAY_ID,
            "id": DEFAULT_GATEWAY_ID,
            "name": "Classe 100X",
            "local_ip": os.environ.get("GATEWAY_IP", "127.0.0.1")
        }]
    }

@app.get("/api/plants/{plant_id}/gateways/{gw_id}/devices")
async def get_devices(plant_id: str, gw_id: str):
    if eliot.auth_token:
        try:
            topo = await eliot.get_topology(plant_id, gw_id)
            if topo and topo.get("devices"):
                return topo
        except Exception as e:
            logger.warning(f"Error consultando topología en la nube Eliot: {e}")
    return {
        "devices": {
            "locks": [
                {
                    "name": "Cerradura Principal (Puerta)",
                    "deviceAddr": "20",
                    "type": "lock"
                }
            ],
            "lights": [
                {
                    "name": "Luz de Escalera",
                    "deviceAddr": "114",
                    "type": "light"
                }
            ],
            "cameras": [
                {
                    "name": "Placa Exterior Principal (PE0)",
                    "deviceAddr": DEFAULT_ENTRANCE_PANEL_GUID,
                    "type": "camera"
                },
                {
                    "name": "Cámara Interior (PE1)",
                    "deviceAddr": DEFAULT_SECONDARY_PANEL_GUID,
                    "type": "camera"
                }
            ]
        }
    }

@app.get("/api/plants/{plant_id}/gateways/{gw_id}/sip")
async def get_sip(plant_id: str, gw_id: str):
    if eliot.auth_token:
        try:
            sip_creds = await eliot.get_sip_credentials(plant_id, gw_id)
            if sip_creds and len(sip_creds) > 0:
                sip.configure_account(sip_creds[0])
                return {"sip_users": sip_creds}
        except Exception as e:
            logger.warning(f"Error consultando credenciales SIP en la nube: {e}")
    default_acc = {
        "Username": DEFAULT_SIP_USER,
        "password": DEFAULT_SIP_PASS,
        "sipServer": f"{DEFAULT_GATEWAY_ID}.bs.iotleg.com"
    }
    sip.configure_account(default_acc)
    return {"sip_users": [default_acc]}

# Gestión de Certificados mTLS (client.crt)
@app.get("/api/certificate/status")
async def certificate_status():
    """Inspecciona client.crt y devuelve validez, emisor, caducidad y días restantes."""
    return get_certificate_info()

@app.post("/api/certificate/generate")
async def certificate_generate(req: CertGenerateRequest):
    """
    Genera y firma un nuevo client.crt mediante login programático en Azure B2C
    y enrolamiento CSR contra la CA de Legrand. Recarga el demonio SIP en caliente.
    """
    if not req.username or not req.password:
        raise HTTPException(status_code=400, detail="Correo y contraseña de Legrand requeridos")
    try:
        logger.info(f"Iniciando login programático Azure B2C para renovación de certificados: {req.username}")
        await eliot.login_b2c(req.username, req.password)
        if not eliot.auth_token:
            raise HTTPException(status_code=401, detail="No se obtuvo token de usuario tras login B2C")

        # Persistir sesión actualizada
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "username": eliot.username,
                "auth_token": eliot.auth_token,
                "refresh_token": eliot.refresh_token,
                "user_data": eliot.user_data
            }, f)

        # Enrolar nuevo certificado mTLS con la CA de Legrand
        logger.info("Firmando nuevo certificado client.crt con la CA oficial de Legrand...")
        enrolled = await sip.enroll_certificates(
            user_token=eliot.auth_token,
            plant_id=DEFAULT_PLANT_ID,
            email=req.username
        )
        if not enrolled:
            raise HTTPException(status_code=500, detail="Fallo en la generación o firma del certificado mTLS")

        # Recargar en caliente el demonio SIP con el nuevo par de claves
        await sip.restart_listener()

        cert_info = get_certificate_info()
        logger.info(f"Certificado renovado exitosamente. Válido por {cert_info.get('days_remaining')} días.")
        return {
            "success": True,
            "message": "Nuevo certificado mTLS generado y cargado con éxito en el videoportero",
            "certificate": cert_info
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generando certificado mTLS: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# Control de Llamadas de Timbre (Atender, Colgar y Consulta de Estado)
@app.get("/api/call/status")
async def call_status():
    """Estado en tiempo real de llamadas entrantes o activación del timbre."""
    return sip.get_call_status()

@app.post("/api/call/answer")
async def call_answer():
    """Atiende la llamada entrante del timbre y arranca el visor de vídeo."""
    is_sim = getattr(sip, "call_context", {}).get("is_simulated", False)
    res = sip.answer_incoming_call(on_frame=camera_mgr.ingest_frame)
    if not res.get("success"):
        return res

    # 1. Activar inmediatamente el gestor de cámara
    camera_mgr.activate(
        gateway_id=DEFAULT_GATEWAY_ID,
        camera_index=0,
        camera_address=DEFAULT_ENTRANCE_PANEL_GUID
    )

    # 2. Si es una llamada simulada/de prueba o no hay receptor RTP corriendo activamente:
    # Iniciar la sesión de vídeo real en PE0 para que el visor cargue de inmediato la imagen de la placa exterior
    if is_sim or not (sip.video_receiver and sip.video_receiver.running):
        logger.info("Iniciando sesión de vídeo PE0 para la llamada atendida...")
        try:
            video_res = await sip.start_video_session(
                gateway_id=DEFAULT_GATEWAY_ID,
                camera_address=DEFAULT_ENTRANCE_PANEL_GUID,
                on_frame=camera_mgr.ingest_frame
            )
            if video_res.get("success"):
                logger.info("Vídeo de PE0 activo para la llamada atendida.")
                res["video_session"] = video_res
            else:
                logger.warning(f"Aviso al iniciar vídeo en llamada atendida: {video_res.get('error')}")
        except Exception as e:
            logger.error(f"Error iniciando vídeo para llamada atendida: {e}")

    return res

@app.post("/api/call/hangup")
async def call_hangup():
    """Rechaza o cuelga la llamada de timbre y libera el bus SCS."""
    res = sip.reject_or_hangup_call()
    try:
        await sip.stop_video_session()
    except Exception as e:
        logger.debug(f"Aviso en stop_video_session al colgar: {e}")
    camera_mgr.deactivate()
    return res

@app.post("/api/call/simulate")
async def call_simulate():
    """Simula una llamada entrante del timbre para pruebas completas de interfaz y sonido."""
    res = sip.simulate_incoming_call()
    return res

# Validación estricta y protección contra inyecciones en el bus SCS
ADDRESS_REGEX = re.compile(r"^\d{1,4}$")
COMMAND_REGEX = re.compile(r"^\*#?[0-9*#]{1,100}##$")

def validate_bus_address(address: str) -> str:
    addr = str(address).strip()
    if not ADDRESS_REGEX.match(addr):
        raise HTTPException(
            status_code=400,
            detail=f"Dirección de bus SCS inválida '{address}'. Debe ser numérica de 1 a 4 dígitos (ej: 20, 114)."
        )
    return addr

def validate_custom_command(cmd: str) -> str:
    cleaned = str(cmd).strip()
    if not COMMAND_REGEX.match(cleaned):
        raise HTTPException(
            status_code=400,
            detail=f"Comando OpenWebNet inválido. Debe seguir el formato *...## o *#...## con caracteres permitidos [0-9*#]."
        )
    return cleaned

# Rate limiter en memoria para protección del bus SCS físico (máx 5 peticiones cada 10s por IP)
door_unlock_history: Dict[str, list] = {}
door_rate_limit_lock = threading.Lock()

def check_door_rate_limit(client_ip: str, max_requests: int = 5, window_sec: float = 10.0):
    now = time.time()
    with door_rate_limit_lock:
        timestamps = door_unlock_history.get(client_ip, [])
        valid_timestamps = [t for t in timestamps if now - t < window_sec]
        if len(valid_timestamps) >= max_requests:
            door_unlock_history[client_ip] = valid_timestamps
            raise HTTPException(
                status_code=429,
                detail=f"Límite de apertura de puerta excedido ({max_requests} intentos en {int(window_sec)}s). Espere antes de reintentar."
            )
        valid_timestamps.append(now)
        door_unlock_history[client_ip] = valid_timestamps

# Control de Cerradura y Luces
@app.post("/api/door/open")
async def open_door(req: DoorOpenRequest, request: Request):
    """Envía la trama OpenWebNet y comando JSON-RPC para desbloquear la cerradura con rate-limiting y validación."""
    client_ip = request.client.host if request.client else "unknown"
    check_door_rate_limit(client_ip)

    addr = validate_bus_address(req.address or "20")
    gw_id = req.gateway_id if is_valid_uuid(req.gateway_id) else DEFAULT_GATEWAY_ID
    frames = sip.build_openwebnet_lock_command(addr)
    rpc_lock = sip.build_jsonrpc_lock_command(addr)
    frames.append(rpc_lock)
    res = await sip.send_command(frames, gateway_id=gw_id)
    return {
        "success": True,
        "message": f"Comando de apertura ejecutado en cerradura {addr}",
        "details": res
    }

@app.post("/api/light/toggle")
async def toggle_light(req: LightToggleRequest):
    """Envía la trama OpenWebNet y comando JSON-RPC para encender luces de escalera con validación."""
    addr = validate_bus_address(req.address or "114")
    gw_id = req.gateway_id if is_valid_uuid(req.gateway_id) else DEFAULT_GATEWAY_ID
    frames = sip.build_openwebnet_light_command(addr)
    rpc_light = sip.build_jsonrpc_light_command(addr)
    frames.append(rpc_light)
    res = await sip.send_command(frames, gateway_id=gw_id)
    return {
        "success": True,
        "message": f"Comando de luz de escalera ejecutado en dirección {addr}",
        "details": res
    }

@app.post("/api/command/custom")
async def custom_command(req: CustomCommandRequest):
    """Permite el envío manual de cualquier trama OpenWebNet personalizada con validación estricta."""
    cmd = validate_custom_command(req.command)
    gw_id = req.gateway_id if is_valid_uuid(req.gateway_id) else DEFAULT_GATEWAY_ID
    frames = [cmd]
    res = await sip.send_command(frames, gateway_id=gw_id)
    return {
        "success": True,
        "message": f"Comando personalizado '{cmd}' enviado",
        "details": res
    }

def is_valid_uuid(val: Any) -> bool:
    if not val:
        return False
    try:
        uuid.UUID(str(val))
        return True
    except Exception:
        return False

# Supervisor de Estado de Sesión y Liberación del Bus SCS
def handle_sip_session_ended():
    """Libera el canal del edificio de inmediato cuando el Classe 100X envía BYE o finaliza."""
    logger.info("Sesión SIP finalizada por el videoportero (BYE o timeout). Canal del edificio libre -> Standby.")
    camera_mgr.deactivate()

sip.on_session_ended = handle_sip_session_ended
sip.on_call_ended = handle_sip_session_ended

# Control de Cámara y Video Streaming (Classe 100X)
@app.get("/api/cameras")
async def get_cameras():
    """Lista las cámaras disponibles en la instalación y su estado."""
    cams = AVAILABLE_CAMERAS
    if eliot.auth_token:
        try:
            topo = await eliot.get_topology(DEFAULT_PLANT_ID, DEFAULT_GATEWAY_ID)
            disc_cams = topo.get("devices", {}).get("cameras", [])
            if disc_cams:
                cams = [
                    {
                        "index": idx,
                        "name": c.get("name") or ("Placa Exterior Principal (PE0)" if idx == 0 else "Cámara Interior (PE1)"),
                        "address": c.get("deviceAddr") or c.get("id"),
                        "bus_address": c.get("bus_address") or ("20" if idx == 0 else "21"),
                        "description": "Entrada de Calle Principal (SCS Bus 2 Hilos)" if idx == 0 else "Cámara Interior / Entrada Secundaria (SCS)"
                    }
                    for idx, c in enumerate(disc_cams)
                ]
        except Exception as e:
            logger.debug(f"Aviso consultando cámaras en topología: {e}")

    if len(cams) < len(AVAILABLE_CAMERAS):
        cams = AVAILABLE_CAMERAS

    return {
        "cameras": cams,
        "single_camera": len(cams) <= 1,
        "active_index": camera_mgr.camera_index,
        "active_address": camera_mgr.camera_address,
        "is_active": camera_mgr.is_active
    }

@app.post("/api/camera/activate")
async def activate_camera(req: Optional[CameraActionRequest] = None):
    """Activa la cámara exterior o interior puntualmente (auto-encendido 30s) sin bucles de videovigilancia."""
    req_gw = req.gateway_id if req and req.gateway_id else None
    gw_id = req_gw if is_valid_uuid(req_gw) else DEFAULT_GATEWAY_ID

    cam_idx = req.camera_index if (req and req.camera_index is not None) else 0
    req_addr = req.address if req and req.address else None

    # Mapeo y resolución de dirección y GUID
    if req_addr and is_valid_uuid(req_addr):
        cam_addr = req_addr
        cam_idx = 1 if req_addr.lower() == DEFAULT_SECONDARY_PANEL_GUID.lower() else 0
    elif req_addr in ("21", "1") or cam_idx == 1:
        cam_addr = DEFAULT_SECONDARY_PANEL_GUID
        cam_idx = 1
    else:
        cam_addr = DEFAULT_ENTRANCE_PANEL_GUID
        cam_idx = 0

    cam_name = "Placa Exterior Principal (PE0)" if cam_idx == 0 else "Cámara Interior (PE1)"

    # Desactivar auto-renovación continua para no monopolizar el canal comunitario
    sip.auto_renew = False

    # Si ya hay una sesión de vídeo activa:
    if sip.video_session_active:
        # Si la cámara solicitada es la que ya está activa y funcionando, mantener flujo sin cortes
        if cam_addr == getattr(sip, "video_camera_address", None) and camera_mgr.is_active:
            logger.info(f"Cámara {cam_name} ({cam_addr}) ya activa. Manteniendo flujo en directo.")
            return {
                "success": True,
                "message": f"{cam_name} ya activa",
                "active": True,
                "camera_index": cam_idx,
                "camera_address": cam_addr,
                "stream_url": "/api/camera/stream"
            }
        # Si se solicita una cámara diferente, conmutar mediante señalización limpia
        logger.info(f"Conmutando hacia cámara PE{cam_idx} ({cam_addr}) mediante cycle_camera_sip...")
        res = await sip.cycle_camera_sip(gw_id, direction="next", target_address=cam_addr)
        if res.get("success", False):
            new_idx = camera_mgr.cycle_camera(new_address=cam_addr)
            return {
                "success": True,
                "message": res.get("message", f"{cam_name} activa"),
                "active": True,
                "camera_index": new_idx,
                "camera_address": cam_addr,
                "stream_url": "/api/camera/stream",
                "details": res
            }
        else:
            return {
                "success": False,
                "message": res.get("message", f"Error conmutando a {cam_name}"),
                "active": camera_mgr.is_active,
                "camera_index": camera_mgr.camera_index,
                "camera_address": camera_mgr.camera_address,
                "details": res
            }

    res = await sip.start_video_session(
        gateway_id=gw_id,
        camera_address=cam_addr,
        on_frame=camera_mgr.ingest_frame
    )
    success = res.get("success", False)
    if success:
        camera_mgr.activate(gateway_id=gw_id, camera_index=cam_idx, camera_address=cam_addr)
        return {
            "success": True,
            "message": f"{cam_name} encendida con éxito",
            "active": True,
            "camera_index": cam_idx,
            "camera_address": cam_addr,
            "stream_url": "/api/camera/stream",
            "details": res
        }
    else:
        return {
            "success": False,
            "message": f"Error al iniciar sesión de vídeo en {cam_name}: {res.get('error', 'desconocido')}",
            "active": False,
            "camera_index": cam_idx,
            "camera_address": cam_addr,
            "details": res
        }

@app.post("/api/camera/push_frame")
async def push_camera_frame(request: Request):
    """Permite inyectar fotogramas JPEG reales procedentes de recepción RTP H.264 o bridge de video."""
    body = await request.body()
    if body and len(body) > 100:
        camera_mgr.ingest_frame(body)
        return {"success": True, "frame_size": len(body), "frame_counter": camera_mgr.frame_counter}
    raise HTTPException(status_code=400, detail="Fotograma inválido o vacío")

@app.post("/api/camera/deactivate")
async def deactivate_camera(req: Optional[CameraActionRequest] = None):
    """Desconecta la cámara exterior cerrando la sesión SIP y liberando el canal."""
    gw_id = (req.gateway_id if req and is_valid_uuid(req.gateway_id) else None) or DEFAULT_GATEWAY_ID
    sip.auto_renew = False
    if sip.call_state != "standby":
        sip.reject_or_hangup_call()
    await sip.stop_video_session(gateway_id=gw_id)
    camera_mgr.deactivate(gateway_id=gw_id)
    return {
        "success": True,
        "message": "Cámara exterior apagada y canal del edificio liberado (Standby)",
        "active": False
    }

@app.post("/api/camera/cycle")
async def cycle_camera(req: Optional[CameraActionRequest] = None):
    """Cambia de cámara o envía señalización CAMERASLIDING al videoportero según la app oficial."""
    gw_id = req.gateway_id if req and is_valid_uuid(req.gateway_id) else DEFAULT_GATEWAY_ID
    direction = req.direction if req else "next"

    # Comprobar topología de cámaras
    cams_count = len(AVAILABLE_CAMERAS)
    if eliot.auth_token:
        try:
            topo = await eliot.get_topology(DEFAULT_PLANT_ID, DEFAULT_GATEWAY_ID)
            disc_cams = topo.get("devices", {}).get("cameras", [])
            if disc_cams:
                cams_count = len(disc_cams)
        except Exception:
            pass

    if cams_count <= 1:
        logger.info("Instalación de cámara única: manteniendo Placa Exterior (PE0).")
        return {
            "success": True,
            "message": "Instalación de cámara única: Placa Exterior (PE0)",
            "camera_index": 0,
            "camera_address": DEFAULT_ENTRANCE_PANEL_GUID,
            "single_camera": True,
            "details": {"single_camera": True}
        }

    target_addr = req.address if req and req.address and is_valid_uuid(req.address) else None
    if not target_addr and req:
        if req.camera_index == 1 or req.address in ("21", "1"):
            target_addr = DEFAULT_SECONDARY_PANEL_GUID
        elif req.camera_index == 0 or req.address in ("20", "0"):
            target_addr = DEFAULT_ENTRANCE_PANEL_GUID

    if not target_addr:
        current_idx = camera_mgr.camera_index
        next_idx = (current_idx + 1) % cams_count if direction == "next" else (current_idx - 1) % cams_count
        target_addr = DEFAULT_SECONDARY_PANEL_GUID if next_idx == 1 else DEFAULT_ENTRANCE_PANEL_GUID

    res = await sip.cycle_camera_sip(gw_id, direction, target_address=target_addr)
    is_single = res.get("single_camera", False)
    act_cam = res.get("active_camera", target_addr or DEFAULT_ENTRANCE_PANEL_GUID)
    new_idx = camera_mgr.cycle_camera(direction=direction, new_address=act_cam, single_camera=is_single)
    cam_name = "Placa Exterior Principal (PE0)" if new_idx == 0 else "Cámara Interior (PE1)"

    return {
        "success": res.get("success", True),
        "message": res.get("message", f"Cambiado a {cam_name}"),
        "camera_index": new_idx,
        "camera_address": act_cam,
        "single_camera": is_single,
        "details": res
    }

@app.get("/api/camera/stream")
async def camera_stream(request: Request):
    """Streaming de video multipart/x-mixed-replace (MJPEG). Muestra Standby si no está activo."""
    client_ip = request.client.host if request.client else "127.0.0.1"
    user_agent = request.headers.get("user-agent", "")
    return StreamingResponse(
        camera_mgr.mjpeg_generator(request_ip=client_ip, user_agent=user_agent, fps=15),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

@app.get("/api/camera/telemetry")
async def camera_telemetry():
    """Telemetría en vivo del búfer de vídeo y clientes conectados agrupados por canal."""
    stats = sip.get_video_stats()
    return camera_mgr.get_channels_telemetry(sip_stats=stats)

@app.get("/api/channels/status")
async def channels_status():
    """Alias para telemetría de canales y estado del búfer."""
    stats = sip.get_video_stats()
    return camera_mgr.get_channels_telemetry(sip_stats=stats)

@app.get("/api/camera/snapshot")
async def camera_snapshot():
    """Captura instantánea de la cámara en formato JPEG con telemetría OSD."""
    snap_bytes = camera_mgr.get_snapshot_jpeg()
    return Response(
        content=snap_bytes,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Content-Disposition": f'inline; filename="snapshot_pe0_{int(time.time())}.jpg"'
        }
    )

@app.get("/api/camera/status")
async def camera_status():
    """Estado actual de la sesión de cámara y telemetría de recepción RTP."""
    stats = sip.get_video_stats()
    return {
        "active": camera_mgr.is_active,
        "camera_index": camera_mgr.camera_index,
        "local_ip": camera_mgr.local_ip,
        "gateway_id": camera_mgr.gateway_id,
        "activated_at": camera_mgr.activated_at,
        "frame_counter": camera_mgr.frame_counter,
        "video_receiver": stats
    }

@app.get("/api/status")
async def status():
    call_stat = sip.get_call_status()
    cert_info = get_certificate_info()
    return {
        "status": "online",
        "authenticated": True,
        "mode": "cloud" if bool(eliot.auth_token) else "autonomous",
        "username": eliot.username or os.environ.get("LEGRAND_USER", "Usuario Conectado"),
        "camera_active": camera_mgr.is_active,
        "camera_index": camera_mgr.camera_index,
        "sip_server": sip.sip_server,
        "sip_account": (
            sip.active_account.get("Username")
            or sip.active_account.get("username")
            or sip.active_account.get("sipId")
        ) if sip.active_account else DEFAULT_SIP_USER,
        "incoming_call": call_stat["incoming_call"],
        "call_state": call_stat["call_state"],
        "remaining_seconds": call_stat["remaining_seconds"],
        "caller": call_stat["caller"],
        "certificate": {
            "exists": cert_info["exists"],
            "valid": cert_info["valid"],
            "days_remaining": cert_info["days_remaining"],
            "subject": cert_info.get("subject"),
            "issuer": cert_info.get("issuer"),
            "not_valid_after": cert_info.get("not_valid_after")
        },
        "app": "Door Entry Web Client (Classe 100X)",
        "oauth_url": eliot.get_authorize_url()
    }

# Servir archivos estáticos del frontend
frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

    @app.get("/")
    async def serve_index():
        return FileResponse(frontend_dir / "index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8080, reload=False)
