import time
import random
import requests
from urllib.parse import urlparse

import logger


class HCaptchaSolver:
    """hCaptcha solver used for the email-verify step (groq-backed node)."""

    def __init__(self, api_key):
        self.api_key = api_key
        self.host = 'http://89.167.31.16:5000'

    def solve(self, sitekey, domain, rqdata=None, proxy=None, retries=3):
        # Format proxy if provided
        formatted_proxy = ""
        if proxy:
            if not proxy.startswith("http"):
                formatted_proxy = f"http://{proxy}"
            else:
                formatted_proxy = proxy

        # Prepare request payload
        payload = {
            "sitekey": sitekey,
            "siteurl": domain,
            "rqdata": rqdata if rqdata else "",
            "proxy": formatted_proxy,
            "groq_api_key": self.api_key,
        }

        # Retry logic
        for i in range(retries):
            try:
                resp = requests.post(
                    f"{self.host}/solve",
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-API-Key": self.api_key,
                    },
                    timeout=125,
                )

                data = resp.json()

                if data.get("success"):
                    return data.get("token")
                logger.dbg(f"[hcaptcha] attempt {i + 1} failed: {str(data)[:200]}")
            except Exception as e:
                logger.dbg(f"[hcaptcha] attempt {i + 1} exception: {e}")

            if i < retries - 1:
                time.sleep(2)

        return {"errors": ["Failed to solve"]}


class Solver:
    def __init__(self, url, sitekey, rqdata="", user_agent="", proxy=None, api_key=""):
        """
        :param proxy: "user:password@host:port" 또는 "http://user:password@host:port"
        """
        self.url = url
        self.sitekey = sitekey
        self.rqdata = rqdata
        self.user_agent = user_agent
        self.proxy = proxy
        self.api_key = api_key
        self.server_url = "https://zrxsolver.online"

    def _parse_proxy(self):
        """프록시 문자열을 srv, usr, pw 로 분해해서 반환"""
        if not self.proxy:
            return {}

        raw = self.proxy

        # 스킴이 없으면 http:// 자동 추가 (urlparse가 제대로 작동하게)
        if "://" not in raw:
            raw = "http://" + raw

        try:
            parsed = urlparse(raw)
        except Exception as e:
            logger.dbg(f"[solver] invalid proxy ({self.proxy}): {e}")
            return {}

        result = {}

        # hostname:port
        if parsed.hostname and parsed.port:
            result["srv"] = f"{parsed.hostname}:{parsed.port}"

        # username
        if parsed.username:
            result["usr"] = parsed.username

        # password
        if parsed.password:
            result["pw"] = parsed.password

        return result

    def solve(self, timeout=300, poll_interval=1):
        """
        서버로 GET /solve?url=...&sitekey=... 과 함께 proxy 정보도 보내서 작업 요청
        """
        params = {
            "url": self.url,
            "sitekey": self.sitekey,
            "rqdata": self.rqdata,
            "user_agent": self.user_agent,
        }

        if self.api_key:
            params["api_key"] = self.api_key


        proxy_params = self._parse_proxy()
        params.update(proxy_params)


        sess = requests.Session()

        # 세션 UA 설정
        if self.user_agent:
            sess.headers.update({"User-Agent": self.user_agent})

        # --- Solve START ---
        try:
            resp = sess.get(f"{self.server_url}/solve", params=params, timeout=30)
        except Exception as e:
            logger.dbg(f"[solver] error initiating solve: {e}")
            return None, None

        if resp.status_code != 200:
            body = resp.text if hasattr(resp, "text") else "<no body>"
            logger.dbg(f"[solver] init failed: {resp.status_code} - {body}")
            return None, None

        # JSON 파싱
        try:
            data = resp.json()
        except Exception as e:
            logger.dbg(f"[solver] invalid JSON on init: {e}")
            return None, None

        taskid = data.get("taskid")
        if not taskid:
            logger.dbg(f"[solver] no taskid received - {data}")
            return None, None

        logger.dbg(f"[solver] task initiated: {taskid}")

        # --- Polling ---
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                poll_resp = sess.get(f"{self.server_url}/task/{taskid}", timeout=30)
            except Exception as e:
                logger.dbg(f"[solver] error checking task: {e}")
                time.sleep(poll_interval + random.uniform(0, 0.5))
                continue

            if poll_resp.status_code != 200:
                body = poll_resp.text if hasattr(poll_resp, "text") else "<no body>"
                logger.dbg(f"[solver] task check failed: {poll_resp.status_code} - {body}")
                time.sleep(poll_interval + random.uniform(0, 0.5))
                continue

            try:
                data = poll_resp.json()
            except Exception as e:
                logger.dbg(f"[solver] invalid JSON while polling: {e}")
                time.sleep(poll_interval + random.uniform(0, 0.5))
                continue

            status = data.get("status")
            uuid = data.get("uuid")
            cookies = data.get("cookies", {})

            if status == "success":
                logger.dbg(f"[solver] task {taskid} solved")
                return uuid, cookies

            if status in {"failed", "error"}:
                logger.dbg(f"[solver] task {taskid} failed")
                return None, None

            if status == "not_found":
                logger.dbg(f"[solver] task {taskid} not found")
                return None, None

            logger.dbg(f"[solver] task {taskid} status: {status} - waiting...")
            time.sleep(poll_interval + random.uniform(0, 0.5))

        logger.dbg(f"[solver] timeout reached for task {taskid}")
        return None, None