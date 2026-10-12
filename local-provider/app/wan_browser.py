import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


SITE = "https://vivareel.ai"
PAGE = SITE + "/zh/workbench/growth"


def browser_executable():
    for root in (os.getenv("PROGRAMFILES"), os.getenv("PROGRAMFILES(X86)"), os.getenv("LOCALAPPDATA")):
        if not root:
            continue
        for relative in ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe"):
            candidate = Path(root) / relative
            if candidate.is_file():
                return str(candidate)
    raise ValueError("未找到 Chrome 或 Edge，无法打开账号浏览器")


def open_account_browser(token, email=""):
    return _launch_browser({"token": token, "email": email})


def open_cookie_browser(cookies, channel):
    if channel not in {"q4-video", "claw-video"} or not isinstance(cookies, dict):
        raise ValueError("账号浏览器参数无效")
    return _launch_browser({"cookies": cookies, "channel": channel})


def _launch_browser(payload):
    encoded = json.dumps(payload).encode()
    if len(encoded) > 128 * 1024:
        raise ValueError("账号浏览器凭据过大")
    # Credentials travel only over stdin, never arguments, URLs or logs.
    helper_command = (
        [sys.executable, "--browser-helper"]
        if getattr(sys, "frozen", False)
        else [sys.executable, str(Path(__file__).resolve()), "--helper"]
    )
    process = subprocess.Popen(
        helper_command,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        process.stdin.write(encoded)
        process.stdin.close()
        response = process.stdout.readline()
        if not response:
            raise ValueError("账号浏览器启动失败")
        result = json.loads(response)
        if not result.get("ok"):
            raise ValueError(result.get("error") or "账号浏览器启动失败")
        return {"ok": True, "message": "已在独立浏览器中打开该账号"}
    finally:
        process.stdout.close()


def run_helper():
    import requests
    import websocket

    payload = json.loads(sys.stdin.buffer.read(128 * 1024 + 1))
    token, email = str(payload.get("token") or ""), str(payload.get("email") or "")
    cookies = payload.get("cookies")
    cookie_mode = isinstance(cookies, dict)
    if cookie_mode:
        if payload.get("channel") not in {"q4-video", "claw-video"} or any(not cookies.get(key) for key in ("JWT", "SSUID")):
            raise ValueError("账号 Cookie 无效")
    elif not token or len(token) > 8192:
        raise ValueError("账号登录凭据无效")
    directory = tempfile.TemporaryDirectory(prefix="zaowu-wan-browser-")
    browser = None
    socket = None
    try:
        browser = subprocess.Popen(
            [browser_executable(), f"--user-data-dir={directory.name}", "--remote-debugging-port=0",
             "--remote-debugging-address=127.0.0.1", "--no-first-run", "--no-default-browser-check",
             "--new-window", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        port_file = Path(directory.name) / "DevToolsActivePort"
        deadline = time.monotonic() + 20
        while not port_file.is_file():
            if browser.poll() is not None or time.monotonic() >= deadline:
                raise ValueError("浏览器启动超时")
            time.sleep(.1)
        port = int(port_file.read_text().splitlines()[0])
        session = requests.Session()
        session.trust_env = False
        response = session.get(f"http://127.0.0.1:{port}/json/list", timeout=5)
        response.raise_for_status()
        target = next(item for item in response.json() if item.get("type") == "page")
        socket = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=10, suppress_origin=True)
        sequence = 0

        def call(method, params):
            nonlocal sequence
            sequence += 1
            socket.send(json.dumps({"id": sequence, "method": method, "params": params}))
            while True:
                message = json.loads(socket.recv())
                if message.get("id") != sequence:
                    continue
                if message.get("error"):
                    raise ValueError("浏览器初始化失败")
                return message.get("result", {})

        call("Page.enable", {})
        call("Network.enable", {})
        if cookie_mode:
            cookie_items = [
                {"name": name, "value": value, "domain": ".vidu.com", "path": "/", "secure": True}
                for name, value in cookies.items()
                if isinstance(name, str) and isinstance(value, str) and name
            ]
            call("Network.setCookies", {"cookies": cookie_items})
            page = "https://www.vidu.com/zh/claw" if payload["channel"] == "claw-video" else "https://www.vidu.com/zh/create/character2video"
        else:
            source = """(() => {
          if (location.origin !== 'https://vivareel.ai') return;
          const token = TOKEN_VALUE;
          const user = {email: EMAIL_VALUE};
          localStorage.setItem('token', JSON.stringify(token));
          localStorage.setItem('user', JSON.stringify(user));
        })()""".replace("TOKEN_VALUE", json.dumps(token)).replace("EMAIL_VALUE", json.dumps(email))
            call("Page.addScriptToEvaluateOnNewDocument", {"source": source})
            for name in ("token", "access_token"):
                call("Network.setCookie", {"name": name, "value": token, "url": SITE,
                                           "path": "/", "secure": True, "sameSite": "Lax"})
            page = PAGE
        call("Page.navigate", {"url": page})
        print(json.dumps({"ok": True}), flush=True)
        browser.wait()
    except Exception:
        print(json.dumps({"ok": False, "error": "账号浏览器启动失败，请检查本机浏览器"}), flush=True)
        if browser and browser.poll() is None:
            browser.terminate()
            browser.wait(timeout=10)
    finally:
        if socket:
            socket.close()
        # Chrome can briefly retain profile locks after its launcher exits.
        for attempt in range(10):
            try:
                directory.cleanup()
                break
            except PermissionError:
                time.sleep(.5)


if __name__ == "__main__":
    run_helper()
