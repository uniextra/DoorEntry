// Door Entry Web Client - Lógica de Frontend Autónomo (Classe 100X)

const state = {
  authenticated: true,
  mode: "autonomous",
  username: "",
  plants: [],
  gateways: [],
  selectedPlantId: "",
  selectedGatewayId: "",
  devices: { locks: [], lights: [], cameras: [] },
  isCalling: false,
  isActivating: false,
  callStartTime: 0,
  callTimer: null,
  callRemainingSeconds: 30,
  callState: "standby", // standby | ringing | connected
  incomingCall: false,
  certificate: { exists: false, valid: false, days_remaining: 0 },
  pollInterval: null,
  telemetryInterval: null,
  telemetryData: null
};

// =========================================================================
// INTERNACIONALIZACIÓN (i18n): ESPAÑOL / ENGLISH
// =========================================================================

let currentLang = localStorage.getItem("doorentry_lang") || "es";

const i18n = {
  es: {
    // Header
    cert_badge_title: "Estado del certificado de cliente mTLS",
    cert_checking: "Cert mTLS: Comprobando...",
    cert_valid: "Cert mTLS: Válido ({days} días)",
    cert_invalid: "Cert mTLS: No Válido / Ausente",
    cert_expired: "Cert mTLS: Caducado",
    cert_expired_tip: "El certificado ha expirado. Pulse para renovarlo.",
    cert_not_found: "Cert mTLS: No encontrado",
    cert_not_found_tip: "client.crt no está instalado. Pulse para generarlo.",
    cert_issued_to: "Emitido para",
    cert_expires: "Caduca",
    btn_renew_cert: "🔑 Generar / Renovar client.crt",
    btn_notifications_title: "Activar notificaciones de escritorio",
    btn_notifications: "🔔 Notificaciones",
    btn_notifications_active: "🔔 Notificaciones: Activas",
    btn_test_ring_title: "Probar sonido y aviso de timbre de casa",
    btn_test_ring: "🔔 Probar Timbre",
    status_autonomous: "Videoportero Autónomo",
    status_connected: "Conectado a la Nube",
    btn_logout: "Cerrar Sesión",

    // Banner de Timbre
    incoming_title: "¡ESTÁN LLAMANDO AL TIMBRE EN LA PLACA DE CALLE!",
    incoming_desc: "{caller} • Llamada entrante en curso",
    incoming_time_left: "Tiempo para contestar:",
    btn_answer: "📹 Atender y Ver Vídeo",
    btn_open_direct: "🔓 Abrir Puerta Directamente",
    btn_reject: "📵 Rechazar / Colgar",

    // Selector
    lbl_plant: "Instalación / Planta:",
    opt_loading_plants: "Cargando instalaciones...",
    lbl_gateway: "Videoportero / Pasarela:",
    opt_select_gateway: "Seleccionar pasarela...",
    btn_refresh_title: "Actualizar dispositivos",
    btn_refresh: "🔄 Actualizar",

    // Digital Twin Display
    lcd_standby_desc: "MODO EN ESPERA",
    lcd_ready: "SCS LISTO • ESPERA",
    lcd_ring_title: "¡TIMBRE EN CASA!",
    lcd_ring_desc: "Toca el botón 📞 (descolgar) para responder",
    badge_live: "● EN DIRECTO",
    btn_snapshot_title: "Capturar imagen actual",
    btn_snapshot: "📸 Foto",
    btn_fullscreen_title: "Pantalla completa",
    led_wifi_title: "Estado de conexión Wi-Fi / Pasarela",
    led_bell_title: "Indicador de timbre / llamada entrante",

    // Hotspots
    tip_dot1: "Punto 1: Alternar Cámara (PE0 / PE1)",
    tip_dot2: "Punto 2: Relé Auxiliar",
    tip_dot3: "Punto 3: Cerradura Secundaria (PE1)",
    tip_dot4: "Punto 4: Capturar Fotografía",
    tip_star: "Estrella: Encender Luz de Escalera (*8*21*114##)",
    tip_key: "Llave: Abrir Puerta Principal (*8*19*20##)",
    tip_eye: "Ojo: Mirar Calle (Auto-encendido 30s)",
    tip_talk: "Descolgar / Atender Timbre / Hablar (PTT)",
    tip_hangup: "Colgar / Liberar Canal del Edificio",

    // Toasts
    toast_door_open: "Puerta Abierta (PE0)",
    toast_door_open2: "Cerradura 2 (PE1)",
    toast_street_view: "Mirar Calle (30s)",
    toast_switching_cam: "Cambiando cámara...",
    toast_light: "Luz de Escalera",
    toast_answered: "Llamada Atendida",
    toast_ptt: "Micrófono (PTT)",
    toast_channel_freed: "Canal SCS Liberado",
    toast_cycle_cam: "Alternando Cámara",
    toast_aux2: "Auxiliar 2 (Dir 115)",
    toast_snapshot: "Foto Capturada",

    // Leyenda Interfono
    lbl_status: "Estado:",
    status_standby: "En Espera (Standby)",
    status_live: "● En Directo ({cam})",
    status_ringing: "¡TIMBRE ENTRANTE EN CASA!",
    lbl_camera: "Cámara:",
    btn_cycle_title: "Alternar entre cámaras",
    btn_cycle: "🔄 Cambiar",

    // Panel Avanzado
    accordion_title: "Opciones Avanzadas, Diagnóstico y Teclado Manual",
    adv_open_door: "Abrir Puerta",
    adv_open_door_sub: "PE0 (Dir 20)",
    adv_light: "Luz Escalera",
    adv_light_sub: "Dir 114",
    adv_street_view: "Mirar Calle",
    adv_street_view_sub: "Auto-encendido 30s",
    adv_talk: "Hablar (PTT)",
    adv_talk_sub: "Micrófono",
    adv_hangup: "Colgar",
    adv_hangup_sub: "Liberar SCS",
    adv_diagnostics: "Test Pasarela",
    adv_diagnostics_sub: "Ping keepalive",
    door_unlocked_msg: "¡Puerta Desbloqueada!",

    // Telemetría
    telemetry_title: "Canales de Vídeo & Búfer de Streaming",
    telemetry_sub: "Monitoreo en vivo de memoria intermedia y sesiones de clientes por canal",
    telemetry_live: "EN VIVO",
    btn_detailed: "⛶ Modo Detallado",
    btn_hide_detailed: "✕ Ocultar Detalles",
    channels_loading: "Cargando estado de canales y búfer...",
    telemetry_details_title: "🔬 Métricas de Recepción SRTP / FFmpeg Interno",
    lbl_udp_port: "Puerto UDP RTP Externo",
    lbl_packets_received: "Paquetes RTP Recibidos",
    lbl_bytes_received: "Bytes RTP Recibidos",
    lbl_frames_decoded: "Fotogramas Decodificados FFmpeg",
    lbl_pipe_cap: "Capacidad Máxima Búfer Pipe",
    lbl_poll_rate: "Refresco de Telemetría",

    // Topología
    topology_title: "Dispositivos Configurados en la Instalación",
    topology_count_loading: "Cargando...",
    devices_configured: "{count} dispositivos configurados",
    lbl_bus_address: "Dirección Bus",
    act_open: "Abrir",
    act_toggle: "Activar",

    // Consola
    console_title: "Consola de Tramas OpenWebNet",
    placeholder_cmd: "Ej: *8*19*20## o *8*21*114##",
    btn_send: "Enviar Trama",
    log_init: "[SISTEMA] Cliente web inicializado. Listo para operar.",

    // Modal
    modal_title: "Generar / Renovar client.crt (mTLS)",
    modal_desc: "Introduce tus credenciales de Legrand Eliot para enrolar un certificado oficial con la CA de Legrand. El videoportero recargará el certificado mTLS inmediatamente.",
    lbl_email: "Correo de cuenta Legrand:",
    lbl_password: "Contraseña:",
    btn_submit_cert: "🔐 Generar nuevo client.crt",

    // Footer
    footer_text: "Door Entry Web Client • Compatible con Legrand & BTicino Classe 100X, 300X y HomeTouch • Desplegado en Docker"
  },
  en: {
    // Header
    cert_badge_title: "mTLS Client Certificate Status",
    cert_checking: "mTLS Cert: Checking...",
    cert_valid: "mTLS Cert: Valid ({days} days)",
    cert_invalid: "mTLS Cert: Invalid / Missing",
    cert_expired: "mTLS Cert: Expired",
    cert_expired_tip: "The certificate has expired. Click to renew.",
    cert_not_found: "mTLS Cert: Not found",
    cert_not_found_tip: "client.crt is missing. Click to generate.",
    cert_issued_to: "Issued to",
    cert_expires: "Expires",
    btn_renew_cert: "🔑 Generate / Renew client.crt",
    btn_notifications_title: "Enable desktop notifications",
    btn_notifications: "🔔 Notifications",
    btn_notifications_active: "🔔 Notifications: Active",
    btn_test_ring_title: "Test doorbell chime and alert",
    btn_test_ring: "🔔 Test Doorbell",
    status_autonomous: "Autonomous Intercom",
    status_connected: "Connected to Cloud",
    btn_logout: "Sign Out",

    // Banner de Timbre
    incoming_title: "DOORBELL IS RINGING AT THE STREET PANEL!",
    incoming_desc: "{caller} • Incoming call in progress",
    incoming_time_left: "Time to answer:",
    btn_answer: "📹 Answer & View Video",
    btn_open_direct: "🔓 Unlock Door Directly",
    btn_reject: "📵 Reject / Hang Up",

    // Selector
    lbl_plant: "Installation / Site:",
    opt_loading_plants: "Loading sites...",
    lbl_gateway: "Intercom / Gateway:",
    opt_select_gateway: "Select gateway...",
    btn_refresh_title: "Refresh devices",
    btn_refresh: "🔄 Refresh",

    // Digital Twin Display
    lcd_standby_desc: "STANDBY MODE",
    lcd_ready: "SCS READY • STANDBY",
    lcd_ring_title: "DOORBELL RINGING!",
    lcd_ring_desc: "Touch 📞 (talk) button to answer",
    badge_live: "● LIVE STREAM",
    btn_snapshot_title: "Take current snapshot",
    btn_snapshot: "📸 Photo",
    btn_fullscreen_title: "Fullscreen",
    led_wifi_title: "Wi-Fi / Gateway Connection Status",
    led_bell_title: "Doorbell Ring / Incoming Call Indicator",

    // Hotspots
    tip_dot1: "Dot 1: Cycle Camera (PE0 / PE1)",
    tip_dot2: "Dot 2: Auxiliary Relay",
    tip_dot3: "Dot 3: Secondary Lock (PE1)",
    tip_dot4: "Dot 4: Take Snapshot",
    tip_star: "Star: Staircase Light (*8*21*114##)",
    tip_key: "Key: Unlock Main Door (*8*19*20##)",
    tip_eye: "Eye: Street View (30s auto-turn-on)",
    tip_talk: "Off-hook / Answer Call / Talk (PTT)",
    tip_hangup: "Hang Up / Release Building Bus",

    // Toasts
    toast_door_open: "Door Unlocked (PE0)",
    toast_door_open2: "Secondary Lock (PE1)",
    toast_street_view: "Street View (30s)",
    toast_switching_cam: "Switching camera...",
    toast_light: "Staircase Light",
    toast_answered: "Call Answered",
    toast_ptt: "Microphone (PTT)",
    toast_channel_freed: "SCS Bus Freed",
    toast_cycle_cam: "Cycling Camera",
    toast_aux2: "Auxiliary 2 (Addr 115)",
    toast_snapshot: "Photo Captured",

    // Leyenda Interfono
    lbl_status: "Status:",
    status_standby: "Standby",
    status_live: "● Live ({cam})",
    status_ringing: "DOORBELL RINGING!",
    lbl_camera: "Camera:",
    btn_cycle_title: "Cycle between cameras",
    btn_cycle: "🔄 Switch",

    // Panel Avanzado
    accordion_title: "Advanced Options, Diagnostics & Manual Keypad",
    adv_open_door: "Unlock Door",
    adv_open_door_sub: "PE0 (Addr 20)",
    adv_light: "Staircase Light",
    adv_light_sub: "Addr 114",
    adv_street_view: "Street View",
    adv_street_view_sub: "30s auto-on",
    adv_talk: "Talk (PTT)",
    adv_talk_sub: "Microphone",
    adv_hangup: "Hang Up",
    adv_hangup_sub: "Release SCS",
    adv_diagnostics: "Gateway Test",
    adv_diagnostics_sub: "Keepalive ping",
    door_unlocked_msg: "Door Unlocked!",

    // Telemetría
    telemetry_title: "Video Channels & Streaming Buffer",
    telemetry_sub: "Live buffer and connected client telemetry per video channel",
    telemetry_live: "LIVE",
    btn_detailed: "⛶ Detailed Mode",
    btn_hide_detailed: "✕ Hide Details",
    channels_loading: "Loading channels & buffer state...",
    telemetry_details_title: "🔬 Internal SRTP / FFmpeg Reception Metrics",
    lbl_udp_port: "External UDP RTP Port",
    lbl_packets_received: "RTP Packets Received",
    lbl_bytes_received: "RTP Bytes Received",
    lbl_frames_decoded: "FFmpeg Frames Decoded",
    lbl_pipe_cap: "Max Pipe Buffer Capacity",
    lbl_poll_rate: "Telemetry Refresh Rate",

    // Topología
    topology_title: "Configured Installation Devices",
    topology_count_loading: "Loading...",
    devices_configured: "{count} devices configured",
    lbl_bus_address: "Bus Address",
    act_open: "Unlock",
    act_toggle: "Toggle",

    // Consola
    console_title: "OpenWebNet Frame Console",
    placeholder_cmd: "E.g. *8*19*20## or *8*21*114##",
    btn_send: "Send Frame",
    log_init: "[SYSTEM] Web client initialized. Ready to operate.",

    // Modal
    modal_title: "Generate / Renew client.crt (mTLS)",
    modal_desc: "Enter your Legrand Eliot credentials to enroll an official client certificate signed by Legrand CA. The intercom bridge will reload the mTLS certificate immediately.",
    lbl_email: "Legrand Account Email:",
    lbl_password: "Password:",
    btn_submit_cert: "🔐 Generate new client.crt",

    // Footer
    footer_text: "Door Entry Web Client • Compatible with Legrand & BTicino Classe 100X, 300X and HomeTouch • Deployed in Docker"
  }
};

