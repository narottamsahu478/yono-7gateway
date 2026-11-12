"""UOTP SMS-Activate-compatible API adapter."""
import aiohttp


class UOTPProvider:
    base_url = "https://uotp.store/api/stubs/handler_api.php"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def _get(self, session: aiohttp.ClientSession, action: str, **params) -> str:
        if not self.api_key:
            raise ValueError("Set UOTP_API_KEY environment variable")
        async with session.get(
            self.base_url,
            params={"api_key": self.api_key, "action": action, **params},
            timeout=aiohttp.ClientTimeout(total=12),
        ) as response:
            response.raise_for_status()
            return (await response.text()).strip()

    async def get_balance(self, session):
        return await self._get(session, "getBalance")

    async def buy_number(self, session, service: str, country: str = "22", operator: str = ""):
        params = {"service": service, "country": country}
        if operator:
            params["operator"] = operator
        return await self._get(session, "getNumber", **params)

    async def get_status(self, session, txn_id: str):
        return await self._get(session, "getStatus", id=str(txn_id))

    async def set_status(self, session, txn_id: str, status: str):
        if str(status) not in ("3", "8"):
            raise ValueError("UOTP supports status 3 or 8")
        return await self._get(session, "setStatus", id=str(txn_id), status=str(status))
