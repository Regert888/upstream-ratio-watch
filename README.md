# NewAPI Price Watch

NewAPI Price Watch is a small Python web dashboard for monitoring NewAPI upstream group ratios. It is designed for a few manually managed upstream sites, not bulk registration or account farming.

## Features

- Monitor NewAPI `GET /api/user/groups`
- Optional authenticated group collection with system access token and `New-Api-User`
- Detect group ratio changes, added groups, removed groups, and description changes
- Show hidden/auth-only group counts
- Store snapshots and change history in SQLite
- Send email notifications for changes through SMTP
- Single-file Python backend with static HTML/CSS/JS frontend

## Run

```bash
python app.py
```

Open:

```text
http://127.0.0.1:8000
```

The app creates runtime data under `data/`. Do not commit that directory because it may contain site URLs, access tokens, SMTP credentials, and monitoring history.

## Notification

Email notification uses SMTP settings configured in the web UI:

- SMTP host
- SMTP port
- username
- password or app authorization code
- sender
- recipients
- SSL toggle

## Notes

- Default monitor interval is 3 minutes.
- Minimum monitor interval is 1 minute.
- This project currently focuses on NewAPI-compatible group ratio monitoring.
