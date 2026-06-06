"""Web 服务：电脑/手机浏览器均可访问"""
from __future__ import annotations

import io
import os
import secrets
import socket
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from functools import wraps

from flask import Flask, jsonify, redirect, render_template, request, session, send_from_directory, url_for, Response

import auth_store
import config
import paths
from http_client import eastmoney_get

# 日志/报告写到 exe 同目录
config.OUTPUT_DIR = os.path.join(paths.app_dir(), "output")
config.LOG_DIR = os.path.join(paths.app_dir(), "logs")

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

app = Flask(
    __name__,
    template_folder=os.path.join(paths.resource_dir(), "templates"),
    static_folder=os.path.join(paths.resource_dir(), "static"),
)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True


def _load_secret_key() -> str:
    secret_path = config.SESSION_SECRET_FILE
    os.makedirs(os.path.dirname(secret_path), exist_ok=True)
    if os.path.isfile(secret_path):
        with open(secret_path, encoding="utf-8") as f:
            key = f.read().strip()
            if key:
                return key
    key = secrets.token_hex(32)
    with open(secret_path, "w", encoding="utf-8") as f:
        f.write(key)
    return key


app.secret_key = _load_secret_key()
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = 86400 * 30

_accounts_file = auth_store.init_database()
print(f"[AUTH] 数据目录: {config.DATA_DIR} · 已有 {auth_store.user_count()} 个账号", flush=True)
if _accounts_file:
    print(f"[AUTH] 已生成 {config.INITIAL_ACCOUNT_COUNT} 个新账号: {_accounts_file}", flush=True)


def _warmup_screener() -> None:
    try:
        import screener  # noqa: F401
        print("[WARMUP] 分析模块已预加载", flush=True)
    except Exception as e:
        print(f"[WARMUP] 预加载失败: {e}", flush=True)


threading.Thread(target=_warmup_screener, daemon=True).start()

_state = {
    "running": False,
    "message": "就绪，点击开始检索",
    "progress": [],
    "result": None,
    "error": None,
}
_lock = threading.Lock()


def _current_user() -> str | None:
    username = session.get("user")
    if not username:
        return None
    if not auth_store.is_user_valid(username):
        session.pop("user", None)
        return None
    return username


def _is_mobile_client() -> bool:
    ua = request.headers.get("User-Agent", "").lower()
    return any(k in ua for k in ("iphone", "android", "mobile", "ipad"))


def _is_mobile_mode() -> bool:
    """手机版/云端：免登录"""
    return os.environ.get("MOBILE_ONLY") == "1" or _is_mobile_client()


def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if _is_mobile_mode():
            return f(*args, **kwargs)
        if not _current_user():
            return jsonify({"ok": False, "msg": "请先登录", "auth": False}), 401
        return f(*args, **kwargs)
    return wrapped


def admin_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return jsonify({"ok": False, "msg": "需要管理员登录", "auth": False}), 401
        return f(*args, **kwargs)
    return wrapped


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _format_duration(seconds: float) -> str:
    total = int(round(seconds))
    if total < 60:
        return f"{total} 秒"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} 分 {secs} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分 {secs} 秒"