function t(key, params = {}) {
  const dict = i18n[currentLang] || i18n.es;
  let str = dict[key] || (i18n.es[key] || key);
  for (const [k, v] of Object.entries(params)) {
    str = str.replace(`{${k}}`, v);
  }
  return str;
}

function setLanguage(lang) {
  currentLang = (lang === "en") ? "en" : "es";
  localStorage.setItem("doorentry_lang", currentLang);
  document.documentElement.lang = currentLang;

  const btnEs = document.getElementById("btnLangEs");
  const btnEn = document.getElementById("btnLangEn");
  if (btnEs && btnEn) {
    btnEs.classList.toggle("active", currentLang === "es");
    btnEn.classList.toggle("active", currentLang === "en");
  }

  // Actualizar elementos con data-i18n
  document.querySelectorAll("[data-i18n]").forEach(el => {
    const k = el.getAttribute("data-i18n");
    if (k) el.textContent = t(k);
  });

  // Actualizar atributos title
  document.querySelectorAll("[data-i18n-title]").forEach(el => {
    const k = el.getAttribute("data-i18n-title");
    if (k) el.title = t(k);
  });

  // Actualizar atributos placeholder
  document.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
    const k = el.getAttribute("data-i18n-placeholder");
    if (k) el.placeholder = t(k);
  });

  // Actualizar reloj LCD con el idioma actual
  if (typeof updateDigitalTwinClock === "function") {
    updateDigitalTwinClock();
  }

  // Actualizar badge de certificado si está disponible
  if (state.certificate) {
    renderCertificateBadge(state.certificate);
  }

  // Actualizar etiqueta del estado del interfono
  const intercomStateLabel = document.getElementById("intercomStateLabel");
  if (intercomStateLabel) {
    if (state.callState === "ringing") {
      intercomStateLabel.textContent = t("status_ringing");
    } else if (state.isCalling) {
      const camIdx = parseInt(selectCam ? selectCam.value : 0) || 0;
      const camShort = AVAILABLE_CAMERAS[camIdx] ? AVAILABLE_CAMERAS[camIdx].short : `PE${camIdx}`;
      intercomStateLabel.textContent = t("status_live", { cam: camShort });
    } else {
      intercomStateLabel.textContent = t("status_standby");
    }
  }

  // Re-renderizar dispositivos si están presentes
  if (state.devices && (state.devices.locks?.length || state.devices.lights?.length)) {
    renderDevices();
  }
}

// Elementos DOM Principales
const authSection = document.getElementById("authSection");
const dashboardSection = document.getElementById("dashboardSection");
const connectionStatus = document.getElementById("connectionStatus");
const userMenu = document.getElementById("userMenu");
const userEmail = document.getElementById("userEmail");
const btnLogout = document.getElementById("btnLogout");

// Certificados mTLS
const certStatusBadge = document.getElementById("certStatusBadge");
const certText = document.getElementById("certText");
const btnOpenCertModal = document.getElementById("btnOpenCertModal");
const certModal = document.getElementById("certModal");
const btnCloseCertModal = document.getElementById("btnCloseCertModal");
const certGenForm = document.getElementById("certGenForm");
const inputCertEmail = document.getElementById("inputCertEmail");
const inputCertPassword = document.getElementById("inputCertPassword");
const certModalAlert = document.getElementById("certModalAlert");
const btnSubmitCert = document.getElementById("btnSubmitCert");
const certSpinner = document.getElementById("certSpinner");
const certCurrentDetails = document.getElementById("certCurrentDetails");
const btnEnableNotifications = document.getElementById("btnEnableNotifications");
const btnSimulateCall = document.getElementById("btnSimulateCall");

// Banner de Timbre Entrante
const incomingCallBanner = document.getElementById("incomingCallBanner");
const incomingCallerText = document.getElementById("incomingCallerText");
const ringCountdown = document.getElementById("ringCountdown");
const btnAnswerCall = document.getElementById("btnAnswerCall");
const btnQuickDoorOpen = document.getElementById("btnQuickDoorOpen");
const btnRejectCall = document.getElementById("btnRejectCall");

// Selectores
const selectPlant = document.getElementById("selectPlant");
const selectGateway = document.getElementById("selectGateway");
const btnRefresh = document.getElementById("btnRefresh");

// Botones de Acción Rápida
const btnOpenDoor = document.getElementById("btnOpenDoor");
const doorCountdown = document.getElementById("doorCountdown");
const btnToggleLight = document.getElementById("btnToggleLight");
const btnCameraTurnOn = document.getElementById("btnCameraTurnOn");
const btnDiagnostics = document.getElementById("btnDiagnostics");

// Visor de Vídeo
const videoViewport = document.getElementById("videoViewport");
const videoPlaceholder = document.getElementById("videoPlaceholder");
const cameraStreamImg = document.getElementById("cameraStreamImg");
const videoOverlay = document.getElementById("videoOverlay");
const callCountdown = document.getElementById("callCountdown");
const btnCaptureSnapshot = document.getElementById("btnCaptureSnapshot");
const btnFullscreen = document.getElementById("btnFullscreen");
const btnCycleCam = document.getElementById("btnCycleCam");
const streamStatusBadge = document.getElementById("streamStatusBadge");
const btnStartCall = document.getElementById("btnStartCall");
const btnTalk = document.getElementById("btnTalk");
const btnEndCall = document.getElementById("btnEndCall");
const selectCam = document.getElementById("selectCam");

