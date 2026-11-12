# UOTP gateway integration

API: https://uotp.store/api/stubs/handler_api.php

Set `UOTP_API_KEY` in your Windows PowerShell session before running the app:

```powershell
$env:UOTP_API_KEY = Read-Host "UOTP API key"
python run.py
```

Select **UOTP Service** in Gateway Provider. Set SERVICE ID (UOTP service code, e.g. `wa`), COUNTRY CODE (`22` is the UI default; verify with UOTP for your country), and optional OPERATOR (leave empty if not needed). Click Apply or Start.

Actions: getBalance, getNumber, getStatus, setStatus. Cancellation requires exact ACCESS_CANCEL response; status 8 may also mean completion, so do not treat other responses as cancelled.

No live purchase, cancellation, or authenticated API request was performed while building this integration.