def _save_report(report: str) -> None:
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(config.OUTPUT_DIR, f"recommend_{ts}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)


def _progress_cb(msg: str, extra: dict) -> None:
    with _lock:
        _state["message"] = msg
        _state["progress"].append({"text": msg, **extra})
        if len(_state["progress"]) > 80:
            _state["progress"] = _state["progress"][-50:]


def _run_job() -> None:
    global _state
    with _lock:
        _state["message"] = "正在加载分析模块..."
        _state["progress"].append({"text": "正在加载分析模块..."})

    from screener import run_screening

    _progress_cb("开始检索，连接东方财富...", {"step": 0})
    started = time.perf_counter()
    finished_at = ""
    try:
        picks, report = run_screening(progress=_progress_cb)
        picks = [p for p in picks if p.price >= config.MIN_STOCK_PRICE]
        elapsed = time.perf_counter() - started
        elapsed_text = _format_duration(elapsed)
        finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        full_report = f"{report}\n\n本次推荐用时: {elapsed_text}（完成于 {finished_at}）"
        _save_report(full_report)
        with _lock:
            _state["result"] = {
                "picks": [p.to_dict() for p in picks],
                "report": full_report,
                "count": len(picks),
                "trade_tip": config.TRADE_TIP,
                "elapsed_sec": round(elapsed, 1),
                "elapsed_text": elapsed_text,
                "finished_at": finished_at,
            }
            _state["message"] = (
                f"检索完成，用时 {elapsed_text}" if picks else f"未找到合适标的，用时 {elapsed_text}"
            )
    except Exception as e:
        elapsed = time.perf_counter() - started
        hint = ""
        if config.IS_CLOUD:
            hint = "（云端服务器在国外，可能无法访问东方财富，建议电脑运行本地版）"
        with _lock:
            _state["error"] = str(e)
            _state["message"] = f"检索失败: {e}{hint}（用时 {_format_duration(elapsed)}）"
            _state["progress"].append({"text": traceback.format_exc(), "error": True})
    finally:
        with _lock:
            _state["running"] = False


def _render_app():
    mobile_mode = _is_mobile_mode()
    template = "mobile.html" if mobile_mode else "index.html"
    user = None if mobile_mode else (auth_store.get_user(_current_user()) if _current_user() else None)
    return render_template(
        template,
        trade_tip=config.TRADE_TIP,
        screen_tip=config.SCREEN_TIP,
        port=config.WEB_PORT,
        app_version=config.APP_VERSION,
        user=user,
        is_cloud=config.IS_CLOUD,
    )


@app.route("/login")
def login_page():
    if _is_mobile_mode():
        return redirect(url_for("index"))
    if _current_user():
        return redirect(url_for("index"))
    return render_template("login.html", valid_days=config.ACCOUNT_VALID_DAYS)


@app.route("/admin")
def admin_page():
    return render_template("admin.html")


@app.route("/")
def index():
    if not _is_mobile_mode() and not _current_user():
        return redirect(url_for("login_page"))
    return _render_app()


@app.route("/health")
def health():
    return jsonify({"ok": True}), 200


@app.route("/manifest.json")
def manifest():
    static_dir = os.path.join(paths.resource_dir(), "static")
    return send_from_directory(static_dir, "manifest.json")


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    user, err = auth_store.authenticate_user(
        data.get("username", ""),
        data.get("password", ""),
    )
    if err:
        return jsonify({"ok": False, "msg": err})
    session.permanent = True
    session["user"] = user["username"]
    return jsonify({"ok": True, "user": user})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.pop("user", None)
    return jsonify({"ok": True})


@app.route("/api/me")
def api_me():
    username = _current_user()
    if not username:
        return jsonify({"ok": False, "auth": False}), 401
    user = auth_store.get_user(username)
    return jsonify({"ok": True, "user": user})


@app.route("/api/status")
@login_required
def api_status():
    with _lock:
        return jsonify(dict(_state))


@app.route("/api/start", methods=["POST"])
@login_required
def api_start():
    with _lock:
        if _state["running"]:
            return jsonify({"ok": False, "msg": "正在检索中，请稍候..."}), 409
        _state.update({
            "running": True,
            "message": "正在启动检索...",
            "progress": [],
            "result": None,
            "error": None,
        })
    threading.Thread(target=_run_job, daemon=True).start()
    return jsonify({"ok": True, "msg": "已开始检索"})


@app.route("/api/network")
@login_required
def api_network():
    try:
        data = eastmoney_get(
            "/api/qt/clist/get",
            {
                "pn": "1", "pz": "5", "po": "1", "np": "1",
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": "2", "invt": "2", "fid": "f12",
                "fs": "m:1 t:2,m:0 t:6",
                "fields": "f12,f14,f2,f3,f22",
            },
            retries=3,
            timeout=12,
        )
        total = data["data"].get("total", 0)
        return jsonify({"ok": True, "msg": f"数据连接正常，约 {total} 只主板股票可查询"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route("/api/admin/login", methods=["POST"])
def api_admin_login():
    data = request.get_json(silent=True) or {}
    if not auth_store.verify_admin(data.get("username", ""), data.get("password", "")):
        return jsonify({"ok": False, "msg": "管理员账号或密码错误"})
    session["admin"] = True
    return jsonify({"ok": True})


@app.route("/api/admin/logout", methods=["POST"])
def api_admin_logout():
    session.pop("admin", None)
    return jsonify({"ok": True})


@app.route("/api/admin/users")
@admin_required
def api_admin_users():
    return jsonify({"ok": True, "users": auth_store.list_users()})


@app.route("/api/admin/user/expire", methods=["POST"])
@admin_required
def api_admin_expire():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    expires_at = (data.get("expires_at") or "").strip()
    if not username or not expires_at:
        return jsonify({"ok": False, "msg": "参数不完整"})
    try:
        datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return jsonify({"ok": False, "msg": "时间格式应为 2026-06-12 15:00:00"})
    if not auth_store.set_expires_at(username, expires_at):
        return jsonify({"ok": False, "msg": "账号不存在"})
    return jsonify({"ok": True, "msg": f"已更新 {username} 到期时间"})


@app.route("/api/admin/user/extend", methods=["POST"])
@admin_required
def api_admin_extend():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    days = int(data.get("days") or 0)
    if not username or days <= 0:
        return jsonify({"ok": False, "msg": "参数无效"})
    if not auth_store.extend_days(username, days):
        return jsonify({"ok": False, "msg": "账号不存在"})
    return jsonify({"ok": True, "msg": f"已为 {username} 延长 {days} 天"})


@app.route("/api/admin/user/reset", methods=["POST"])
@admin_required
def api_admin_reset():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    if not username:
        return jsonify({"ok": False, "msg": "参数无效"})
    if not auth_store.reset_activation(username):
        return jsonify({"ok": False, "msg": "账号不存在"})
    return jsonify({"ok": True, "msg": f"已重置 {username}，下次登录重新计时"})


@app.route("/api/admin/user/toggle", methods=["POST"])
@admin_required
def api_admin_toggle():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    enabled = bool(data.get("enabled"))
    if not username:
        return jsonify({"ok": False, "msg": "参数无效"})
    if not auth_store.set_enabled(username, enabled):
        return jsonify({"ok": False, "msg": "账号不存在"})
    return jsonify({"ok": True, "msg": f"已{'启用' if enabled else '禁用'} {username}"})


@app.route("/api/admin/user/password", methods=["POST"])
@admin_required
def api_admin_password():
    import secrets
    import string

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    if not username:
        return jsonify({"ok": False, "msg": "参数无效"})
    if not password:
        chars = string.ascii_letters + string.digits
        password = "".join(secrets.choice(chars) for _ in range(8))
    if not auth_store.set_password(username, password):
        return jsonify({"ok": False, "msg": "账号不存在"})
    return jsonify({
        "ok": True,
        "msg": f"已重置 {username} 的密码",
        "username": username,
        "password": password,
    })


@app.route("/api/admin/info")
@admin_required
def api_admin_info():
    return jsonify({
        "ok": True,
        "data_dir": auth_store.data_dir(),
        "user_count": auth_store.user_count(),
        "is_cloud": config.IS_CLOUD,
        "persistent_hint": (
            "Render 免费版重启/重新部署会清空账号。"
            "请使用「备份账号库」保存，或升级 Starter 并挂载 Persistent Disk（DATA_DIR=/var/data）。"
            if config.IS_CLOUD else "本地版账号保存在 data 目录，一般不会丢失。"
        ),
    })


@app.route("/api/admin/backup")
@admin_required
def api_admin_backup():
    data = auth_store.backup_database()
    return Response(
        data,
        mimetype="application/octet-stream",
        headers={"Content-Disposition": "attachment; filename=accounts_backup.db"},
    )


@app.route("/api/admin/restore", methods=["POST"])
@admin_required
def api_admin_restore():
    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "msg": "请选择 accounts_backup.db 文件"})
    try:
        auth_store.restore_database(file.read())
    except ValueError as e:
        return jsonify({"ok": False, "msg": str(e)})
    return jsonify({"ok": True, "msg": f"已恢复 {auth_store.user_count()} 个账号"})


def main() -> None:
    ip = _local_ip()
    print("=" * 52)
    print("  A股智能推荐 · Web 版")
    print(f"  版本: {config.APP_VERSION} · 股价≥{config.MIN_STOCK_PRICE}元 · 需登录")
    print(f"  模板目录: {paths.resource_dir()}")
    print("=" * 52)
    print(f"  服务监听: {config.WEB_HOST}:{config.WEB_PORT}（任意网络可访问）")
    print(f"  本机访问: http://127.0.0.1:{config.WEB_PORT}")
    print(f"  他人访问: http://{ip}:{config.WEB_PORT}")
    print(f"  管理后台: http://127.0.0.1:{config.WEB_PORT}/admin")
    print(f"  管理员: {config.ADMIN_USERNAME} / （见环境变量 ADMIN_PASSWORD 或 config）")
    if _accounts_file:
        print(f"  初始50账号已生成: {_accounts_file}")
    print(f"  {config.TRADE_TIP}")
    print("=" * 52)
    url = f"http://127.0.0.1:{config.WEB_PORT}"
    try:
        webbrowser.open(url)
    except Exception:
        pass
    app.run(host=config.WEB_HOST, port=config.WEB_PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