// Telemetría de Canales y Búfer
const channelsContainer = document.getElementById("channelsContainer");
const telemetryPulseBadge = document.getElementById("telemetryPulseBadge");
const telemetryPulseText = document.getElementById("telemetryPulseText");
const btnToggleDetailedTelemetry = document.getElementById("btnToggleDetailedTelemetry");
const detailedTelemetryPanel = document.getElementById("detailedTelemetryPanel");
const telemetryRtpPort = document.getElementById("telemetryRtpPort");
const telemetryRtpPackets = document.getElementById("telemetryRtpPackets");
const telemetryRtpBytes = document.getElementById("telemetryRtpBytes");
const telemetryFramesDecoded = document.getElementById("telemetryFramesDecoded");
const telemetryPollRate = document.getElementById("telemetryPollRate");

const devicesList = document.getElementById("devicesList");
const devicesCount = document.getElementById("devicesCount");

// Consola
const toggleConsole = document.getElementById("toggleConsole");
const consoleBody = document.getElementById("consoleBody");
const consoleArrow = document.getElementById("consoleArrow");
const inputCustomCommand = document.getElementById("inputCustomCommand");
const btnSendCustom = document.getElementById("btnSendCustom");
const logOutput = document.getElementById("logOutput");

// Singleton Web Audio API para evitar fugas de contexto (límite del navegador: 6 contextos)
function getAudioContext() {
  if (!window._audioCtx) {
    const AudioCtxClass = window.AudioContext || window.webkitAudioContext;
    if (AudioCtxClass) {
      window._audioCtx = new AudioCtxClass();
    }
  }
  if (window._audioCtx && window._audioCtx.state === "suspended") {
    window._audioCtx.resume().catch(() => {});
  }
  return window._audioCtx;
}

// Utilidad de Audio Feedback (Sintetizador Web Audio API)
function playTone(freq, duration = 0.15, type = "sine") {
  try {
    const audioCtx = getAudioContext();
    if (!audioCtx) return;
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, audioCtx.currentTime);
    gain.gain.setValueAtTime(0.2, audioCtx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + duration);
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.start();
    osc.stop(audioCtx.currentTime + duration);
  } catch (e) {
    // Si el navegador bloquea audio antes de interacción
  }
}

// Timbre "Ding-Dong" tradicional para llamada entrante
function playDoorbellChime() {
  try {
    const audioCtx = getAudioContext();
    if (!audioCtx) return;
    const now = audioCtx.currentTime;

    // "Ding" (E5 ~659 Hz)
    const osc1 = audioCtx.createOscillator();
    const gain1 = audioCtx.createGain();
    osc1.type = "sine";
    osc1.frequency.setValueAtTime(659.25, now);
    gain1.gain.setValueAtTime(0.4, now);
    gain1.gain.exponentialRampToValueAtTime(0.001, now + 0.8);
    osc1.connect(gain1);
    gain1.connect(audioCtx.destination);
    osc1.start(now);
    osc1.stop(now + 0.8);

    // "Dong" (C5 ~523 Hz) con desfase de 0.35s
    const osc2 = audioCtx.createOscillator();
    const gain2 = audioCtx.createGain();
    osc2.type = "sine";
    osc2.frequency.setValueAtTime(523.25, now + 0.35);
    gain2.gain.setValueAtTime(0.4, now + 0.35);
    gain2.gain.exponentialRampToValueAtTime(0.001, now + 1.4);
    osc2.connect(gain2);
    gain2.connect(audioCtx.destination);
    osc2.start(now + 0.35);
    osc2.stop(now + 1.4);
  } catch (e) {
    console.warn("AudioContext bloqueado:", e);
  }
}

// Desbloqueo proactivo de AudioContext en cualquier interacción del usuario
document.addEventListener("click", () => {
  getAudioContext();
}, { passive: true });
document.addEventListener("keydown", () => {
  getAudioContext();
}, { passive: true });

// Alertas de Timbre Continuo (Chime repetitivo y parpadeo de pestaña)
let ringChimeInterval = null;
let ringTitleInterval = null;
let originalDocumentTitle = document.title || "Door Entry Web | Legrand & BTicino Intercom";

function startRingingAlerts(caller) {
  playDoorbellChime();
  notifyDoorbell(caller);
  if (navigator.vibrate) {
    try { navigator.vibrate([300, 150, 300, 150, 300]); } catch (e) {}
  }
  if (!ringChimeInterval) {
    ringChimeInterval = setInterval(() => {
      playDoorbellChime();
    }, 3500);
  }
  if (!ringTitleInterval) {
    let toggle = false;
    ringTitleInterval = setInterval(() => {
      toggle = !toggle;
      document.title = toggle ? "🔔 ¡¡TIMBRE EN CASA!! 🔔" : "🚪 Door Entry Web";
    }, 700);
  }
}

function stopRingingAlerts() {
  if (ringChimeInterval) {
    clearInterval(ringChimeInterval);
    ringChimeInterval = null;
  }
  if (ringTitleInterval) {
    clearInterval(ringTitleInterval);
    ringTitleInterval = null;
    document.title = originalDocumentTitle;
  }
}

// Notificación de escritorio del navegador
function notifyDoorbell(caller = "Placa de calle PE0") {
  if ("Notification" in window && Notification.permission === "granted") {
    try {
      const notif = new Notification("🔔 ¡Están llamando al timbre!", {
        body: `${caller} - Alguien ha tocado el timbre en la placa exterior. Haz clic para atender o abrir la puerta.`,
        requireInteraction: true,
        icon: "data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🔔</text></svg>"
      });
      notif.onclick = () => {
        window.focus();
        notif.close();
      };
    } catch (e) {
      console.warn("Aviso en notificación:", e);
    }
  }
}

function updateNotificationButton() {
  if (!btnEnableNotifications) return;
  if (!("Notification" in window)) {
    btnEnableNotifications.style.display = "none";
    return;
  }
  if (Notification.permission === "granted") {
    btnEnableNotifications.textContent = "🔔 Notificaciones: Activas";
    btnEnableNotifications.style.borderColor = "rgba(16, 185, 129, 0.4)";
    btnEnableNotifications.style.color = "#34d399";
  } else if (Notification.permission === "denied") {
    btnEnableNotifications.textContent = "🔕 Notificaciones: Bloqueadas";
    btnEnableNotifications.style.borderColor = "rgba(239, 68, 68, 0.4)";
    btnEnableNotifications.style.color = "#f87171";
  } else {
    btnEnableNotifications.textContent = "🔔 Activar Notificaciones";
    btnEnableNotifications.style.borderColor = "rgba(59, 130, 246, 0.4)";
    btnEnableNotifications.style.color = "#60a5fa";
  }
}

if (btnEnableNotifications) {
  btnEnableNotifications.addEventListener("click", async () => {
    getAudioContext();
    if ("Notification" in window) {
      try {
        const perm = await Notification.requestPermission();
        updateNotificationButton();
        if (perm === "granted") {
          appendLog("Notificaciones de escritorio concedidas.", "success");
          new Notification("🔔 Notificaciones Activas", {
            body: "Door Entry te avisará con sonido y aviso cuando toquen el timbre en tu casa.",
          });
        } else {
          appendLog("Permiso de notificaciones denegado o no concedido.", "warning");
        }
      } catch (e) {
        appendLog(`Error solicitando notificaciones: ${e.message}`, "error");
      }
    }
  });
}

if (btnSimulateCall) {
  btnSimulateCall.addEventListener("click", async () => {
    getAudioContext();
    appendLog("Probando timbre entrante (simulación)...", "cmd");
    try {
      const res = await fetch("/api/call/simulate", { method: "POST" });
      const data = await res.json();
      if (data.success) {
        appendLog("Prueba de timbre enviada. Sonando y mostrando aviso en pantalla...", "success");
      } else {
        appendLog(`Error en prueba de timbre: ${data.error || 'Desconocido'}`, "error");
      }
    } catch (e) {
      appendLog(`Error de red en simulación: ${e.message}`, "error");
    }
  });
}

// Log en consola visual
function appendLog(msg, type = "info") {
  const time = new Date().toLocaleTimeString();
  const entry = document.createElement("div");
  entry.className = `log-entry ${type}`;
  entry.textContent = `[${time}] ${msg}`;
  if (logOutput) {
    logOutput.appendChild(entry);
    logOutput.scrollTop = logOutput.scrollHeight;
  }
}

// =========================================================================
// INICIALIZACIÓN AUTÓNOMA (100% SIN BLOQUEO DE LOGIN)
// =========================================================================

async function initAutonomousApp() {
  updateNotificationButton();

  // Entrar DIRECTAMENTE al Dashboard
  if (authSection) authSection.classList.add("hidden");
  if (dashboardSection) dashboardSection.classList.remove("hidden");

  if (connectionStatus) {
    connectionStatus.className = "status-indicator online";
    connectionStatus.querySelector(".status-text").textContent = "Videoportero Autónomo";
  }

  // Comprobar estado de certificado mTLS
  await checkCertificateStatus();

  // Cargar topología y dispositivos
  await loadPlants();

  // Iniciar sondeo en tiempo real de llamadas de timbre y estado SIP
  startStatusPolling();

  // Iniciar sondeo en tiempo real de telemetría de canales y búfer MJPEG
  startTelemetryPolling();

  appendLog("Videoportero autónomo Classe 100X listo. Canal del edificio en Standby.", "success");
}

// Consulta de estado de certificado mTLS
async function checkCertificateStatus() {
  try {
    const res = await fetch("/api/certificate/status");
    const cert = await res.json();
    state.certificate = cert;
    renderCertificateBadge(cert);
  } catch (e) {
    console.warn("No se pudo obtener estado del certificado:", e);
  }
}

