import re
import ssl
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid
from typing import Optional, Tuple

class PermanentEmailError(Exception):
    """Exception raised for permanent delivery failures (e.g., mailbox full, invalid address)."""
    pass

class TransientEmailError(Exception):
    """Exception raised for temporary failures (e.g., connection timeout, auth failure, rate limiting)."""
    pass

class SMTPMailer:
    def __init__(self, host: str, port: int, user: str, passwd: str, from_name: Optional[str] = None):
        self.host = host
        self.port = port
        self.user = user
        self.passwd = passwd
        self.from_name = from_name or user

    @staticmethod
    def _html_to_text(html_body: str) -> str:
        """Converts HTML to simple plaintext using regular expressions."""
        text = html_body or ''
        # Replace line breaks and paragraphs with newlines
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</p>', '\n\n', text, flags=re.IGNORECASE)
        # Strip all other HTML tags
        text = re.sub(r'<[^>]+>', '', text)
        # Replace multiple spaces/newlines
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def send(self, to_email: str, subject: str, html_body: str,
             in_reply_to: Optional[str] = None) -> Tuple[str, str]:
        """
        Sends an email using the Hostinger SMTP settings.
        Supports thread reply headers if in_reply_to is provided.
        Returns:
            Tuple[msg_id, "SENT"]
        Raises:
            PermanentEmailError: If the recipient's mailbox is full, invalid, or permanently blocked.
            TransientEmailError: If a connection, auth, or temporary issue occurs.
        """
        # Clean emails
        to_email = to_email.strip()
        from_domain = self.user.split('@')[-1]
        
        # Prepare message
        msg = MIMEMultipart('alternative')
        msg['From'] = f"{self.from_name} <{self.user}>"
        msg['To'] = to_email
        msg['Subject'] = subject
        
        # Unique Message-ID with matching sender domain
        mid = make_msgid(domain=from_domain)
        msg['Message-ID'] = mid
        
        if in_reply_to:
            msg['In-Reply-To'] = in_reply_to
            msg['References'] = in_reply_to
            
        # Attach plain and html bodies
        text_body = self._html_to_text(html_body)
        msg.attach(MIMEText(text_body, 'plain', 'utf-8'))
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))
        
        # Connect and send
        try:
            # Select SSL/TLS based on port
            if self.port == 465:
                context = ssl.create_default_context()
                server = smtplib.SMTP_SSL(self.host, self.port, context=context, timeout=30)
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=30)
                context = ssl.create_default_context()
                server.starttls(context=context)
                
            with server:
                server.login(self.user, self.passwd)
                server.sendmail(self.user, [to_email], msg.as_string())
                
            return mid, "SENT"
            
        except smtplib.SMTPRecipientsRefused as e:
            # Raised if all recipients are refused. Check response code/message.
            refused_info = e.recipients.get(to_email, (0, ""))
            code, error_msg = refused_info[0], str(refused_info[1]).lower()
            
            # 550: Mailbox unavailable/not found
            # 552: Mailbox full / storage exceeded
            # 554: Transaction failed (permanent rejection)
            is_mailbox_full = "full" in error_msg or "quota" in error_msg or "storage" in error_msg or code == 552
            is_invalid = "not found" in error_msg or "does not exist" in error_msg or "invalid" in error_msg or code == 550
            
            reason = f"Recipient refused ({code}: {refused_info[1]})"
            if is_mailbox_full:
                reason = "Mailbox is full / quota exceeded"
            elif is_invalid:
                reason = "Email address does not exist / invalid"
                
            raise PermanentEmailError(reason)
            
        except smtplib.SMTPDataError as e:
            # Raised if server rejects message data (e.g., spam filter block or mailbox full detected post-data)
            code, error_msg = e.smtp_code, str(e.smtp_error).lower()
            is_mailbox_full = "full" in error_msg or "quota" in error_msg or "storage" in error_msg or code == 552
            
            reason = f"Data rejected ({code}: {e.smtp_error})"
            if is_mailbox_full:
                reason = "Mailbox is full / quota exceeded"
                raise PermanentEmailError(reason)
                
            # Other data rejections (e.g. local policy/spam blocks) are usually treated as transient/config issues to review.
            raise TransientEmailError(reason)
            
        except smtplib.SMTPAuthenticationError as e:
            raise TransientEmailError(f"SMTP authentication failed: {e.smtp_error.decode('utf-8', errors='ignore')}")
            
        except (smtplib.SMTPConnectError, smtplib.SMTPHeloError) as e:
            raise TransientEmailError(f"SMTP connection error: {str(e)}")
            
        except Exception as e:
            # Handle generic socket timeouts or connection drops as transient
            error_str = str(e).lower()
            if any(k in error_str for k in ["timeout", "connection refused", "broken pipe", "reset"]):
                raise TransientEmailError(f"Transient network error: {str(e)}")
            # For safety, raise anything else as transient to avoid accidentally discarding leads due to code errors
            raise TransientEmailError(f"Unexpected SMTP error: {str(e)}")
