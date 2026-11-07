"""Resend migration checks: mocked SMTP only, no real recipients or credentials."""
import sys
import smtplib
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parent))
import payments, gmail_mail, mail_transport


class ResendTransportTest(unittest.TestCase):
    def setUp(self):
        self.config = patch.object(payments, 'config', return_value={
            'MAIL_PROVIDER': 'resend', 'SMTP_PASSWORD': 're_unit_test_placeholder'})
        self.config.start()

    def tearDown(self):
        self.config.stop()

    def test_sender_reply_pdf_and_private_key_not_in_message(self):
        pdf = ('Reservation.pdf', b'%PDF-unit-test')
        client = gmail_mail.compose('client@example.com', 'Reservation', 'Bonjour', pdf)
        owner = gmail_mail.compose(gmail_mail.OWNER, 'Alerte', 'Nouvelle reservation')
        self.assertIn('bonjour@eventsbooth360.fr', client['From'])
        self.assertIn('notifications@eventsbooth360.fr', owner['From'])
        self.assertEqual(client['Reply-To'], 'contact@eventsbooth360.fr')
        self.assertIn('contact@eventsbooth360.fr', client.get_body(('plain',)).get_content())
        self.assertEqual(list(client.iter_attachments())[0].get_payload(decode=True), pdf[1])
        self.assertNotIn('re_unit_test_placeholder', client.as_string())

    def test_secure_smtp_and_stable_idempotency_without_google(self):
        with patch.object(mail_transport.smtplib, 'SMTP_SSL') as smtp, patch.object(gmail_mail, 'urlopen', side_effect=AssertionError('No Gmail access')):
            connection = smtp.return_value.__enter__.return_value
            connection.send_message.return_value = {}
            first = gmail_mail.send('client@example.com', 'Confirmation', 'Reference booking-123', gmail_mail.access_token())
            second = gmail_mail.send('client@example.com', 'Confirmation', 'Reference booking-123', gmail_mail.access_token())
            self.assertEqual(first, second)
            self.assertEqual(smtp.call_args.args, ('smtp.resend.com', 465))
            self.assertEqual(smtp.call_args.kwargs['context'].verify_mode, __import__('ssl').CERT_REQUIRED)
            connection.login.assert_called_with('resend', 're_unit_test_placeholder')
            message = connection.send_message.call_args.args[0]
            self.assertTrue(message['Resend-Idempotency-Key'].startswith('eventsbooth360/'))
            self.assertNotEqual(mail_transport.delivery_key('other@example.com', 'Confirmation', 'Reference booking-123', None), message['Resend-Idempotency-Key'])

    def test_smtp_failure_never_falls_back_to_gmail(self):
        with patch.object(mail_transport.smtplib, 'SMTP_SSL') as smtp, patch.object(gmail_mail, 'urlopen') as google:
            smtp.return_value.__enter__.return_value.send_message.side_effect = TimeoutError('ambiguous')
            with self.assertRaises(TimeoutError):
                gmail_mail.send('client@example.com', 'Confirmation', 'Reference b', 'resend-smtp')
            google.assert_not_called()

    def test_explicit_refusal_is_distinct_from_timeout(self):
        with patch.object(mail_transport.smtplib, 'SMTP_SSL') as smtp:
            smtp.return_value.__enter__.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b'not accepted')
            with self.assertRaises(mail_transport.MailRejected):
                gmail_mail.send('client@example.com', 'Confirmation', 'Reference b', 'resend-smtp')

    def test_missing_smtp_key_does_not_use_old_gmail_connection(self):
        with patch.object(payments, 'config', return_value={'MAIL_PROVIDER': 'resend'}):
            self.assertFalse(gmail_mail.configured())
            self.assertFalse(gmail_mail.connected())
            with patch.object(gmail_mail.server, 'connect', side_effect=AssertionError('No Gmail token lookup')):
                with self.assertRaises(RuntimeError): gmail_mail.access_token()


if __name__ == '__main__': unittest.main()