function renderCertificateBadge(cert) {
  if (!certStatusBadge || !certText) return;
  if (cert.valid) {
    certStatusBadge.className = "cert-badge valid";
    certText.textContent = `🔐 ${t("cert_valid", { days: cert.days_remaining })}`;
    certStatusBadge.title = `${t("cert_issued_to")}: ${cert.subject}\n${t("cert_expires")}: ${cert.not_valid_after}`;
  } else if (cert.exists) {
    certStatusBadge.className = "cert-badge invalid";
    certText.textContent = `🔐 ${t("cert_expired")}`;
    certStatusBadge.title = t("cert_expired_tip");
  } else {
    certStatusBadge.className = "cert-badge invalid";
    certText.textContent = `⚠️ ${t("cert_not_found")}`;
    certStatusBadge.title = t("cert_not_found_tip");
  }
}

// Modal de Generación / Renovación de client.crt
if (btnOpenCertModal) {
  btnOpenCertModal.addEventListener("click", async () => {
    if (certModal) certModal.classList.remove("hidden");
    if (certModalAlert) certModalAlert.classList.add("hidden");
    if (certCurrentDetails) {
      const c = state.certificate || {};
      certCurrentDetails.innerHTML = "";

      const rowStatus = document.createElement("div");
      const strongStatus = document.createElement("strong");
      strongStatus.textContent = "Estado actual: ";
      rowStatus.appendChild(strongStatus);
      rowStatus.appendChild(document.createTextNode(c.valid ? "✅ Válido" : "❌ No válido o caducado"));
      certCurrentDetails.appendChild(rowStatus);

      const rowDays = document.createElement("div");
      const strongDays = document.createElement("strong");
      strongDays.textContent = "Días restantes: ";
      rowDays.appendChild(strongDays);
      rowDays.appendChild(document.createTextNode(`${c.days_remaining || 0} días`));
      certCurrentDetails.appendChild(rowDays);

      const rowExpiry = document.createElement("div");
      const strongExpiry = document.createElement("strong");
      strongExpiry.textContent = "Caducidad: ";
      rowExpiry.appendChild(strongExpiry);
      rowExpiry.appendChild(document.createTextNode(c.not_valid_after || "Desconocida"));
      certCurrentDetails.appendChild(rowExpiry);

      const rowSubj = document.createElement("div");
      const strongSubj = document.createElement("strong");
      strongSubj.textContent = "Sujeto: ";
      rowSubj.appendChild(strongSubj);
      const spanSubj = document.createElement("span");
      spanSubj.style.fontSize = "0.75rem";
      spanSubj.style.wordBreak = "break-all";
      spanSubj.textContent = c.subject || "Ninguno";
      rowSubj.appendChild(spanSubj);
      certCurrentDetails.appendChild(rowSubj);
    }
  });
}

if (btnCloseCertModal) {
  btnCloseCertModal.addEventListener("click", () => {
    if (certModal) certModal.classList.add("hidden");
  });
}

if (certGenForm) {
  certGenForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = inputCertEmail ? inputCertEmail.value.trim() : "";
    const pass = inputCertPassword ? inputCertPassword.value : "";
    if (!email || !pass) return;

    if (certSpinner) certSpinner.classList.remove("hidden");
    if (btnSubmitCert) btnSubmitCert.disabled = true;
    if (certModalAlert) {
      certModalAlert.className = "alert info";
      certModalAlert.textContent = "Autenticando en Azure B2C y firmando certificado con la CA de Legrand...";
      certModalAlert.classList.remove("hidden");
    }

    try {
      const res = await fetch("/api/certificate/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: email, password: pass })
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Error generando el certificado");
      }

      state.certificate = data.certificate || {};
      renderCertificateBadge(state.certificate);

      if (certModalAlert) {
        certModalAlert.className = "alert-success";
        certModalAlert.style.background = "rgba(16, 185, 129, 0.2)";
        certModalAlert.style.color = "#34d399";
        certModalAlert.textContent = "¡Certificado client.crt renovado con éxito! Demonio SIP recargado.";
      }
      playTone(1046.5, 0.2); // C6 éxito
      appendLog("Certificado client.crt generado y recargado en caliente.", "success");

      setTimeout(() => {
        if (certModal) certModal.classList.add("hidden");
        if (inputCertPassword) inputCertPassword.value = "";
      }, 2000);
    } catch (err) {
      if (certModalAlert) {
        certModalAlert.className = "alert-error";
        certModalAlert.textContent = err.message;
      }
      playTone(220, 0.3, "sawtooth");
      appendLog(`Error generando certificado: ${err.message}`, "error");
    } finally {
      if (certSpinner) certSpinner.classList.add("hidden");
      if (btnSubmitCert) btnSubmitCert.disabled = false;
    }
  });
}

// =========================================================================
// SONDEO EN TIEMPO REAL (TIMBRE ENTRANTE Y ESTADO SIP)
// =========================================================================

function startStatusPolling() {
  if (state.pollInterval) clearInterval(state.pollInterval);
  state.pollInterval = setInterval(async () => {
    try {
      const res = await fetch("/api/status");
      if (!res.ok) return;
      const data = await res.json();

      // 1. Estado de llamada o timbre
      handleCallStateChange(data);

      // 2. Si hay datos de certificado en status
      if (data.certificate) {
        state.certificate = { ...state.certificate, ...data.certificate };
        renderCertificateBadge(state.certificate);
      }
    } catch (e) {
      // Silenciar errores de red transitorios
    }
  }, 1500);
}

function handleCallStateChange(data) {
  const previousState = state.callState;
  state.callState = data.call_state || "standby";

  const intercomLedBell = document.getElementById("intercomLedBell");
  const lcdRingAlert = document.getElementById("lcdRingAlert");
  const intercomStateLabel = document.getElementById("intercomStateLabel");

  // Alguien está tocando el timbre en la calle
  if (data.call_state === "ringing") {
    if (incomingCallBanner) incomingCallBanner.classList.remove("hidden");
    if (ringCountdown) ringCountdown.textContent = (data.remaining_seconds || 45) + "s";
    if (incomingCallerText) {
      incomingCallerText.textContent = `${data.caller || 'Placa de calle PE0'} • Llamada entrante`;
    }

    if (intercomLedBell) intercomLedBell.classList.add("active");
    if (lcdRingAlert) lcdRingAlert.classList.remove("hidden");
    if (intercomStateLabel) intercomStateLabel.textContent = "¡TIMBRE ENTRANTE EN CASA!";

    // Si es el primer instante en que empieza a sonar
    if (previousState !== "ringing") {
      startRingingAlerts(data.caller || "Placa de calle PE0");
      appendLog("🔔 ¡TIMBRE ENTRANTE! Alguien toca el videoportero en la calle.", "error");
    }
  } else {
    // Si dejó de sonar
    stopRingingAlerts();
    if (incomingCallBanner && !incomingCallBanner.classList.contains("hidden")) {
      incomingCallBanner.classList.add("hidden");
    }
    if (intercomLedBell) intercomLedBell.classList.remove("active");
    if (lcdRingAlert) lcdRingAlert.classList.add("hidden");

    if (intercomStateLabel) {
      intercomStateLabel.textContent = state.isCalling ? "En Directo (Live)" : "En Espera (Standby)";
    }
  }

  // Si la cámara exterior finalizó llamada o timeout mientras teníamos el visor abierto
  const isGracePeriod = (Date.now() - (state.callStartTime || 0)) < 8000;
  if (state.isCalling && !state.isActivating && !isGracePeriod && data.camera_active === false && data.call_state === "standby") {
    appendLog("Llamada finalizada por el videoportero (BYE recibido). Canal del edificio libre.", "info");
    stopCameraStream(false);
  }
}

// Botones del Banner de Timbre
if (btnAnswerCall) {
  btnAnswerCall.addEventListener("click", async () => {
    stopRingingAlerts();
    appendLog("Atendiendo llamada de timbre y conectando vídeo...", "cmd");

    state.isActivating = true;
    state.isCalling = true;
    state.callStartTime = Date.now();

    if (incomingCallBanner) incomingCallBanner.classList.add("hidden");
    if (videoPlaceholder) videoPlaceholder.classList.remove("hidden");
    if (cameraStreamImg) cameraStreamImg.classList.add("hidden");
    if (streamStatusBadge) {
      streamStatusBadge.className = "badge-status live";
      streamStatusBadge.textContent = "● CONECTANDO CON PE0...";
    }
    if (videoViewport) {
      videoViewport.scrollIntoView({ behavior: "smooth", block: "center" });
    }

    try {
      const res = await fetch("/api/call/answer", { method: "POST" });
      const data = await res.json();
      if (data.success) {
        // Arrancar visor de vídeo para la llamada atendida (ventana de 60s)
        openLiveView(60);
        appendLog("Llamada atendida. Vídeo y canal conectados.", "success");
      } else {
        appendLog(`Aviso al atender timbre: ${data.error || 'Respuesta no exitosa'}`, "warning");
        stopCameraStream(false);
      }
    } catch (e) {
      appendLog(`Error al atender timbre: ${e.message}`, "error");
      stopCameraStream(false);
    } finally {
      state.isActivating = false;
    }
  });
}

if (btnQuickDoorOpen) {
  btnQuickDoorOpen.addEventListener("click", async () => {
    stopRingingAlerts();
    appendLog("Abriendo puerta directamente desde llamada entrante...", "cmd");
    await triggerDoorOpen("20");
    try {
      await fetch("/api/call/hangup", { method: "POST" });
    } catch (e) {}
    if (incomingCallBanner) incomingCallBanner.classList.add("hidden");
  });
}

if (btnRejectCall) {
  btnRejectCall.addEventListener("click", async () => {
    stopRingingAlerts();
    appendLog("Rechazando llamada de timbre...", "cmd");
    try {
      await fetch("/api/call/hangup", { method: "POST" });
    } catch (e) {}
    if (incomingCallBanner) incomingCallBanner.classList.add("hidden");
  });
}

