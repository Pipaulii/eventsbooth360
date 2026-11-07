"""Resend SMTP transport. Credentials stay in private server configuration."""
import hashlib
import json
import smtplib
import ssl
import payments


class MailRejected(RuntimeError):
    """The provider explicitly refused the message; it was not accepted."""


def enabled():
    return payments.config().get('MAIL_PROVIDER', 'gmail').strip().lower() == 'resend'


def settings():
    c = payments.config()
    host = c.get('SMTP_HOST', 'smtp.resend.com').strip()
    port = str(c.get('SMTP_PORT', '465')).strip()
    user = c.get('SMTP_USERNAME', 'resend').strip()
    password = c.get('SMTP_PASSWORD', '')
    if host != 'smtp.resend.com' or port != '465' or user != 'resend' or not password.startswith('re_'):
        raise RuntimeError('Configuration Resend SMTP incomplète. Vérifiez les paramètres privés du serveur.')
    return host, int(port), user, password


def configured():
    try:
        settings()
        return True
    except RuntimeError:
        return False


def sender(recipient, owner):
    return 'notifications@eventsbooth360.fr' if recipient.casefold() == owner.casefold() else 'bonjour@eventsbooth360.fr'


def reply_to():
    return 'contact@eventsbooth360.fr'


def delivery_key(recipient, subject, body, attachment):
    document = hashlib.sha256(attachment[1]).hexdigest() if attachment else ''
    content = json.dumps([recipient, subject, body, document], ensure_ascii=False).encode('utf-8')
    return 'eventsbooth360/' + hashlib.sha256(content).hexdigest()


def send(message, key):
    host, port, user, password = settings()
    message['Resend-Idempotency-Key'] = key
    message['Message-ID'] = '<' + key.split('/')[-1] + '@eventsbooth360.fr>'
    # Errors after DATA can be ambiguous: callers retain the durable unknown state.
    with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=20) as smtp:
        try:
            smtp.login(user, password)
            refused = smtp.send_message(message)
            if refused:
                raise MailRejected('Message refusé par le service d’envoi.')
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPSenderRefused,
                smtplib.SMTPRecipientsRefused, smtplib.SMTPDataError):
            raise MailRejected('Message refusé par le service d’envoi.') from None
        # QUIT failure after acceptance must not turn a successful send into a retry.
        try:
            smtp.quit()
        except (OSError, smtplib.SMTPException):
            pass
    return str(message['Message-ID'])
