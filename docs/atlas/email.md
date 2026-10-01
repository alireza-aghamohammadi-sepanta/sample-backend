---
type: concept
title: Email Service
summary: Transactional email dispatch for password recovery with logging fallback and test inspection capabilities.
related: ["auth.md", "architecture.md"]
source_paths: ["app/services/email.py"]
---

# Email Service

The email dispatch subsystem in `app/services/email.py` provides transactional messaging capabilities for the backend, primarily used for account notifications and password reset tokens in [authentication](auth.md).

## Architecture

The service is encapsulated in the `EmailService` class:

```python
class EmailService:
    def __init__(self, from_email: str = "noreply@example.com"):
        self.from_email = from_email
        self.sent_emails: list[dict[str, Any]] = []
```

### In-Memory Tracking & Test Inspection
To support unit testing, local development, and CI environments without connecting to external SMTP or transactional email providers (e.g. SendGrid, Mailgun):
- All sent emails are appended to the internal `self.sent_emails` list.
- Each email dispatch is logged using Python's standard `logging.getLogger(__name__)`.
- Automated test suites can inspect `sent_emails` directly to assert that password reset tokens and links were properly generated and routed to the correct recipient.

## Methods

### 1. `send_password_reset_email`
Dispatches a password recovery message containing the unhashed reset token:
```python
def send_password_reset_email(
    self,
    to_email: str,
    reset_token: str,
    reset_url: str | None = None,
) -> None:
    url = reset_url or f"/reset-password?token={reset_token}"
    ...
```
- Constructs the destination URL (`/reset-password?token={reset_token}`).
- Appends the payload (`to_email`, `reset_token`, `reset_url`, `subject`) to `sent_emails`.
- Logs dispatch details for auditing and debugging.

### 2. `send_email`
Generic transactional email dispatch:
```python
def send_email(self, to_email: str, subject: str, body: str) -> None:
    ...
```

## Lifespan and Dependency Injection

- Instantiated via `build_email_service()` on application startup and assigned to `app.state.email_service` (see [architecture](architecture.md)).
- Consumed in API route handlers via the `get_email_service` FastAPI dependency:
  ```python
  def get_email_service(request: Request = None) -> EmailService:
      if request is not None and hasattr(request, "app"):
          service = getattr(request.app.state, "email_service", None)
          if service is not None:
              return service
      return _default_email_service
  ```
