import os
import html
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def send_password_reset_email(to_email, user_name, reset_link):
    backend = (os.getenv("MAIL_BACKEND") or "SMTP").upper()

    if backend == "GRAPH":
        return _send_via_graph(to_email, user_name, reset_link)

    return _send_via_smtp(to_email, user_name, reset_link)


def _build_email_body(user_name, reset_link):
    user_name = html.escape(str(user_name or "User"))
    reset_link = html.escape(str(reset_link or ""), quote=True)
    return f"""
    <html>
    <body style="font-family:Arial,sans-serif;background:#f5f7fb;padding:20px;">
        <div style="max-width:600px;margin:auto;background:#fff;border-radius:12px;padding:24px;border:1px solid #e5e7eb;">
            <h2 style="color:#1f2937;">Password Reset Request</h2>
            <p>Dear {user_name},</p>
            <p>We received a request to reset your TERIQEN password.</p>
            <p style="margin:24px 0;">
                <a href="{reset_link}" style="background:#2563eb;color:#fff;padding:12px 20px;text-decoration:none;border-radius:8px;display:inline-block;">
                    Reset Password
                </a>
            </p>
            <p>This link will expire in 30 minutes.</p>
            <p>If you did not request this, please ignore this email.</p>
            <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;">
            <p style="font-size:12px;color:#6b7280;">This is an automated email from TERIQEN for Cadupius Healthassist Pvt. Ltd.</p>
        </div>
    </body>
    </html>
    """


def _send_via_smtp(to_email, user_name, reset_link):
    mail_server = os.getenv("MAIL_SERVER") or os.getenv("SMTP_HOST")
    mail_port = int(os.getenv("MAIL_PORT") or os.getenv("SMTP_PORT") or "587")
    mail_username = os.getenv("MAIL_USERNAME") or os.getenv("SMTP_USERNAME")
    mail_password = os.getenv("MAIL_PASSWORD") or os.getenv("SMTP_PASSWORD")
    mail_sender = os.getenv("MAIL_DEFAULT_SENDER") or os.getenv("EMAIL_FROM") or mail_username

    subject = "TERIQEN - Password Reset"
    html_body = _build_email_body(user_name, reset_link)

    msg = MIMEMultipart("alternative")
    msg["From"] = mail_sender
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(mail_server, mail_port) as server:
        server.starttls()
        server.login(mail_username, mail_password)
        server.sendmail(mail_sender, to_email, msg.as_string())


def _send_via_graph(to_email, user_name, reset_link):
    tenant_id = os.getenv("MS_TENANT_ID")
    client_id = os.getenv("MS_CLIENT_ID")
    client_secret = os.getenv("MS_CLIENT_SECRET")
    sender_email = (
        os.getenv("MS_GRAPH_USER_EMAIL")
        or os.getenv("GRAPH_SENDER_EMAIL")
        or os.getenv("MAIL_DEFAULT_SENDER")
    )

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

    token_payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": os.getenv("MS_GRAPH_SCOPES", "https://graph.microsoft.com/.default"),
        "grant_type": "client_credentials"
    }

    token_response = requests.post(token_url, data=token_payload, timeout=30)
    token_response.raise_for_status()

    access_token = token_response.json()["access_token"]

    subject = "TERIQEN - Password Reset"
    html_body = _build_email_body(user_name, reset_link)

    graph_url = f"https://graph.microsoft.com/v1.0/users/{sender_email}/sendMail"

    message_payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "HTML",
                "content": html_body
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": to_email
                    }
                }
            ]
        },
        "saveToSentItems": True
    }

    response = requests.post(
        graph_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        },
        json=message_payload,
        timeout=30
    )

    response.raise_for_status()


def send_login_otp_email(to_email, user_name, otp_code):
    subject = "TERIQEN - Sign-in verification code"
    safe_name = html.escape(str(user_name or "User"))
    safe_code = html.escape(str(otp_code or ""))
    body = f"""
    <html><body style="font-family:Arial,sans-serif;background:#f5f7fb;padding:20px;">
      <div style="max-width:600px;margin:auto;background:#fff;border-radius:12px;padding:24px;border:1px solid #e5e7eb;">
        <h2>Sign-in verification</h2><p>Dear {safe_name},</p>
        <p>Your TERIQEN verification code is:</p>
        <p style="font-size:28px;font-weight:700;letter-spacing:6px">{safe_code}</p>
        <p>This code expires shortly. Do not share it with anyone.</p>
      </div></body></html>"""
    backend = (os.getenv("MAIL_BACKEND") or "SMTP").upper()
    if backend == "GRAPH":
        tenant_id=os.getenv("MS_TENANT_ID"); client_id=os.getenv("MS_CLIENT_ID"); client_secret=os.getenv("MS_CLIENT_SECRET")
        sender=os.getenv("MS_GRAPH_USER_EMAIL") or os.getenv("GRAPH_SENDER_EMAIL") or os.getenv("MAIL_DEFAULT_SENDER")
        tr=requests.post(f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token", data={"client_id":client_id,"client_secret":client_secret,"scope":os.getenv("MS_GRAPH_SCOPES","https://graph.microsoft.com/.default"),"grant_type":"client_credentials"}, timeout=30); tr.raise_for_status()
        r=requests.post(f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail", headers={"Authorization":f"Bearer {tr.json()['access_token']}","Content-Type":"application/json"}, json={"message":{"subject":subject,"body":{"contentType":"HTML","content":body},"toRecipients":[{"emailAddress":{"address":to_email}}]},"saveToSentItems":False}, timeout=30); r.raise_for_status(); return
    mail_server=os.getenv("MAIL_SERVER") or os.getenv("SMTP_HOST"); mail_port=int(os.getenv("MAIL_PORT") or os.getenv("SMTP_PORT") or "587")
    user=os.getenv("MAIL_USERNAME") or os.getenv("SMTP_USERNAME"); pw=os.getenv("MAIL_PASSWORD") or os.getenv("SMTP_PASSWORD"); sender=os.getenv("MAIL_DEFAULT_SENDER") or os.getenv("EMAIL_FROM") or user
    msg=MIMEMultipart("alternative"); msg["From"]=sender; msg["To"]=to_email; msg["Subject"]=subject; msg.attach(MIMEText(body,"html"))
    with smtplib.SMTP(mail_server,mail_port) as server: server.starttls(); server.login(user,pw); server.sendmail(sender,to_email,msg.as_string())
