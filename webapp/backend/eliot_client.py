"""
Cliente para la API Eliot de Legrand / BTicino (Classe 100X / 300X / HomeTouch)
Basado en ingeniería inversa del APK com.legrandgroup.c100x (Classe 100X).
"""
import io
import json
import logging
import re
import urllib.parse
from typing import Dict, Any, List, Optional
import httpx

logger = logging.getLogger("eliot_client")

# Configuración Oficial de Classe 100X (Azure AD B2C)
B2C_TENANT = "EliotClouduamprd.onmicrosoft.com"
B2C_BASE_URL = "https://eliotclouduamprd.b2clogin.com/EliotClouduamprd.onmicrosoft.com/oauth2/v2.0"
POLICY_SIGN_IN = "B2C_1_DoorEliot-C100X-SignUporSignIn"
POLICY_PASSWORD = "B2C_1_DoorEliot-C100X-password"
POLICY_PROFILE = "B2C_1_DoorEliot-C100X-profile"

CLIENT_ID = "7d11af71-ab98-4832-aa62-6b00bff3bcc8"
REDIRECT_URI = "com.legrandgroup.c100x://oauth2redirect"
SCOPES = "https://EliotClouduamprd.onmicrosoft.com/security/access.full offline_access openid"

# Legrand Developer API / Azure APIM
API_BASE_URL = os.environ.get("LEGRAND_API_BASE_URL", "https://api.developer.legrand.com")
SUBSCRIPTION_KEY = os.environ.get("LEGRAND_SUBSCRIPTION_KEY", "")
SIP_DOMAIN = os.environ.get("SIP_DOMAIN", "vdesip.bs.iotleg.com")

# Default hardware IDs for Classe 100X
DEFAULT_GATEWAY_ID = os.environ.get("GATEWAY_ID", "")
DEFAULT_ENTRANCE_PANEL_GUID = os.environ.get("ENTRANCE_PANEL_GUID", "")

# Azure AD Client Credentials (App Token)
APP_TENANT_ID = os.environ.get("LEGRAND_APP_TENANT_ID", "")
APP_CLIENT_ID = os.environ.get("LEGRAND_APP_CLIENT_ID", "")
APP_CLIENT_SECRET = os.environ.get("LEGRAND_APP_CLIENT_SECRET", "")
APP_TOKEN_URL = f"https://login.microsoftonline.com/{APP_TENANT_ID}/oauth2/token?resource={APP_CLIENT_ID}" if APP_TENANT_ID else ""

# Legacy Fallback
LEGACY_HOST = "https://www.myhomeweb.com"
LEGACY_PRJ_NAMES = ["C100X", "ELIOT", "LEGRAND", "MHT"]