// =========================================================================
// CARGA DE DISPOSITIVOS Y TOPOLOGÍA
// =========================================================================

async function loadPlants() {
  if (selectPlant) selectPlant.innerHTML = '<option value="">Cargando...</option>';
  try {
    const res = await fetch("/api/plants");
    const data = await res.json();
    state.plants = data.plants || [];
    if (selectPlant) {
      selectPlant.innerHTML = "";
      state.plants.forEach((p, idx) => {
        const opt = document.createElement("option");
        opt.value = p.PlantId || p.id || state.selectedPlantId;
        opt.textContent = `${p.name || "Instalación"} (ID: ${(p.PlantId || p.id || '').substring(0, 8)}...)`;
        selectPlant.appendChild(opt);
      });
      state.selectedPlantId = selectPlant.value || state.selectedPlantId;
    }
    await loadGateways(state.selectedPlantId);
  } catch (e) {
    appendLog(`Aviso cargando plantas: ${e.message}`, "info");
    await loadGateways(state.selectedPlantId);
  }
}

async function loadGateways(plantId) {
  if (selectGateway) selectGateway.innerHTML = '<option value="">Cargando pasarelas...</option>';
  try {
    const res = await fetch(`/api/plants/${plantId}/gateways`);
    const data = await res.json();
    state.gateways = data.gateways || [];
    if (selectGateway) {
      selectGateway.innerHTML = "";
      state.gateways.forEach((gw, idx) => {
        const opt = document.createElement("option");
        opt.value = gw.GatewayId || gw.id || state.selectedGatewayId;
        opt.textContent = `${gw.name || "Classe 100X"} - ${gw.GatewayId ? gw.GatewayId.substring(0, 8) + '...' : 'Pasarela'}`;
        selectGateway.appendChild(opt);
      });
      state.selectedGatewayId = selectGateway.value || state.selectedGatewayId;
    }
    await loadDevices(state.selectedPlantId, state.selectedGatewayId);
  } catch (e) {
    appendLog(`Aviso cargando pasarelas: ${e.message}`, "info");
    await loadDevices(state.selectedPlantId, state.selectedGatewayId);
  }
}

async function loadDevices(plantId, gwId) {
  if (devicesList) devicesList.innerHTML = '<div class="loading-devs">Cargando dispositivos...</div>';
  try {
    const res = await fetch(`/api/plants/${plantId}/gateways/${gwId}/devices`);
    const data = await res.json();
    state.devices = data.devices || { locks: [], lights: [], cameras: [] };

    // Sincronizar cámaras activas detectadas en la topología real
    if (state.devices.cameras && state.devices.cameras.length > 0) {
      AVAILABLE_CAMERAS.length = 0;
      state.devices.cameras.forEach((cam, idx) => {
        AVAILABLE_CAMERAS.push({
          index: idx,
          name: cam.name || (idx === 0 ? "Placa Exterior Principal (PE0)" : "Cámara Interior (PE1)"),
          address: cam.deviceAddr || cam.id,
          short: `PE${idx}`
        });
      });
      // Salvaguarda: si solo vino 1 cámara en la pasarela, permitir alternar con secundaria
      if (AVAILABLE_CAMERAS.length === 1 && !gwId) {
        AVAILABLE_CAMERAS.push({
          index: 1,
          name: "Cámara Interior (PE1)",
          address: "7c5a796f-7868-46a3-9fa6-091d73e25cad",
          short: "PE1"
        });
      }
      if (selectCam) {
        const curVal = selectCam.value;
        selectCam.innerHTML = "";
        AVAILABLE_CAMERAS.forEach(cam => {
          const opt = document.createElement("option");
          opt.value = String(cam.index);
          opt.textContent = cam.name;
          selectCam.appendChild(opt);
        });
        if (curVal && AVAILABLE_CAMERAS.some(c => String(c.index) === curVal)) {
          selectCam.value = curVal;
        }
      }
      if (btnCycleCam) {
        btnCycleCam.title = AVAILABLE_CAMERAS.length <= 1 ? "Instalación de cámara única (PE0)" : "Alternar entre cámaras";
      }
    }

    renderDevices();
  } catch (e) {
    appendLog(`Aviso cargando dispositivos: ${e.message}`, "info");
  }
}

function renderDevices() {
  if (!devicesList) return;
  devicesList.innerHTML = "";
  const allDevices = [
    ...(state.devices.locks || []).map(d => ({ ...d, type: "lock", icon: "🚪" })),
    ...(state.devices.lights || []).map(d => ({ ...d, type: "light", icon: "💡" })),
    ...(state.devices.cameras || []).map(d => ({ ...d, type: "camera", icon: "📹" }))
  ];

  if (devicesCount) devicesCount.textContent = `${allDevices.length} dispositivos configurados`;

  allDevices.forEach(dev => {
    const item = document.createElement("div");
    item.className = "device-item-card";

    const meta = document.createElement("div");
    meta.className = "device-meta";

    const badge = document.createElement("span");
    badge.className = "device-badge-icon";
    badge.textContent = dev.icon;
    meta.appendChild(badge);

    const info = document.createElement("div");
    const title = document.createElement("div");
    title.className = "device-title";
    title.textContent = dev.name || "Dispositivo";
    info.appendChild(title);

    const addrTag = document.createElement("div");
    addrTag.className = "device-addr-tag";
    addrTag.textContent = `Dirección Bus: ${dev.deviceAddr || "20"}`;
    info.appendChild(addrTag);

    meta.appendChild(info);
    item.appendChild(meta);

    const actions = document.createElement("div");
    if (dev.type === "lock") {
      const btn = document.createElement("button");
      btn.className = "btn-sm btn-primary btn-open-specific";
      btn.setAttribute("data-addr", dev.deviceAddr || "20");
      btn.textContent = "Abrir";
      btn.addEventListener("click", () => triggerDoorOpen(btn.getAttribute("data-addr")));
      actions.appendChild(btn);
    } else if (dev.type === "light") {
      const btn = document.createElement("button");
      btn.className = "btn-sm btn-secondary btn-light-specific";
      btn.setAttribute("data-addr", dev.deviceAddr || "114");
      btn.textContent = "Activar";
      btn.addEventListener("click", () => triggerLightToggle(btn.getAttribute("data-addr")));
      actions.appendChild(btn);
    }
    item.appendChild(actions);

    devicesList.appendChild(item);
  });
}

// Selectores
if (selectPlant) {
  selectPlant.addEventListener("change", (e) => {
    state.selectedPlantId = e.target.value;
    loadGateways(state.selectedPlantId);
  });
}
if (selectGateway) {
  selectGateway.addEventListener("change", (e) => {
    state.selectedGatewayId = e.target.value;
    loadDevices(state.selectedPlantId, state.selectedGatewayId);
  });
}
if (btnRefresh) {
  btnRefresh.addEventListener("click", () => {
    loadPlants();
    checkCertificateStatus();
  });
}

// =========================================================================
// ACCIONES DE CERRADURA Y LUZ
// =========================================================================

async function triggerDoorOpen(addr = "20") {
  if (navigator.vibrate) navigator.vibrate([150, 50, 150]);
  playTone(880, 0.12);

  if (btnOpenDoor) btnOpenDoor.classList.add("unlocking");
  if (doorCountdown) doorCountdown.classList.remove("hidden");
  appendLog(`Comando apertura enviado: cerradura ${addr}`, "cmd");

  try {
    const res = await fetch("/api/door/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        gateway_id: state.selectedGatewayId,
        address: addr,
        plant_id: state.selectedPlantId
      })
    });
    const data = await res.json();
    appendLog(data.message, "success");
    playTone(1046.5, 0.2);
  } catch (e) {
    appendLog(`Fallo al abrir puerta: ${e.message}`, "error");
  } finally {
    setTimeout(() => {
      if (btnOpenDoor) btnOpenDoor.classList.remove("unlocking");
      if (doorCountdown) doorCountdown.classList.add("hidden");
    }, 3000);
  }
}

if (btnOpenDoor) {
  btnOpenDoor.addEventListener("click", () => triggerDoorOpen("20"));
}

async function triggerLightToggle(addr = "114") {
  playTone(523.25, 0.1);
  appendLog(`Comando luz de escalera enviado: ${addr}`, "cmd");
  try {
    const res = await fetch("/api/light/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        gateway_id: state.selectedGatewayId,
        address: addr,
        plant_id: state.selectedPlantId
      })
    });
    const data = await res.json();
    appendLog(data.message, "success");
  } catch (e) {
    appendLog(`Error luz de escalera: ${e.message}`, "error");
  }
}

if (btnToggleLight) {
  btnToggleLight.addEventListener("click", () => triggerLightToggle("114"));
}

if (btnDiagnostics) {
  btnDiagnostics.addEventListener("click", async () => {
    appendLog("Ejecutando diagnóstico pasarela...", "cmd");
    try {
      const res = await fetch("/api/command/custom", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          gateway_id: state.selectedGatewayId,
          command: "*#1*0##"
        })
      });
      const data = await res.json();
      appendLog("Diagnóstico respondido: Pasarela online", "success");
    } catch (e) {
      appendLog(`Error en diagnóstico: ${e.message}`, "error");
    }
  });
}

// =========================================================================
// MODO EN ESPERA (STANDBY) Y VISTA PUNTUAL 30s ("MIRAR CALLE")
// =========================================================================

const AVAILABLE_CAMERAS = [
  {
    index: 0,
    name: "Placa Exterior Principal (PE0)",
    address: "a8276649-89c0-445c-8f7d-312c1f392fd1",
    short: "PE0"
  },
  {
    index: 1,
    name: "Cámara Interior (PE1)",
    address: "7c5a796f-7868-46a3-9fa6-091d73e25cad",
    short: "PE1"
  }
];

