import aiohttp


class OTPIndiaProvider:
    """OTP INDIA handler_api adapter. No live purchases during setup."""
    base_url = "https://otpindia.org/api/stubs/handler_api.php"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def _get(self, session: aiohttp.ClientSession, action: str, **params) -> str:
        if not self.api_key or self.api_key == "YOUR_OTP_INDIA_API_KEY":
            raise ValueError("Configure OTP_INDIA_API_KEY in config.py")
        async with session.get(self.base_url, params={"api_key": self.api_key, "action": action, **params}, timeout=aiohttp.ClientTimeout(total=12)) as response:
            response.raise_for_status()
            return (await response.text()).strip()

    async def get_balance(self, session):
        return await self._get(session, "getBalance")

    async def buy_number(self, session, service: str = "yonoslots", server: str = "Provider-1"):
        if not server:
            raise ValueError("OTP INDIA server code is required")
        return await self._get(session, "getNumber", service=service, server=server)

    async def get_status(self, session, txn_id: str):
        return await self._get(session, "getStatus", id=str(txn_id))

    async def set_status(self, session, txn_id: str, status: str):
        if str(status) not in ("3", "8"):
            raise ValueError("Only documented OTP INDIA statuses 3 and 8 are supported")
        return await self._get(session, "setStatus", id=str(txn_id), status=str(status))
