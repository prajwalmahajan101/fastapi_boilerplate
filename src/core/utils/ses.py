"""``AsyncSESClient`` — async SES email with attachments and threading headers.

Builds raw MIME on the application side so the ``Message-ID`` is
deterministic — it's the same value that hits SES, so callers can store
it on the parent thread for ``In-Reply-To`` / ``References`` reuse later
without guessing the host's format.

Transient SES errors (throttling, internal failures, request expiry)
surface as ``TransientError`` so ``@resilient("ses")`` retries them.
Permanent failures (rejected sender, unverified identity, configuration
problems) surface as ``SESError`` and propagate without retry.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import uuid
from dataclasses import dataclass, field
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import Any, Iterable

from src.core.exceptions.infrastructure import SESError, TransientError
from src.core.resilience.decorators import resilient
from src.core.runtime import get_settings
from src.core.utils.aws import get_aws_client
from src.core.utils.log_sanitization import safe_log_dict

logger = logging.getLogger(__name__)

_SES_TRANSIENT_ERROR_CODES = frozenset(
    {
        "Throttling",
        "ThrottlingException",
        "RequestLimitExceeded",
        "TooManyRequestsException",
        "ServiceUnavailable",
        "ServiceUnavailableException",
        "InternalFailure",
        "InternalServerError",
        "RequestExpired",
        "RequestTimeout",
        "RequestTimeoutException",
    }
)


@dataclass(frozen=True)
class EmailAttachment:
    """One MIME attachment for ``AsyncSESClient.send_email``."""

    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class EmailMessage:
    """Full message payload for ``send_email_with_attachments``."""

    subject: str
    body_html: str
    to_addresses: list[str]
    cc_addresses: list[str] | None = None
    bcc_addresses: list[str] | None = None
    in_reply_to: str | None = None
    references: list[str] | None = None
    attachments: list[EmailAttachment] = field(default_factory=list)


def _ses_region() -> str | None:
    """Read the optional SES-specific AWS region from settings.

    Returns:
        Region name (e.g. ``"us-east-1"``) or ``None`` to fall back to
        the global AWS region.
    """
    region = get_settings().ses_region or ""
    return region or None


def _sender_domain(sender: str) -> str:
    """Extract the domain portion of an email address (host for Message-ID).

    Args:
        sender: Full email address (or anything without ``@``).

    Returns:
        Domain after the last ``@``; ``"localhost"`` when no ``@``
        was present.
    """
    if "@" in sender:
        return sender.rsplit("@", 1)[-1].strip() or "localhost"
    return "localhost"


def _generate_message_id(sender: str) -> str:
    """Mint an RFC-5322 Message-ID whose host matches ``sender``'s domain.

    Generated client-side so the value the caller stores for future
    ``In-Reply-To`` is the same one SES will see on the wire.

    Args:
        sender: ``From`` address.

    Returns:
        ``<uuid@domain>`` Message-ID string.
    """
    return f"<{uuid.uuid4().hex}@{_sender_domain(sender)}>"


def _build_attachment_part(att: EmailAttachment):
    """Build a MIME part for ``att`` with the right ``Content-Type`` subtype.

    Image attachments use :class:`MIMEImage`, text use :class:`MIMEText`,
    everything else falls back to :class:`MIMEApplication`. The
    ``Content-Disposition`` header is set to ``attachment``.

    Args:
        att: Attachment payload + content-type metadata.

    Returns:
        MIME part ready to attach to a multipart message.
    """
    main, _, sub = att.content_type.partition("/")
    if main == "image" and sub:
        part = MIMEImage(att.data, _subtype=sub)
    elif main == "text":
        part = MIMEText(
            att.data.decode("utf-8", errors="replace"),
            _subtype=sub or "plain",
            _charset="utf-8",
        )
    else:
        sub = sub or mimetypes.guess_extension(att.content_type) or "octet-stream"
        part = MIMEApplication(att.data, _subtype=sub.lstrip("."))
    part.add_header("Content-Disposition", "attachment", filename=att.filename)
    return part


def _classify_client_error(exc: Exception) -> Exception:
    """Wrap a SES ``ClientError`` as ``TransientError`` or ``SESError``.

    Throttling / internal failures retry-eligible (TransientError); rejected
    sender / unverified identity / config issues do not (SESError).

    Args:
        exc: A boto3 ``ClientError`` raised by SES.

    Returns:
        Either ``TransientError`` (retryable) or ``SESError`` (terminal).
    """
    code = ""
    try:
        code = exc.response.get("Error", {}).get("Code", "")  # type: ignore[attr-defined]
    except AttributeError:
        pass
    if code in _SES_TRANSIENT_ERROR_CODES:
        return TransientError(f"SES transient failure ({code}): {exc}")
    return SESError(
        f"SES request failed ({code or 'Unknown'}): {exc}", operation="send"
    )


class AsyncSESClient:
    """Static async methods over the sync boto3 SES client (via ``to_thread``)."""

    @staticmethod
    @resilient("ses")
    async def send_email(
        *,
        recipient_emails: list[str],
        subject: str,
        body_html: str,
        sender_email: str | None = None,
        cc_emails: list[str] | None = None,
        bcc_emails: list[str] | None = None,
        in_reply_to: str | None = None,
        references: list[str] | None = None,
        attachments: Iterable[EmailAttachment] | None = None,
    ) -> dict[str, Any]:
        """Send an HTML email through SES, returning the wire Message-ID for threading.

        Args:
            recipient_emails: ``To`` addresses.
            subject: Email subject line.
            body_html: HTML body.
            sender_email: Override ``ses_default_sender`` for this call.
            cc_emails: Optional ``Cc`` addresses.
            bcc_emails: Optional ``Bcc`` addresses (envelope only).
            in_reply_to: Parent message's wire Message-ID for threading.
            references: Existing thread references; appended with
                ``in_reply_to``.
            attachments: Optional MIME attachments.

        Returns:
            ``{"message_id": <SES MessageId>, "message_id_header": <wire ID>,
            "response": <raw>}`` — store ``message_id_header`` for the next
            reply's ``in_reply_to``.

        Raises:
            SESError: No sender configured, or permanent SES failure.
            TransientError: Throttling / internal SES errors (retried by
                the ``@resilient`` wrapper).
        """
        from botocore.exceptions import BotoCoreError, ClientError

        sender = sender_email or get_settings().ses_default_sender
        if not sender:
            raise SESError(
                "No sender email configured (set ses_default_sender or pass sender_email).",
                operation="send",
            )

        cc_emails = cc_emails or []
        bcc_emails = bcc_emails or []
        attachment_list = list(attachments or [])
        envelope_recipients = list(recipient_emails) + cc_emails + bcc_emails
        message_id_header = _generate_message_id(sender)

        if attachment_list:
            msg: Any = MIMEMultipart("mixed")
            msg.attach(MIMEText(body_html, "html", "utf-8"))
            for att in attachment_list:
                msg.attach(_build_attachment_part(att))
        else:
            msg = MIMEText(body_html, "html", "utf-8")

        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = ", ".join(recipient_emails)
        if cc_emails:
            msg["Cc"] = ", ".join(cc_emails)
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = message_id_header
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            chain = list(references or [])
            if in_reply_to not in chain:
                chain.append(in_reply_to)
            msg["References"] = " ".join(chain)

        logger.info(
            "Sending email via SES",
            extra=safe_log_dict(
                recipient_count=len(recipient_emails),
                has_cc=bool(cc_emails),
                has_bcc=bool(bcc_emails),
                attachment_count=len(attachment_list),
                subject=subject,
                sender=sender,
                threaded=bool(in_reply_to),
                message_id_header=message_id_header,
            ),
        )

        client = get_aws_client("ses", region=_ses_region())
        try:
            response = await asyncio.to_thread(
                client.send_raw_email,
                Source=sender,
                Destinations=envelope_recipients,
                RawMessage={"Data": msg.as_string()},
            )
        except ClientError as client_exc:
            logger.error(
                "Failed to send email via SES",
                extra=safe_log_dict(
                    recipient_count=len(recipient_emails),
                    subject=subject,
                    error=str(client_exc),
                ),
            )
            classified_exc = _classify_client_error(client_exc)
            if isinstance(classified_exc, TransientError):
                raise TransientError(str(classified_exc)) from client_exc
            raise SESError(str(classified_exc), operation="send") from client_exc
        except BotoCoreError as exc:
            logger.error(
                "Transient SES failure",
                extra=safe_log_dict(
                    recipient_count=len(recipient_emails),
                    subject=subject,
                    error=str(exc),
                ),
            )
            raise TransientError(f"SES transport failure: {exc}") from exc

        ses_message_id = response.get("MessageId", "")
        logger.info(
            "Email sent via SES",
            extra=safe_log_dict(
                ses_message_id=ses_message_id,
                message_id_header=message_id_header,
                recipient_count=len(recipient_emails),
                subject=subject,
            ),
        )
        return {
            "message_id": ses_message_id,
            "message_id_header": message_id_header,
            "response": response,
        }

    @staticmethod
    async def send_message(
        message: EmailMessage, *, sender_email: str | None = None
    ) -> dict[str, Any]:
        """Send an email described by an :class:`EmailMessage` dataclass.

        Thin adapter over :meth:`send_email` for callers that prefer
        passing one object rather than nine kwargs.

        Args:
            message: Populated ``EmailMessage`` instance.
            sender_email: Optional sender override.

        Returns:
            Same payload as :meth:`send_email`.
        """
        return await AsyncSESClient.send_email(
            recipient_emails=message.to_addresses,
            subject=message.subject,
            body_html=message.body_html,
            sender_email=sender_email,
            cc_emails=message.cc_addresses,
            bcc_emails=message.bcc_addresses,
            in_reply_to=message.in_reply_to,
            references=message.references,
            attachments=message.attachments,
        )
