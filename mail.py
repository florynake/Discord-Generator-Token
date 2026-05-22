# Burner mail service — API propriu pe tuciuriusilent.xyz
import time
import requests
import re
import os
import json
import imaplib
import email as email_lib
import threading
from email.header import decode_header
from urllib.parse import quote


_VERIFY_LINK_RE = re.compile(r'https?://[^\s"\'<>]+')


def _pick_verify_url(combined_text, resolve_fn=None):
    """Extract the Discord verify URL from a mail body (html + text combined).

    Mirrors the logic used for the burner API: prefer the second Discord link
    (the real verify/click link), fall back to the longest one, and resolve
    `click.discord.com` trackers to their final destination.
    """
    all_links = _VERIFY_LINK_RE.findall(combined_text or "")
    target_links = [
        l for l in all_links
        if ("discord.com" in l or "click.discord.com" in l)
        and "support." not in l and "blog." not in l
    ]
    if not target_links:
        return None

    url = None
    if len(target_links) >= 2:
        second_url = target_links[1]
        if ("verify" in second_url.lower() or "token=" in second_url.lower()
                or "click.discord.com" in second_url):
            url = second_url
    if not url:
        url = max(target_links, key=len)

    if "click.discord.com" in url and resolve_fn:
        resolved = resolve_fn(url)
        if resolved:
            return resolved
    return url


