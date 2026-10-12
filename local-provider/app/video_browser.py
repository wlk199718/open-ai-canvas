import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

import requests
import websocket

from .wan_browser import browser_executable

VIDEO_PAGE = "https://www.vidu.com/zh/create/character2video"
VIDEO_ORIGIN = "https://www.vidu.com"


class BrowserSession:
    def __init__(self, cookies, proxy):
        self.directory = tempfile.TemporaryDirectory(prefix="zaowu-q4-helper-")
        self.process = None
        self.socket = None
        self.job = None
        self.sequence = 0
        self.proxy_auth = None
        arguments = [
            browser_executable(), f"--user-data-dir={self.directory.name}",
            "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
            "--no-first-run", "--no-default-browser-check", "--new-window", "about:blank",
        ]
        if proxy:
            parsed = urlsplit(proxy)
            if parsed.username and parsed.scheme.startswith("socks"):
                self.directory.cleanup()
                raise ValueError("浏览器辅助暂不支持带用户名的 SOCKS5 代理，请使用 HTTP 代理")
            host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
            port = parsed.port or (80 if parsed.scheme == "http" else 443)
            arguments.append(f"--proxy-server={parsed.scheme.replace('socks5h', 'socks5')}://{host}:{port}")
            if parsed.username:
                self.proxy_auth = (unquote(parsed.username), unquote(parsed.password or ""))
        try:
            self.process = subprocess.Popen(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.name == "nt":
                import win32api
                import win32job
                self.job = win32job.CreateJobObject(None, "")
                limits = win32job.QueryInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation)
                limits["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                win32job.SetInformationJobObject(self.job, win32job.JobObjectExtendedLimitInformation, limits)
                handle = win32api.OpenProcess(0x0100 | 0x0001, False, self.process.pid)
                try:
                    win32job.AssignProcessToJobObject(self.job, handle)
                finally:
                    handle.Close()
            port_file = Path(self.directory.name) / "DevToolsActivePort"
            end = time.monotonic() + 20
            while not port_file.is_file():
                if self.process.poll() is not None or time.monotonic() > end:
                    raise ValueError("Q4 浏览器辅助启动失败")
                time.sleep(0.1)
            port = int(port_file.read_text().splitlines()[0])
            local = requests.Session()
            local.trust_env = False
            try:
                response = local.get(f"http://127.0.0.1:{port}/json/list", timeout=5)
                response.raise_for_status()
                target = next(item for item in response.json() if item.get("type") == "page")
            finally:
                local.close()
            self.socket = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=25, suppress_origin=True)
            self.call("Network.enable", {})
            if self.proxy_auth:
                self.call("Fetch.enable", {"handleAuthRequests": True})
            self.call("Network.setCookies", {"cookies": [
                {"name": key, "value": value, "domain": ".vidu.com", "path": "/", "secure": True}
                for key, value in cookies.items()
            ]})
            self.call("Page.navigate", {"url": VIDEO_PAGE})
            end = time.monotonic() + 25
            while time.monotonic() < end:
                state = self.call("Runtime.evaluate", {"expression": f"location.origin === {json.dumps(VIDEO_ORIGIN)} && document.readyState !== 'loading'", "returnByValue": True})
                if state.get("result", {}).get("value"):
                    break
                time.sleep(0.2)
            else:
                raise ValueError("Q4 页面未加载，请检查代理或浏览器页面")
        except Exception as exc:
            self.close()
            raise ValueError("Q4 浏览器辅助启动失败，请检查本机浏览器和代理") from exc

    def send(self, method, params):
        self.sequence += 1
        self.socket.send(json.dumps({"id": self.sequence, "method": method, "params": params}))
        return self.sequence

    def call(self, method, params):
        identifier = self.send(method, params)
        while True:
            value = json.loads(self.socket.recv())
            if value.get("method") == "Fetch.authRequired":
                challenge = value["params"]
                credentials = {"response": "CancelAuth"}
                if self.proxy_auth and challenge.get("authChallenge", {}).get("source") == "Proxy":
                    credentials = {"response": "ProvideCredentials", "username": self.proxy_auth[0], "password": self.proxy_auth[1]}
                self.send("Fetch.continueWithAuth", {"requestId": challenge["requestId"], "authChallengeResponse": credentials})
            elif value.get("method") == "Fetch.requestPaused":
                self.send("Fetch.continueRequest", {"requestId": value["params"]["requestId"]})
            elif value.get("id") == identifier:
                if "error" in value:
                    raise ValueError("Q4 浏览器请求未完成")
                return value.get("result", {})

    def request(self, method, url, headers, body, timeout):
        safe_headers = {key: value for key, value in headers.items() if key.lower() in {"accept", "content-type", "cache-control", "pragma"} or key.lower().startswith("x-")}
        self.socket.settimeout(timeout + 5)
        options = {"method": method, "headers": safe_headers, "credentials": "include"}
        if body is not None:
            options["body"] = json.dumps(body)
        expression = """(async () => {
            const options = OPTIONS;
            options.signal = AbortSignal.timeout(TIMEOUT);
            const response = await fetch(URL_VALUE, options);
            return {status:response.status,headers:Object.fromEntries(response.headers),body:await response.text()};
        })()""".replace("OPTIONS", json.dumps(options)).replace("TIMEOUT", str(int(timeout * 1000))).replace("URL_VALUE", json.dumps(url))
        result = self.call("Runtime.evaluate", {"expression": expression, "awaitPromise": True, "returnByValue": True})
        if result.get("exceptionDetails") or not result.get("result", {}).get("value"):
            raise requests.ConnectionError("Q4 浏览器请求未返回结果，请检查浏览器页面；提交不会自动重发")
        value = result["result"]["value"]
        response = requests.Response()
        response.status_code = value["status"]
        response._content = value["body"].encode()
        response.headers.update(value["headers"])
        return response

    def close(self):
        if self.socket:
            self.socket.close()
        if self.job:
            self.job.Close()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        try:
            self.directory.cleanup()
        except PermissionError:
            pass


class BrowserPool:
    def __init__(self):
        self.lock = threading.RLock()
        self.sessions = {}

    def request(self, cookies, proxy, limit, method, url, headers, body, timeout):
        key = hashlib.sha256(json.dumps([cookies, proxy], sort_keys=True).encode()).hexdigest()
        with self.lock:
            while len(self.sessions) > limit:
                oldest = next(item for item in self.sessions if item != key)
                self.sessions.pop(oldest).close()
            browser = self.sessions.get(key)
            if browser and browser.process.poll() is not None:
                browser.close()
                del self.sessions[key]
                browser = None
            if not browser:
                while len(self.sessions) >= limit:
                    oldest = next(iter(self.sessions))
                    self.sessions.pop(oldest).close()
                browser = BrowserSession(cookies, proxy)
                self.sessions[key] = browser
            return browser.request(method, url, headers, body, timeout)

    def state(self):
        return {"windows": len(self.sessions), "browser_available": browser_available()}

    def close(self):
        with self.lock:
            for browser in self.sessions.values():
                browser.close()
            self.sessions.clear()


def browser_available():
    try:
        browser_executable()
        return True
    except ValueError:
        return False


browser_pool = BrowserPool()