function openLiveView(durationSeconds = 30) {
  state.isCalling = true;
  state.callStartTime = Date.now();
  state.callRemainingSeconds = durationSeconds;

  if (videoPlaceholder) videoPlaceholder.classList.add("hidden");
  if (cameraStreamImg) {
    cameraStreamImg.classList.remove("hidden");
    cameraStreamImg.onerror = () => {
      console.warn("Reintentando flujo de vídeo...");
      setTimeout(() => {
        if (state.isCalling && cameraStreamImg) {
          cameraStreamImg.src = "/api/camera/stream?t=" + Date.now();
        }
      }, 1000);
    };
    cameraStreamImg.src = "/api/camera/stream?t=" + Date.now();
  }
  if (videoOverlay) videoOverlay.classList.remove("hidden");
  if (callCountdown) {
    callCountdown.textContent = `${durationSeconds}s`;
    callCountdown.classList.remove("hidden");
  }
  if (streamStatusBadge) {
    const camIdx = parseInt(selectCam ? selectCam.value : 0) || 0;
    const camShort = AVAILABLE_CAMERAS[camIdx] ? AVAILABLE_CAMERAS[camIdx].short : `PE${camIdx}`;
    streamStatusBadge.className = "badge-status live";
    streamStatusBadge.textContent = `● EN DIRECTO (${camShort} - CANAL OCUPADO)`;
  }
  if (btnTalk) btnTalk.disabled = false;
  if (btnEndCall) btnEndCall.disabled = false;

  fetchTelemetry();

  // Iniciar temporizador de cuenta atrás
  if (state.callTimer) clearInterval(state.callTimer);
  state.callTimer = setInterval(() => {
    state.callRemainingSeconds--;
    if (callCountdown) {
      callCountdown.textContent = `${Math.max(0, state.callRemainingSeconds)}s`;
    }
    if (state.callRemainingSeconds <= 0) {
      clearInterval(state.callTimer);
      state.callTimer = null;
      stopCameraStream(true);
    }
  }, 1000);
}

async function startCameraStream(targetIdx = null) {
  if (state.isActivating) return;
  if (state.isCalling && targetIdx === null) return;
  const selectedIdx = (targetIdx !== null) ? targetIdx : (parseInt(selectCam ? selectCam.value : 0) || 0);
  const targetCam = AVAILABLE_CAMERAS[selectedIdx] || AVAILABLE_CAMERAS[0];

  state.isActivating = true;
  state.isCalling = true;
  state.callStartTime = Date.now();

  playTone(659.25, 0.1);
  appendLog(`Conectando con ${targetCam.name} (auto-encendido puntual 30s)...`, "cmd");

  if (videoPlaceholder) videoPlaceholder.classList.remove("hidden");
  if (cameraStreamImg) cameraStreamImg.classList.add("hidden");
  if (streamStatusBadge) {
    streamStatusBadge.className = "badge-status live";
    streamStatusBadge.textContent = `● CONECTANDO CON ${targetCam.short}...`;
  }
  if (btnCameraTurnOn) btnCameraTurnOn.disabled = true;
  if (btnStartCall) btnStartCall.disabled = true;

  try {
    const res = await fetch("/api/camera/activate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        gateway_id: state.selectedGatewayId,
        camera_index: targetCam.index,
        address: targetCam.address
      })
    });
    const data = await res.json();
    if (data.success) {
      if (selectCam) selectCam.value = String(data.camera_index);
      appendLog(data.message || `${targetCam.name} encendida con éxito`, "success");
      // Abrir visor de streaming una vez establecida la sesión SIP y recepción de vídeo
      openLiveView(30);
    } else {
      appendLog(data.message || "Aviso en activación de cámara", "error");
      stopCameraStream(false);
    }
  } catch (e) {
    appendLog(`Fallo al activar cámara: ${e.message}`, "error");
    stopCameraStream(false);
  } finally {
    state.isActivating = false;
    if (btnCameraTurnOn) btnCameraTurnOn.disabled = false;
    if (btnStartCall) btnStartCall.disabled = false;
  }
}

async function stopCameraStream(notifyServer = true) {
  state.isActivating = false;
  state.isCalling = false;
  state.callStartTime = 0;

  if (state.callTimer) {
    clearInterval(state.callTimer);
    state.callTimer = null;
  }

  if (notifyServer) {
    try {
      if (state.callState !== "standby") {
        await fetch("/api/call/hangup", { method: "POST" });
      }
      await fetch("/api/camera/deactivate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          gateway_id: state.selectedGatewayId
        })
      });
    } catch (e) {}
  }

  if (cameraStreamImg) {
    cameraStreamImg.src = "";
    cameraStreamImg.classList.add("hidden");
  }
  if (videoOverlay) videoOverlay.classList.add("hidden");
  if (callCountdown) callCountdown.classList.add("hidden");
  if (videoPlaceholder) videoPlaceholder.classList.remove("hidden");
  if (streamStatusBadge) {
    streamStatusBadge.className = "badge-status standby";
    streamStatusBadge.textContent = "MODO EN ESPERA (STANDBY)";
  }
  if (btnTalk) btnTalk.disabled = true;
  if (btnEndCall) btnEndCall.disabled = true;
  if (btnCameraTurnOn) btnCameraTurnOn.disabled = false;
  if (btnStartCall) btnStartCall.disabled = false;

  fetchTelemetry();

  appendLog("Cámara apagada y canal del edificio liberado (Standby).", "info");
}

if (btnCameraTurnOn) btnCameraTurnOn.addEventListener("click", () => startCameraStream());
if (btnStartCall) btnStartCall.addEventListener("click", () => startCameraStream());
if (btnEndCall) btnEndCall.addEventListener("click", () => stopCameraStream(true));

// Captura de foto
if (btnCaptureSnapshot) {
  btnCaptureSnapshot.addEventListener("click", () => {
    playTone(1046.5, 0.08);
    const currentIdx = parseInt(selectCam ? selectCam.value : 0) || 0;
    const link = document.createElement("a");
    link.href = `/api/camera/snapshot?t=${Date.now()}`;
    link.download = `captura_pe${currentIdx}_${Date.now()}.jpg`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  });
}

// Pantalla completa
if (btnFullscreen) {
  btnFullscreen.addEventListener("click", () => {
    if (!document.fullscreenElement) {
      if (videoViewport && videoViewport.requestFullscreen) videoViewport.requestFullscreen();
    } else {
      if (document.exitFullscreen) document.exitFullscreen();
    }
  });
}

// Conmutación mediante selector dropdown
if (selectCam) {
  selectCam.addEventListener("change", async (e) => {
    if (AVAILABLE_CAMERAS.length <= 1) return;
    const newIdx = parseInt(e.target.value) || 0;
    const targetCam = AVAILABLE_CAMERAS[newIdx] || AVAILABLE_CAMERAS[0];
    appendLog(`Seleccionada: ${targetCam.name}`, "info");

    if (state.isCalling) {
      playTone(587.33, 0.08);
      appendLog(`Conmutando en caliente a ${targetCam.name}...`, "cmd");
      if (streamStatusBadge) {
        streamStatusBadge.textContent = `● CAMBIANDO A ${targetCam.short}...`;
      }
      try {
        const res = await fetch("/api/camera/activate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            gateway_id: state.selectedGatewayId,
            camera_index: targetCam.index,
            address: targetCam.address
          })
        });
        const data = await res.json();
        if (data.success) {
          appendLog(data.message || `Cambiado a ${targetCam.name}`, "success");
          if (streamStatusBadge) {
            streamStatusBadge.textContent = `● EN DIRECTO (${targetCam.short} - CANAL OCUPADO)`;
          }
          if (cameraStreamImg) {
            cameraStreamImg.src = `/api/camera/stream?t=${Date.now()}`;
          }
        } else {
          appendLog(`Error conmutando a ${targetCam.name}: ${data.message}`, "error");
        }
      } catch (err) {
        appendLog(`Error de red al cambiar de cámara: ${err.message}`, "error");
      }
    } else {
      try {
        await fetch("/api/camera/cycle", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            gateway_id: state.selectedGatewayId,
            camera_index: targetCam.index,
            address: targetCam.address
          })
        });
      } catch (e) {
        // Fallback silencioso
      }
    }
  });
}

// Alternar cámaras (Botón Ciclar)
if (btnCycleCam) {
  btnCycleCam.addEventListener("click", async () => {
    playTone(523.25, 0.08);
    if (AVAILABLE_CAMERAS.length <= 1) {
      appendLog("Instalación con placa exterior única (PE0). No hay cámaras adicionales instaladas.", "info");
      return;
    }
    const currentVal = parseInt(selectCam ? selectCam.value : 0) || 0;
    const nextVal = (currentVal + 1) % AVAILABLE_CAMERAS.length;
    const targetCam = AVAILABLE_CAMERAS[nextVal];
    if (selectCam) selectCam.value = String(nextVal);

    if (state.isCalling) {
      appendLog(`Alternando cámara en caliente a ${targetCam.name}...`, "cmd");
      if (streamStatusBadge) {
        streamStatusBadge.textContent = `● CAMBIANDO A ${targetCam.short}...`;
      }
      try {
        const res = await fetch("/api/camera/activate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            gateway_id: state.selectedGatewayId,
            camera_index: targetCam.index,
            address: targetCam.address
          })
        });
        const data = await res.json();
        if (data.success) {
          appendLog(data.message || `Cambiado a ${targetCam.name}`, "success");
          if (streamStatusBadge) {
            streamStatusBadge.textContent = `● EN DIRECTO (${targetCam.short} - CANAL OCUPADO)`;
          }
          if (cameraStreamImg) {
            cameraStreamImg.src = `/api/camera/stream?t=${Date.now()}`;
          }
        } else {
          appendLog(`Error alternando cámara: ${data.message}`, "error");
        }
      } catch (e) {
        appendLog(`Error cambiando cámara: ${e.message}`, "error");
      }
    } else {
      try {
        const res = await fetch("/api/camera/cycle", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ gateway_id: state.selectedGatewayId, direction: "next" })
        });
        const data = await res.json();
        if (selectCam) selectCam.value = String(data.camera_index);
        appendLog(data.message, "info");
      } catch (e) {
        appendLog(`Error cambiando cámara: ${e.message}`, "error");
      }
    }
  });
}