class TempTfMailApi:
    DEFAULT_BASE_URL = "https://tuciuriusilent.xyz/api"
    DEFAULT_DOMAIN = "tuciuriusilent.xyz"

    def __init__(self, logger=None, forced_domain: str = None, api_key: str = None, base_url: str = None):
        self.created_emails = {}
        self.burner_ids = {}
        self.logger = logger
        self.forced_domain = forced_domain

        cfg = self._read_config()
        mail_cfg = cfg.get("mail", {}) if isinstance(cfg, dict) else {}
        # Support both the nested ("mail": {"api": {...}}) and the legacy flat
        # ("mail": {"api_key": ...}) config layouts.
        api_cfg = mail_cfg.get("api", mail_cfg) if isinstance(mail_cfg, dict) else {}

        self.api_key = (
            api_key
            or os.getenv("TUCIURI_API_KEY", "")
            or os.getenv("FCE_API_KEY", "")
            or api_cfg.get("api_key", "")
        ).strip()

        self.base_url = (
            base_url
            or os.getenv("TUCIURI_BASE_URL", "")
            or api_cfg.get("base_url", "")
            or self.DEFAULT_BASE_URL
        ).rstrip("/")

        self.domain = (
            forced_domain
            or api_cfg.get("domain", "")
            or self.DEFAULT_DOMAIN
        ).strip()

    def _read_config(self):
        try:
            with open("input/config.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _build_proxies(self, proxy: str = None):
        if proxy and "://" not in proxy:
            proxy = f"http://{proxy}"
        return {"http": proxy, "https": proxy} if proxy else None

    def _log(self, message: str):
        if self.logger:
            try:
                self.logger(f"[mail] {message}")
            except Exception:
                pass

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def create_account(self, email: str = None, password: str = None, proxy: str = None):
        if not self.api_key:
            self._log("API key missing — set 'mail.api_key' in input/config.json")
            return None

        payload = {}
        if self.domain:
            payload["domain"] = self.domain

        try:
            resp = requests.post(
                f"{self.base_url}/v1/burner",
                json=payload,
                headers=self._headers(),
                timeout=20,
            )
            if resp.status_code == 402:
                self._log(f"v1/burner: insufficient balance — {resp.text[:200]}")
                return None
            if resp.status_code not in (200, 201):
                self._log(f"v1/burner failed: {resp.status_code} {resp.text[:200]}")
                return None

            data = resp.json() if resp.content else {}
            if not isinstance(data, dict):
                self._log(f"v1/burner returned non-object: {data!r}")
                return None

            created_email = data.get("email")
            created_password = data.get("password") or ""
            burner_id = data.get("id")
            if not created_email or burner_id is None:
                self._log(f"v1/burner response missing email/id: {data!r}")
                return None

            normalized = created_email.strip().lower()
            self.created_emails[normalized] = created_password
            self.burner_ids[normalized] = burner_id
            self._log(f"created: {created_email} (id={burner_id})")
            return created_email
        except Exception as e:
            self._log(f"v1/burner exception: {e}")
            return None

    def get_verify_url(self, email: str, poll_interval: int = 3, timeout: int = 120, proxy: str = None):
        start_time = time.time()
        used_message_ids = set()

        while time.time() - start_time < timeout:
            try:
                messages = self._read_inbox(email)
                if messages:
                    for msg in messages:
                        msg_id = msg.get("id")
                        if msg_id and msg_id in used_message_ids:
                            continue

                        subject = msg.get("subject", "") or ""
                        if "discord" in subject.lower() or "verify" in subject.lower() or "verif" in subject.lower():
                            detail = self._read_message_detail(email, msg_id) if msg_id else None
                            html_body = (detail.get("html", "") if detail else "") or msg.get("html", "")
                            text_body = (detail.get("body", "") if detail else "") or msg.get("body", "") or msg.get("intro", "")

                            combined = html_body + " " + text_body
                            url = _pick_verify_url(
                                combined,
                                resolve_fn=lambda u: self._resolve_url(u, proxy=proxy),
                            )
                            if url:
                                return url

                            if msg_id:
                                used_message_ids.add(msg_id)
            except Exception as e:
                self._log(f"poll error: {e}")
            time.sleep(poll_interval)

        self._log(f"timeout waiting for verify URL on {email}")
        return None

    def _burner_id_for(self, email: str):
        if not email:
            return None
        normalized = email.strip().lower()
        return self.burner_ids.get(normalized)

    def get_password(self, email: str):
        if not email:
            return None
        normalized = email.strip().lower()
        return self.created_emails.get(normalized) or None

    def _read_inbox(self, email: str, proxy: str = None):
        if not self.api_key:
            return None
        burner_id = self._burner_id_for(email)
        if burner_id is None:
            self._log(f"unknown burner id for {email} — was it created via this client?")
            return None
        try:
            resp = requests.get(
                f"{self.base_url}/v1/burner/{burner_id}/messages",
                headers=self._headers(),
                timeout=15,
            )
            if resp.status_code == 404:
                return []
            if not resp.ok:
                self._log(f"messages {resp.status_code} for id={burner_id}: {resp.text[:120]}")
                return None

            data = resp.json()
            messages = []
            if isinstance(data, dict):
                messages = data.get("messages") or []
            elif isinstance(data, list):
                messages = data
            if not isinstance(messages, list):
                messages = []

            normalized_msgs = []
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                normalized_msgs.append({
                    "id": msg.get("id") or msg.get("message_id") or msg.get("_id") or msg.get("uid"),
                    "uid": msg.get("uid"),
                    "from": msg.get("from"),
                    "fromName": msg.get("fromName"),
                    "subject": msg.get("subject", ""),
                    "intro": msg.get("intro", ""),
                    "date": msg.get("date"),
                    "seen": msg.get("seen"),
                })
            return normalized_msgs
        except Exception as e:
            self._log(f"inbox exception: {e}")
            return None

    def _read_message_detail(self, email_or_id, message_id=None, proxy: str = None):
        # Backwards-compat: dacă e apelat cu un singur arg (vechi: doar message_id),
        # nu putem rezolva fără burner_id, deci ieșim graceful.
        if message_id is None:
            return None
        if not self.api_key or not message_id:
            return None
        burner_id = self._burner_id_for(email_or_id)
        if burner_id is None:
            self._log(f"detail: unknown burner id for {email_or_id}")
            return None
        encoded_id = quote(str(message_id), safe="")
        try:
            resp = requests.get(
                f"{self.base_url}/v1/burner/{burner_id}/message/{encoded_id}",
                headers=self._headers(),
                timeout=15,
            )
            if not resp.ok:
                return None
            data = resp.json()
            if not isinstance(data, dict):
                return None
            return {
                "id": data.get("id"),
                "subject": data.get("subject", ""),
                "body": data.get("text", "") or "",
                "html": data.get("html", "") or "",
            }
        except Exception as e:
            self._log(f"message detail exception: {e}")
            return None

    def _resolve_url(self, url: str, proxy: str = None) -> str:
        proxies = self._build_proxies(proxy)
        try:
            resp = requests.head(url, allow_redirects=True, timeout=10, proxies=proxies)
            final_url = resp.url
            if "discord.com/verify" in final_url:
                self._log(f"resolved tracker → {final_url[:40]}...")
                return final_url
        except Exception:
            pass
        return None

    def delete_inbox(self, email: str, proxy: str = None):
        if not self.api_key or not email:
            return True
        normalized = email.strip().lower()
        burner_id = self.burner_ids.get(normalized)
        if burner_id is not None:
            try:
                requests.delete(
                    f"{self.base_url}/v1/burner/{burner_id}",
                    headers=self._headers(),
                    timeout=10,
                )
            except Exception:
                pass
        self.created_emails.pop(email, None)
        self.created_emails.pop(normalized, None)
        self.burner_ids.pop(normalized, None)
        return True


class LocalMailClient:
    """Local email source: reads `email:password` lines from a file and checks
    the verification mail over IMAP.

    Drop-in replacement for `TempTfMailApi` (same public surface used by the
    generator): `create_account()`, `get_password()`, `get_verify_url()` and
    `delete_inbox()`. Each address is consumed (removed from the file) the
    moment it is handed out, so it is never reused.
    """

    def __init__(self, logger=None, file_path: str = "input/emails.txt",
                 imap_host: str = "", imap_port: int = 993):
        self.logger = logger
        self.file_path = file_path
        self.imap_host = (imap_host or "").strip()
        try:
            self.imap_port = int(imap_port)
        except (TypeError, ValueError):
            self.imap_port = 993
        self.created_emails = {}      # normalized email -> password
        self._lock = threading.Lock()

    def _log(self, message: str):
        if self.logger:
            try:
                self.logger(f"[mail] {message}")
            except Exception:
                pass

    def _build_proxies(self, proxy: str = None):
        if proxy and "://" not in proxy:
            proxy = f"http://{proxy}"
        return {"http": proxy, "https": proxy} if proxy else None

    def create_account(self, email: str = None, password: str = None, proxy: str = None):
        """Pop the first `email:password` line off the file and consume it."""
        with self._lock:
            if not os.path.exists(self.file_path):
                self._log(f"{self.file_path} not found")
                return None
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    lines = [line for line in f if line.strip()]
            except Exception as e:
                self._log(f"failed reading {self.file_path}: {e}")
                return None

            if not lines:
                self._log("no emails left in file")
                return None

            first = lines.pop(0).strip()
            try:
                with open(self.file_path, "w", encoding="utf-8") as f:
                    for line in lines:
                        f.write(line.rstrip("\n") + "\n")
            except Exception as e:
                self._log(f"failed rewriting {self.file_path}: {e}")

        if ":" not in first:
            self._log(f"invalid line (expected email:password): {first}")
            return None

        addr, pwd = first.split(":", 1)
        addr = addr.strip()
        pwd = pwd.strip()
        if not addr:
            return None

        self.created_emails[addr.lower()] = pwd
        self._log(f"using local email: {addr}")
        return addr

    def get_password(self, email: str):
        if not email:
            return None
        return self.created_emails.get(email.strip().lower()) or None

    def _connect(self, email: str, password: str):
        if self.imap_port == 143:
            imap = imaplib.IMAP4(self.imap_host, self.imap_port)
        else:
            imap = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
        imap.login(email, password)
        return imap

    @staticmethod
    def _decode(value) -> str:
        if not value:
            return ""
        out = ""
        for text, enc in decode_header(value):
            if isinstance(text, bytes):
                try:
                    out += text.decode(enc or "utf-8", "replace")
                except Exception:
                    out += text.decode("utf-8", "replace")
            else:
                out += text
        return out

    @staticmethod
    def _extract_body(msg) -> str:
        parts = []
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() in ("text/plain", "text/html"):
                    try:
                        payload = part.get_payload(decode=True)
                        if payload:
                            charset = part.get_content_charset() or "utf-8"
                            parts.append(payload.decode(charset, "replace"))
                    except Exception:
                        pass
        else:
            try:
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or "utf-8"
                    parts.append(payload.decode(charset, "replace"))
            except Exception:
                pass
        return " ".join(parts)

    def _resolve_url(self, url: str, proxy: str = None) -> str:
        proxies = self._build_proxies(proxy)
        try:
            resp = requests.head(url, allow_redirects=True, timeout=10, proxies=proxies)
            final_url = resp.url
            if "discord.com/verify" in final_url:
                return final_url
        except Exception:
            pass
        return None

    def get_verify_url(self, email: str, poll_interval: int = 3, timeout: int = 120,
                       proxy: str = None):
        if not self.imap_host:
            self._log("imap_host not configured (mail.local.imap_host)")
            return None
        password = self.get_password(email)
        if not password:
            self._log(f"no password stored for {email}")
            return None

        start_time = time.time()
        seen_uids = set()

        while time.time() - start_time < timeout:
            try:
                imap = self._connect(email, password)
                try:
                    imap.select("INBOX")
                    typ, data = imap.search(None, "ALL")
                    if typ == "OK" and data and data[0]:
                        # Newest first.
                        for num in reversed(data[0].split()):
                            if num in seen_uids:
                                continue
                            seen_uids.add(num)
                            typ, msg_data = imap.fetch(num, "(RFC822)")
                            if typ != "OK" or not msg_data or not msg_data[0]:
                                continue
                            msg = email_lib.message_from_bytes(msg_data[0][1])
                            subject = self._decode(msg.get("Subject", "")).lower()
                            if "discord" not in subject and "verif" not in subject:
                                continue
                            body = self._extract_body(msg)
                            url = _pick_verify_url(
                                body,
                                resolve_fn=lambda u: self._resolve_url(u, proxy=proxy),
                            )
                            if url:
                                return url
                finally:
                    try:
                        imap.logout()
                    except Exception:
                        pass
            except Exception as e:
                self._log(f"imap poll error: {e}")
            time.sleep(poll_interval)

        self._log(f"timeout waiting for verify URL on {email}")
        return None

    def delete_inbox(self, email: str, proxy: str = None):
        # The address was already removed from the file when it was handed out;
        # just drop the in-memory password.
        if email:
            self.created_emails.pop(email.strip().lower(), None)
        return True