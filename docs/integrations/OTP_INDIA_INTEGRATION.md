# OTP INDIA integration

Set `OTP_INDIA_API_KEY` in `config.py` (same location as existing provider keys). Do not commit real credentials.

Select OTP INDIA in the dashboard, enter service code and server code from OTP INDIA provider list. The documented handler_api actions are getBalance, getNumber, getStatus, setStatus (3=new SMS, 8=cancel). No documented finish action, so a received OTP is not cancelled. No live requests or paid purchases were made during integration.

Important: OTP INDIA server codes are not the same as other providers' operator values. Validate with a low-risk authorized test before production. Keep existing backup before deployment.
