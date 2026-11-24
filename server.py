#!/usr/bin/env python3
"""OPC 一人公司 AI 操作系统 — 本地控制台
一个进程同时提供：本地模型(Ollama) / 云端大模型(aihubmix·DeepSeek·OpenRouter) 路由、
MCP 服务器一键接入与工具调用、参赛任务面板与计划书导出。
仅用标准库，无第三方依赖。启动：python server.py  → http://127.0.0.1:8770
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = Path(os.environ.get("HERMES_ENV", r"C:\Users\czy\AppData\Local\hermes\.env"))
MCP_FILE = ROOT / "mcp_servers.json"
REGISTRY = ROOT / "mcp_registry.json"
CONFIG = ROOT / "config.json"
TASK_FILE = ROOT / "task" / "task.json"
PORT = int(os.environ.get("OPC_PORT", "8770"))

# ----------------------------------------------------------------------------- env/keys
def load_keys():
    keys = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
            if m:
                keys[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return keys

KEYS = load_keys()

CLOUD = {
    "aihubmix": {
        "base": "https://aihubmix.com/v1/chat/completions",
        "key": KEYS.get("AIHUBMIX_API_KEY", ""),
        "models": [
            {"id": "deepseek-v4.1-flash", "label": "DeepSeek V4.1 Flash · 快", "tier": "开源权重第一"},
            {"id": "gpt-6.2-sol", "label": "GPT-6.2 Sol · 强推理", "tier": "策略/评审"},
            {"id": "glm-5.3-flash", "label": "GLM-5.3 Flash", "tier": "中文长文"},
            {"id": "kimi-k3.1-pro", "label": "Kimi K3.1 Pro", "tier": "超长上下文"},
        ],
    },
    "deepseek": {
        "base": "https://api.deepseek.com/v1/chat/completions",
        "key": KEYS.get("DEEPSEEK_API_KEY", ""),
        "models": [{"id": "deepseek-chat", "label": "DeepSeek 官方 · chat", "tier": "直连"}],
    },
    "openrouter": {
        "base": "https://openrouter.ai/api/v1/chat/completions",
        "key": KEYS.get("OPENROUTER_API_KEY", ""),
        "models": [
            {"id": "deepseek/deepseek-v3.2", "label": "OpenRouter · DS V3.2", "tier": "开源"},
            {"id": "z-ai/glm-4.6", "label": "OpenRouter · GLM-4.6", "tier": "开源"},
        ],
    },
}

OLLAMA_CANDIDATES = ["http://127.0.0.1:11435", "http://127.0.0.1:11434"]
OLLAMA = OLLAMA_CANDIDATES[1]

def ollama_base():
    """优先用备用实例（11435，模型目录在 D:\\ai\\ollama-models，不受火绒拦截）。"""
    for url in OLLAMA_CANDIDATES:
        try:
            get_json(f"{url}/api/tags", timeout=3)
            return url
        except Exception:
            continue
    return OLLAMA_CANDIDATES[0]

# ----------------------------------------------------------------------------- http helper
def post_json(url, payload, headers=None, timeout=180):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))

def get_json(url, timeout=10):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))

# ----------------------------------------------------------------------------- ollama
def ollama_up():
    try:
        get_json(f"{ollama_base()}/api/tags", timeout=5)
        return True
    except Exception:
        return False

EMBED_HINTS = ("bge", "embed", "nomic", "minilm", "rerank", "bert")

def is_chat_model(mid):
    return not any(h in mid.lower() for h in EMBED_HINTS)

def ollama_models():
    try:
        tags = get_json(f"{ollama_base()}/api/tags", timeout=6)
        out = []
        for m in tags.get("models", []):
            fam = (m.get("details") or {}).get("family", "")
            out.append({"id": m["name"], "label": m["name"],
                        "tier": f"本地 {fam} {round(m.get('size',0)/1e9,1)}GB"})
        return out
    except Exception:
        return []

def ollama_chat(model, messages, system):
    """本地对话。embedding 类模型不能对话 → 明确报错让上层回落。"""
    msgs = ([{"role": "system", "content": system}] if system else []) + messages
    r = post_json(f"{ollama_base()}/api/chat",
                  {"model": model, "messages": msgs, "stream": False}, timeout=300)
    return r.get("message", {}).get("content", "")

# ----------------------------------------------------------------------------- cloud chat
def cloud_chat(provider, model, messages, system):
    cfg = CLOUD.get(provider)
    if not cfg:
        raise RuntimeError(f"未知提供商 {provider}")
    if not cfg["key"]:
        raise RuntimeError(f"{provider} 未配置 API key")
    msgs = ([{"role": "system", "content": system}] if system else []) + messages
    body = {"model": model, "messages": msgs, "max_tokens": 3000, "temperature": 0.6}
    r = post_json(cfg["base"], body, {"Authorization": f"Bearer {cfg['key']}"}, timeout=240)
    ch = (r.get("choices") or [{}])[0]
    return (ch.get("message") or {}).get("content") or ""

# ----------------------------------------------------------------------------- MCP client
class McpStdio:
    """最小 MCP stdio 客户端：initialize → tools/list → tools/call。"""
    def __init__(self, command, cwd=None, timeout=90):
        env = dict(os.environ)
        env.setdefault("npm_config_registry", "https://registry.npmmirror.com")
        env.setdefault("NPM_CONFIG_REGISTRY", "https://registry.npmmirror.com")
        self.proc = subprocess.Popen(command, shell=True, cwd=cwd, env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                     errors="replace", bufsize=1)
        self._id = 0
        self.timeout = timeout

    def _send(self, obj):
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _recv(self, want_id):
        end = time.time() + self.timeout
        while time.time() < end:
            line = self.proc.stdout.readline()
            if not line:
                err = ""
                try:
                    err = self.proc.stderr.read(600) or ""
                except Exception:
                    pass
                raise RuntimeError(f"MCP 进程无响应（退出）。{err.strip()[:400]}")
            line = line.strip()
            if not line.startswith("{"):
                continue  # 日志/横幅
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("id") == want_id:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"])[:400])
                return msg.get("result", {})
        raise TimeoutError("MCP 调用超时")

    def request(self, method, params=None):
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})
        return self._recv(self._id)

    def initialize(self):
        r = self.request("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "opc-os", "version": "1.0"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        return r

    def close(self):
        try:
            self.proc.terminate()
        except Exception:
            pass


def mcp_probe(entry):
    """启动一次，握手 + 取工具列表。"""
    c = McpStdio(entry["command"], entry.get("cwd"), timeout=120)
    try:
        init = c.initialize()
        tools = c.request("tools/list").get("tools", [])
        return {"ok": True, "server": (init.get("serverInfo") or {}),
                "tools": [{"name": t["name"], "desc": (t.get("description") or "")[:160]} for t in tools]}
    finally:
        c.close()


def mcp_call(entry, tool, args):
    c = McpStdio(entry["command"], entry.get("cwd"), timeout=180)
    try:
        c.initialize()
        res = c.request("tools/call", {"name": tool, "arguments": args})
        parts = []
        for item in res.get("content", []):
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                parts.append(f"[{item.get('type')}]")
        return {"ok": True, "text": "\n".join(parts)[:4000], "raw_isError": res.get("isError", False)}
    finally:
        c.close()

# ----------------------------------------------------------------------------- state
def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# ----------------------------------------------------------------------------- 计划书
def build_plan(task):
    mods = task.get("modules", [])
    lines = [
        f"# {task.get('project','（未命名）')}",
        "",
        f"**参赛方向**：{task.get('track','')}　|　**参赛形式**：{task.get('form','')}",
        f"**负责人**：{task.get('name','')}　|　**单位**：{task.get('school','')} {task.get('dept','')}",
        f"**联系方式**：{task.get('email','')}",
        "",
        "## 一、项目一句话",
        task.get("oneline", ""),
        "",
        "## 二、痛点",
        task.get("pain", ""),
        "",
        "## 三、产品与技术架构",
        "```",
        task.get("arch", ""),
        "```",
        "",
        "## 四、三大业务模块",
    ]
    for m in mods:
        lines += [f"### {m.get('name','')}", m.get("detail", ""), ""]
    lines += [
        "## 五、开源模型与工具链（全部公开可下载）",
        task.get("stack", ""),
        "",
        "## 六、可落地性与孵化路径",
        task.get("landing", ""),
        "",
        "## 七、交付物",
        task.get("deliverables", ""),
        "",
        f"## 八、报名信息\n- 报名入口：{task.get('signup','')}\n- 截止时间：{task.get('deadline','待附件确认')}",
    ]
    return "\n".join(lines)

DEFAULT_TASK = {
    "project": "OPC 一人公司 AI 操作系统（One Person Company OS）",
    "track": "AI+自主创新（联动 AI+体育文旅、智慧汽车/具身）",
    "form": "个人（可扩展为团队）",
    "name": "陈征宇",
    "school": "成都体育学院",
    "dept": "经济管理学院 经管26经管1班",
    "email": "czy20080222@outlook.com",
    "oneline": "一个人 + 一个 AI 操作台，就能接单、出片、出报告、出模型——让“一个人的公司”真的跑起来。",
    "pain": "小微创业者与个人创作者（学生创客尤甚）缺的不是点子，而是同时具备“会写、会画、会剪、会算、会交付”的团队。雇一个全能团队月成本以万计，而通用 AI 工具零散、切换成本高、数据还要外传。",
    "arch": "A · OPC 核心（指令中枢 → 任务分解 → MCP 工具调用 → 交付物）\n ├ 云腿①：DeepSeek-V4.1-Flash（成本低、日常运营）\n ├ 云腿②：GPT-6.2-Sol（强推理、策略与评审）\n ├ 本地兜底：Qwen3.5-9B（断网 / 隐私场景）\n ├ 模块 B · 内容与体育服务线：RTMPose 动作评分 + Wan2.2 出片 + ChatTTS 配音\n └ 模块 C · 具身交付线：SolidWorks/FreeCAD 模型 + Blender MCP 自动改型 + GR00T/LeRobot 示教",
    "modules": [
        {"name": "模块 B · 内容与体育服务线", "detail": "面向体育院校与文旅单位：一次拍摄，自动输出动作评分报告 + 文旅短视频。技术路径：RTMPose/MediaPipe 关键点 → 规则打分 → 报告模板；Wan2.2/Cosmos3-Nano 生成画面 → ChatTTS 配音 → 自动剪辑成片。"},
        {"name": "模块 C · 具身交付线", "detail": "面向中小制造与教学场景：SolidWorks/FreeCAD 建模 + Blender MCP 由自然语言驱动改型渲染，Isaac GR00T N1.7 / LeRobot 做低成本机械臂示教与动作迁移。"},
        {"name": "模块 A · 一人公司操作台", "detail": "统一对话界面：模型路由（本地/云自动切换）、MCP 一键接入（filesystem/git/office/comfyui/blender…）、任务面板与计划书一键导出。"},
    ],
    "stack": "本地：Qwen3.5-9B(Q4 5.68GB) / Gemma 4 12B / gpt-oss-20b；云：DeepSeek-V4.1-Flash、Kimi K3.1 Pro、GLM-5.3、Qwen3.8；专用：RTMPose、Wan-2.2、Cosmos3-Nano、Hunyuan3D-2.1、Fun-ASR、ChatTTS、GR00T、LeRobot；工具协议：MCP（modelcontextprotocol/servers 92.2k★，best-of-mcp-servers 400+ 收录）",
    "landing": "1) 学生创客与个体工作室：按需订阅，月费远低于雇人；2) 体育院校/俱乐部：动作分析服务按次收费；3) 中小制造：模型与示教交付。可对接成都东部新区 AI+OPC 孵化资源，先做校园与本地商家样板间，再走政府/园区采购。",
    "deliverables": "① 可运行 Demo（双击启动的本地控制台）② 2 分钟演示视频 ③ 项目计划书 ④ 路演 PPT ⑤ 开源模型与 MCP 选型清单",
    "signup": "http://112.195.201.35/app/2112098882311163905/published/2113552530711052290",
    "deadline": "待附件确认",
}

# ----------------------------------------------------------------------------- handler
class Handler(BaseHTTPRequestHandler):
    server_version = "OPCOS/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    # ---- helpers
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _text(self, text, code=200, ctype="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    # ---- GET
    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            f = ROOT / "ui.html"
            return self._text(f.read_text(encoding="utf-8"), ctype="text/html; charset=utf-8")
        if p == "/api/health":
            local = ollama_models()
            return self._json({
                "ok": True,
                "ollama_up": ollama_up(),
                "ollama": any(is_chat_model(m["id"]) for m in local),
                "local_models": local,
                "chat_models": [m for m in local if is_chat_model(m["id"])],
                "cloud": {k: bool(v["key"]) for k, v in CLOUD.items()},
                "mcp_registered": len(load_json(MCP_FILE, [])),
                "port": PORT,
            })
        if p == "/api/models":
            out = [{"group": "本地 Ollama", "items": ollama_models()}]
            for name, cfg in CLOUD.items():
                out.append({"group": f"云端 · {name}", "items":
                            [dict(m, group=name) for m in cfg["models"]] if cfg["key"] else []})
            return self._json({"groups": out})
        if p == "/api/mcp":
            reg = load_json(MCP_FILE, [])
            cat = load_json(REGISTRY, [])
            return self._json({"registered": reg, "registry": cat})
        if p == "/api/task":
            return self._json(load_json(TASK_FILE, DEFAULT_TASK))
        if p == "/api/plan":
            task = load_json(TASK_FILE, DEFAULT_TASK)
            md = build_plan(task)
            (ROOT / "task" / "计划书_草稿.md").write_text(md, encoding="utf-8")
            return self._json({"markdown": md})
        if p == "/api/plan.md":
            task = load_json(TASK_FILE, DEFAULT_TASK)
            return self._text(build_plan(task), ctype="text/markdown; charset=utf-8")
        return self._json({"error": "not found"}, 404)

    # ---- POST
    def do_POST(self):
        p = self.path.split("?")[0]
        b = self._body()

        if p == "/api/chat":
            return self._chat(b)

        if p == "/api/models/pull":
            name = (b.get("model") or "").strip()
            if not name:
                return self._json({"ok": False, "error": "缺少模型名"}, 400)
            def run():
                env = dict(os.environ)
                env["OLLAMA_HOST"] = ollama_base().split("//")[1]
                subprocess.Popen(["ollama", "pull", name], shell=True, env=env,
                                 creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            run()
            return self._json({"ok": True, "msg": f"已在新窗口开始拉取 {name}（需数分钟）"})

        if p == "/api/mcp/add":
            cat = load_json(REGISTRY, [])
            reg = load_json(MCP_FILE, [])
            entry = next((c for c in cat if c["id"] == b.get("id")), None)
            if not entry:
                return self._json({"ok": False, "error": "注册表中无此 id"}, 404)
            entry = dict(entry, added=time.strftime("%Y-%m-%d %H:%M"))
            entry.pop("verified", None)
            reg = [r for r in reg if r["id"] != entry["id"]] + [entry]
            save_json(MCP_FILE, reg)
            return self._json({"ok": True, "msg": f"已接入 {entry['name']}"})

        if p == "/api/mcp/remove":
            reg = [r for r in load_json(MCP_FILE, []) if r["id"] != b.get("id")]
            save_json(MCP_FILE, reg)
            return self._json({"ok": True})

        if p == "/api/mcp/probe":
            entry = next((r for r in load_json(MCP_FILE, []) if r["id"] == b.get("id")), None)
            if not entry:
                return self._json({"ok": False, "error": "尚未接入该服务器"}, 404)
            try:
                return self._json(mcp_probe(entry))
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:500]})

        if p == "/api/mcp/call":
            entry = next((r for r in load_json(MCP_FILE, []) if r["id"] == b.get("id")), None)
            if not entry:
                return self._json({"ok": False, "error": "尚未接入该服务器"}, 404)
            try:
                return self._json(mcp_call(entry, b.get("tool"), b.get("args") or {}))
            except Exception as e:
                return self._json({"ok": False, "error": str(e)[:500]})

        if p == "/api/task":
            task = load_json(TASK_FILE, DEFAULT_TASK)
            task.update(b)
            save_json(TASK_FILE, task)
            return self._json({"ok": True, "task": task})

        return self._json({"error": "not found"}, 404)

    def _chat(self, b):
        model = b.get("model") or "auto"
        messages = b.get("messages") or []
        system = b.get("system") or "你是 OPC 一人公司 AI 操作台的助手。回答简洁、可执行，中文作答。"
        tried = []
        start = time.time()

        def attempt(kind, prov, mid):
            tried.append(f"{kind}:{prov}/{mid}")
            if kind == "local":
                return ollama_chat(mid, messages, system)
            return cloud_chat(prov, mid, messages, system)

        plan = []
        if model == "auto":
            locals_ = [m for m in ollama_models() if is_chat_model(m["id"])]
            if locals_:
                plan.append(("local", "ollama", locals_[0]["id"]))
            plan.append(("cloud", "aihubmix", "deepseek-v4.1-flash"))
            plan.append(("cloud", "aihubmix", "gpt-6.2-sol"))
            plan.append(("cloud", "deepseek", "deepseek-chat"))
        elif model.startswith("local:"):
            plan = [("local", "ollama", model.split(":", 1)[1])]
            plan.append(("cloud", "aihubmix", "deepseek-v4.1-flash"))
        else:
            prov, mid = model.split(":", 1) if ":" in model else ("aihubmix", model)
            plan = [("cloud", prov, mid)]

        errors = []
        for kind, prov, mid in plan:
            try:
                text = attempt(kind, prov, mid)
                if text and text.strip():
                    return self._json({"ok": True, "reply": text, "used": f"{kind}:{prov}/{mid}",
                                       "fallback_from": tried[:-1], "errors": errors,
                                       "ms": int((time.time() - start) * 1000)})
                errors.append(f"{prov}/{mid}: 空回复")
            except Exception as e:
                errors.append(f"{prov}/{mid}: {str(e)[:200]}")
        return self._json({"ok": False, "reply": "", "used": None, "tried": tried,
                           "errors": errors, "ms": int((time.time() - start) * 1000)}, 200)


def main():
    if not TASK_FILE.exists():
        save_json(TASK_FILE, DEFAULT_TASK)
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"\n  OPC 一人公司 AI 操作系统\n  → http://127.0.0.1:{PORT}\n  Ctrl+C 停止\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")

if __name__ == "__main__":
    main()
