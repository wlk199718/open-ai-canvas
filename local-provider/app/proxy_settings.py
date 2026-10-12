import base64
import hashlib
import threading
import time
from urllib.parse import quote, urlsplit

import requests
import yaml

from .wan import public_https

_cache = {}
_lock = threading.Lock()


def proxy_nodes(text, limit):
    text = text.strip()
    if not text:
        return []
    if not text.startswith(("http://", "https://", "socks5://", "socks5h://", "{", "proxies:")):
        try:
            text = base64.b64decode(text + "=" * (-len(text) % 4), validate=True).decode()
        except (ValueError, UnicodeError):
            raise ValueError("代理订阅格式无效") from None
    parsed = yaml.safe_load(text) if text.startswith(("{", "proxies:")) else text.splitlines()
    if isinstance(parsed, dict):
        parsed = parsed.get("proxies", [])
    if not isinstance(parsed, list):
        raise ValueError("订阅需包含 HTTP / SOCKS5 节点")
    nodes = []
    for item in parsed[:2000]:
        if isinstance(item, dict):
            scheme = {"http": "http", "https": "https", "socks5": "socks5h"}.get(item.get("type"))
            host, port = item.get("server"), item.get("port")
            if not scheme or not isinstance(host, str) or not isinstance(port, int):
                continue
            host = f"[{host}]" if ":" in host and not host.startswith("[") else host
            credentials = ""
            if item.get("username") is not None:
                credentials = quote(str(item["username"]), safe="") + ":" + quote(str(item.get("password", "")), safe="") + "@"
            item = f"{scheme}://{credentials}{host}:{port}"
        if not isinstance(item, str):
            continue
        url = urlsplit(item.strip())
        if url.scheme not in {"http", "https", "socks5", "socks5h"} or not url.hostname or url.path not in {"", "/"} or url.query or url.fragment:
            continue
        try:
            port = url.port
        except ValueError:
            continue
        if port is None and url.scheme.startswith("socks"):
            continue
        nodes.append(item.strip())
        if len(nodes) == limit:
            break
    if not nodes:
        raise ValueError("订阅没有可用的 HTTP / SOCKS5 节点；暂不支持 SS / VMess / VLESS 节点")
    return nodes


def resolve_proxy(settings, account_key):
    source = settings.get("proxy_server", "")
    if not source:
        return ""
    parsed = urlsplit(source)
    subscription = parsed.scheme == "https" and (parsed.path not in {"", "/"} or bool(parsed.query))
    if subscription:
        public_https(source)
        cache_key = hashlib.sha256(source.encode()).hexdigest()
        with _lock:
            cached = _cache.get(cache_key)
        if not cached or cached[0] < time.monotonic():
            session = requests.Session()
            session.trust_env = False
            try:
                response = session.get(source, timeout=settings["proxy_subscription_timeout"], allow_redirects=False, stream=True)
                response.raise_for_status()
                if response.is_redirect:
                    raise ValueError("代理订阅不接受重定向")
                data = bytearray()
                for chunk in response.iter_content(64 * 1024):
                    data.extend(chunk)
                    if len(data) > 1024 * 1024:
                        raise ValueError("代理订阅超过 1 MB")
                nodes = proxy_nodes(data.decode("utf-8-sig"), 50)
                with _lock:
                    _cache[cache_key] = (time.monotonic() + 300, nodes)
            finally:
                session.close()
        else:
            nodes = cached[1]
        nodes = nodes[:settings["max_proxy_nodes"]]
    else:
        nodes = proxy_nodes(source, 1)
    index = int(hashlib.sha256(str(account_key).encode()).hexdigest(), 16) % len(nodes)
    return nodes[index]


def apply_proxy(session, settings, account_key):
    proxy = resolve_proxy(settings, account_key)
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return proxy