// Consola de tramas OpenWebNet
if (toggleConsole) {
  toggleConsole.addEventListener("click", () => {
    const isHidden = consoleBody.style.display === "none";
    consoleBody.style.display = isHidden ? "block" : "none";
    if (consoleArrow) consoleArrow.textContent = isHidden ? "▼" : "▲";
  });
}

if (btnSendCustom) {
  btnSendCustom.addEventListener("click", async () => {
    const cmd = inputCustomCommand ? inputCustomCommand.value.trim() : "";
    if (!cmd) return;
    appendLog(`Trama manual: ${cmd}`, "cmd");
    try {
      const res = await fetch("/api/command/custom", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ gateway_id: state.selectedGatewayId, command: cmd })
      });
      const data = await res.json();
      appendLog(data.message, "success");
      if (inputCustomCommand) inputCustomCommand.value = "";
    } catch (e) {
      appendLog(`Error enviando trama: ${e.message}`, "error");
    }
  });
}

if (inputCustomCommand) {
  inputCustomCommand.addEventListener("keypress", (e) => {
    if (e.key === "Enter") btnSendCustom.click();
  });
}

// =========================================================================
// TELEMETRÍA EN VIVO DE CANALES DE VÍDEO Y BÚFER MJPEG
// =========================================================================

function startTelemetryPolling() {
  if (state.telemetryInterval) clearInterval(state.telemetryInterval);
  fetchTelemetry();
  state.telemetryInterval = setInterval(fetchTelemetry, 1500);
}

async function fetchTelemetry() {
  try {
    const res = await fetch("/api/camera/telemetry");
    if (!res.ok) return;
    const data = await res.json();
    state.telemetryData = data;
    renderChannelsTelemetry(data);
  } catch (e) {
    // Silencioso en desconexiones temporales
  }
}

function renderChannelsTelemetry(data) {
  if (!channelsContainer || !data || !Array.isArray(data.channels)) return;

  // Actualizar panel de telemetría detallada
  const raw = data.raw_stats || {};
  if (telemetryRtpPort) telemetryRtpPort.textContent = String(raw.rtp_port || 9078);
  if (telemetryRtpPackets) telemetryRtpPackets.textContent = Number(raw.packets_received || 0).toLocaleString();
  if (telemetryRtpBytes) {
    const b = Number(raw.bytes_received || 0);
    telemetryRtpBytes.textContent = b > 1048576 ? `${(b / 1048576).toFixed(2)} MB` : `${(b / 1024).toFixed(1)} KB`;
  }
  if (telemetryFramesDecoded) telemetryFramesDecoded.textContent = Number(raw.frames_decoded || 0).toLocaleString();
  if (telemetryPollRate) telemetryPollRate.textContent = state.isCalling ? "1.0s (Live)" : "1.5s";

  channelsContainer.innerHTML = "";

  data.channels.forEach(ch => {
    const card = document.createElement("div");
    card.className = "channel-card" + (ch.is_active ? " active" : "");

    // 1. Cabecera del Canal
    const topRow = document.createElement("div");
    topRow.className = "channel-card-top";

    const titleRow = document.createElement("div");
    titleRow.className = "channel-title-row";

    const badge = document.createElement("span");
    badge.className = "channel-badge";
    badge.textContent = ch.channel_id;
    titleRow.appendChild(badge);

    const title = document.createElement("h4");
    title.className = "channel-name";
    title.textContent = ch.name;
    titleRow.appendChild(title);

    const statusBadge = document.createElement("span");
    statusBadge.className = "channel-status-badge " + (ch.is_active ? "live" : "standby");
    statusBadge.textContent = ch.is_active ? "● EN DIRECTO" : "STANDBY";
    titleRow.appendChild(statusBadge);

    topRow.appendChild(titleRow);

    const viewersBadge = document.createElement("div");
    const count = ch.active_viewers || 0;
    viewersBadge.className = "channel-viewers-badge" + (count > 0 ? " has-viewers" : "");
    const vIcon = document.createElement("span");
    vIcon.textContent = "👥";
    const vText = document.createElement("span");
    vText.textContent = `${count} cliente${count === 1 ? "" : "s"} conectado${count === 1 ? "" : "s"}`;
    viewersBadge.appendChild(vIcon);
    viewersBadge.appendChild(vText);
    topRow.appendChild(viewersBadge);

    card.appendChild(topRow);

    // 2. Sección del Búfer de Streaming
    const buf = ch.buffer || {};
    const bufSection = document.createElement("div");
    bufSection.className = "channel-buffer-section";

    const bufHeader = document.createElement("div");
    bufHeader.className = "buffer-header";

    const bufLabel = document.createElement("span");
    bufLabel.className = "buffer-label";
    bufLabel.textContent = "Búfer de Vídeo (MJPEG Pipe):";
    bufHeader.appendChild(bufLabel);

    const bufStatsText = document.createElement("span");
    bufStatsText.className = "buffer-stats-text";
    const strongPct = document.createElement("strong");
    const pctVal = Number(buf.pct || 0);
    strongPct.textContent = `${pctVal.toFixed(1)}%`;
    bufStatsText.appendChild(strongPct);
    const curKb = Number(buf.current_kb || (buf.current_bytes ? (buf.current_bytes / 1024) : 0)).toFixed(1);
    const maxMb = Number(buf.max_mb || (buf.max_bytes ? (buf.max_bytes / 1048576) : 5)).toFixed(1);
    bufStatsText.appendChild(document.createTextNode(` (${curKb} KB / ${maxMb} MB)`));
    bufHeader.appendChild(bufStatsText);
    bufSection.appendChild(bufHeader);

    const barBg = document.createElement("div");
    barBg.className = "buffer-progress-bar-bg";
    const barFill = document.createElement("div");
    const colorClass = pctVal < 60 ? "green" : (pctVal < 85 ? "amber" : "red");
    barFill.className = `buffer-progress-bar-fill ${colorClass}`;
    barFill.style.width = `${Math.min(100, Math.max(1, pctVal))}%`;
    barBg.appendChild(barFill);
    bufSection.appendChild(barBg);

    const chipsRow = document.createElement("div");
    chipsRow.className = "buffer-chips-row";

    const chipFps = document.createElement("span");
    chipFps.className = "buffer-chip";
    chipFps.textContent = `⚡ ${(buf.fps || 0).toFixed(1)} FPS`;
    chipsRow.appendChild(chipFps);

    const chipFrame = document.createElement("span");
    chipFrame.className = "buffer-chip";
    const fKb = Number(buf.latest_frame_size_kb || (buf.latest_frame_size ? (buf.latest_frame_size / 1024) : 0)).toFixed(1);
    chipFrame.textContent = `🖼️ Frame: ${fKb} KB`;
    chipsRow.appendChild(chipFrame);

    const chipLat = document.createElement("span");
    chipLat.className = "buffer-chip";
    chipLat.textContent = `⏱️ Latencia: ${buf.latest_frame_age_ms || 0} ms`;
    chipsRow.appendChild(chipLat);

    const chipRtp = document.createElement("span");
    chipRtp.className = "buffer-chip";
    chipRtp.textContent = `📦 RTP: ${Number(buf.packets_received || 0).toLocaleString()} pkts`;
    chipsRow.appendChild(chipRtp);

    bufSection.appendChild(chipsRow);
    card.appendChild(bufSection);

    // 3. Clientes Viendo este Canal
    const clientsSec = document.createElement("div");
    clientsSec.className = "channel-clients-section";

    const clientsHeading = document.createElement("h5");
    clientsHeading.className = "clients-heading";
    clientsHeading.textContent = `Clientes Viendo Este Canal (${(ch.clients || []).length})`;
    clientsSec.appendChild(clientsHeading);

    if (!ch.clients || ch.clients.length === 0) {
      const emptyMsg = document.createElement("div");
      emptyMsg.className = "channel-no-clients";
      emptyMsg.textContent = ch.is_active 
        ? "Canal emitiendo, pero ningún cliente web conectado al stream."
        : "No hay clientes conectados a este canal. El vídeo se encuentra en reposo.";
      clientsSec.appendChild(emptyMsg);
    } else {
      const clientsList = document.createElement("div");
      clientsList.className = "clients-list";

      ch.clients.forEach(cli => {
        const row = document.createElement("div");
        row.className = "client-telemetry-row";

        // Columna 1: Dispositivo + IP
        const devCol = document.createElement("div");
        devCol.className = "client-device-col";
        const iconSpan = document.createElement("span");
        iconSpan.className = "device-icon";
        const dName = (cli.device || "").toLowerCase();
        iconSpan.textContent = (dName.includes("apple") || dName.includes("ios") || dName.includes("android")) ? "📱" : (dName.includes("script") ? "⚙️" : "💻");
        devCol.appendChild(iconSpan);

        const dInfo = document.createElement("div");
        dInfo.className = "device-info";
        const dTitle = document.createElement("span");
        dTitle.className = "device-name";
        dTitle.textContent = cli.device || "Navegador Web";
        dInfo.appendChild(dTitle);
        const dIp = document.createElement("span");
        dIp.className = "client-ip";
        dIp.textContent = cli.ip || "127.0.0.1";
        dInfo.appendChild(dIp);
        devCol.appendChild(dInfo);
        row.appendChild(devCol);

        // Columna 2: Tiempo Conectado
        const mConn = document.createElement("div");
        mConn.className = "client-metric";
        const lConn = document.createElement("span");
        lConn.className = "metric-label";
        lConn.textContent = "Conectado";
        const vConn = document.createElement("span");
        vConn.className = "metric-value";
        vConn.textContent = cli.connected_time_str || "00:01";
        mConn.appendChild(lConn);
        mConn.appendChild(vConn);
        row.appendChild(mConn);

        // Columna 3: Fotogramas y Datos Enviados
        const mSent = document.createElement("div");
        mSent.className = "client-metric";
        const lSent = document.createElement("span");
        lSent.className = "metric-label";
        lSent.textContent = "Enviados";
        const vSent = document.createElement("span");
        vSent.className = "metric-value";
        const sentMb = (cli.bytes_sent_kb > 1024) ? `${(cli.bytes_sent_kb / 1024).toFixed(1)} MB` : `${cli.bytes_sent_kb} KB`;
        vSent.textContent = `${Number(cli.frames_sent || 0).toLocaleString()} fr (${sentMb})`;
        mSent.appendChild(lSent);
        mSent.appendChild(vSent);
        row.appendChild(mSent);

        // Columna 4: Tasa de Transferencia
        const mRate = document.createElement("div");
        mRate.className = "client-metric";
        const lRate = document.createElement("span");
        lRate.className = "metric-label";
        lRate.textContent = "Tasa";
        const vRate = document.createElement("span");
        vRate.className = "metric-value";
        vRate.textContent = `${cli.kbps || 0} kbps`;
        mRate.appendChild(lRate);
        mRate.appendChild(vRate);
        row.appendChild(mRate);

        // Columna 5: Estado de Sincronización
        const stCol = document.createElement("div");
        stCol.className = "client-status";
        const stBadge = document.createElement("span");
        const isLag = (cli.last_frame_age_ms > 2000);
        stBadge.className = "badge-client-sync" + (isLag ? " lag" : "");
        stBadge.textContent = isLag ? "🟡 Retardo" : "🟢 Sincronizado";
        stCol.appendChild(stBadge);
        row.appendChild(stCol);

        clientsList.appendChild(row);
      });

      clientsSec.appendChild(clientsList);
    }

    card.appendChild(clientsSec);
    channelsContainer.appendChild(card);
  });
}

