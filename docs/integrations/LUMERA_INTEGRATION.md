# Lumera SMS (LMR BAL / P7)

Set the `LUMERA_API_KEY` environment variable before starting the engine. PowerShell 5.1 example:

```powershell
$env:LUMERA_API_KEY = Read-Host "Enter Lumera API key"
python run.py
```

Never commit or share the API key. The confirmed `getBalance` response is `ACCESS_BALANCE:50` from the user test. P7 shows this balance on startup, and refreshes it while Lumera is selected and the engine is running. Purchase (`getNumber`), polling (`getStatus`) and cancellation (`setStatus`, status 8) use an assumed SMS-Activate-compatible protocol and have NOT been live-tested; test with a controlled activation before production use. The country parameter is fixed to 22. Service ID and operator are configurable in the dashboard.
