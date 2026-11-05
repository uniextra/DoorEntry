"""
Receptor y decodificador de vídeo en tiempo real para Legrand / BTicino Classe 100X.
Recibe paquetes SRTP (RFC 3711 / RFC 6184) por UDP en el puerto 9078,
realiza UDP hole punching hacia el relay de medios de Eliot/Flexisip,
mantiene un keepalive continuo cada 2.0s para evitar la expiración del NAT UDP,
retransmite los paquetes al puerto interno para descifrado SRTP con FFmpeg nativo,
y extrae fotogramas JPEG continuos que se ingestan en CameraManager para el streaming web en vivo.
"""

import os
import sys
import time
import socket
import logging
import threading
import subprocess
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger("video_receiver")

DEFAULT_RTP_PORT = int(os.environ.get("RTP_VIDEO_PORT", 9078))
DEFAULT_AUDIO_RTP_PORT = int(os.environ.get("RTP_AUDIO_PORT", 7076))
INTERNAL_RTP_PORT = int(os.environ.get("INTERNAL_RTP_PORT", 19078))


class VideoReceiver:
    """
    Servicio de recepción de vídeo SRTP por UDP para Classe 100X.
    1. Escucha en el puerto RTP (9078).
    2. Ejecuta UDP hole-punching hacia el servidor relay remoto negociado en el 200 OK.
    3. Hilo de keepalive dedicado que envía paquetes dummy periódicos cada 2s para mantener abierto el NAT.
    4. Retransmite paquetes a FFmpeg con perfil SRTP AES_CM_128_HMAC_SHA1_80.
    5. Lee fotogramas MJPEG de stdout y los envía a CameraManager vía on_frame callback.
    """
    def __init__(self, port: int = DEFAULT_RTP_PORT, internal_port: int = INTERNAL_RTP_PORT, on_frame: Optional[Callable[[bytes], None]] = None):
        self.port = port
        self.internal_port = internal_port
        self.on_frame = on_frame

        self.running = False
        self.sock: Optional[socket.socket] = None
        self.forward_sock: Optional[socket.socket] = None
        self.recv_thread: Optional[threading.Thread] = None
        self.keepalive_thread: Optional[threading.Thread] = None
        self.reader_thread: Optional[threading.Thread] = None
        self.ffmpeg_proc: Optional[subprocess.Popen] = None
        self.sdp_path: Optional[Path] = None

        self.remote_ip: Optional[str] = None
        self.remote_port: Optional[int] = None
        self.crypto_key: Optional[str] = None

        self.packets_received = 0
        self.packets_forwarded = 0
        self.bytes_received = 0
        self.frames_decoded = 0

        # Telemetría de búfer y rendimiento en tiempo real
        self._current_buffer_size = 0
        self.buffer_max_bytes = 5 * 1024 * 1024  # 5 MB
        self.latest_frame_size = 0
        self.latest_frame_time: float = 0.0
        self._frame_timestamps: list = []
        self._fps_lock = threading.Lock()

    def start(self, remote_ip: str, remote_port: int, crypto_key: str) -> bool:
        """
        Inicia la recepción de vídeo para los parámetros remotos negociados en el 200 OK.
        """
        if self.running:
            self.stop()

        self.remote_ip = remote_ip
        self.remote_port = remote_port
        self.crypto_key = crypto_key
        self.packets_received = 0
        self.packets_forwarded = 0
        self.bytes_received = 0
        self.frames_decoded = 0
        self._current_buffer_size = 0
        self.latest_frame_size = 0
        self.latest_frame_time = 0.0
        with self._fps_lock:
            self._frame_timestamps.clear()

        # 1. Crear SDP dinámico para descifrado SRTP con FFmpeg
        temp_dir = Path(os.environ.get("DATA_DIR", Path(__file__).parent.parent / "data"))
        temp_dir.mkdir(parents=True, exist_ok=True)
        self.sdp_path = temp_dir / f"stream_{self.internal_port}.sdp"

        sdp_content = (
            "v=0\r\n"
            "o=- 0 0 IN IP4 127.0.0.1\r\n"
            "s=Classe100X\r\n"
            "c=IN IP4 127.0.0.1\r\n"
            "t=0 0\r\n"
            f"m=video {self.internal_port} RTP/SAVP 96\r\n"
            "a=rtpmap:96 H264/90000\r\n"
            "a=fmtp:96 profile-level-id=42801F\r\n"
            f"a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:{crypto_key}\r\n"
        )
        self.sdp_path.write_text(sdp_content, encoding="ascii")
        logger.info(f"SDP generado en {self.sdp_path} con clave SRTP {crypto_key[:12]}...")

        # 2. Iniciar subproceso FFmpeg para decodificación continua a MJPEG
        cmd = [
            "ffmpeg",
            "-loglevel", "warning",
            "-protocol_whitelist", "file,crypto,data,udp,rtp,srtp",
            "-i", str(self.sdp_path.absolute()),
            "-an",
            "-f", "image2pipe",
            "-vcodec", "mjpeg",
            "-q:v", "3",
            "-"
        ]
        try:
            self.ffmpeg_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=10**6
            )
            logger.info(f"Subproceso FFmpeg inicializado (PID {self.ffmpeg_proc.pid})")
        except FileNotFoundError:
            logger.error("FFmpeg no encontrado en PATH del sistema.")
            self._cleanup_sdp()
            return False
        except Exception as e:
            logger.error(f"Error iniciando proceso FFmpeg: {e}")
            self._cleanup_sdp()
            return False

        # 3. Iniciar socket UDP en el puerto exterior (9078)
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind(("0.0.0.0", self.port))
            self.sock.settimeout(0.5)

            self.forward_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except Exception as e:
            logger.error(f"Error abriendo socket UDP {self.port}: {e}")
            self.stop()
            return False

        self.running = True

        # 4. Lanzar hilo de lectura de fotogramas desde stdout de FFmpeg
        self.reader_thread = threading.Thread(target=self._read_mjpeg_frames, daemon=True)
        self.reader_thread.start()

        # 5. Lanzar hilo de recepción y forwarding UDP
        self.recv_thread = threading.Thread(target=self._udp_forward_loop, daemon=True)
        self.recv_thread.start()

        # 6. Lanzar hilo dedicado de keepalive y hole-punching continuo
        self.keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
        self.keepalive_thread.start()

        logger.info(f"VideoReceiver activo: recibiendo en {self.port}, perforando NAT hacia {remote_ip}:{remote_port}")
        return True

    def _keepalive_loop(self):
        """
        Envía paquetes RTP dummy al Media Relay de forma continua cada 2.0 segundos
        para asegurar que las tablas NAT UDP del router y cortafuegos se mantengan abiertas
        durante TODA la llamada.
        """
        # Hole-punching inicial en ráfaga rápida
        if self.sock and self.remote_ip and self.remote_port:
            for _ in range(5):
                try:
                    self.sock.sendto(b'\x80\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00', (self.remote_ip, self.remote_port))
                except Exception:
                    pass
                time.sleep(0.02)

        while self.running:
            try:
                time.sleep(2.0)
                if not self.running:
                    break
                if self.sock and self.remote_ip and self.remote_port:
                    self.sock.sendto(b'\x80\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00', (self.remote_ip, self.remote_port))
            except Exception as e:
                if self.running:
                    logger.debug(f"Aviso en keepalive UDP: {e}")

    def _cleanup_sdp(self):
        """Elimina de forma segura el archivo SDP temporal si existe."""
        if self.sdp_path:
            try:
                if self.sdp_path.exists():
                    self.sdp_path.unlink()
            except Exception as e:
                logger.debug(f"Aviso limpiando archivo SDP {self.sdp_path}: {e}")
            self.sdp_path = None

    def _read_mjpeg_frames(self):
        """Lee fotogramas JPEG delimitados por cabecera SOI (0xFFD8) y EOI (0xFFD9)."""
        buffer = bytearray()
        stdout = self.ffmpeg_proc.stdout if self.ffmpeg_proc else None
        while self.running and stdout:
            try:
                chunk = stdout.read(8192)
                if not chunk:
                    break
                buffer.extend(chunk)
                self._current_buffer_size = len(buffer)

                # Protección contra desbordamiento de búfer por paquetes corruptos
                if len(buffer) > self.buffer_max_bytes:
                    logger.warning("Búfer MJPEG excedió 5MB sin marcadores válidos. Reiniciando búfer.")
                    buffer = bytearray()
                    self._current_buffer_size = 0
                    continue

                # Extraer fotogramas JPEG completos
                while True:
                    soi = buffer.find(b"\xff\xd8")
                    if soi == -1:
                        buffer = bytearray()
                        self._current_buffer_size = 0
                        break
                    if soi > 0:
                        buffer = buffer[soi:]
                        self._current_buffer_size = len(buffer)

                    eoi = buffer.find(b"\xff\xd9", 2)
                    if eoi != -1:
                        frame_bytes = bytes(buffer[:eoi+2])
                        buffer = buffer[eoi+2:]
                        self._current_buffer_size = len(buffer)
                        if len(frame_bytes) > 200:
                            self.frames_decoded += 1
                            now = time.time()
                            self.latest_frame_size = len(frame_bytes)
                            self.latest_frame_time = now
                            with self._fps_lock:
                                self._frame_timestamps.append(now)
                                cutoff = now - 3.0
                                while self._frame_timestamps and self._frame_timestamps[0] < cutoff:
                                    self._frame_timestamps.pop(0)

                            if self.on_frame:
                                try:
                                    self.on_frame(frame_bytes)
                                except Exception as fe:
                                    logger.debug(f"Error despachando fotograma: {fe}")
                    else:
                        break
            except Exception as e:
                logger.debug(f"Lectura de fotogramas finalizada: {e}")
                break

    def get_stats(self) -> dict:
        """Devuelve telemetría en tiempo real del receptor, búfer y tasa de fotogramas."""
        now = time.time()
        with self._fps_lock:
            cutoff = now - 3.0
            while self._frame_timestamps and self._frame_timestamps[0] < cutoff:
                self._frame_timestamps.pop(0)
            n_frames = len(self._frame_timestamps)
            if n_frames >= 2:
                time_span = max(0.1, self._frame_timestamps[-1] - self._frame_timestamps[0])
                fps = round((n_frames - 1) / time_span, 1)
            elif n_frames == 1:
                fps = 1.0
            else:
                fps = 0.0

        buffer_pct = round((self._current_buffer_size / self.buffer_max_bytes) * 100, 2)
        frame_age_ms = int((now - self.latest_frame_time) * 1000) if self.latest_frame_time else 0

        return {
            "running": self.running,
            "packets_received": self.packets_received,
            "packets_forwarded": self.packets_forwarded,
            "bytes_received": self.bytes_received,
            "frames_decoded": self.frames_decoded,
            "buffer_current_bytes": self._current_buffer_size,
            "buffer_max_bytes": self.buffer_max_bytes,
            "buffer_pct": buffer_pct,
            "latest_frame_size": self.latest_frame_size,
            "latest_frame_age_ms": frame_age_ms,
            "fps_estimate": fps
        }

    def _udp_forward_loop(self):
        """Bucle de recepción UDP con forwarding hacia FFmpeg local."""
        while self.running and self.sock:
            try:
                data, addr = self.sock.recvfrom(65535)
                if not data:
                    continue
                self.packets_received += 1
                self.bytes_received += len(data)

                # Retransmitir al puerto interno de FFmpeg
                if self.forward_sock and self.running:
                    try:
                        self.forward_sock.sendto(data, ('127.0.0.1', self.internal_port))
                        self.packets_forwarded += 1
                    except Exception:
                        pass

                if self.packets_received % 100 == 1:
                    logger.debug(f"RTP Video: {self.packets_received} paquetes ({self.bytes_received} bytes) de {addr}, fotogramas: {self.frames_decoded}")

            except socket.timeout:
                continue
            except Exception as e:
                if not self.running:
                    break
                logger.debug(f"Aviso transitorio en socket UDP: {e}")
                time.sleep(0.05)

    def stop(self):
        """Detiene de forma limpia los procesos de recepción, keepalive y decodificación."""
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

        if self.forward_sock:
            try:
                self.forward_sock.close()
            except Exception:
                pass
            self.forward_sock = None

        if self.ffmpeg_proc:
            try:
                self.ffmpeg_proc.terminate()
                self.ffmpeg_proc.wait(timeout=0.5)
            except Exception:
                try:
                    self.ffmpeg_proc.kill()
                except Exception:
                    pass
            self.ffmpeg_proc = None

        self._cleanup_sdp()
        self._current_buffer_size = 0
        with self._fps_lock:
            self._frame_timestamps.clear()

        logger.info(f"VideoReceiver detenido. Paquetes: {self.packets_received}, Fotogramas decodificados: {self.frames_decoded}")