// Alternar panel detallado
if (btnToggleDetailedTelemetry) {
  btnToggleDetailedTelemetry.addEventListener("click", () => {
    if (detailedTelemetryPanel) {
      const isHidden = detailedTelemetryPanel.classList.contains("hidden");
      if (isHidden) {
        detailedTelemetryPanel.classList.remove("hidden");
        btnToggleDetailedTelemetry.textContent = "✕ Ocultar Detalles";
      } else {
        detailedTelemetryPanel.classList.add("hidden");
        btnToggleDetailedTelemetry.textContent = "⛶ Modo Detallado";
      }
    }
  });
}

// =========================================================================
// CONTROLADOR DIGITAL TWIN: BTICINO CLASSE 100X
// =========================================================================

let toastTimeout = null;
function showLcdToast(icon, message, durationMs = 2400) {
  const lcdToast = document.getElementById("lcdToast");
  const lcdToastIcon = document.getElementById("lcdToastIcon");
  const lcdToastMsg = document.getElementById("lcdToastMsg");
  if (!lcdToast) return;

  if (lcdToastIcon) lcdToastIcon.textContent = icon;
  if (lcdToastMsg) lcdToastMsg.textContent = message;

  lcdToast.classList.add("visible");
  if (toastTimeout) clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => {
    lcdToast.classList.remove("visible");
  }, durationMs);
}

const dayNamesI18n = {
  es: ["DOM", "LUN", "MAR", "MIÉ", "JUE", "VIE", "SÁB"],
  en: ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]
};
const monthNamesI18n = {
  es: ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"],
  en: ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
};

function updateDigitalTwinClock() {
  const lcdClock = document.getElementById("lcdClock");
  const lcdDate = document.getElementById("lcdDate");
  const now = new Date();
  const hh = String(now.getHours()).padStart(2, "0");
  const mm = String(now.getMinutes()).padStart(2, "0");
  if (lcdClock) lcdClock.textContent = `${hh}:${mm}`;

  if (lcdDate) {
    const lang = (currentLang === "en") ? "en" : "es";
    const dayName = dayNamesI18n[lang][now.getDay()];
    const dayNum = now.getDate();
    const monthName = monthNamesI18n[lang][now.getMonth()];
    lcdDate.textContent = `${dayName}, ${dayNum} ${monthName}`;
  }
}

function initDigitalTwinClock() {
  updateDigitalTwinClock();
  setInterval(updateDigitalTwinClock, 1000);
}

function setupDigitalTwinHotspots() {
  // Hotspot Llave (Door Unlock)
  const hotspotKey = document.getElementById("hotspotKey");
  if (hotspotKey) {
    hotspotKey.addEventListener("click", () => {
      hotspotKey.classList.add("unlocked");
      setTimeout(() => hotspotKey.classList.remove("unlocked"), 1200);
      showLcdToast("🔑", t("toast_door_open"));
      triggerDoorOpen("20");
    });
  }

  // Hotspot Ojo (Mirar Calle / Alternar)
  const hotspotEye = document.getElementById("hotspotEye");
  if (hotspotEye) {
    hotspotEye.addEventListener("click", () => {
      hotspotEye.classList.add("pressed");
      setTimeout(() => hotspotEye.classList.remove("pressed"), 250);
      if (!state.isCalling) {
        showLcdToast("👁️", t("toast_street_view"));
        startCameraStream();
      } else {
        showLcdToast("🔄", t("toast_switching_cam"));
        if (btnCycleCam) btnCycleCam.click();
      }
    });
  }

  // Hotspot Estrella (Luz Escalera)
  const hotspotStar = document.getElementById("hotspotStar");
  if (hotspotStar) {
    hotspotStar.addEventListener("click", () => {
      hotspotStar.classList.add("pressed");
      setTimeout(() => hotspotStar.classList.remove("pressed"), 250);
      showLcdToast("💡", t("toast_light"));
      triggerLightToggle("114");
    });
  }

  // Hotspot Hablar / Descolgar (Talk PTT o Responder Timbre)
  const hotspotTalk = document.getElementById("hotspotTalk");
  if (hotspotTalk) {
    hotspotTalk.addEventListener("click", () => {
      hotspotTalk.classList.add("pressed");
      setTimeout(() => hotspotTalk.classList.remove("pressed"), 250);
      if (state.callState === "ringing") {
        showLcdToast("📞", t("toast_answered"));
        if (btnAnswerCall) btnAnswerCall.click();
      } else if (state.isCalling) {
        showLcdToast("🎙️", t("toast_ptt"));
        if (btnTalk) btnTalk.click();
      } else {
        showLcdToast("👁️", t("toast_street_view"));
        startCameraStream();
      }
    });
  }

  // Hotspot Colgar (Liberar Canal)
  const hotspotHangup = document.getElementById("hotspotHangup");
  if (hotspotHangup) {
    hotspotHangup.addEventListener("click", () => {
      hotspotHangup.classList.add("pressed");
      setTimeout(() => hotspotHangup.classList.remove("pressed"), 250);
      showLcdToast("📵", t("toast_channel_freed"));
      if (state.callState === "ringing" && btnRejectCall) {
        btnRejectCall.click();
      } else {
        stopCameraStream(true);
      }
    });
  }

  // Hotspots Puntos 1 a 4
  const hotspotDot1 = document.getElementById("hotspotDot1");
  if (hotspotDot1) {
    hotspotDot1.addEventListener("click", () => {
      hotspotDot1.classList.add("pressed");
      setTimeout(() => hotspotDot1.classList.remove("pressed"), 250);
      showLcdToast("🔄", t("toast_cycle_cam"));
      if (btnCycleCam) btnCycleCam.click();
    });
  }

  const hotspotDot2 = document.getElementById("hotspotDot2");
  if (hotspotDot2) {
    hotspotDot2.addEventListener("click", () => {
      hotspotDot2.classList.add("pressed");
      setTimeout(() => hotspotDot2.classList.remove("pressed"), 250);
      showLcdToast("⚡", t("toast_aux2"));
      triggerLightToggle("115");
    });
  }

  const hotspotDot3 = document.getElementById("hotspotDot3");
  if (hotspotDot3) {
    hotspotDot3.addEventListener("click", () => {
      hotspotDot3.classList.add("pressed");
      setTimeout(() => hotspotDot3.classList.remove("pressed"), 250);
      showLcdToast("🔓", t("toast_door_open2"));
      triggerDoorOpen("21");
    });
  }

  const hotspotDot4 = document.getElementById("hotspotDot4");
  if (hotspotDot4) {
    hotspotDot4.addEventListener("click", () => {
      hotspotDot4.classList.add("pressed");
      setTimeout(() => hotspotDot4.classList.remove("pressed"), 250);
      showLcdToast("📸", t("toast_snapshot"));
      if (btnCaptureSnapshot) btnCaptureSnapshot.click();
    });
  }

  // Acordeón de controles avanzados
  const accordionToggle = document.getElementById("accordionToggle");
  const advancedAccordion = document.getElementById("advancedAccordion");
  if (accordionToggle && advancedAccordion) {
    accordionToggle.addEventListener("click", () => {
      advancedAccordion.classList.toggle("open");
    });
  }

  // Conmutador de idioma
  const btnLangEs = document.getElementById("btnLangEs");
  const btnLangEn = document.getElementById("btnLangEn");
  if (btnLangEs) btnLangEs.addEventListener("click", () => setLanguage("es"));
  if (btnLangEn) btnLangEn.addEventListener("click", () => setLanguage("en"));
}

// Iniciar aplicación al cargar el DOM
document.addEventListener("DOMContentLoaded", () => {
  initAutonomousApp();
  initDigitalTwinClock();
  setupDigitalTwinHotspots();
  setLanguage(currentLang);
});
