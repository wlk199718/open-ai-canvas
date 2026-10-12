import os
import sys
import threading
import webbrowser
import uvicorn


def desktop_admin_session():
    import base64
    import json
    from pathlib import Path
    import requests
    from app.security import PRODUCT_ID, decrypt_secret
    from app.store import Store

    port = int(os.getenv("ZAOWU_PROVIDER_PORT", "8787"))
    base_url = f"http://127.0.0.1:{port}"
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(base_url + "/api/health", timeout=3)
        response.raise_for_status()
        state = response.json()
        if state.get("service") != PRODUCT_ID or state.get("dev_mode"):
            raise ValueError("请启动正式生成服务")
        root = Path(os.getenv("ZAOWU_PROVIDER_DATA_DIR") or Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "ZaowuProvider")
        store = Store(root)
        try:
            credential = store.setting("license_credential", "")
        finally:
            store.db.close()
        if not credential:
            return None
        key = decrypt_secret(base64.b64decode(credential, validate=True))
        # The existing activation API verifies the saved card online and issues
        # a fresh management session; no cached license state grants access.
        response = session.post(base_url + "/api/admin/activate", json={"license": key}, timeout=25)
        response.raise_for_status()
        result = response.json()
        if result.get("activated") is not True or not isinstance(result.get("token"), str) or not result["token"]:
            raise ValueError("已保存卡密未能验证")
        return result
    finally:
        session.close()


def desktop_connection():
    import base64
    import json
    import requests
    from app.security import PRODUCT_ID, decrypt_secret, digest
    from app.store import Store
    from pathlib import Path

    port = int(os.getenv("ZAOWU_PROVIDER_PORT", "8787"))
    base_url = f"http://127.0.0.1:{port}"
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(base_url + "/api/health", timeout=3)
        response.raise_for_status()
        state = response.json()
        if state.get("service") != PRODUCT_ID or not state.get("activated") or state.get("dev_mode"):
            raise ValueError("请在生成服务客户端验证卡密后连接")
        root = Path(os.getenv("ZAOWU_PROVIDER_DATA_DIR") or Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "ZaowuProvider")
        store = Store(root)
        try:
            credential = store.setting("client_credential", "")
            if not credential:
                if store.setting("client_hash", ""):
                    raise ValueError("请在生成服务客户端重新生成连接令牌")
                import secrets
                from app.security import encrypt_secret
                token = secrets.token_urlsafe(40)
                credential = base64.b64encode(encrypt_secret(token)).decode("ascii")
                with store.transaction() as db:
                    for key, value in (("client_hash", digest(token)), ("client_credential", credential)):
                        db.execute("INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
            token = decrypt_secret(base64.b64decode(credential, validate=True))
            import secrets
            if not secrets.compare_digest(digest(token), store.setting("client_hash", "")):
                raise ValueError("连接令牌不一致，请在生成服务客户端重新生成")
            # Only the parent desktop process reads this private stdout pipe.
            print(json.dumps({"baseUrl": base_url, "token": token}), flush=True)
        finally:
            store.db.close()
    finally:
        session.close()


def main():
    if "--browser-helper" in sys.argv:
        from app.wan_browser import run_helper
        run_helper()
        return
    if "--desktop-admin-session" in sys.argv:
        import json
        try:
            print(json.dumps(desktop_admin_session()), flush=True)
        except Exception:
            print(json.dumps({"error": "已保存卡密未能验证，请检查网络或重新输入有效卡密"}), flush=True)
            raise SystemExit(1)
        return
    if "--desktop-connection" in sys.argv:
        import json
        try:
            desktop_connection()
        except Exception:
            print(json.dumps({"error": "请先启动生成服务客户端并验证有效卡密"}), flush=True)
            raise SystemExit(1)
        return
    host = os.getenv("ZAOWU_PROVIDER_HOST", "127.0.0.1")
    port = int(os.getenv("ZAOWU_PROVIDER_PORT", "8787"))
    browser_host = host if host not in {"0.0.0.0", "::"} else "127.0.0.1"
    if os.getenv("ZAOWU_PROVIDER_OPEN_BROWSER", "1") != "0" and host not in {"0.0.0.0", "::"}:
        threading.Timer(
            1.0,
            lambda: webbrowser.open(f"http://{browser_host}:{port}"),
        ).start()
    from app.main import app
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_config=None if getattr(sys, "frozen", False) else uvicorn.config.LOGGING_CONFIG,
    )
    server = uvicorn.Server(config)
    if "--desktop-service" in sys.argv:
        def watch_parent():
            while sys.stdin.buffer.read(1):
                pass
            server.should_exit = True
        threading.Thread(target=watch_parent, daemon=True, name="desktop-parent").start()
    server.run()


if __name__ == "__main__":
    main()
