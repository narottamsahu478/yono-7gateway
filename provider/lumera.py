"""Lumera SMS adapter. SMS-Activate-style actions except balance are unverified."""
from provider.uotp import UOTPProvider


class LumeraProvider(UOTPProvider):
    base_url = "https://lumerasms.org/api/stubs/handler_api.php"

    async def _get(self, session, action: str, **params):
        if not self.api_key:
            raise ValueError("LUMERA_API_KEY is missing in config.py")
        return await super()._get(session, action, **params)
