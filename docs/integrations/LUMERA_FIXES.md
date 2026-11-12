# Lumera fixes (v3.7 patched)

- P7 remains **LMR BAL** and fetches balance using `getBalance`.
- Lumera defaults to service `yonoslots` and operator `1` when selected in the UI. Country remains `22`.
- Removed the automatic five-success completion threshold for Lumera and UOTP.
- Lumera completion now calls `setStatus(status=8)` and only reports completion on an acknowledged activation/ready response. Cancellation still requires `ACCESS_CANCEL`.
- Failed purchase API response codes are logged without exposing credentials.
- Existing providers and credentials were otherwise preserved.

**Important:** This package has not been tested against a live paid activation. `setStatus=8` is state-changing. Test carefully with a number you control. API credentials are embedded in `config.py`; keep the ZIP private and rotate any exposed key.
