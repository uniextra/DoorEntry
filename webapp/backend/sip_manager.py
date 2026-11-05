"""
Gestor de señalización SIP, envío de tramas OpenWebNet y gestión de sesiones de video
para videoporteros Legrand / BTicino Classe 100X.
Basado en ingeniería inversa de Linphone Core v5.3 y de la aplicación oficial com.legrandgroup.c100x.
Utiliza autenticación mTLS con certificados de cliente y autenticación Digest MD5 contra Flexisip.
"""
import os
import json
import base64
import httpx
import asyncio
import logging
import ssl
import uuid
import socket
import struct
import random
import hashlib
import re
import time
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Callable

from video_receiver import VideoReceiver, DEFAULT_RTP_PORT, DEFAULT_AUDIO_RTP_PORT

logger = logging.getLogger("sip_manager")

DEFAULT_SIP_SERVER = os.environ.get("SIP_SERVER", "vdesip.bs.iotleg.com")
DEFAULT_SIP_PORT = int(os.environ.get("SIP_PORT", 5061))
DEFAULT_SIP_USER = os.environ.get("SIP_USER", "")
DEFAULT_SIP_DOMAIN = os.environ.get("SIP_DOMAIN", "")
DEFAULT_SIP_PASSWORD = os.environ.get("SIP_PASSWORD", "")
DEFAULT_LOCAL_IP = os.environ.get("LOCAL_IP", "")

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent.parent / "data"))


