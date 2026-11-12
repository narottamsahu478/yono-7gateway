# OTP backward-only fallback — laptop test

This is your uploaded full project with the OTP fallback patch merged into `poller/global_poller.py` and the new `utils/otp_state_db.py`.

Rules:
- A code first observed at 07:30:32 can be tried against a still-pending request sent at 07:28:52.
- It cannot be tried against a request sent at 07:33:50, even on later polls.
- An invalid code can fall back only to earlier sequence positions, not upcoming ones.
- Per-activation code first-seen times and per-game attempts persist in SQLite at `data/otp_fallback.sqlite3`.
- A code is consumed after successful verification or an already-registered response.

Caveats:
- First-seen time is the first *fetch* observation, not necessarily actual SMS delivery time, unless the provider exposes a trusted timestamp.
- SQLite claims are per provider activation + code + game. A new request for the same game within the same activation will not retry that same code.
- API verification and provider responses have NOT been live-tested; test on one number first.
- This archive contains your original configuration and data. Do not share it publicly.

Windows PowerShell:
1. Stop your local bot.
2. Extract this full ZIP into a NEW folder, not on top of the running project.
3. `cd "PATH_TO_EXTRACTED_FOLDER"`
4. `python -m compileall -q .`
5. `python run.py`
6. Verify invalid OTPs only go to earlier pending games and never to later OTP requests.

No VPS files were changed.
