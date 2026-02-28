"""
Claude Cookie 捕获代理脚本 (mitmproxy addon)
用法:
  pip install mitmproxy
  mitmdump -s proxy_capture.py --listen-port 8080
  浏览器设置代理 127.0.0.1:8080 → 登录 claude.ai → Cookie 自动保存到 cookies.json
  curl http://localhost:8765/api/auth/proxy/load-file?path=cookies.json
"""
import json, os
from datetime import datetime
from mitmproxy import http, ctx

TARGET_DOMAINS = ["claude.ai", "a.claude.ai"]
OUTPUT_FILE = "cookies.json"
captured: dict = {}
capture_count: int = 0

def request(flow: http.HTTPFlow):
    global captured, capture_count
    if not any(d in flow.request.pretty_host for d in TARGET_DOMAINS):
        return
    cookies = dict(flow.request.cookies)
    if "sessionKey" in cookies and cookies["sessionKey"].startswith("sk-ant-"):
        changed = any(cookies.get(k) != captured.get(k) for k in cookies)
        if changed:
            captured.update(cookies)
            capture_count += 1
            _save()
            ctx.log.info(f"[Capture #{capture_count}] {len(captured)} cookies | sessionKey: {cookies['sessionKey'][:20]}...")

def response(flow: http.HTTPFlow):
    if not any(d in flow.request.pretty_host for d in TARGET_DOMAINS):
        return
    set_cookie = flow.response.headers.get("set-cookie", "")
    if set_cookie and "=" in set_cookie:
        for part in set_cookie.split(";"):
            part = part.strip()
            low = part.lower()
            if "=" in part and not any(low.startswith(x) for x in ("path","domain","expires","samesite","secure","httponly","max-age")):
                idx = part.index("=")
                captured[part[:idx].strip()] = part[idx+1:].strip()
        if captured.get("sessionKey","").startswith("sk-ant-"):
            _save()

def _save():
    output = {"_meta": {"captured_at": datetime.utcnow().isoformat(), "total_count": len(captured),
                         "has_session_key": "sessionKey" in captured, "org_uuid": captured.get("lastActiveOrg","")},
              **captured}
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    ctx.log.info(f"Saved to {OUTPUT_FILE} | 导入命令: curl 'http://localhost:8765/api/auth/proxy/load-file?path={OUTPUT_FILE}'")

def done():
    status = "✅ 已捕获" if "sessionKey" in captured else "❌ 未捕获"
    ctx.log.info(f"\n[Done] {len(captured)} cookies | sessionKey: {status} | File: {os.path.abspath(OUTPUT_FILE)}")
