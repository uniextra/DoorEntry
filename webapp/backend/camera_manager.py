"""
Gestor de cámara, recepción de video y streaming para el videoportero Legrand/BTicino Classe 100X.
Permite activar la cámara de la placa de calle mediante señalización SIP SRTP,
ingerir fotogramas reales de video (RTP H.264 / MJPEG / snapshots) y
transmitirlos en vivo al navegador web vía multipart/x-mixed-replace (MJPEG) con auto-renovación continua.
"""

import io
import time
import random
import asyncio
import logging
import threading
from datetime import datetime
from typing import Optional, AsyncGenerator, Callable, Dict, Any, List
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("camera_manager")

DEFAULT_GATEWAY_ID = os.environ.get("GATEWAY_ID", "")
DEFAULT_CAMERA_GUID = os.environ.get("ENTRANCE_PANEL_GUID", "")  # Placa exterior principal PE0
SECONDARY_CAMERA_GUID = os.environ.get("SECONDARY_PANEL_GUID", "")  # Cámara interior / PE1
DEFAULT_CAMERA_ADDR = DEFAULT_CAMERA_GUID


class CameraManager:
    def __init__(self, local_ip: Optional[str] = None, default_gw: Optional[str] = None):
        self.local_ip = local_ip or os.environ.get("LOCAL_IP", "127.0.0.1")
        self.default_gw = default_gw or DEFAULT_GATEWAY_ID
        self.local_ip = local_ip
        self.default_gw = default_gw
        self.is_active = False
        self.camera_index = 0
        self.camera_address = DEFAULT_CAMERA_ADDR
        self.activated_at: Optional[float] = None
        self.frame_counter = 0
        self.gateway_id: Optional[str] = default_gw
        self.active_viewers = 0

        # Registro y telemetría de clientes conectados al streaming MJPEG
        self.streaming_clients: Dict[str, Dict[str, Any]] = {}
        self._clients_lock = threading.Lock()

        # Buffer de fotogramas reales
        self.latest_frame: Optional[bytes] = None
        self.latest_frame_time: float = 0.0

        # Caché de fotograma de reposo (Standby) para optimización de CPU
        self._cached_standby_frame: Optional[bytes] = None
        self._cached_standby_time: float = 0.0
        self._cached_standby_state: tuple = ()

        # Fuente predeterminada para telemetría
        try:
            self.font = ImageFont.load_default()
        except Exception:
            self.font = None

    def activate(self, gateway_id: Optional[str] = None, camera_index: int = 0, camera_address: str = DEFAULT_CAMERA_ADDR) -> bool:
        """Activa la sesión de video de la placa de calle."""
        self.is_active = True
        self.camera_index = camera_index
        self.camera_address = camera_address or DEFAULT_CAMERA_ADDR
        self.gateway_id = gateway_id or self.default_gw
        self.activated_at = time.time()
        self.frame_counter = 0
        self._cached_standby_frame = None
        logger.info(f"Cámara {camera_index} (PE{camera_index}, addr {self.camera_address}) activada para pasarela {self.gateway_id}")
        return True

    def deactivate(self, gateway_id: Optional[str] = None) -> bool:
        """Finaliza la sesión de video."""
        self.is_active = False
        self.activated_at = None
        self._cached_standby_frame = None
        logger.info("Cámara exterior desactivada. Sesión en reposo.")
        return True

    def cycle_camera(self, direction: str = "next", new_address: Optional[str] = None, single_camera: bool = False) -> int:
        """
        Actualiza el estado de cámara tras ciclado.
        Si la instalación es de cámara única (PE0), se mantiene en PE0.
        """
        self._cached_standby_frame = None
        if single_camera:
            self.camera_index = 0
            self.camera_address = DEFAULT_CAMERA_GUID
            logger.info("Cámara exterior: Placa principal PE0 (cámara única en el bus SCS)")
            return 0

        if new_address:
            self.camera_address = new_address
            self.camera_index = 0 if new_address == DEFAULT_CAMERA_GUID else 1
        else:
            if direction == "next":
                self.camera_index = (self.camera_index + 1) % 2
            else:
                self.camera_index = (self.camera_index - 1) % 2
            self.camera_address = DEFAULT_CAMERA_GUID if self.camera_index == 0 else SECONDARY_CAMERA_GUID

        logger.info(f"Cambiado a cámara índice {self.camera_index} (PE{self.camera_index}, addr {self.camera_address})")
        return self.camera_index

    def ingest_frame(self, frame_bytes: bytes) -> bool:
        """
        Ingiere un fotograma real de video (JPEG) recibido de la pasarela,
        decodificador RTP H.264 o stream de red.
        """
        if not frame_bytes or len(frame_bytes) < 100:
            return False
        self.latest_frame = frame_bytes
        self.latest_frame_time = time.time()
        self.frame_counter += 1
        return True

    def generate_reconnecting_frame(self) -> bytes:
        """
        Genera un fotograma de transición suave durante la auto-renovación de la sesión SIP,
        mostrando el último fotograma capturado con un banner discreto de sincronización.
        """
        try:
            if self.latest_frame:
                img = Image.open(io.BytesIO(self.latest_frame))
                draw = ImageDraw.Draw(img)
                w, h = img.size

                # Barra superior semi-transparente
                draw.rectangle([0, 0, w, 32], fill=(15, 23, 42))
                draw.line([0, 32, w, 32], fill=(51, 65, 85), width=1)

                blink = (int(time.time() * 2) % 2 == 0)
                dot_color = (234, 179, 8) if blink else (161, 98, 7)
                draw.ellipse([14, 10, 24, 20], fill=dot_color)

                cam_label = f"PE{self.camera_index} ({'EXTERIOR' if self.camera_index == 0 else 'INTERIOR'})"
                status_txt = f"SINCRONIZANDO SESIÓN SCS... | {cam_label}"
                draw.text((32, 9), status_txt, fill=(254, 240, 138), font=self.font)

                now_str = datetime.now().strftime("%H:%M:%S")
                draw.text((w - 80, 9), now_str, fill=(203, 213, 225), font=self.font)

                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                return buf.getvalue()
        except Exception:
            pass
        return self.generate_standby_frame()

    def generate_standby_frame(self, width: int = 854, height: int = 480) -> bytes:
        """
        Genera un fotograma limpio de diagnóstico y estado para la placa de calle,
        mostrando telemetría real del bus SCS 2 hilos y pasarela.
        Optimizado con caché en memoria (máximo 1 render/s) para evitar sobrecarga de CPU.
        """
        now_time = time.time()
        current_state = (self.is_active, self.camera_index, self.gateway_id, width, height)
        # Refresco dinámico (0.2s) si la sesión está activa para animación fluida de estado y telemetría
        max_cache_age = 0.2 if self.is_active else 1.0
        if self._cached_standby_frame and (now_time - self._cached_standby_time < max_cache_age) and (self._cached_standby_state == current_state):
            return self._cached_standby_frame

        self.frame_counter += 1
        now = datetime.now()
        timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # Fondo sólido oscuro profesional (Slate 950)
        img = Image.new("RGB", (width, height), color=(15, 23, 42))
        draw = ImageDraw.Draw(img)

        # Barra superior de encabezado
        draw.rectangle([0, 0, width, 40], fill=(10, 15, 30))
        draw.line([0, 40, width, 40], fill=(30, 41, 59), width=1)

        cam_label = f"PE{self.camera_index} - {'PLACA EXTERIOR (PE0)' if self.camera_index == 0 else 'CÁMARA INTERIOR (PE1)'}"

        # Indicador de estado
        if self.is_active:
            blink = (int(time.time() * 2) % 2 == 0)
            dot_color = (239, 68, 68) if blink else (185, 28, 28)
            draw.ellipse([14, 14, 26, 26], fill=dot_color)
            draw.text((34, 14), f"CONECTANDO EN DIRECTO | {cam_label}", fill=(241, 245, 249), font=self.font)
        else:
            draw.ellipse([14, 14, 26, 26], fill=(100, 116, 139))
            draw.text((34, 14), f"REPOSO | {cam_label}", fill=(148, 163, 184), font=self.font)

        draw.text((width - 180, 14), timestamp_str, fill=(203, 213, 225), font=self.font)

        # Panel central de información
        cx, cy = width // 2, height // 2
        card_w, card_h = 580, 160
        x1 = cx - card_w // 2
        y1 = cy - card_h // 2
        x2 = cx + card_w // 2
        y2 = cy + card_h // 2

        draw.rectangle([x1, y1, x2, y2], fill=(22, 30, 49), outline=(51, 65, 85), width=1)

        if self.is_active:
            status_title = "SESIÓN DE VIDEO ACTIVA (SIP/SRTP)"
            status_desc1 = f"Pasarela: {self.gateway_id or self.default_gw}"
            status_desc2 = f"Cámara: PE{self.camera_index} ({self.camera_address or DEFAULT_CAMERA_GUID})"
            status_desc3 = "Sincronizando bus SCS 2 hilos y recepción de vídeo H.264..."
            title_color = (56, 189, 248)
        else:
            status_title = "MODO EN ESPERA (STANDBY) - CANAL DEL EDIFICIO LIBRE"
            status_desc1 = "Listo para recibir timbre o vista puntual (30s)."
            gw_ip = os.environ.get("GATEWAY_IP", "LAN")
            status_desc2 = f"Pasarela: {self.gateway_id or self.default_gw or 'Detectada'} ({gw_ip})"
            status_desc3 = f"Placas SCS: PE0 ({DEFAULT_CAMERA_GUID[:8]}...) | PE1 ({SECONDARY_CAMERA_GUID[:8]}...)"
            title_color = (52, 211, 153)

        draw.text((x1 + 24, y1 + 20), status_title, fill=title_color, font=self.font)
        draw.text((x1 + 24, y1 + 55), status_desc1, fill=(226, 232, 240), font=self.font)
        draw.text((x1 + 24, y1 + 85), status_desc2, fill=(203, 213, 225), font=self.font)
        draw.text((x1 + 24, y1 + 115), status_desc3, fill=(148, 163, 184), font=self.font)

        # Barra inferior de telemetría
        draw.rectangle([0, height - 36, width, height], fill=(10, 15, 30))
        draw.line([0, height - 36, width, height - 36], fill=(30, 41, 59), width=1)

        telemetry = f"LEGRAND CLASSE 100X  |  IP: {self.local_ip}  |  SCS BUS 2-WIRE  |  STANDBY"
        draw.text((16, height - 25), telemetry, fill=(148, 163, 184), font=self.font)

        if self.is_active and self.activated_at:
            elapsed = int(time.time() - self.activated_at)
            mins, secs = divmod(elapsed, 60)
            draw.text((width - 130, height - 25), f"ACTIVO: {mins:02d}:{secs:02d}", fill=(52, 211, 153), font=self.font)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        frame_bytes = buf.getvalue()
        self._cached_standby_frame = frame_bytes
        self._cached_standby_time = now_time
        self._cached_standby_state = current_state
        return frame_bytes

    def get_frame(self) -> bytes:
        """
        Devuelve el fotograma más reciente:
        - Si la sesión no está activa, devuelve directamente el fotograma de Standby (canal libre).
        - Si la sesión está activa y hay fotograma real fresco (últimos 3.5 segundos), devuelve el fotograma real.
        - Si la sesión está activa y tuvimos fotograma previo reciente, devuelve fotograma de reconexión.
        - De lo contrario, devuelve el fotograma de Standby.
        """
        if not self.is_active:
            return self.generate_standby_frame()
        now = time.time()
        if self.latest_frame and (now - self.latest_frame_time < 3.5):
            return self.latest_frame
        if self.latest_frame and (now - self.latest_frame_time < 6.0):
            return self.generate_reconnecting_frame()
        return self.generate_standby_frame()

    def get_snapshot_jpeg(self) -> bytes:
        """Devuelve una captura instantánea en formato JPEG."""
        return self.get_frame()

    @staticmethod
    def _parse_device(user_agent: str) -> str:
        """Determina de forma amigable el dispositivo o navegador del cliente."""
        if not user_agent:
            return "Navegador Web"
        ua = user_agent.lower()
        if "iphone" in ua:
            return "iPhone (iOS)"
        if "ipad" in ua:
            return "iPad (iPadOS)"
        if "android" in ua:
            return "Móvil / Tablet Android"
        if "edg" in ua:
            return "Microsoft Edge"
        if "chrome" in ua:
            return "Google Chrome"
        if "firefox" in ua:
            return "Mozilla Firefox"
        if "safari" in ua:
            return "Apple Safari"
        if "curl" in ua or "python" in ua or "postman" in ua:
            return "Script / API Client"
        return "Navegador Web"

    async def mjpeg_generator(self, request_ip: str = "127.0.0.1", user_agent: str = "", fps: int = 15) -> AsyncGenerator[bytes, None]:
        """
        Generador asíncrono para streaming continuo multipart/x-mixed-replace (MJPEG).
        Permite a cualquier navegador ver el video nativamente sin WebRTC ni plugins.
        Registra cada cliente conectado para telemetría y buffer tracking en vivo.
        """
        delay = 1.0 / max(1, fps)
        client_id = f"cli_{int(time.time() * 1000)}_{random.randint(100, 999)}"
        client_info = {
            "id": client_id,
            "ip": request_ip,
            "user_agent": user_agent,
            "device": self._parse_device(user_agent),
            "start_time": time.time(),
            "frames_sent": 0,
            "bytes_sent": 0,
            "last_frame_time": time.time(),
        }

        with self._clients_lock:
            self.streaming_clients[client_id] = client_info
            self.active_viewers = len(self.streaming_clients)

        try:
            while True:
                frame_bytes = self.get_frame()
                chunk = (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(frame_bytes)).encode("ascii") + b"\r\n\r\n"
                    + frame_bytes + b"\r\n"
                )
                yield chunk
                client_info["frames_sent"] += 1
                client_info["bytes_sent"] += len(chunk)
                client_info["last_frame_time"] = time.time()
                await asyncio.sleep(delay)
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            with self._clients_lock:
                self.streaming_clients.pop(client_id, None)
                self.active_viewers = len(self.streaming_clients)

    def get_channels_telemetry(self, sip_stats: Optional[dict] = None) -> dict:
        """
        Devuelve la telemetría en tiempo real agrupada por canal de vídeo y desglose de clientes conectados.
        """
        now = time.time()
        sip_stats = sip_stats or {}

        # Lista de clientes conectados formateados
        formatted_clients: List[Dict[str, Any]] = []
        with self._clients_lock:
            for c_id, c in list(self.streaming_clients.items()):
                start_time = c.get("start_time", now)
                duration = max(1, int(now - start_time))
                bytes_sent = c.get("bytes_sent", 0)
                last_time = c.get("last_frame_time", now)
                age_ms = int((now - last_time) * 1000)

                # Estado del buffer individual
                if age_ms < 1000:
                    buf_status = "Sincronizado"
                elif age_ms < 3000:
                    buf_status = "Retardo Ligero"
                else:
                    buf_status = "Latencia Alta"

                mins, secs = divmod(duration, 60)
                time_str = f"{mins:02d}:{secs:02d}"

                formatted_clients.append({
                    "id": c_id,
                    "ip": c.get("ip", "127.0.0.1"),
                    "device": c.get("device", "Navegador Web"),
                    "user_agent": c.get("user_agent", ""),
                    "connected_seconds": duration,
                    "connected_time_str": time_str,
                    "frames_sent": c.get("frames_sent", 0),
                    "bytes_sent": bytes_sent,
                    "bytes_sent_kb": round(bytes_sent / 1024, 1),
                    "kbps": round((bytes_sent * 8) / (1024 * duration), 1),
                    "last_frame_age_ms": age_ms,
                    "buffer_status": buf_status
                })

        # Datos del búfer del canal activo
        cur_bytes = sip_stats.get("buffer_current_bytes", 0)
        max_bytes = sip_stats.get("buffer_max_bytes", 5 * 1024 * 1024)
        pct = sip_stats.get("buffer_pct", 0.0)
        latest_frame_sz = sip_stats.get("latest_frame_size", 0)
        if not latest_frame_sz and self.latest_frame:
            latest_frame_sz = len(self.latest_frame)
        elif not latest_frame_sz and self._cached_standby_frame:
            latest_frame_sz = len(self._cached_standby_frame)

        frame_age_ms = sip_stats.get("latest_frame_age_ms", 0)
        if not frame_age_ms and self.latest_frame_time:
            frame_age_ms = int((now - self.latest_frame_time) * 1000)

        fps = sip_stats.get("fps_estimate", 15.0 if self.is_active else 0.0)
        pkts = sip_stats.get("packets_received", 0)
        frames_dec = sip_stats.get("frames_decoded", self.frame_counter)

        if pct < 60:
            health_status = "Óptimo"
        elif pct < 85:
            health_status = "Moderado"
        else:
            health_status = "Saturado"

        buffer_metrics_active = {
            "current_bytes": cur_bytes,
            "current_kb": round(cur_bytes / 1024, 1),
            "max_bytes": max_bytes,
            "max_mb": round(max_bytes / (1024 * 1024), 1),
            "pct": pct,
            "latest_frame_size": latest_frame_sz,
            "latest_frame_size_kb": round(latest_frame_sz / 1024, 1),
            "latest_frame_age_ms": frame_age_ms,
            "fps": fps,
            "packets_received": pkts,
            "frames_decoded": frames_dec,
            "status": health_status
        }

        buffer_metrics_idle = {
            "current_bytes": 0,
            "current_kb": 0.0,
            "max_bytes": max_bytes,
            "max_mb": round(max_bytes / (1024 * 1024), 1),
            "pct": 0.0,
            "latest_frame_size": 0,
            "latest_frame_size_kb": 0.0,
            "latest_frame_age_ms": 0,
            "fps": 0.0,
            "packets_received": 0,
            "frames_decoded": 0,
            "status": "Reposo"
        }

        # Agrupación por Canales de Vídeo
        # Canal PE0 (Placa Exterior Principal)
        is_pe0_selected = (self.camera_index == 0)
        pe0_channel = {
            "channel_id": "PE0",
            "name": "Placa Principal (PE0)",
            "description": "Entrada de Calle Principal (SCS Bus 2 Hilos)",
            "address": DEFAULT_CAMERA_GUID,
            "is_selected": is_pe0_selected,
            "is_active": (is_pe0_selected and self.is_active),
            "active_viewers": len(formatted_clients) if is_pe0_selected else 0,
            "buffer": buffer_metrics_active if is_pe0_selected else buffer_metrics_idle,
            "clients": formatted_clients if is_pe0_selected else []
        }

        # Canal PE1 (Placa Secundaria / Cámara Interior)
        is_pe1_selected = (self.camera_index == 1)
        pe1_channel = {
            "channel_id": "PE1",
            "name": "Cámara Interior (PE1)",
            "description": "Cámara Interior / Entrada Secundaria (SCS)",
            "address": SECONDARY_CAMERA_GUID,
            "is_selected": is_pe1_selected,
            "is_active": (is_pe1_selected and self.is_active),
            "active_viewers": len(formatted_clients) if is_pe1_selected else 0,
            "buffer": buffer_metrics_active if is_pe1_selected else buffer_metrics_idle,
            "clients": formatted_clients if is_pe1_selected else []
        }

        return {
            "success": True,
            "timestamp": now,
            "active_channel_id": f"PE{self.camera_index}",
            "is_active": self.is_active,
            "total_active_viewers": len(formatted_clients),
            "channels": [pe0_channel, pe1_channel],
            "raw_stats": sip_stats
        }