class EliotClient:
    def __init__(self, api_base_url: str = API_BASE_URL):
        self.api_base_url = api_base_url.rstrip("/")
        self.auth_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.username: Optional[str] = None
        self.user_data: Dict[str, Any] = {}
        self.application_token: Optional[str] = None
        self.client = httpx.AsyncClient(timeout=30.0, verify=True)

    def _headers(self, token: Optional[str] = None) -> Dict[str, str]:
        """Headers estándar requeridos por la API Azure de Legrand."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Ocp-Apim-Subscription-Key": SUBSCRIPTION_KEY,
        }
        active_token = token or self.auth_token
        if active_token:
            headers["Authorization"] = f"Bearer {active_token}"
        return headers

    def get_authorize_url(self, state: Optional[str] = None) -> str:
        """
        Genera la URL de autorización OAuth2 de Azure AD B2C para Classe 100X.
        El usuario puede abrir este enlace para autenticarse directamente en Legrand.
        """
        params = {
            "p": POLICY_SIGN_IN,
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": SCOPES,
        }
        if state:
            params["state"] = state
        return f"{B2C_BASE_URL}/authorize?{urllib.parse.urlencode(params)}"

    async def exchange_code(self, code_or_url: str) -> Dict[str, Any]:
        """
        Intercambia un código de autorización (o URL de redirección completa)
        por tokens de acceso y de refresco contra Azure AD B2C.
        """
        code = code_or_url.strip()
        if "code=" in code:
            parsed = urllib.parse.urlparse(code)
            qs = urllib.parse.parse_qs(parsed.query)
            if "code" in qs:
                code = qs["code"][0]

        token_url = f"{B2C_BASE_URL}/token?p={POLICY_SIGN_IN}"
        data = {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
        }

        resp = await self.client.post(token_url, data=data)
        if resp.status_code != 200:
            raise Exception(f"Fallo al canjear código OAuth ({resp.status_code}): {resp.text}")

        token_data = resp.json()
        self.auth_token = token_data.get("access_token")
        self.refresh_token = token_data.get("refresh_token")
        
        # Extraer claims del id_token o JWT
        self._parse_jwt_user(token_data.get("id_token") or self.auth_token)
        self.user_data = token_data
        
        logger.info(f"OAuth exitoso para {self.username}. Token obtenido.")
        return {
            "success": True,
            "username": self.username,
            "auth_token": self.auth_token,
            "refresh_token": self.refresh_token,
            "user_data": self.user_data
        }

    async def refresh_access_token(self, refresh_token: Optional[str] = None) -> Dict[str, Any]:
        """Refresca el token de acceso OAuth2 usando el refresh_token."""
        token_to_use = refresh_token or self.refresh_token
        if not token_to_use:
            raise Exception("No hay refresh_token disponible para renovar la sesión.")

        token_url = f"{B2C_BASE_URL}/token?p={POLICY_SIGN_IN}"
        data = {
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": token_to_use,
        }

        resp = await self.client.post(token_url, data=data)
        if resp.status_code != 200:
            raise Exception(f"Error renovando token ({resp.status_code}): {resp.text}")

        token_data = resp.json()
        self.auth_token = token_data.get("access_token")
        if "refresh_token" in token_data:
            self.refresh_token = token_data["refresh_token"]
        return token_data

    async def get_application_token(self) -> str:
        """Obtiene el token de infraestructura de Azure AD para servicios de soporte/push."""
        if self.application_token:
            return self.application_token

        data = {
            "client_id": APP_CLIENT_ID,
            "client_secret": APP_CLIENT_SECRET,
            "grant_type": "client_credentials"
        }
        resp = await self.client.post(APP_TOKEN_URL, data=data)
        if resp.status_code == 200:
            self.application_token = resp.json().get("access_token")
            return self.application_token
        raise Exception(f"Error obteniendo token de aplicación ({resp.status_code}): {resp.text}")

    def _parse_jwt_user(self, jwt_str: Optional[str]):
        """Decodifica el payload JWT para extraer email o username sin validar firma localmente."""
        if not jwt_str or "." not in jwt_str:
            return
        try:
            import base64
            parts = jwt_str.split(".")
            if len(parts) >= 2:
                payload_b64 = parts[1]
                # Pad base64
                payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
                payload_json = json.loads(base64.urlsafe_b64decode(payload_b64.encode()))
                email = (
                    payload_json.get("emails", [None])[0]
                    or payload_json.get("email")
                    or payload_json.get("preferred_username")
                    or payload_json.get("sub")
                )
                if email:
                    self.username = email
        except Exception as e:
            logger.debug(f"No se pudo extraer claims de JWT: {e}")

    async def login_b2c(self, username: str, password: str) -> Dict[str, Any]:
        """
        Realiza la autenticación programática completa en Azure AD B2C (SelfAsserted)
        emulando el flujo web oficial y canjeando el código de autorización automáticamente.
        Evita cualquier bloqueo de navegador por protocolo móvil com.legrandgroup.c100x://.
        """
        authorize_url = self.get_authorize_url()
        async with httpx.AsyncClient(follow_redirects=False, timeout=30.0) as b2c_http:
            # 1. Solicitar página de login y extraer SETTINGS
            r1 = await b2c_http.get(authorize_url)
            if r1.status_code != 200:
                raise Exception(f"Fallo cargando página B2C ({r1.status_code})")

            settings_match = re.search(r"var\s+SETTINGS\s*=\s*(\{.*?\});", r1.text)
            if not settings_match:
                raise Exception("No se encontró el objeto SETTINGS en la respuesta B2C")

            settings = json.loads(settings_match.group(1))
            csrf = settings["csrf"]
            trans_id = settings["transId"]
            tenant = settings["hosts"]["tenant"]
            policy = settings["hosts"]["policy"]
            api = settings.get("api", "CombinedSigninAndSignup")

            # 2. SelfAsserted POST con credenciales
            self_asserted_url = f"https://eliotclouduamprd.b2clogin.com{tenant}/SelfAsserted?tx={trans_id}&p={policy}"
            headers = {
                "X-CSRF-TOKEN": csrf,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": "https://eliotclouduamprd.b2clogin.com",
                "Referer": authorize_url,
                "X-Requested-With": "XMLHttpRequest",
            }
            data = {
                "request_type": "RESPONSE",
                "logonIdentifier": username.strip(),
                "password": password.strip(),
            }

            r2 = await b2c_http.post(self_asserted_url, headers=headers, data=data)
            if r2.status_code != 200:
                raise Exception(f"Fallo en SelfAsserted ({r2.status_code}): {r2.text}")

            res2_json = r2.json()
            if res2_json.get("status") != "200":
                err = res2_json.get("message") or str(res2_json)
                raise Exception(f"Credenciales incorrectas o error B2C: {err}")

            # 3. GET confirmed para capturar el 302 redirect hacia com.legrandgroup.c100x://oauth2redirect
            confirmed_url = f"https://eliotclouduamprd.b2clogin.com{tenant}/api/{api}/confirmed?csrf_token={csrf}&tx={trans_id}&p={policy}"
            r3 = await b2c_http.get(confirmed_url)

            redirect_loc = r3.headers.get("location")
            if not redirect_loc:
                loc_match = re.search(r'href=[\"\'](com\.legrandgroup\.c100x:[^\"\']+)[\"\']', r3.text)
                if loc_match:
                    redirect_loc = loc_match.group(1)
                else:
                    raise Exception(f"No se recibió redirección tras confirmar login ({r3.status_code})")

            # 4. Canjear el código por los tokens
            return await self.exchange_code(redirect_loc)

    async def login(self, username: str, password: str) -> Dict[str, Any]:
        """
        Flujo unificado de inicio de sesión de Classe 100X.
        Soporta:
        1. Token JWT o código OAuth directo pasado en username/password.
        2. Autenticación programática automática con Azure AD B2C (SelfAsserted).
        3. Fallback de cliente de aplicación Azure AD / legado.
        """
        cleaned_user = username.strip()
        cleaned_pass = password.strip()

        # 1. ¿El usuario introdujo un token Bearer o JWT directo?
        if cleaned_user.startswith("eyJ") or cleaned_pass.startswith("eyJ"):
            jwt_token = cleaned_user if cleaned_user.startswith("eyJ") else cleaned_pass
            self.auth_token = jwt_token
            self._parse_jwt_user(jwt_token)
            if not self.username:
                self.username = "c100x_user"
            self.user_data = {"auth_token": jwt_token, "source": "direct_jwt"}
            return {
                "success": True,
                "username": self.username,
                "auth_token": self.auth_token,
                "user_data": self.user_data
            }

        # 2. ¿El usuario introdujo un código o enlace de retorno de OAuth2?
        if "code=" in cleaned_user or "oauth2redirect" in cleaned_user:
            return await self.exchange_code(cleaned_user)
        if "code=" in cleaned_pass or "oauth2redirect" in cleaned_pass:
            return await self.exchange_code(cleaned_pass)

        # 3. Flujo automático de Azure AD B2C (SelfAsserted -> confirmed -> token)
        if cleaned_user and cleaned_pass:
            try:
                logger.info(f"Iniciando login automático Azure AD B2C para {cleaned_user}...")
                return await self.login_b2c(cleaned_user, cleaned_pass)
            except Exception as b2ce:
                logger.warning(f"Intento B2C automático fallido: {b2ce}")

        # 4. Fallback retrocompatible con el endpoint de legado (/eliot/users/sign_in)
        for prj in LEGACY_PRJ_NAMES:
            try:
                headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "prj_name": prj,
                    "User-Agent": "doorentry-classe100x-android",
                }
                payload = {
                    "username": cleaned_user,
                    "pwd": cleaned_pass,
                    "appVersion": "1.0.0",
                    "registrationId": ""
                }
                resp = await self.client.post(f"{LEGACY_HOST}/eliot/users/sign_in", json=payload, headers=headers, timeout=10.0)
                if resp.status_code in (200, 201):
                    token = resp.headers.get("auth_token")
                    body = resp.json() if resp.text else {}
                    if not token and "auth_token" in body:
                        token = body["auth_token"]
                    if token:
                        self.auth_token = token
                        self.username = cleaned_user
                        self.user_data = body
                        logger.info(f"Login exitoso en modo compatibilidad legado ({prj}) para {cleaned_user}")
                        return {
                            "success": True,
                            "username": self.username,
                            "auth_token": self.auth_token,
                            "user_data": body
                        }
            except Exception as le:
                logger.debug(f"Fallo fallback legado prj {prj}: {le}")

        # Si no se pudo autenticar por ninguna vía, devolver error claro
        auth_url = self.get_authorize_url()
        raise Exception(
            f"No se pudo autenticar las credenciales. "
            f"Para Classe 100X con Azure AD B2C, utilice el enlace de inicio de sesión de Eliot: {auth_url}"
        )

    async def logout(self) -> bool:
        """Cierra la sesión localmente."""
        self.auth_token = None
        self.refresh_token = None
        self.username = None
        self.user_data = {}
        return True

    async def get_modules(self) -> List[Dict[str, Any]]:
        """
        Obtiene los módulos/dispositivos asociados al usuario en la nube de Eliot
        GET https://api.developer.legrand.com/servicecatalog/api/v3.0/modules
        """
        url = f"{self.api_base_url}/servicecatalog/api/v3.0/modules"
        resp = await self.client.get(url, headers=self._headers())
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("modules", [data])
            return []
        
        # Fallback a v2.0
        url_v2 = f"{self.api_base_url}/devicemanagement/api/v2.0/modules"
        resp_v2 = await self.client.get(url_v2, headers=self._headers())
        if resp_v2.status_code == 200:
            data = resp_v2.json()
            return data if isinstance(data, list) else data.get("modules", [])

        # Fallback a endpoint legado si el token corresponde al sistema anterior
        try:
            url_leg = f"{LEGACY_HOST}/eliot/plants"
            resp_leg = await self.client.get(url_leg, headers={"auth_token": self.auth_token or "", "prj_name": "C100X"})
            if resp_leg.status_code == 200:
                return resp_leg.json()
        except Exception:
            pass

        return []

    async def get_plants(self) -> List[Dict[str, Any]]:
        """
        Obtiene la lista de instalaciones/plantas del usuario.
        Combina el catálogo de plantas con los módulos de Classe 100X.
        """
        modules = await self.get_modules()
        
        # Consultar plantas oficiales del servicecatalog
        plants_catalog = []
        try:
            resp = await self.client.get(f"{self.api_base_url}/servicecatalog/api/v3.0/plants", headers=self._headers())
            if resp.status_code == 200:
                plants_catalog = resp.json() if isinstance(resp.json(), list) else []
        except Exception as pe:
            logger.debug(f"Error consultando catalogo de plantas: {pe}")

        catalog_map = {p.get("id"): p for p in plants_catalog if isinstance(p, dict) and p.get("id")}
        plants_dict = {}

        # Mapear primero todas las plantas del catálogo
        for p_id, p_info in catalog_map.items():
            plants_dict[p_id] = {
                "id": p_id,
                "PlantId": p_id,
                "name": p_info.get("name") or "Instalación Classe 100X",
                "type": p_info.get("type", "house"),
                "country": p_info.get("country", "es"),
                "deviceModel": "bs-classe100x",
                "modules": []
            }

        # Asociar módulos a cada planta
        for mod in modules:
            p_id = mod.get("plantId") or mod.get("PlantId") or "plant_c100x"
            if p_id not in plants_dict:
                plants_dict[p_id] = {
                    "id": p_id,
                    "PlantId": p_id,
                    "name": mod.get("name") or mod.get("deviceModel") or "Instalación Classe 100X",
                    "type": "C100X",
                    "deviceModel": mod.get("deviceModel", "C1X"),
                    "modules": []
                }
            plants_dict[p_id]["modules"].append(mod)

        # Ordenar: poner primero aquellas plantas que tengan módulos y pasarela conectada
        sorted_plants = sorted(
            plants_dict.values(),
            key=lambda x: (
                len(x["modules"]) > 0,
                any(m.get("connectionState") == "CONNECTED" for m in x["modules"]),
                len(x["modules"])
            ),
            reverse=True
        )

        if sorted_plants:
            return sorted_plants

        return [{
            "id": "c100x_main",
            "PlantId": "c100x_main",
            "name": "Classe 100X (Vivienda)",
            "type": "C100X",
            "modules": []
        }]

    async def get_gateways(self, plant_id: str) -> List[Dict[str, Any]]:
        """
        Obtiene las pasarelas Classe 100X asociadas a una planta.
        """
        modules = await self.get_modules()
        gateways = []

        for mod in modules:
            mod_plant = mod.get("plantId") or mod.get("PlantId")
            if not plant_id or mod_plant == plant_id or plant_id == "c100x_main":
                is_gw = (
                    mod.get("device") == "gateway"
                    or "classe" in mod.get("deviceModel", "").lower()
                    or "c100x" in mod.get("deviceModel", "").lower()
                    or (mod.get("gatewayInstallation") is True and not mod.get("gatewayId"))
                )
                if is_gw:
                    gateways.append({
                        "id": mod.get("id") or "gw_c100x",
                        "gatewayId": mod.get("id") or "gw_c100x",
                        "name": mod.get("name") or f"Classe 100X ({mod.get('ipAddress') or mod.get('macAddress') or 'Conectado'})",
                        "deviceModel": mod.get("deviceModel", "bs-classe100x"),
                        "macAddress": mod.get("macAddress", ""),
                        "ipAddress": mod.get("ipAddress", ""),
                        "firmwareVersion": mod.get("firmwareVersion", ""),
                        "connectionState": mod.get("connectionState", "CONNECTED"),
                        "plantId": plant_id
                    })

        if gateways:
            return gateways

        seen_gw_ids = set()
        for mod in modules:
            mod_plant = mod.get("plantId") or mod.get("PlantId")
            if (not plant_id or mod_plant == plant_id) and mod.get("gatewayId"):
                gw_id = mod["gatewayId"]
                if gw_id not in seen_gw_ids:
                    seen_gw_ids.add(gw_id)
                    gateways.append({
                        "id": gw_id,
                        "gatewayId": gw_id,
                        "name": f"Videoportero Classe 100X ({gw_id[:8]}...)",
                        "deviceModel": "bs-classe100x",
                        "macAddress": "",
                        "ipAddress": "",
                        "plantId": plant_id
                    })

        if gateways:
            return gateways

        return [{
            "id": DEFAULT_GATEWAY_ID,
            "gatewayId": DEFAULT_GATEWAY_ID,
            "name": "Classe 100X (a80fcd70)",
            "deviceModel": "bs-classe100x",
            "macAddress": "",
            "ipAddress": "",
            "plantId": plant_id
        }]

    async def get_gateway_info(self, plant_id: str, gw_id: str) -> Dict[str, Any]:
        """Obtiene información detallada de una pasarela Classe 100X."""
        url = f"{self.api_base_url}/servicecatalog/api/v3.0/modules/{gw_id}"
        resp = await self.client.get(url, headers=self._headers())
        if resp.status_code == 200:
            return resp.json()
        return {"id": gw_id or DEFAULT_GATEWAY_ID, "plantId": plant_id, "deviceModel": "bs-classe100x"}

    async def get_sip_credentials(self, plant_id: str, gw_id: str) -> List[Dict[str, Any]]:
        """
        Obtiene las cuentas SIP aprovisionadas para el dispositivo Classe 100X:
        GET https://api.developer.legrand.com/vde/sip/v1.0/devices/{device_id}/sipaccounts
        """
        url = f"{self.api_base_url}/vde/sip/v1.0/devices/{gw_id}/sipaccounts"
        resp = await self.client.get(url, headers=self._headers())
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return data
            return data.get("sipUsers", data.get("SipUsers", [data]))

        # Si no existen credenciales, intentar aprovisionar una nueva cuenta SIP
        try:
            new_acc = await self.create_sip_user(gw_id)
            if new_acc:
                return [new_acc]
        except Exception as ce:
            logger.debug(f"No se pudo crear usuario SIP automático: {ce}")

        # Retornar objeto validado para la pasarela Classe 100X
        target_gw = gw_id if (gw_id and len(gw_id) == 36 and "-" in gw_id) else DEFAULT_GATEWAY_ID
        sip_user = os.environ.get("SIP_USER", "")
        sip_pass = os.environ.get("SIP_PASSWORD", "")
        return [{
            "sipId": f"sip_{target_gw}",
            "Username": sip_user,
            "username": sip_user,
            "password": sip_pass,
            "sipServer": f"{target_gw}.bs.iotleg.com",
            "deviceId": target_gw,
            "plantId": plant_id
        }]

    async def create_sip_user(self, gw_id: str) -> Dict[str, Any]:
        """
        Crea una cuenta SIP para el dispositivo:
        POST https://api.developer.legrand.com/vde/sip/v1.0/devices/{device_id}/sipaccount
        """
        url = f"{self.api_base_url}/vde/sip/v1.0/devices/{gw_id}/sipaccount"
        resp = await self.client.post(url, headers=self._headers(), json={})
        if resp.status_code in (200, 201):
            return resp.json()
        return {}

    async def get_topology(self, plant_id: str, gw_id: str) -> Dict[str, Any]:
        """
        Devuelve los dispositivos controlables (cerraduras, luz de escalera, placa de calle)
        configurados para Classe 100X extrayendo las direcciones de los tags de los módulos.
        """
        modules = await self.get_modules()
        locks = []
        lights = []
        cameras = []

        relevant = [
            m for m in modules
            if m.get("gatewayId") == gw_id
            or m.get("id") == gw_id
            or (not gw_id and m.get("plantId") == plant_id)
        ]

        for mod in relevant:
            dev_type = mod.get("device")
            mod_id = mod.get("id", "")
            address = "4"
            button_id = None
            visible = 1
            for tag in mod.get("tags", []):
                if tag.get("key") == "PrivateAddress":
                    try:
                        val = json.loads(tag.get("value", "{}"))
                        for av in val.get("addressValues", []):
                            if av.get("name") == "address":
                                address = av.get("value", address)
                        button_id = val.get("buttonId")
                        if "visible" in val:
                            visible = int(val.get("visible", 1))
                    except Exception:
                        pass

            if dev_type == "lock":
                name = f"Cerradura (Dir {address})"
                if address == "20":
                    name = "Cerradura Principal PE0 (Dir 20)"
                elif address == "21":
                    name = "Cerradura Secundaria (Dir 21)"
                elif address == "22":
                    name = "Cerradura Auxiliar (Dir 22)"

                locks.append({
                    "id": mod_id,
                    "name": name,
                    "deviceAddr": address,
                    "buttonId": button_id,
                    "cid": "10060",
                    "action": "lock.setStatus"
                })
            elif dev_type == "light":
                name = f"Luz de Escalera (Dir {address})"
                lights.append({
                    "id": mod_id,
                    "name": name,
                    "deviceAddr": address,
                    "buttonId": button_id,
                    "cid": "2009",
                    "action": "light.setStatus"
                })
            elif dev_type == "audioVideoTerminal":
                # Solo aceptar unidades exteriores / cámaras (EU)
                if mod.get("deviceType") == "IU":
                    continue
                cam_guid = mod_id if (mod_id and len(mod_id) == 36 and "-" in mod_id) else DEFAULT_ENTRANCE_PANEL_GUID
                if address == "20" or cam_guid == DEFAULT_ENTRANCE_PANEL_GUID:
                    cam_name = "Placa Exterior Principal (PE0)"
                    bus_addr = "20"
                elif address == "21" or cam_guid == "7c5a796f-7868-46a3-9fa6-091d73e25cad":
                    cam_name = "Cámara Interior (PE1)"
                    bus_addr = "21"
                else:
                    cam_name = f"Cámara Exterior (PE{address})"
                    bus_addr = address

                cameras.append({
                    "id": cam_guid,
                    "name": cam_name,
                    "deviceAddr": cam_guid,
                    "bus_address": bus_addr,
                    "buttonId": button_id,
                    "action": "camera.turnOn"
                })

        # Ordenar cámaras canónicamente: PE0 (dir 20) primero, PE1 (dir 21) segundo
        cameras.sort(key=lambda c: 0 if (c.get("bus_address") == "20" or c.get("id") == DEFAULT_ENTRANCE_PANEL_GUID) else (1 if (c.get("bus_address") == "21" or c.get("id") == "7c5a796f-7868-46a3-9fa6-091d73e25cad") else 2))

        # Ordenar cerraduras por dirección de bus
        locks.sort(key=lambda l: l.get("deviceAddr", "99"))

        if not locks:
            locks = [{
                "id": "main",
                "name": "Cerradura Principal (Classe 100X)",
                "deviceAddr": "4",
                "cid": "10060",
                "action": "lock.setStatus"
            }]
        if not lights:
            lights = [{
                "id": "staircase",
                "name": "Luz de Escalera (Classe 100X)",
                "deviceAddr": "4",
                "cid": "2009",
                "action": "light.setStatus"
            }]
        if not cameras:
            cameras = [
                {
                    "id": DEFAULT_ENTRANCE_PANEL_GUID,
                    "name": "Placa Exterior Principal (PE0)",
                    "deviceAddr": DEFAULT_ENTRANCE_PANEL_GUID,
                    "bus_address": "20",
                    "action": "camera.turnOn"
                },
                {
                    "id": "7c5a796f-7868-46a3-9fa6-091d73e25cad",
                    "name": "Cámara Interior (PE1)",
                    "deviceAddr": "7c5a796f-7868-46a3-9fa6-091d73e25cad",
                    "bus_address": "21",
                    "action": "camera.turnOn"
                }
            ]
        elif len(cameras) == 1 and (gw_id == DEFAULT_GATEWAY_ID or not gw_id):
            cameras.append({
                "id": "7c5a796f-7868-46a3-9fa6-091d73e25cad",
                "name": "Cámara Interior (PE1)",
                "deviceAddr": "7c5a796f-7868-46a3-9fa6-091d73e25cad",
                "bus_address": "21",
                "action": "camera.turnOn"
            })

        return {
            "devices": {
                "locks": locks,
                "lights": lights,
                "cameras": cameras,
                "others": []
            }
        }
