"""Email dispatch service: password reset notifications and email delivery.

Provides email dispatch capabilities with mock/logging fallback support for local
and test environments. Sent emails are recorded in memory and logged so tests
can inspect dispatches without contacting real mail servers.
"""

import logging
from typing import Any

from fastapi import Request

logger = logging.getLogger(__name__)


class EmailService:
    """Service responsible for composing and sending transactional emails."""

    def __init__(self, from_email: str = "noreply@example.com"):
        self.from_email = from_email
        self.sent_emails: list[dict[str, Any]] = []

    def send_password_reset_email(
        self,
        to_email: str,
        reset_token: str,
        reset_url: str | None = None,
    ) -> None:
        """Dispatch a password reset email containing the reset token / link."""
        url = reset_url or f"/reset-password?token={reset_token}"
        email_record = {
            "to_email": to_email,
            "reset_token": reset_token,
            "reset_url": url,
            "subject": "Password Reset Request",
        }
        self.sent_emails.append(email_record)
        logger.info(
            "Password reset email dispatched to %s (reset token: %s, url: %s)",
            to_email,
            reset_token,
            url,
        )

    def send_email(self, to_email: str, subject: str, body: str) -> None:
        """Dispatch a generic email."""
        email_record = {
            "to_email": to_email,
            "subject": subject,
            "body": body,
        }
        self.sent_emails.append(email_record)
        logger.info("Email dispatched to %s: %s", to_email, subject)


def build_email_service() -> EmailService:
    """Return a new email service instance."""
    return EmailService()


_default_email_service = EmailService()


def get_email_service(request: Request = None) -> EmailService:
    """FastAPI dependency returning the email service of the application.

    Returns the application-level instance from ``app.state`` if available,
    or falls back to a default instance.
    """
    if request is not None and hasattr(request, "app"):
        service = getattr(request.app.state, "email_service", None)
        if service is not None:
            return service
    return _default_email_service