class SipManager:
    def __init__(self, data_dir: Optional[Path] = None):
        self.sip_server = DEFAULT_SIP_SERVER
        self.sip_port = DEFAULT_SIP_PORT
        self.active_account: Optional[Dict[str, Any]] = None
        self.connected = False
        self.data_dir = data_dir or DATA_DIR
        self.client_cert_path = self.data_dir / "client.crt"
        self.client_key_path = self.data_dir / "client.key"
        self.ca_chain_path = self.data_dir / "ca_chain.crt"

        # Estado de sesión de video en tiempo real
        self.video_receiver: Optional[VideoReceiver] = None
        self.video_session_active = False
        self.video_call_id: Optional[str] = None
        self.video_target_uri: Optional[str] = None
        self.video_to_tag: Optional[str] = None
        self.video_from_tag: Optional[str] = None
        self.video_remote_contact: Optional[str] = None
        self.video_routes: list = []
        self.video_ss: Optional[ssl.SSLSocket] = None
        self.video_listen_thread: Optional[threading.Thread] = None
        self.video_gateway_id: Optional[str] = None
        self.video_camera_address: str = "a8276649-89c0-445c-8f7d-312c1f392fd1"
        self.video_crypto_key: Optional[str] = None
        self.cseq_counter = 1
        self.cseq_lock = threading.Lock()
        self._video_stopping = False
        self._bye_response_event = threading.Event()
        self.auto_renew = False
        self.on_session_ended: Optional[Callable[[], None]] = None
        self.last_video_stop_time = 0.0
        self.session_lock = threading.RLock()

        # Demonio SIP de escucha pasiva y llamadas entrantes (timbre)
        self.listener_running = False
        self.listener_thread: Optional[threading.Thread] = None
        self.listener_ss: Optional[ssl.SSLSocket] = None
        self.listener_lock = threading.Lock()
        self.incoming_call = False
        self.call_state = "standby"  # "standby" | "ringing" | "connected"
        self.call_start_time = 0.0
        self.call_context: Dict[str, Any] = {}
        self.on_incoming_call: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_call_ended: Optional[Callable[[], None]] = None

        # Sincronización para re-INVITE (camera cycle)
        self.reinvite_event = threading.Event()
        self.reinvite_response: Optional[str] = None
        self.pending_reinvite_cseq: Optional[int] = None

        # Demonio RTP Audio Keepalive (mantiene activos el Media Relay y el videoportero)
        self.audio_keepalive_thread: Optional[threading.Thread] = None
        self.audio_keepalive_stop_event = threading.Event()
        self.audio_keepalive_sock: Optional[socket.socket] = None

    @property
    def is_video_active(self) -> bool:
        return self.video_session_active

    def configure_account(self, sip_data: Dict[str, Any]):
        """Configura la cuenta SIP obtenida de la pasarela Eliot."""
        self.active_account = sip_data
        uname = sip_data.get("Username") or sip_data.get("username") or sip_data.get("sipId")
        logger.info(f"Cuenta SIP configurada: {uname} @ {self.sip_server}")

    def _next_cseq(self, step: int = 1) -> int:
        """Incrementa de forma atómica y thread-safe el contador CSeq."""
        with self.cseq_lock:
            val = self.cseq_counter
            self.cseq_counter += step
            return val

    def _get_credentials(self, gateway_id: str = ""):
        """Obtiene las credenciales activas o predeterminadas para SIP."""
        user = DEFAULT_SIP_USER
        domain = DEFAULT_SIP_DOMAIN
        password = DEFAULT_SIP_PASSWORD

        if self.active_account:
            acc_user = self.active_account.get("Username") or self.active_account.get("username")
            if acc_user and not acc_user.startswith("c100x_"):
                user = acc_user
            acc_srv = self.active_account.get("sipServer") or ""
            if acc_srv and "bs.iotleg.com" in acc_srv and not acc_srv.startswith("vdesip"):
                domain = acc_srv
            if self.active_account.get("password"):
                password = self.active_account.get("password")

        # SOLO actualizar domain si gateway_id es un UUID válido de 36 caracteres
        def is_valid_uuid(val: Any) -> bool:
            try:
                uuid.UUID(str(val))
                return True
            except Exception:
                return False

        if gateway_id and is_valid_uuid(gateway_id):
            domain = f"{gateway_id}.bs.iotleg.com"
        elif not domain or domain.startswith("vdesip"):
            domain = DEFAULT_SIP_DOMAIN

        return user, domain, password

    def _get_local_ip(self) -> str:
        """Obtiene la IP local para los mensajes y SDP."""
        if DEFAULT_LOCAL_IP:
            return DEFAULT_LOCAL_IP
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect((self.sip_server, self.sip_port))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _get_or_create_instance_id(self) -> str:
        """Obtiene o genera un UUID persistente para +sip.instance (RFC 5626 Outbound)."""
        inst_file = self.data_dir / "instance_id.txt"
        if inst_file.exists():
            try:
                val = inst_file.read_text(encoding='utf-8').strip()
                if val and val.startswith("urn:uuid:"):
                    return val
            except Exception:
                pass
        new_uuid = f"urn:uuid:{uuid.uuid4()}"
        try:
            inst_file.write_text(new_uuid, encoding='utf-8')
        except Exception as e:
            logger.debug(f"Aviso guardando instance_id: {e}")
        return new_uuid

    def get_ssl_context(self) -> ssl.SSLContext:
        """Crea un contexto TLS con mTLS (certificados de cliente Legrand)."""
        ca_file = str(self.ca_chain_path) if self.ca_chain_path.exists() else None
        ssl_ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=ca_file)
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        if self.client_cert_path.exists() and self.client_key_path.exists():
            try:
                ssl_ctx.load_cert_chain(
                    certfile=str(self.client_cert_path),
                    keyfile=str(self.client_key_path)
                )
                logger.debug("Certificado de cliente mTLS cargado correctamente.")
            except Exception as e:
                logger.warning(f"Error cargando certificado de cliente mTLS: {e}")
        else:
            alt_cert = Path(__file__).parent.parent.parent / "client.crt"
            alt_key = Path(__file__).parent.parent.parent / "client.key"
            if alt_cert.exists() and alt_key.exists():
                try:
                    ssl_ctx.load_cert_chain(certfile=str(alt_cert), keyfile=str(alt_key))
                except Exception as e:
                    logger.warning(f"Error cargando certificados alternativos: {e}")

        return ssl_ctx

    def _connect_tls_socket(self) -> Optional[ssl.SSLSocket]:
        """Abre un socket TLS mutuo (mTLS) al servidor SIP Flexisip."""
        try:
            ssl_ctx = self.get_ssl_context()
            s = socket.create_connection((self.sip_server, self.sip_port), timeout=8.0)
            ss = ssl_ctx.wrap_socket(s, server_hostname=self.sip_server)
            return ss
        except Exception as e:
            logger.error(f"Fallo abriendo conexión TLS SIP a {self.sip_server}:{self.sip_port}: {e}")
            return None

    def _calc_digest(self, method: str, target_uri: str, auth_hdr: str, user: str, domain: str, password: str) -> str:
        """Calcula cabecera Proxy-Authorization Digest MD5 conforme a RFC 2617."""
        realm_m = re.search(r'realm="([^"]+)"', auth_hdr)
        nonce_m = re.search(r'nonce="([^"]+)"', auth_hdr)
        realm = realm_m.group(1) if realm_m else domain
        nonce = nonce_m.group(1) if nonce_m else ""
        qop = 'auth' if 'qop=' in auth_hdr.lower() else None
        opaque_m = re.search(r'opaque="([^"]+)"', auth_hdr)
        opaque = opaque_m.group(1) if opaque_m else None

        ha1 = hashlib.md5(f"{user}:{realm}:{password}".encode('utf-8')).hexdigest()
        ha2 = hashlib.md5(f"{method}:{target_uri}".encode('utf-8')).hexdigest()
        nc = "00000001"
        cnonce = uuid.uuid4().hex[:8]

        if qop == 'auth':
            resp_digest = hashlib.md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}".encode('utf-8')).hexdigest()
            header = f'Proxy-Authorization: Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{target_uri}", response="{resp_digest}", algorithm=MD5, cnonce="{cnonce}", nc={nc}, qop={qop}'
        else:
            resp_digest = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode('utf-8')).hexdigest()
            header = f'Proxy-Authorization: Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{target_uri}", response="{resp_digest}", algorithm=MD5'

        if opaque:
            header += f', opaque="{opaque}"'
        return header

    def _register_sync(self, ss: ssl.SSLSocket, user: str, domain: str, local_ip: str) -> bool:
        """
        Envía REGISTER sip:{domain} para mantener presencia en el proxy Flexisip.
        Si Flexisip responde 401 Unauthorized o 407 Proxy Authentication Required,
        calcula la respuesta Digest MD5 y envía el segundo REGISTER para confirmar 200 OK.
        """
        try:
            branch = 'z9hG4bK' + uuid.uuid4().hex[:12]
            call_id = uuid.uuid4().hex
            tag = uuid.uuid4().hex[:8]
            cseq = self._next_cseq(2)
            instance_id = self._get_or_create_instance_id()

            msg = (
                f"REGISTER sip:{domain} SIP/2.0\r\n"
                f"Via: SIP/2.0/TLS {local_ip}:5061;branch={branch};rport\r\n"
                f"Max-Forwards: 70\r\n"
                f"From: <sip:{user}@{domain}>;tag={tag}\r\n"
                f"To: <sip:{user}@{domain}>\r\n"
                f"Call-ID: {call_id}\r\n"
                f"CSeq: {cseq} REGISTER\r\n"
                f'Contact: <sip:{user}@{local_ip}:5061;transport=tls>;+sip.instance="<{instance_id}>";reg-id=1\r\n'
                f"Supported: outbound, path\r\n"
                f"Expires: 600\r\n"
                f"User-Agent: LinphoneAndroid/4.5.3 (c100x)\r\n"
                f"Content-Length: 0\r\n\r\n"
            )
            ss.sendall(msg.encode('utf-8'))
            ss.settimeout(4.0)
            resp = ss.recv(4096).decode('utf-8', errors='ignore')

            if "200" in resp:
                logger.info("REGISTER SIP confirmado con 200 Registration successful.")
                return True

            if "401" in resp or "407" in resp:
                is_401 = "401" in resp
                auth_lines = [l for l in resp.splitlines() if 'authenticate' in l.lower()]
                if auth_lines:
                    _, _, password = self._get_credentials()
                    digest_val = self._calc_digest("REGISTER", f"sip:{domain}", auth_lines[0], user, domain, password)
                    if is_401 and digest_val.startswith("Proxy-Authorization:"):
                        digest_hdr = "Authorization:" + digest_val[len("Proxy-Authorization:"):]
                    else:
                        digest_hdr = digest_val

                    branch2 = 'z9hG4bK' + uuid.uuid4().hex[:12]
                    msg2 = (
                        f"REGISTER sip:{domain} SIP/2.0\r\n"
                        f"Via: SIP/2.0/TLS {local_ip}:5061;branch={branch2};rport\r\n"
                        f"Max-Forwards: 70\r\n"
                        f"From: <sip:{user}@{domain}>;tag={tag}\r\n"
                        f"To: <sip:{user}@{domain}>\r\n"
                        f"Call-ID: {call_id}\r\n"
                        f"CSeq: {cseq + 1} REGISTER\r\n"
                        f'Contact: <sip:{user}@{local_ip}:5061;transport=tls>;+sip.instance="<{instance_id}>";reg-id=1\r\n'
                        f"Supported: outbound, path\r\n"
                        f"{digest_hdr}\r\n"
                        f"Expires: 600\r\n"
                        f"User-Agent: LinphoneAndroid/4.5.3 (c100x)\r\n"
                        f"Content-Length: 0\r\n\r\n"
                    )
                    ss.sendall(msg2.encode('utf-8'))
                    ss.settimeout(4.0)
                    resp2 = ss.recv(4096).decode('utf-8', errors='ignore')
                    if "200" in resp2:
                        logger.info("REGISTER autenticado con Digest MD5 confirmado con 200 Registration successful.")
                        return True
                    else:
                        logger.warning(f"Respuesta a REGISTER autenticado: {resp2[:120]}")
                        return False
            return False
        except Exception as e:
            logger.debug(f"Aviso durante REGISTER SIP: {e}")
            return False

    def _send_single_message_sync(self, body: str, gateway_id: str = "") -> bool:
        """
        Envía un comando (OpenWebNet o JSON-RPC) como SIP MESSAGE
        completando la autenticación Digest MD5 cuando Flexisip responde con 407.
        """
        user, domain, password = self._get_credentials(gateway_id)
        local_ip = self._get_local_ip()
        target_uri = f"sip:c100x@{domain}"

        ss = self._connect_tls_socket()
        if not ss:
            return False

        try:
            call_id = uuid.uuid4().hex
            tag = uuid.uuid4().hex[:8]
            branch1 = 'z9hG4bK' + uuid.uuid4().hex[:12]
            cseq_num = self._next_cseq(2)

            # 1. Enviar MESSAGE inicial (sin auth)
            req1 = (
                f"MESSAGE {target_uri} SIP/2.0\r\n"
                f"Via: SIP/2.0/TLS {local_ip}:5061;branch={branch1};rport\r\n"
                f"Max-Forwards: 70\r\n"
                f"From: <sip:{user}@{domain}>;tag={tag}\r\n"
                f"To: <{target_uri}>\r\n"
                f"Call-ID: {call_id}\r\n"
                f"CSeq: {cseq_num} MESSAGE\r\n"
                f"User-Agent: LinphoneAndroid/4.5.3 (c100x)\r\n"
                f"Content-Type: text/plain\r\n"
                f"Content-Length: {len(body)}\r\n\r\n"
                f"{body}"
            )
            ss.sendall(req1.encode('utf-8'))

            # Leer respuesta hasta encontrar 407 o 200
            full_resp = ""
            start_t = time.time()
            ss.settimeout(4.0)
            while time.time() - start_t < 6.0:
                try:
                    chunk = ss.recv(4096).decode('utf-8', errors='ignore')
                    if not chunk:
                        break
                    full_resp += chunk
                    if "200 Ok" in full_resp:
                        logger.info(f"Comando SIP '{body}' aceptado directamente con 200 Ok.")
                        return True
                    if "407" in full_resp:
                        break
                except socket.timeout:
                    break

            if "407" not in full_resp:
                logger.warning(f"Respuesta inesperada al comando SIP inicial: {full_resp[:120]}")
                return False

            # 2. Extraer desafío Proxy-Authenticate y calcular Digest MD5
            auth_lines = [l for l in full_resp.splitlines() if 'proxy-authenticate' in l.lower()]
            if not auth_lines:
                return False

            proxy_auth = self._calc_digest("MESSAGE", target_uri, auth_lines[0], user, domain, password)
            branch2 = 'z9hG4bK' + uuid.uuid4().hex[:12]

            req2 = (
                f"MESSAGE {target_uri} SIP/2.0\r\n"
                f"Via: SIP/2.0/TLS {local_ip}:5061;branch={branch2};rport\r\n"
                f"Max-Forwards: 70\r\n"
                f"From: <sip:{user}@{domain}>;tag={tag}\r\n"
                f"To: <{target_uri}>\r\n"
                f"Call-ID: {call_id}\r\n"
                f"CSeq: {cseq_num + 1} MESSAGE\r\n"
                f"{proxy_auth}\r\n"
                f"User-Agent: LinphoneAndroid/4.5.3 (c100x)\r\n"
                f"Content-Type: text/plain\r\n"
                f"Content-Length: {len(body)}\r\n\r\n"
                f"{body}"
            )
            ss.sendall(req2.encode('utf-8'))

            resp2 = ""
            start_t2 = time.time()
            ss.settimeout(5.0)
            while time.time() - start_t2 < 6.0:
                try:
                    chunk2 = ss.recv(4096).decode('utf-8', errors='ignore')
                    if not chunk2:
                        break
                    resp2 += chunk2
                    if "200 Ok" in resp2:
                        logger.info(f"Comando SIP '{body}' autenticado exitosamente (200 Ok recibido).")
                        return True
                except socket.timeout:
                    break

            logger.warning(f"Comando SIP '{body}' enviado pero sin confirmación 200 Ok ({resp2[:100]})")
            return "200" in resp2
        except Exception as e:
            logger.error(f"Error transmitiendo comando SIP '{body}': {e}")
            return False
        finally:
            try:
                ss.close()
            except Exception as e:
                logger.debug(f"Aviso cerrando socket tras MESSAGE: {e}")

    async def send_command(self, frames: list[str], gateway_id: str = "", local_ip: str = "") -> Dict[str, Any]:
        """
        Envía comandos OpenWebNet o JSON-RPC a la pasarela Classe 100X
        utilizando SIP MESSAGE autenticado con Digest MD5.
        """
        results = []
        user, domain, _ = self._get_credentials(gateway_id)
        target = f"sip:c100x@{domain}"

        for idx, frame in enumerate(frames, start=1):
            logger.info(f"Enviando trama [{idx}/{len(frames)}]: {frame} -> {target}")
            sent_ok = await asyncio.to_thread(self._send_single_message_sync, frame, gateway_id)
            results.append({
                "frame": frame,
                "destination": target,
                "status": "sent" if sent_ok else "failed"
            })
            await asyncio.sleep(0.1)

        return {
            "success": all(r["status"] == "sent" for r in results) if results else True,
            "target": target,
            "frames": results,
            "gateway_id": gateway_id
        }

    # =========================================================================
    # GESTIÓN DEL FLUJO DE VIDEO EN TIEMPO REAL (CLASSE 100X)
    # =========================================================================

    DEFAULT_ENTRANCE_PANEL_GUID = "a8276649-89c0-445c-8f7d-312c1f392fd1"  # PE0 (Exterior)
    DEFAULT_SECONDARY_PANEL_GUID = "7c5a796f-7868-46a3-9fa6-091d73e25cad"  # PE1 (Interior)

    def release_scs_bus_sync(self, gateway_id: str = "") -> bool:
        """
        Libera de forma forzada los bloqueos del bus SCS 2 hilos en el videoportero Classe 100X:
        - *7*0*##: Clear active stream session (cancela cualquier sesión de streaming abierta en el bus).
        - *7*9**##: Release intercom video/audio bus locks (libera el candado de ocupado / 486 Busy).
        """
        gw_id = gateway_id or self.video_gateway_id or DEFAULT_GATEWAY_ID
        logger.info(f"Liberando bloqueos del bus SCS (*7*0*## y *7*9**##) para pasarela {gw_id}...")
        try:
            ok1 = self._send_single_message_sync("*7*0*##", gw_id)
            ok2 = self._send_single_message_sync("*7*9**##", gw_id)
            return ok1 and ok2
        except Exception as e:
            logger.debug(f"Aviso liberando bus SCS: {e}")
            return False

    async def release_scs_bus(self, gateway_id: str = "") -> bool:
        return await asyncio.to_thread(self.release_scs_bus_sync, gateway_id)

    def _cleanup_video_session(self):
        """Limpia los recursos de vídeo y sockets de forma segura."""
        self._stop_audio_keepalive()
        if self.video_receiver:
            try:
                self.video_receiver.stop()
            except Exception as e:
                logger.debug(f"Aviso deteniendo video_receiver en cleanup: {e}")
        if self.video_ss:
            try:
                self.video_ss.close()
            except Exception as e:
                logger.debug(f"Aviso cerrando video_ss en cleanup: {e}")
            self.video_ss = None
        self.video_session_active = False

    def _start_video_session_sync(self, gateway_id: str, camera_address: str, local_ip: str, on_frame: Optional[Callable[[bytes], None]] = None) -> Dict[str, Any]:
        """
        Secuencia completa síncrona para iniciar la sesión de vídeo con reintentos automáticos
        si el intercomunicador Classe 100X está liberando el bus SCS (486 Busy here).
        Libera activamente el bus con *7*0*## y *7*9**## si detecta bloqueo.
        """
        with self.session_lock:
            # Si la sesión de vídeo ya está activa con receptor corriendo, responder de inmediato
            if self.video_session_active and self.video_receiver and self.video_receiver.running:
                logger.info("Sesión de vídeo ya activa. Manteniendo flujo en directo.")
                return {
                    "success": True,
                    "call_id": self.video_call_id,
                    "target": self.video_target_uri,
                    "status": "connected",
                    "camera_address": self.video_camera_address,
                    "rtp_port": DEFAULT_RTP_PORT
                }

            self._cleanup_video_session()

            max_retries = 3
            for attempt in range(max_retries):
                res = self._do_start_video_session(gateway_id, camera_address, local_ip, on_frame)
                if res.get("success"):
                    return res
                if res.get("error") == "Intercom busy (486)":
                    logger.warning(f"Intercomunicador ocupado (486 Busy here). Liberando bus SCS con *7*0*## y *7*9**## (intento {attempt + 1}/{max_retries})...")
                    self.release_scs_bus_sync(gateway_id)
                    if attempt < max_retries - 1:
                        time.sleep(3.0)
                        continue
                self._cleanup_video_session()
                return res

            self._cleanup_video_session()
            return {"success": False, "error": "Max retries exceeded"}

    def _do_start_video_session(self, gateway_id: str, camera_address: str, local_ip: str, on_frame: Optional[Callable[[bytes], None]] = None) -> Dict[str, Any]:
        user, domain, password = self._get_credentials(gateway_id)
        target_uri = f"sip:c100x@{domain}"

        # Respetar tiempo de enfriamiento físico del bus SCS (8.5s tras fin de llamada)
        last_stop = getattr(self, "last_video_stop_time", 0.0)
        cooldown = 8.5 - (time.time() - last_stop)
        if cooldown > 0:
            logger.info(f"Esperando estabilización de relés del bus SCS 2 hilos ({cooldown:.1f}s)...")
            time.sleep(cooldown)

        # Mapeo de dirección lógica a GUID de módulo validado para Classe 100X
        def is_valid_uuid(val: Any) -> bool:
            try:
                uuid.UUID(str(val))
                return True
            except Exception:
                return False

        cam_str = str(camera_address).strip() if camera_address else ""
        if not cam_str:
            devaddr = self.DEFAULT_ENTRANCE_PANEL_GUID
        elif cam_str in ["21", "1", self.DEFAULT_SECONDARY_PANEL_GUID]:
            devaddr = self.DEFAULT_SECONDARY_PANEL_GUID
        elif cam_str in ["20", "0", self.DEFAULT_ENTRANCE_PANEL_GUID]:
            devaddr = self.DEFAULT_ENTRANCE_PANEL_GUID
        elif is_valid_uuid(cam_str):
            devaddr = cam_str
        else:
            devaddr = self.DEFAULT_ENTRANCE_PANEL_GUID

        # 1. Conectar socket TLS para el diálogo de llamada SIP
        ss = self._connect_tls_socket()
        if not ss:
            return {"success": False, "error": "No se pudo conectar TLS al proxy SIP"}

        # Clave SRTP aleatoria de 30 bytes requerida por belle-sip
        crypto_key = base64.b64encode(os.urandom(30)).decode('ascii')

        # SDP idéntico al que Linphone Android / Classe 100X genera para auto-encendido
        sdp = (
            "v=0\r\n"
            f"o={user} 123456 123456 IN IP4 {local_ip}\r\n"
            "s=Talk\r\n"
            f"c=IN IP4 {local_ip}\r\n"
            "t=0 0\r\n"
            f"a=DEVADDR:{devaddr}\r\n"
            f"m=audio {DEFAULT_AUDIO_RTP_PORT} RTP/SAVP 8 0\r\n"
            "a=rtpmap:8 PCMA/8000\r\n"
            "a=rtpmap:0 PCMU/8000\r\n"
            f"a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:{crypto_key}\r\n"
            "a=sendrecv\r\n"
            f"m=video {DEFAULT_RTP_PORT} RTP/SAVP 96\r\n"
            "a=rtpmap:96 H264/90000\r\n"
            "a=fmtp:96 profile-level-id=42801F;packetization-mode=1\r\n"
            f"a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:{crypto_key}\r\n"
            "a=recvonly\r\n"
        )

        inv_call_id = uuid.uuid4().hex
        inv_tag = uuid.uuid4().hex[:8]
        inv_branch = 'z9hG4bK' + uuid.uuid4().hex[:12]
        cseq_inv = self._next_cseq(2)

        # 2. Enviar primer INVITE (sin auth)
        inv_msg1 = (
            f"INVITE {target_uri} SIP/2.0\r\n"
            f"Via: SIP/2.0/TLS {local_ip}:5061;branch={inv_branch};rport\r\n"
            f"Max-Forwards: 70\r\n"
            f"From: <sip:{user}@{domain}>;tag={inv_tag}\r\n"
            f"To: <{target_uri}>\r\n"
            f"Call-ID: {inv_call_id}\r\n"
            f"CSeq: {cseq_inv} INVITE\r\n"
            f"Contact: <sip:{user}@{local_ip}:5061;transport=tls>\r\n"
            f"User-Agent: LinphoneAndroid/4.5.3 (c100x)\r\n"
            f"Content-Type: application/sdp\r\n"
            f"Content-Length: {len(sdp)}\r\n\r\n"
            f"{sdp}"
        )
        ss.sendall(inv_msg1.encode('utf-8'))

        # Leer respuesta 407
        full_resp = ""
        ss.settimeout(5.0)
        start_t = time.time()
        while time.time() - start_t < 6.0:
            try:
                chunk = ss.recv(4096).decode('utf-8', errors='ignore')
                if not chunk:
                    break
                full_resp += chunk
                if "407" in full_resp:
                    break
            except socket.timeout:
                break

        if "407" not in full_resp:
            logger.warning(f"Desafío 407 no recibido para INVITE: {full_resp[:120]}")
            ss.close()
            return {"success": False, "error": "No challenge received"}

        # 3. Calcular Digest MD5 y enviar INVITE autenticado
        auth_lines = [l for l in full_resp.splitlines() if 'proxy-authenticate' in l.lower()]
        if not auth_lines:
            logger.warning(f"Cabecera Proxy-Authenticate no encontrada en 407: {full_resp[:120]}")
            ss.close()
            return {"success": False, "error": "No Proxy-Authenticate header in 407"}
        proxy_auth = self._calc_digest("INVITE", target_uri, auth_lines[0], user, domain, password)
        inv_branch2 = 'z9hG4bK' + uuid.uuid4().hex[:12]

        inv_msg2 = (
            f"INVITE {target_uri} SIP/2.0\r\n"
            f"Via: SIP/2.0/TLS {local_ip}:5061;branch={inv_branch2};rport\r\n"
            f"Max-Forwards: 70\r\n"
            f"From: <sip:{user}@{domain}>;tag={inv_tag}\r\n"
            f"To: <{target_uri}>\r\n"
            f"Call-ID: {inv_call_id}\r\n"
            f"CSeq: {cseq_inv + 1} INVITE\r\n"
            f"Contact: <sip:{user}@{local_ip}:5061;transport=tls>\r\n"
            f"{proxy_auth}\r\n"
            f"User-Agent: LinphoneAndroid/4.5.3 (c100x)\r\n"
            f"Content-Type: application/sdp\r\n"
            f"Content-Length: {len(sdp)}\r\n\r\n"
            f"{sdp}"
        )
        ss.sendall(inv_msg2.encode('utf-8'))
        logger.info(f"INVITE autenticado enviado a {target_uri} con DEVADDR:{devaddr}")

        # 4. Esperar confirmación (180 Ringing y luego 200 OK)
        to_tag = None
        routes = []
        remote_contact = None
        full_200 = ""
        got_200 = False
        remote_ip = None
        remote_port = None
        remote_audio_port = None
        remote_crypto = None

        ss.settimeout(2.0)
        start_t2 = time.time()

        while time.time() - start_t2 < 15.0:
            try:
                chunk = ss.recv(4096).decode('utf-8', errors='ignore')
                if not chunk:
                    break

                if "200 ok" in chunk.lower() or got_200:
                    got_200 = True
                    full_200 += chunk

                    to_m = re.search(r'To:.*tag=([^\r\n;]+)', full_200)
                    if to_m and not to_tag:
                        to_tag = to_m.group(1)

                    r_routes = [l.split(":", 1)[1].strip() for l in full_200.splitlines() if l.lower().startswith("record-route:")]
                    if r_routes:
                        routes = r_routes

                    contact_m = re.search(r'Contact:\s*<([^>]+)>', full_200, re.IGNORECASE)
                    if not contact_m:
                        contact_m = re.search(r'Contact:\s*([^\r\n;]+)', full_200, re.IGNORECASE)
                    if contact_m:
                        remote_contact = contact_m.group(1).strip()
                        logger.info(f"Contact de la llamada 200 OK: {remote_contact}")

                    if "m=video" in full_200 and "a=crypto" in full_200:
                        c_m = re.search(r'c=IN IP4 ([0-9.]+)', full_200)
                        m_m = re.search(r'm=video (\d+)', full_200)
                        m_audio_m = re.search(r'm=audio (\d+)', full_200)
                        cr_m = re.findall(r'a=crypto:\d+ \S+ inline:([A-Za-z0-9+/=]+)', full_200)

                        if c_m: remote_ip = c_m.group(1)
                        if m_m: remote_port = int(m_m.group(1))
                        if m_audio_m: remote_audio_port = int(m_audio_m.group(1))
                        if cr_m: remote_crypto = cr_m[-1]

                        logger.info(f"200 OK recibido: Media Relay {remote_ip}:{remote_port}, Audio Relay Port {remote_audio_port}")
                        logger.info(f"200 OK SDP completo:\n{full_200}")

                        # Enviar ACK con Route headers invertidos
                        routes_rev = list(reversed(routes))
                        route_hdrs = "".join(f"Route: {r}\r\n" for r in routes_rev)
                        ack_branch = 'z9hG4bK' + uuid.uuid4().hex[:12]
                        to_hdr = f"<{target_uri}>;tag={to_tag}" if to_tag else f"<{target_uri}>"
                        ack_req_uri = remote_contact or target_uri
                        ack_msg = (
                            f"ACK {ack_req_uri} SIP/2.0\r\n"
                            f"Via: SIP/2.0/TLS {local_ip}:5061;branch={ack_branch};rport\r\n"
                            f"Max-Forwards: 70\r\n"
                            f"{route_hdrs}"
                            f"From: <sip:{user}@{domain}>;tag={inv_tag}\r\n"
                            f"To: {to_hdr}\r\n"
                            f"Call-ID: {inv_call_id}\r\n"
                            f"CSeq: {cseq_inv + 1} ACK\r\n"
                            f"User-Agent: LinphoneAndroid/4.5.3 (c100x)\r\n"
                            f"Content-Length: 0\r\n\r\n"
                        )
                        ss.sendall(ack_msg.encode('utf-8'))
                        logger.info(f"ACK enviado para 200 OK a {ack_req_uri}. Diálogo SIP establecido.")
                        break

                elif "486" in chunk or "4xx" in chunk:
                    to_m = re.search(r'To:.*tag=([^\r\n;]+)', chunk)
                    to_t = to_m.group(1) if to_m else None
                    to_hdr = f"<{target_uri}>;tag={to_t}" if to_t else f"<{target_uri}>"
                    ack_msg = (
                        f"ACK {target_uri} SIP/2.0\r\n"
                        f"Via: SIP/2.0/TLS {local_ip}:5061;branch={inv_branch2};rport\r\n"
                        f"Max-Forwards: 70\r\n"
                        f"From: <sip:{user}@{domain}>;tag={inv_tag}\r\n"
                        f"To: {to_hdr}\r\n"
                        f"Call-ID: {inv_call_id}\r\n"
                        f"CSeq: {cseq_inv + 1} ACK\r\n"
                        f"User-Agent: LinphoneAndroid/4.5.3 (c100x)\r\n"
                        f"Content-Length: 0\r\n\r\n"
                    )
                    ss.sendall(ack_msg.encode('utf-8'))
                    logger.warning(f"Respuesta de ocupado recibida: {chunk[:100]}")
                    ss.close()
                    return {"success": False, "error": "Intercom busy (486)"}

            except socket.timeout:
                continue

        if not got_200 or not remote_ip or not remote_port:
            logger.warning("No se recibió 200 OK con SDP válido de vídeo.")
            ss.close()
            return {"success": False, "error": "No 200 OK received with video SDP"}

        # 5. Iniciar receptor de vídeo y decodificador FFmpeg
        if not self.video_receiver:
            self.video_receiver = VideoReceiver(port=DEFAULT_RTP_PORT, on_frame=on_frame)
        else:
            self.video_receiver.on_frame = on_frame

        self.video_receiver.start(remote_ip=remote_ip, remote_port=remote_port, crypto_key=remote_crypto or crypto_key)

        # 6. Iniciar demonio RTP Audio Keepalive hacia el Media Relay
        if remote_audio_port and remote_ip:
            self._start_audio_keepalive(remote_ip, remote_audio_port)

        # 7. Guardar estado de la sesión activa
        self.video_call_id = inv_call_id
        self.video_target_uri = target_uri
        self.video_remote_contact = remote_contact or target_uri
        self.video_to_tag = to_tag
        self.video_from_tag = inv_tag
        self.video_routes = routes
        self.video_ss = ss
        self.video_crypto_key = remote_crypto or crypto_key
        self.video_session_active = True
        self.video_gateway_id = gateway_id
        self.video_camera_address = devaddr

        # Lanzar hilo en segundo plano para escuchar eventos en el socket SIP
        self.video_listen_thread = threading.Thread(target=self._video_sip_listener, args=(ss, inv_call_id, target_uri, inv_tag, to_tag), daemon=True)
        self.video_listen_thread.start()

        logger.info(f"Sesión de vídeo activa para cámara {devaddr} en pasarela {gateway_id}")
        return {
            "success": True,
            "call_id": inv_call_id,
            "target": target_uri,
            "status": "connected",
            "camera_address": devaddr,
            "rtp_port": DEFAULT_RTP_PORT,
            "remote_relay": f"{remote_ip}:{remote_port}",
            "remote_audio_port": remote_audio_port
        }

    def _video_sip_listener(self, ss: ssl.SSLSocket, call_id: str, target_uri: str, from_tag: str, to_tag: Optional[str]):
        """Mantiene el socket TLS activo y procesa mensajes entrantes (como BYE o respuestas a re-INVITE)."""
        user, domain, _ = self._get_credentials(self.video_gateway_id or "")
        while self.video_session_active and not getattr(self, "_video_stopping", False) and self.video_ss == ss:
            try:
                ss.settimeout(0.5)
                data = ss.recv(4096).decode('utf-8', errors='ignore')
                if not data:
                    break

                # Si ya estamos deteniendo la sesión, ignorar y salir
                if getattr(self, "_video_stopping", False):
                    break

                # Respuesta 200 OK o 481 a nuestro BYE saliente
                if ("200" in data or "481" in data) and "bye" in data.lower():
                    logger.info("Confirmación 200 OK recibida para BYE SIP saliente.")
                    if hasattr(self, "_bye_response_event"):
                        self._bye_response_event.set()
                    break

                # 1. Petición BYE entrante enviada por el videoportero (no una respuesta SIP/2.0)
                if (data.startswith("BYE ") or "\r\nBYE " in data) and not data.startswith("SIP/2.0"):
                    logger.info("BYE recibido del videoportero (cierre de llamada o fin de temporizador SCS bus).")
                    cseq_m = re.search(r'CSeq:\s*(\d+)\s+BYE', data)
                    cseq_num = cseq_m.group(1) if cseq_m else "1"
                    via_m = re.search(r'Via:[^\r\n]+', data)
                    via_line = via_m.group(0) if via_m else ""
                    resp = (
                        "SIP/2.0 200 OK\r\n"
                        f"{via_line}\r\n"
                        f"From: <{target_uri}>;tag={to_tag or ''}\r\n"
                        f"To: <sip:{user}@{domain}>;tag={from_tag}\r\n"
                        f"Call-ID: {call_id}\r\n"
                        f"CSeq: {cseq_num} BYE\r\n"
                        "Content-Length: 0\r\n\r\n"
                    )
                    try:
                        ss.sendall(resp.encode('utf-8'))
                    except Exception as e:
                        logger.debug(f"Aviso respondiendo 200 OK a BYE: {e}")

                    with self.session_lock:
                        self.video_session_active = False
                        self.last_video_stop_time = time.time()
                        self._stop_audio_keepalive()
                        if self.video_receiver:
                            try:
                                self.video_receiver.stop()
                            except Exception as e:
                                logger.debug(f"Aviso deteniendo video_receiver tras BYE: {e}")
                        try:
                            ss.close()
                        except Exception as e:
                            logger.debug(f"Aviso cerrando socket tras BYE: {e}")
                        if self.video_ss == ss:
                            self.video_ss = None
                        self.release_scs_bus_sync(self.video_gateway_id)

                    # Notificar para auto-renovación si aplica
                    if self.auto_renew and self.on_session_ended:
                        try:
                            self.on_session_ended()
                        except Exception as e:
                            logger.error(f"Error en on_session_ended: {e}")
                    break

                # 2. Comprobar si es respuesta a re-INVITE (camera cycle o refresh)
                if self.pending_reinvite_cseq and f"{self.pending_reinvite_cseq} INVITE" in data:
                    status_codes = [int(c) for c in re.findall(r'SIP/2\.0\s+(\d{3})', data)]
                    if status_codes and not any(c >= 200 for c in status_codes):
                        logger.debug(f"Respuesta provisional al re-INVITE ignorada: {status_codes}")
                        continue
                    self.reinvite_response = data
                    self.reinvite_event.set()

            except socket.timeout:
                continue
            except Exception as e:
                logger.debug(f"Escucha de socket SIP de vídeo finalizada: {e}")
                break

    def _cycle_camera_in_dialog_sync(self, gateway_id: str = "", direction: str = "next", target_address: Optional[str] = None) -> Dict[str, Any]:
        """
        Cicla de cámara en el videoportero Classe 100X:
        1. Determina la cámara objetivo (PE0 Exterior o PE1 Interior).
        2. Si ya está en esa cámara, no hace nada.
        3. En Legrand cloud / Classe 100X, la conmutación de cámara requiere cerrar el diálogo
           actual (BYE), liberar el bus (*7*0*## y *7*9**##), y emitir un nuevo INVITE con DEVADDR.
        """
        gw_id = gateway_id or self.video_gateway_id or DEFAULT_GATEWAY_ID
        local_ip = self._get_local_ip()

        # Determinar cámara objetivo
        if target_address:
            cam_str = str(target_address).strip()
            if cam_str in ["21", "1", self.DEFAULT_SECONDARY_PANEL_GUID]:
                target_camera_addr = self.DEFAULT_SECONDARY_PANEL_GUID
            else:
                target_camera_addr = self.DEFAULT_ENTRANCE_PANEL_GUID
        elif self.video_camera_address == self.DEFAULT_SECONDARY_PANEL_GUID:
            target_camera_addr = self.DEFAULT_ENTRANCE_PANEL_GUID
        else:
            target_camera_addr = self.DEFAULT_SECONDARY_PANEL_GUID

        target_name = "Cámara Interior (PE1)" if target_camera_addr == self.DEFAULT_SECONDARY_PANEL_GUID else "Placa Exterior (PE0)"

        if not (self.video_session_active and self.video_ss and self.video_call_id):
            return {
                "success": True,
                "single_camera": False,
                "active_camera": target_camera_addr,
                "message": f"Seleccionada {target_name}"
            }

        if target_camera_addr == self.video_camera_address:
            logger.info(f"Cámara {target_name} ya activa en el diálogo actual.")
            return {
                "success": True,
                "single_camera": False,
                "active_camera": target_camera_addr,
                "message": f"{target_name} ya activa"
            }

        logger.info(f"Conmutando limpiamente de {self.video_camera_address} a {target_camera_addr} ({target_name})...")
        on_frame = self.video_receiver.on_frame if self.video_receiver else None

        # 1. Parar diálogo actual y liberar bus SCS
        self._stop_video_session_sync(gw_id)
        time.sleep(0.4)

        # 2. Iniciar nueva sesión con el DEVADDR objetivo
        res = self._start_video_session_sync(gw_id, target_camera_addr, local_ip, on_frame)
        if res.get("success"):
            return {
                "success": True,
                "single_camera": False,
                "active_camera": target_camera_addr,
                "message": f"Cambiado a {target_name}",
                "details": res
            }
        else:
            logger.warning(f"No se pudo iniciar {target_name}: {res.get('error')}. Liberando bus SCS...")
            self.release_scs_bus_sync(gw_id)
            return {
                "success": False,
                "single_camera": False,
                "active_camera": self.video_camera_address,
                "message": f"Error cambiando a {target_name}: {res.get('error', 'desconocido')}",
                "details": res
            }

    async def cycle_camera_sip(self, gateway_id: str = "", direction: str = "next", target_address: Optional[str] = None) -> Dict[str, Any]:
        """
        Cicla de cámara en el videoportero Classe 100X:
        Conmuta entre la Placa Exterior (PE0) y la Cámara Interior (PE1).
        """
        if self.video_session_active and self.video_ss and self.video_call_id:
            return await asyncio.to_thread(self._cycle_camera_in_dialog_sync, gateway_id, direction, target_address)
        else:
            gw_id = gateway_id or self.video_gateway_id or DEFAULT_GATEWAY_ID
            # Alternar objetivo en espera
            if target_address:
                cam_str = str(target_address).strip()
                target_camera_addr = self.DEFAULT_SECONDARY_PANEL_GUID if cam_str in ["21", "1", self.DEFAULT_SECONDARY_PANEL_GUID] else self.DEFAULT_ENTRANCE_PANEL_GUID
            elif self.video_camera_address == self.DEFAULT_SECONDARY_PANEL_GUID:
                target_camera_addr = self.DEFAULT_ENTRANCE_PANEL_GUID
            else:
                target_camera_addr = self.DEFAULT_SECONDARY_PANEL_GUID

            self.video_camera_address = target_camera_addr
            target_name = "Cámara Interior (PE1)" if target_camera_addr == self.DEFAULT_SECONDARY_PANEL_GUID else "Placa Exterior (PE0)"

            frames = self.build_openwebnet_camera_cycle(direction)
            res = await self.send_command(frames, gateway_id=gw_id)
            return {
                "success": True,
                "single_camera": False,
                "active_camera": target_camera_addr,
                "message": f"Seleccionada {target_name}",
                "details": res
            }

    def _stop_video_session_sync(self, gateway_id: str = "") -> bool:
        """Detiene la sesión de video de forma limpia: envía BYE y para el receptor."""
        with self.session_lock:
            gw_id = gateway_id or self.video_gateway_id or ""
            self.auto_renew = False

            sock_to_close = self.video_ss
            listener = self.video_listen_thread

            # 1. Enviar BYE si hay diálogo activo
            if sock_to_close and self.video_call_id:
                try:
                    bye_branch = 'z9hG4bK' + uuid.uuid4().hex[:12]
                    user, domain, _ = self._get_credentials(gw_id)
                    to_hdr = f"<{self.video_target_uri}>;tag={self.video_to_tag}" if self.video_to_tag else f"<{self.video_target_uri}>"
                    routes_rev = list(reversed(getattr(self, "video_routes", [])))
                    route_hdrs = "".join(f"Route: {r}\r\n" for r in routes_rev)
                    req_uri = getattr(self, "video_remote_contact", None) or self.video_target_uri
                    bye_msg = (
                        f"BYE {req_uri} SIP/2.0\r\n"
                        f"Via: SIP/2.0/TLS {self._get_local_ip()}:5061;branch={bye_branch};rport\r\n"
                        f"Max-Forwards: 70\r\n"
                        f"{route_hdrs}"
                        f"From: <sip:{user}@{domain}>;tag={self.video_from_tag}\r\n"
                        f"To: {to_hdr}\r\n"
                        f"Call-ID: {self.video_call_id}\r\n"
                        f"CSeq: 99 BYE\r\n"
                        f"User-Agent: LinphoneAndroid/4.5.3 (c100x)\r\n"
                        f"Content-Length: 0\r\n\r\n"
                    )
                    self._bye_response_event.clear()
                    sock_to_close.sendall(bye_msg.encode('utf-8'))
                    logger.info(f"BYE enviado a {req_uri} para Call-ID {self.video_call_id}")

                    # Esperar hasta 1.5s a que el servidor confirme el BYE
                    if listener and listener.is_alive() and threading.current_thread() != listener:
                        if self._bye_response_event.wait(timeout=1.5):
                            logger.info("BYE confirmado con 200 OK por el servidor.")
                        else:
                            logger.debug("Timeout esperando 200 OK de BYE.")
                    else:
                        try:
                            sock_to_close.settimeout(1.5)
                            resp = sock_to_close.recv(4096).decode('utf-8', errors='ignore')
                            if ("200" in resp or "481" in resp) and "bye" in resp.lower():
                                logger.info("Confirmación 200 OK recibida directamente para BYE.")
                        except Exception:
                            pass
                except Exception as e:
                    logger.debug(f"Error enviando BYE SIP: {e}")

            # 2. Señalizar detención al listener y esperar a que termine
            self._video_stopping = True
            self.video_session_active = False

            if listener and listener.is_alive() and threading.current_thread() != listener:
                listener.join(timeout=1.0)
            self.video_listen_thread = None

            # 3. Cerrar el socket SSL solo una vez el hilo listener haya finalizado
            if sock_to_close:
                try:
                    sock_to_close.close()
                except Exception as e:
                    logger.debug(f"Aviso cerrando video_ss: {e}")
            self.video_ss = None

            # 4. Detener receptor UDP y decodificador
            if self.video_receiver:
                try:
                    self.video_receiver.stop()
                except Exception as e:
                    logger.debug(f"Aviso deteniendo video_receiver: {e}")

            # 5. Detener demonio RTP Audio Keepalive
            self._stop_audio_keepalive()

            # 6. Liberar el bus SCS para garantizar desconexión inmediata de los relés físicos
            try:
                self.release_scs_bus_sync(gw_id)
            except Exception as e:
                logger.debug(f"Aviso liberando bus SCS en stop: {e}")

            self.video_call_id = None
            self.video_target_uri = None
            self.video_remote_contact = None
            self.video_to_tag = None
            self.video_from_tag = None
            self._video_stopping = False
            self.last_video_stop_time = time.time()

            logger.info("Sesión de vídeo detenida limpiamente.")
            return True

    async def start_video_session(self, gateway_id: str = "", camera_address: str = "20", on_frame: Optional[Callable[[bytes], None]] = None) -> Dict[str, Any]:
        """Arranca de forma asíncrona la sesión completa de vídeo y recepción RTP."""
        local_ip = self._get_local_ip()
        return await asyncio.to_thread(self._start_video_session_sync, gateway_id, camera_address, local_ip, on_frame)

    async def stop_video_session(self, gateway_id: str = "") -> bool:
        """Detiene de forma asíncrona la sesión de vídeo."""
        return await asyncio.to_thread(self._stop_video_session_sync, gateway_id)

    def get_video_stats(self) -> Dict[str, Any]:
        """Devuelve telemetría y contadores del receptor RTP de vídeo y búfer MJPEG."""
        if self.video_receiver:
            stats = self.video_receiver.get_stats()
            stats["active"] = self.video_session_active
            stats["receiver_running"] = self.video_receiver.running
            stats["rtp_port"] = self.video_receiver.port
            stats["camera_address"] = self.video_camera_address
            return stats
        return {
            "active": self.video_session_active,
            "receiver_running": False,
            "packets_received": 0,
            "packets_forwarded": 0,
            "bytes_received": 0,
            "frames_decoded": 0,
            "rtp_port": DEFAULT_RTP_PORT,
            "camera_address": self.video_camera_address,
            "buffer_current_bytes": 0,
            "buffer_max_bytes": 5 * 1024 * 1024,
            "buffer_pct": 0.0,
            "latest_frame_size": 0,
            "latest_frame_age_ms": 0,
            "fps_estimate": 0.0
        }

    # =========================================================================
    # DEMONIO RTP AUDIO KEEPALIVE (BTicino / Legrand Media Relay Anti-Timeout)
    # =========================================================================

    def _start_audio_keepalive(self, remote_ip: str, remote_port: int):
        """
        Lanza un demonio en segundo plano que envía paquetes RTP de audio (keepalive)
        hacia (remote_ip, remote_port) cada 5.0 segundos para evitar que el Media Relay
        o el videoportero Legrand/BTicino corte la llamada o vista en directo por inactividad.
        """
        self._stop_audio_keepalive()

        self.audio_keepalive_stop_event = threading.Event()
        self.audio_keepalive_thread = threading.Thread(
            target=self._audio_keepalive_loop,
            args=(remote_ip, remote_port, self.audio_keepalive_stop_event),
            daemon=True,
            name="rtp_audio_keepalive"
        )
        self.audio_keepalive_thread.start()
        logger.info(f"RTP Audio Keepalive iniciado hacia {remote_ip}:{remote_port} (intervalo 5s)")

    def _audio_keepalive_loop(self, remote_ip: str, remote_port: int, stop_event: threading.Event):
        """
        Envía paquetes RTP de audio dummy cada 5.0 segundos:
        Cabecera RTP estándar RFC 3550 (12 bytes: V=2, P=0, X=0, CC=0, M=0, PT=8 PCMA)
        + 1 byte de carga útil (silencio PCMA 0xD5).
        Mantiene abiertas las tablas NAT y satisface el watchdog de medios del servidor Legrand Eliot.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('0.0.0.0', DEFAULT_AUDIO_RTP_PORT))
        except Exception:
            pass  # Si el puerto 7076 está ocupado, usar efímero

        self.audio_keepalive_sock = sock
        seq = random.randint(1000, 50000)
        ts = random.randint(10000, 500000)
        ssrc = random.randint(0x10000000, 0xEFFFFFFF)

        # Ráfaga inicial de hole-punching UDP
        for _ in range(3):
            try:
                header = struct.pack("!BBHII", 0x80, 8, seq & 0xFFFF, ts & 0xFFFFFFFF, ssrc & 0xFFFFFFFF)
                sock.sendto(header + b"\xd5", (remote_ip, remote_port))
                seq = (seq + 1) & 0xFFFF
                ts = (ts + 160) & 0xFFFFFFFF
            except Exception:
                pass
            time.sleep(0.05)

        logger.info(f"RTP Audio Keepalive hole-punching inicial enviado a {remote_ip}:{remote_port}")

        while not stop_event.is_set():
            if stop_event.wait(timeout=5.0):
                break
            try:
                header = struct.pack("!BBHII", 0x80, 8, seq & 0xFFFF, ts & 0xFFFFFFFF, ssrc & 0xFFFFFFFF)
                sock.sendto(header + b"\xd5", (remote_ip, remote_port))
                seq = (seq + 1) & 0xFFFF
                ts = (ts + 160) & 0xFFFFFFFF
                logger.debug(f"RTP Audio Keepalive enviado (seq={seq}) a {remote_ip}:{remote_port}")
            except Exception as e:
                logger.debug(f"Aviso en RTP Audio Keepalive: {e}")

        try:
            sock.close()
        except Exception:
            pass
        self.audio_keepalive_sock = None
        logger.info("RTP Audio Keepalive detenido limpiamente.")

    def _stop_audio_keepalive(self):
        """Detiene de forma segura el demonio de keepalive de audio RTP."""
        if hasattr(self, "audio_keepalive_stop_event") and self.audio_keepalive_stop_event:
            self.audio_keepalive_stop_event.set()
        if hasattr(self, "audio_keepalive_thread") and self.audio_keepalive_thread:
            if self.audio_keepalive_thread.is_alive():
                self.audio_keepalive_thread.join(timeout=1.0)
            self.audio_keepalive_thread = None
        if hasattr(self, "audio_keepalive_sock") and self.audio_keepalive_sock:
            try:
                self.audio_keepalive_sock.close()
            except Exception:
                pass
            self.audio_keepalive_sock = None

    # =========================================================================
    # DEMONIO DE ESCUCHA PASIVA Y GESTIÓN DE TIMBRE ENTRANTE (INVITE)
    # =========================================================================

    def start_listener_daemon(self):
        """Inicia el demonio de escucha pasiva en segundo plano."""
        if self.listener_running:
            return
        self.listener_running = True
        self.listener_thread = threading.Thread(
            target=self._listener_daemon_loop,
            daemon=True,
            name="sip_listener_daemon"
        )
        self.listener_thread.start()
        logger.info("Demonio SIP de escucha pasiva arrancado.")

    def stop_listener_daemon(self):
        """Detiene el demonio de escucha pasiva."""
        self.listener_running = False
        with self.listener_lock:
            if self.listener_ss:
                try:
                    self.listener_ss.close()
                except Exception as e:
                    logger.debug(f"Aviso cerrando listener_ss al parar daemon: {e}")
                self.listener_ss = None
        if self.listener_thread and self.listener_thread.is_alive():
            self.listener_thread.join(timeout=3.0)
        logger.info("Demonio SIP de escucha pasiva detenido.")

    async def restart_listener(self):
        """Reinicia el demonio de escucha pasiva para recargar certificados mTLS en caliente."""
        await asyncio.to_thread(self.stop_listener_daemon)
        await asyncio.sleep(1.0)
        await asyncio.to_thread(self.start_listener_daemon)

    def _listener_daemon_loop(self):
        """
        Bucle del demonio de escucha pasiva.
        Mantiene un socket TLS autenticado contra Flexisip (vdesip.bs.iotleg.com:5061),
        envía keepalives (CRLF ping) cada 25s y re-registra cada 5 minutos.
        Permite detectar instantáneamente cuando alguien toca el timbre en la calle.
        """
        logger.info("Bucle de escucha pasiva SIP iniciado.")
        local_ip = self._get_local_ip()

        while self.listener_running:
            ss = None
            try:
                user, domain, _ = self._get_credentials()
                logger.info(f"Conectando socket TLS pasivo a Flexisip ({self.sip_server}:{self.sip_port})...")
                ss = self._connect_tls_socket()
                if not ss:
                    logger.warning("No se pudo conectar TLS en demonio SIP. Reintentando en 5s...")
                    time.sleep(5.0)
                    continue

                with self.listener_lock:
                    self.listener_ss = ss

                reg_ok = self._register_sync(ss, user, domain, local_ip)
                if reg_ok:
                    logger.info("Demonio SIP registrado con 200 OK. En espera pasiva de llamadas (canal del edificio LIBRE).")
                else:
                    logger.warning("Demonio SIP: Registro pendiente o no confirmado. Manteniendo escucha...")

                last_keepalive = time.time()
                last_register = time.time()
                buffer = ""

                while self.listener_running and self.listener_ss == ss:
                    now = time.time()

                    # Keepalive cada 25s (CRLF ping RFC 5626)
                    if now - last_keepalive > 25.0:
                        try:
                            ss.sendall(b"\r\n\r\n")
                            last_keepalive = now
                        except Exception as ke:
                            logger.warning(f"Error keepalive en demonio SIP: {ke}")
                            break

                    # Re-registro periódico cada 300s (5 minutos)
                    if now - last_register > 300.0:
                        try:
                            branch_reg = 'z9hG4bK' + uuid.uuid4().hex[:12]
                            cseq_reg = self._next_cseq(1)
                            inst_reg = self._get_or_create_instance_id()
                            msg_reg = (
                                f"REGISTER sip:{domain} SIP/2.0\r\n"
                                f"Via: SIP/2.0/TLS {local_ip}:5061;branch={branch_reg};rport\r\n"
                                f"Max-Forwards: 70\r\n"
                                f"From: <sip:{user}@{domain}>;tag={uuid.uuid4().hex[:8]}\r\n"
                                f"To: <sip:{user}@{domain}>\r\n"
                                f"Call-ID: {uuid.uuid4().hex}\r\n"
                                f"CSeq: {cseq_reg} REGISTER\r\n"
                                f'Contact: <sip:{user}@{local_ip}:5061;transport=tls>;+sip.instance="<{inst_reg}>";reg-id=1\r\n'
                                f"Supported: outbound, path\r\n"
                                f"Expires: 600\r\n"
                                f"User-Agent: LinphoneAndroid/4.5.3 (c100x)\r\n"
                                f"Content-Length: 0\r\n\r\n"
                            )
                            ss.sendall(msg_reg.encode('utf-8'))
                            last_register = now
                            logger.debug("Refresco periódico REGISTER enviado a Flexisip.")
                        except Exception as re_err:
                            logger.warning(f"Error en re-registro periódico SIP: {re_err}")
                            break

                    # Lectura con timeout de 1.5s
                    try:
                        ss.settimeout(1.5)
                        chunk = ss.recv(4096).decode('utf-8', errors='ignore')
                        if not chunk:
                            logger.warning("Conexión del socket de escucha SIP cerrada por el servidor.")
                            break
                        buffer += chunk
                    except socket.timeout:
                        # Auto-timeout si nadie atiende la llamada sonando a los 45s o conectada supera 60s
                        if self.call_state == "ringing" and (time.time() - self.call_start_time > 45.0):
                            logger.info("Timeout de llamada entrante sin atender (45s). Volviendo a standby.")
                            self.reject_or_hangup_call()
                        elif self.call_state == "connected" and (time.time() - self.call_start_time > 60.0):
                            logger.info("Timeout de llamada activa (60s). Finalizando llamada.")
                            self.reject_or_hangup_call()
                        continue
                    except Exception as re_err:
                        if self.listener_running:
                            logger.warning(f"Lectura de demonio SIP interrumpida: {re_err}")
                        break

                    # Procesar mensajes SIP completos ignorando CRLFs inter-mensaje (RFC 3261 Section 7.5)
                    while "\r\n\r\n" in buffer:
                        buffer = buffer.lstrip("\r\n ")
                        if "\r\n\r\n" not in buffer:
                            break
                        parts = buffer.split("\r\n\r\n", 1)
                        hdr_part = parts[0]
                        rest = parts[1]

                        cl_m = re.search(r'content-length:\s*(\d+)', hdr_part, re.IGNORECASE)
                        content_len = int(cl_m.group(1)) if cl_m else 0

                        rest_bytes = rest.encode('utf-8')
                        if len(rest_bytes) < content_len:
                            # Esperar más bytes para completar el cuerpo
                            break

                        body_str = rest_bytes[:content_len].decode('utf-8', errors='ignore')
                        buffer = rest_bytes[content_len:].decode('utf-8', errors='ignore').lstrip("\r\n ")

                        full_msg = hdr_part + "\r\n\r\n" + body_str
                        self._handle_incoming_sip_message(ss, full_msg, user, domain, local_ip)

            except Exception as e:
                if self.listener_running:
                    logger.error(f"Error en demonio SIP: {e}")
            finally:
                with self.listener_lock:
                    if self.listener_ss == ss:
                        self.listener_ss = None
                if ss:
                    try:
                        ss.close()
                    except Exception as e:
                        logger.debug(f"Aviso cerrando socket de daemon listener: {e}")
                if self.listener_running:
                    time.sleep(3.0)

    def _handle_incoming_sip_message(self, ss: ssl.SSLSocket, msg: str, user: str, domain: str, local_ip: str):
        """Interpreta eventos SIP entrantes desde la placa de calle o proxy Flexisip."""
        clean_lines = [l.strip() for l in msg.splitlines() if l.strip()]
        if not clean_lines:
            return
        first_line = clean_lines[0]
        instance_id = self._get_or_create_instance_id()

        # 1. INVITE entrante: Alguien pulsa el timbre en la calle
        if first_line.startswith("INVITE"):
            logger.info("🔔 ¡TIMBRE ENTRANTE! El videoportero ha recibido INVITE de la placa de calle.")

            call_id_m = re.search(r'call-id:\s*([^\r\n]+)', msg, re.IGNORECASE)
            from_m = re.search(r'from:\s*([^\r\n]+)', msg, re.IGNORECASE)
            to_m = re.search(r'to:\s*([^\r\n]+)', msg, re.IGNORECASE)
            cseq_m = re.search(r'cseq:\s*(\d+)\s+INVITE', msg, re.IGNORECASE)

            call_id = call_id_m.group(1).strip() if call_id_m else uuid.uuid4().hex
            from_hdr = from_m.group(1).strip() if from_m else ""
            to_hdr = to_m.group(1).strip() if to_m else ""
            cseq = cseq_m.group(1).strip() if cseq_m else "1"

            via_lines = [l for l in clean_lines if l.lower().startswith("via:")]
            via_block = "\r\n".join(via_lines) + "\r\n" if via_lines else f"Via: SIP/2.0/TLS {local_ip}:5061;rport\r\n"

            record_routes = [l for l in clean_lines if l.lower().startswith("record-route:")]
            routes_block = "\r\n".join(record_routes) + "\r\n" if record_routes else ""

            # Tag para cabecera To
            to_tag_m = re.search(r'tag=([^\r\n;]+)', to_hdr)
            local_to_tag = to_tag_m.group(1) if to_tag_m else uuid.uuid4().hex[:8]
            to_with_tag = to_hdr if to_tag_m else f"{to_hdr};tag={local_to_tag}"

            # Extraer SDP de oferta (remoto) de forma estructurada
            remote_ip = None
            remote_port = None
            remote_audio_port = None
            remote_crypto = None
            remote_audio_crypto = None

            current_media = None
            for line in msg.splitlines():
                line_s = line.strip()
                if line_s.lower().startswith("c=in ip4 "):
                    ip_val = line_s.split()[-1]
                    if not remote_ip or current_media == "video":
                        remote_ip = ip_val
                elif line_s.lower().startswith("m=video "):
                    current_media = "video"
                    parts = line_s.split()
                    if len(parts) > 1 and parts[1].isdigit():
                        remote_port = int(parts[1])
                elif line_s.lower().startswith("m=audio "):
                    current_media = "audio"
                    parts = line_s.split()
                    if len(parts) > 1 and parts[1].isdigit():
                        remote_audio_port = int(parts[1])
                elif line_s.lower().startswith("a=crypto:"):
                    cr_match = re.search(r'inline:([A-Za-z0-9+/=]+)', line_s)
                    if cr_match:
                        key_val = cr_match.group(1)
                        if current_media == "video":
                            remote_crypto = key_val
                        elif current_media == "audio":
                            remote_audio_crypto = key_val

            if not remote_crypto:
                cr_all = re.findall(r'a=crypto:\d+\s+\S+\s+inline:([A-Za-z0-9+/=]+)', msg)
                if cr_all:
                    remote_crypto = cr_all[-1]

            if not remote_ip:
                c_m = re.search(r'c=IN IP4\s+([0-9.]+)', msg, re.IGNORECASE)
                if c_m:
                    remote_ip = c_m.group(1)

            self.call_context = {
                "call_id": call_id,
                "from_hdr": from_hdr,
                "to_hdr": to_hdr,
                "to_with_tag": to_with_tag,
                "local_to_tag": local_to_tag,
                "cseq": cseq,
                "via_block": via_block,
                "routes_block": routes_block,
                "remote_ip": remote_ip,
                "remote_port": remote_port,
                "remote_audio_port": remote_audio_port,
                "remote_crypto": remote_crypto,
                "timestamp": time.time(),
                "caller": from_hdr
            }

            # Enviar 180 Ringing de inmediato
            ringing_msg = (
                "SIP/2.0 180 Ringing\r\n"
                f"{via_block}"
                f"{routes_block}"
                f"From: {from_hdr}\r\n"
                f"To: {to_with_tag}\r\n"
                f"Call-ID: {call_id}\r\n"
                f"CSeq: {cseq} INVITE\r\n"
                f'Contact: <sip:{user}@{local_ip}:5061;transport=tls>;+sip.instance="<{instance_id}>"\r\n'
                f"User-Agent: LinphoneAndroid/4.5.3 (c100x)\r\n"
                f"Content-Length: 0\r\n\r\n"
            )
            try:
                ss.sendall(ringing_msg.encode('utf-8'))
                logger.info("Respuesta 180 Ringing enviada a Flexisip.")
            except Exception as se:
                logger.error(f"Error enviando 180 Ringing: {se}")

            self.incoming_call = True
            self.call_state = "ringing"
            self.call_start_time = time.time()

            if self.on_incoming_call:
                try:
                    self.on_incoming_call(self.call_context)
                except Exception as cb_err:
                    logger.error(f"Error en on_incoming_call: {cb_err}")

        # 2. CANCEL entrante: timbre cancelado
        elif first_line.startswith("CANCEL"):
            logger.info("CANCEL recibido en demonio SIP.")
            call_id_m = re.search(r'call-id:\s*([^\r\n]+)', msg, re.IGNORECASE)
            from_m = re.search(r'from:\s*([^\r\n]+)', msg, re.IGNORECASE)
            to_m = re.search(r'to:\s*([^\r\n]+)', msg, re.IGNORECASE)
            cseq_m = re.search(r'cseq:\s*(\d+)\s+CANCEL', msg, re.IGNORECASE)

            via_lines = [l for l in clean_lines if l.lower().startswith("via:")]
            via_block = "\r\n".join(via_lines) + "\r\n" if via_lines else ""

            resp_cancel = (
                "SIP/2.0 200 OK\r\n"
                f"{via_block}"
                f"From: {from_m.group(1) if from_m else ''}\r\n"
                f"To: {to_m.group(1) if to_m else ''}\r\n"
                f"Call-ID: {call_id_m.group(1) if call_id_m else ''}\r\n"
                f"CSeq: {cseq_m.group(1) if cseq_m else '1'} CANCEL\r\n"
                f"Content-Length: 0\r\n\r\n"
            )
            try:
                ss.sendall(resp_cancel.encode('utf-8'))
            except Exception as e:
                logger.debug(f"Aviso enviando 200 OK a CANCEL: {e}")

            to_tag = self.call_context.get("to_with_tag") or (to_m.group(1) if to_m else "")
            cseq_inv = self.call_context.get("cseq", "1")
            resp_487 = (
                "SIP/2.0 487 Request Terminated\r\n"
                f"{via_block}"
                f"From: {from_m.group(1) if from_m else ''}\r\n"
                f"To: {to_tag}\r\n"
                f"Call-ID: {call_id_m.group(1) if call_id_m else ''}\r\n"
                f"CSeq: {cseq_inv} INVITE\r\n"
                f"Content-Length: 0\r\n\r\n"
            )
            try:
                ss.sendall(resp_487.encode('utf-8'))
            except Exception as e:
                logger.debug(f"Aviso enviando 487 Request Terminated: {e}")

            self.incoming_call = False
            self.call_state = "standby"
            self.call_context = {}
            if self.on_call_ended:
                try:
                    self.on_call_ended()
                except Exception as e:
                    logger.debug(f"Aviso en on_call_ended tras CANCEL: {e}")

        # 3. BYE entrante: llamada finalizada
        elif first_line.startswith("BYE"):
            logger.info("BYE recibido en demonio SIP.")
            call_id_m = re.search(r'call-id:\s*([^\r\n]+)', msg, re.IGNORECASE)
            from_m = re.search(r'from:\s*([^\r\n]+)', msg, re.IGNORECASE)
            to_m = re.search(r'to:\s*([^\r\n]+)', msg, re.IGNORECASE)
            cseq_m = re.search(r'cseq:\s*(\d+)\s+BYE', msg, re.IGNORECASE)
            via_lines = [l for l in clean_lines if l.lower().startswith("via:")]
            via_block = "\r\n".join(via_lines) + "\r\n" if via_lines else ""

            resp_bye = (
                "SIP/2.0 200 OK\r\n"
                f"{via_block}"
                f"From: {from_m.group(1) if from_m else ''}\r\n"
                f"To: {to_m.group(1) if to_m else ''}\r\n"
                f"Call-ID: {call_id_m.group(1) if call_id_m else ''}\r\n"
                f"CSeq: {cseq_m.group(1) if cseq_m else '1'} BYE\r\n"
                f"Content-Length: 0\r\n\r\n"
            )
            try:
                ss.sendall(resp_bye.encode('utf-8'))
            except Exception as e:
                logger.debug(f"Aviso enviando 200 OK a BYE: {e}")

            if self.video_receiver:
                try:
                    self.video_receiver.stop()
                except Exception as e:
                    logger.debug(f"Aviso deteniendo video_receiver tras BYE: {e}")

            self.incoming_call = False
            self.call_state = "standby"
            self.video_session_active = False
            self.call_context = {}
            if self.on_call_ended:
                try:
                    self.on_call_ended()
                except Exception:
                    pass

        # 4. OPTIONS entrante: ping de presencia enviado por Flexisip
        elif first_line.startswith("OPTIONS"):
            logger.info("OPTIONS recibido de Flexisip (keepalive ping del servidor). Respondiendo 200 OK.")
            call_id_m = re.search(r'call-id:\s*([^\r\n]+)', msg, re.IGNORECASE)
            from_m = re.search(r'from:\s*([^\r\n]+)', msg, re.IGNORECASE)
            to_m = re.search(r'to:\s*([^\r\n]+)', msg, re.IGNORECASE)
            cseq_m = re.search(r'cseq:\s*(\d+)\s+OPTIONS', msg, re.IGNORECASE)
            via_lines = [l for l in clean_lines if l.lower().startswith("via:")]
            via_block = "\r\n".join(via_lines) + "\r\n" if via_lines else ""

            resp_opt = (
                "SIP/2.0 200 OK\r\n"
                f"{via_block}"
                f"From: {from_m.group(1) if from_m else ''}\r\n"
                f"To: {to_m.group(1) if to_m else ''}\r\n"
                f"Call-ID: {call_id_m.group(1) if call_id_m else ''}\r\n"
                f"CSeq: {cseq_m.group(1) if cseq_m else '1'} OPTIONS\r\n"
                f'Contact: <sip:{user}@{local_ip}:5061;transport=tls>;+sip.instance="<{instance_id}>"\r\n'
                f"User-Agent: LinphoneAndroid/4.5.3 (c100x)\r\n"
                f"Allow: INVITE, ACK, CANCEL, OPTIONS, BYE, REFER, NOTIFY, MESSAGE, SUBSCRIBE, INFO\r\n"
                f"Content-Length: 0\r\n\r\n"
            )
            try:
                ss.sendall(resp_opt.encode('utf-8'))
            except Exception as e:
                logger.debug(f"Aviso respondiendo a OPTIONS: {e}")

        # 5. ACK entrante: confirmación de 200 OK de respuesta a llamada
        elif first_line.startswith("ACK"):
            logger.info("ACK recibido de Flexisip. Diálogo SIP de llamada entrante completamente confirmado.")

        # 6. Respuestas 200 OK de Flexisip (ej: confirmación a re-REGISTER periódico)
        elif "200" in first_line and "register" in msg.lower():
            logger.info("Refresco REGISTER SIP confirmado con 200 Registration successful.")

    def answer_incoming_call(self, on_frame: Optional[Callable[[bytes], None]] = None) -> Dict[str, Any]:
        """
        Responde a la llamada entrante del timbre:
        Envía 200 OK con SDP al INVITE entrante e inicializa VideoReceiver para recibir el stream H.264.
        """
        with self.session_lock:
            if not self.incoming_call or self.call_state != "ringing":
                return {"success": False, "error": "No hay llamada entrante timbrando"}

            ctx = self.call_context
            is_simulated = ctx.get("is_simulated", False)
            ss = self.listener_ss
            if not is_simulated and not ss:
                return {"success": False, "error": "Socket SIP de escucha desconectado"}

            user, domain, _ = self._get_credentials()
            local_ip = self._get_local_ip()
            instance_id = self._get_or_create_instance_id()
            crypto_key = base64.b64encode(os.urandom(30)).decode('ascii')

            sdp = (
                "v=0\r\n"
                f"o={user} 123456 123456 IN IP4 {local_ip}\r\n"
                "s=Talk\r\n"
                f"c=IN IP4 {local_ip}\r\n"
                "t=0 0\r\n"
                f"m=audio {DEFAULT_AUDIO_RTP_PORT} RTP/SAVP 8 0\r\n"
                "a=rtpmap:8 PCMA/8000\r\n"
                "a=rtpmap:0 PCMU/8000\r\n"
                f"a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:{crypto_key}\r\n"
                "a=sendrecv\r\n"
                f"m=video {DEFAULT_RTP_PORT} RTP/SAVP 96\r\n"
                "a=rtpmap:96 H264/90000\r\n"
                "a=fmtp:96 profile-level-id=42801F;packetization-mode=1\r\n"
                f"a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:{crypto_key}\r\n"
                "a=recvonly\r\n"
            )

            call_id = ctx.get("call_id", "")
            from_hdr = ctx.get("from_hdr", "")
            to_with_tag = ctx.get("to_with_tag", "")
            cseq = ctx.get("cseq", "1")
            via_block = ctx.get("via_block", "")
            routes_block = ctx.get("routes_block", "")

            resp_200 = (
                "SIP/2.0 200 OK\r\n"
                f"{via_block}"
                f"{routes_block}"
                f"From: {from_hdr}\r\n"
                f"To: {to_with_tag}\r\n"
                f"Call-ID: {call_id}\r\n"
                f"CSeq: {cseq} INVITE\r\n"
                f"Contact: <sip:{user}@{local_ip}:5061;transport=tls>;+sip.instance=\"<{instance_id}>\";reg-id=1\r\n"
                f"User-Agent: LinphoneAndroid/4.5.3 (c100x)\r\n"
                f"Content-Type: application/sdp\r\n"
                f"Content-Length: {len(sdp)}\r\n\r\n"
                f"{sdp}"
            )

            if not is_simulated and ss:
                try:
                    ss.sendall(resp_200.encode('utf-8'))
                    logger.info(f"SIP/2.0 200 OK enviado para atender llamada {call_id}")
                except Exception as e:
                    logger.error(f"Error enviando 200 OK: {e}")
                    return {"success": False, "error": str(e)}
            else:
                logger.info(f"Llamada simulada {call_id} atendida (modo prueba).")

            remote_ip = ctx.get("remote_ip") or os.environ.get("GATEWAY_IP", "127.0.0.1")
            remote_port = ctx.get("remote_port") or DEFAULT_RTP_PORT
            remote_crypto = ctx.get("remote_crypto") or crypto_key

            if not is_simulated:
                if not self.video_receiver:
                    self.video_receiver = VideoReceiver(port=DEFAULT_RTP_PORT, on_frame=on_frame)
                else:
                    self.video_receiver.on_frame = on_frame

                self.video_receiver.start(remote_ip=remote_ip, remote_port=remote_port, crypto_key=remote_crypto)

                # Iniciar RTP Audio Keepalive para mantener la llamada viva
                remote_audio_port = ctx.get("remote_audio_port")
                if remote_audio_port and remote_ip:
                    self._start_audio_keepalive(remote_ip, remote_audio_port)

            self.call_state = "connected"
            self.call_start_time = time.time()
            self.video_session_active = True
            self.video_call_id = call_id
            self.video_target_uri = from_hdr
            self.video_to_tag = ctx.get("local_to_tag")
            self.video_camera_address = self.DEFAULT_ENTRANCE_PANEL_GUID

            return {
                "success": True,
                "call_id": call_id,
                "remote_ip": remote_ip,
                "remote_port": remote_port,
                "status": "connected",
                "is_simulated": is_simulated
            }

    def reject_or_hangup_call(self) -> Dict[str, Any]:
        """Rechaza una llamada que esté sonando o cuelga una llamada conectada."""
        with self.session_lock:
            state = self.call_state
            ctx = self.call_context
            ss = self.listener_ss
            is_simulated = ctx.get("is_simulated", False)

            if state == "ringing":
                if not is_simulated and ss and ctx:
                    call_id = ctx.get("call_id", "")
                    from_hdr = ctx.get("from_hdr", "")
                    to_with_tag = ctx.get("to_with_tag", "")
                    cseq = ctx.get("cseq", "1")
                    via_block = ctx.get("via_block", "")

                    busy_msg = (
                        "SIP/2.0 486 Busy here\r\n"
                        f"{via_block}"
                        f"From: {from_hdr}\r\n"
                        f"To: {to_with_tag}\r\n"
                        f"Call-ID: {call_id}\r\n"
                        f"CSeq: {cseq} INVITE\r\n"
                        f"Content-Length: 0\r\n\r\n"
                    )
                    try:
                        ss.sendall(busy_msg.encode('utf-8'))
                        logger.info("486 Busy here enviado para rechazar timbre.")
                    except Exception as e:
                        logger.warning(f"Error enviando 486 Busy: {e}")

            elif state == "connected":
                if not is_simulated and ss and ctx:
                    user, domain, _ = self._get_credentials()
                    call_id = ctx.get("call_id", self.video_call_id or "")
                    from_hdr = ctx.get("from_hdr", "")
                    to_with_tag = ctx.get("to_with_tag", "")
                    bye_branch = 'z9hG4bK' + uuid.uuid4().hex[:12]
                    routes_block = ctx.get("routes_block", "")

                    bye_msg = (
                        f"BYE sip:c100x@{domain} SIP/2.0\r\n"
                        f"Via: SIP/2.0/TLS {self._get_local_ip()}:5061;branch={bye_branch};rport\r\n"
                        f"Max-Forwards: 70\r\n"
                        f"{routes_block}"
                        f"From: {to_with_tag}\r\n"
                        f"To: {from_hdr}\r\n"
                        f"Call-ID: {call_id}\r\n"
                        f"CSeq: 99 BYE\r\n"
                        f"Content-Length: 0\r\n\r\n"
                    )
                    try:
                        ss.sendall(bye_msg.encode('utf-8'))
                        logger.info("BYE enviado para finalizar llamada.")
                    except Exception as e:
                        logger.warning(f"Error enviando BYE: {e}")

            # Detener sesión de vídeo saliente si fue iniciada para la llamada
            if self.video_ss:
                try:
                    self._stop_video_session_sync()
                except Exception as e:
                    logger.debug(f"Aviso deteniendo sesión de vídeo saliente en colgar: {e}")

            if self.video_receiver:
                try:
                    self.video_receiver.stop()
                except Exception as e:
                    logger.debug(f"Aviso deteniendo video_receiver en colgar: {e}")

            self._stop_audio_keepalive()

            self.incoming_call = False
            self.call_state = "standby"
            self.video_session_active = False
            self.call_context = {}

            if self.on_call_ended:
                try:
                    self.on_call_ended()
                except Exception as e:
                    logger.debug(f"Aviso en on_call_ended al colgar: {e}")

            return {"success": True, "call_state": "standby"}

    def simulate_incoming_call(self, caller: str = "Placa de calle PE0 (Simulación)") -> Dict[str, Any]:
        """
        Simula una llamada entrante del timbre para pruebas completas de frontend:
        Notificación en pantalla, banner flotante, alerta sonora, respuesta y apertura de puerta.
        """
        with self.session_lock:
            now = time.time()
            self.incoming_call = True
            self.call_state = "ringing"
            self.call_start_time = now
            sim_call_id = f"sim-{uuid.uuid4().hex[:10]}"
            self.call_context = {
                "caller": caller,
                "call_id": sim_call_id,
                "timestamp": now,
                "is_simulated": True,
                "from_hdr": f'"{caller}" <sip:c100x@vdesip.bs.iotleg.com>',
                "to_with_tag": f"<sip:simuser@vdesip.bs.iotleg.com>;tag={uuid.uuid4().hex[:8]}",
                "cseq": "100",
                "via_block": "",
                "routes_block": "",
                "remote_ip": os.environ.get("GATEWAY_IP", "127.0.0.1"),
                "remote_port": DEFAULT_RTP_PORT,
            }
            logger.info(f"🔔 Llamada simulada iniciada con Call-ID: {sim_call_id}")
            if self.on_incoming_call:
                try:
                    self.on_incoming_call(self.call_context)
                except Exception as e:
                    logger.debug(f"Aviso en callback on_incoming_call (simulada): {e}")

            return {
                "success": True,
                "call_id": sim_call_id,
                "status": "ringing",
                "caller": caller,
                "message": "Llamada entrante simulada activada. El frontend mostrará el aviso de llamada."
            }

    def get_call_status(self) -> Dict[str, Any]:
        """Devuelve el estado de llamada entrante para polling del frontend."""
        elapsed = int(time.time() - self.call_start_time) if self.call_start_time > 0 else 0
        rem_sec = 0
        if self.call_state == "ringing":
            rem_sec = max(0, 45 - elapsed)
        elif self.call_state == "connected":
            rem_sec = max(0, 60 - elapsed)

        return {
            "incoming_call": self.incoming_call,
            "call_state": self.call_state,
            "duration_seconds": elapsed,
            "remaining_seconds": rem_sec,
            "caller": self.call_context.get("caller", "Placa de calle PE0"),
            "timestamp": self.call_context.get("timestamp", 0)
        }

    # =========================================================================
    # GENERADORES DE COMANDOS OPENWEBNET Y JSON-RPC
    # =========================================================================

    def build_openwebnet_lock_command(self, address: str = "20") -> list[str]:
        """Apertura de cerradura OpenWebNet (*8*19*<addr>##)."""
        return [f"*8*19*{address}##", f"*8*20*{address}##"]

    def build_openwebnet_light_command(self, address: str = "114") -> list[str]:
        """Luz de escalera OpenWebNet (*8*21*<addr>##)."""
        return [f"*8*21*{address}##", f"*8*22*{address}##"]

    def build_openwebnet_camera_turn_on(self) -> list[str]:
        """Auto-encendido OpenWebNet legado (en Classe 100X se usa SIP INVITE)."""
        return ["*13*35*##"]

    def build_openwebnet_camera_turn_off(self) -> list[str]:
        """Spegnimento de cámara exterior (*13*36*##)."""
        return ["*13*36*##"]

    def build_openwebnet_camera_cycle(self, direction: str = "next") -> list[str]:
        """Alternar entre cámaras (*13*37*## siguiente, *13*38*## previa)."""
        return ["*13*37*##"] if direction == "next" else ["*13*38*##"]

    def build_jsonrpc_lock_command(self, lock_id: str = "20") -> str:
        """Mensaje JSON-RPC 2.0 de apertura de cerradura."""
        import random
        rpc_id = str(random.randint(100000, 999999))
        payload = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "lock.setStatus",
            "params": [
                {
                    "action": "Lock",
                    "status": "open",
                    "receiver": {
                        "plant": {
                            "coal": {"id": lock_id},
                            "module": {"id": None}
                        }
                    }
                }
            ]
        }
        return json.dumps(payload)

    def build_jsonrpc_light_command(self, light_id: str = "114") -> str:
        """Mensaje JSON-RPC 2.0 de luz de escalera."""
        import random
        rpc_id = str(random.randint(100000, 999999))
        payload = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "light.setStatus",
            "params": [
                {
                    "action": "Stairlight",
                    "status": "on",
                    "receiver": {
                        "plant": {
                            "coal": {"id": light_id},
                            "module": {"id": None}
                        }
                    }
                }
            ]
        }
        return json.dumps(payload)

    # =========================================================================
    # APROVISIONAMIENTO DE CERTIFICADOS mTLS
    # =========================================================================

    async def enroll_certificates(self, user_token: str, plant_id: str, email: str = "user@example.com", cn: Optional[str] = None) -> bool:
        """Genera par de claves EC (secp256r1) y firma certificado con la CA de Legrand."""
        try:
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography import x509
            from cryptography.x509.oid import NameOID

            def _generate_csr():
                key = ec.generate_private_key(ec.SECP256R1())
                key_pem = key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption()
                )

                user, domain, _ = self._get_credentials()
                sip_uri = f"sip:{user}@{domain}"
                common_name = cn or (user.split("_")[-1] if "_" in user else uuid.uuid4().hex[:16].upper())

                builder = x509.CertificateSigningRequestBuilder()
                builder = builder.subject_name(x509.Name([
                    x509.NameAttribute(NameOID.EMAIL_ADDRESS, email),
                    x509.NameAttribute(NameOID.COUNTRY_NAME, "FR"),
                    x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "France"),
                    x509.NameAttribute(NameOID.LOCALITY_NAME, "Paris"),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LEGRAND"),
                    x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "C100X"),
                    x509.NameAttribute(NameOID.COMMON_NAME, common_name),
                ]))
                builder = builder.add_extension(
                    x509.SubjectAlternativeName([x509.UniformResourceIdentifier(sip_uri)]),
                    critical=False,
                )
                csr = builder.sign(key, hashes.SHA256())
                csr_der_b64 = base64.b64encode(csr.public_bytes(serialization.Encoding.DER)).decode("ascii")
                return key_pem, csr_der_b64

            key_pem, csr_der_b64 = await asyncio.to_thread(_generate_csr)

            # Token de Azure AD mediante httpx asíncrono
            tenant_id = os.environ.get("LEGRAND_APP_TENANT_ID", "")
            app_client_id = os.environ.get("LEGRAND_APP_CLIENT_ID", "")
            app_client_secret = os.environ.get("LEGRAND_APP_CLIENT_SECRET", "")
            sub_key = os.environ.get("LEGRAND_SUBSCRIPTION_KEY", "")

            token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/token?resource={app_client_id}"
            token_data = {
                "client_id": app_client_id,
                "client_secret": app_client_secret,
                "grant_type": "client_credentials"
            }
            ca_url = "https://api.developer.legrand.com/certificate/api/v1.0/ca/information/CACerts"
            cert_url = "https://api.developer.legrand.com/certificate/api/v1.0/ca/information/clientCerts"
            cert_payload = {
                "csr": csr_der_b64,
                "template": "sipuser",
                "sender": {
                    "system": "information",
                    "addressType": "addressLocation",
                    "plant": {"id": plant_id}
                }
            }

            async with httpx.AsyncClient(timeout=15.0) as client:
                resp_token = await client.post(token_url, data=token_data)
                resp_token.raise_for_status()
                app_token = resp_token.json()["access_token"]

                headers = {
                    "UserToken": user_token,
                    "Authorization": f"Bearer {app_token}",
                    "Ocp-Apim-Subscription-Key": sub_key,
                    "Content-Type": "application/json"
                }

                resp_ca = await client.get(ca_url, headers=headers)
                resp_ca.raise_for_status()
                ca_data = resp_ca.json()
                ca_der = base64.b64decode(ca_data["chain"])
                ca_cert = x509.load_der_x509_certificate(ca_der)
                ca_pem = ca_cert.public_bytes(serialization.Encoding.PEM)

                resp_cert = await client.post(cert_url, headers=headers, json=cert_payload)
                resp_cert.raise_for_status()
                cert_resp = resp_cert.json()
                client_der = base64.b64decode(cert_resp["cert"])
                client_cert = x509.load_der_x509_certificate(client_der)
                client_pem = client_cert.public_bytes(serialization.Encoding.PEM)

            def _write_files():
                self.data_dir.mkdir(parents=True, exist_ok=True)
                with open(self.client_key_path, "wb") as f:
                    f.write(key_pem)
                with open(self.client_cert_path, "wb") as f:
                    f.write(client_pem)
                with open(self.ca_chain_path, "wb") as f:
                    f.write(ca_pem)

            await asyncio.to_thread(_write_files)
            logger.info("Certificados mTLS generados y guardados con éxito.")
            return True
        except Exception as e:
            logger.error(f"Fallo aprovisionando certificados mTLS: {e}")
            return False
            return False
