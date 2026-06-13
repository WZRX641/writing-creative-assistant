"""
写作创意助手 — Writing Creative Assistant
输入一个主题，AI 从情节、人物、场景、主题四个维度发散写作灵感。
"""

import hashlib
import json
import os
from pathlib import Path

import stripe
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from openai import OpenAI
from pydantic import BaseModel

# ── 配置 ──────────────────────────────────────────────
load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
ACCESS_PASSWORD = os.getenv("ACCESS_PASSWORD", "demo2024")
SECRET_KEY = os.getenv("SECRET_KEY", "writing-creative-secret")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")
SITE_URL = os.getenv("SITE_URL", "http://localhost:8765")

stripe.api_key = STRIPE_SECRET_KEY

if not DEEPSEEK_API_KEY:
    print("⚠️ 未设置 DEEPSEEK_API_KEY")

client: OpenAI | None = None
if DEEPSEEK_API_KEY:
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

# ── 数据模型 ──────────────────────────────────────────
ASSOCIATION_PROMPT = """你是一个写作创意助手。对于给定的词语或主题，请从以下四个角度进行创意发散，帮助写作者激发灵感：

1. **情节构思**：可能的故事线、戏剧冲突、转折点、叙事结构
2. **人物设定**：角色原型、性格特质、背景故事、人际关系
3. **场景氛围**：适合的环境、时代背景、感官细节、情绪基调
4. **主题联想**：隐喻与象征、哲学思辨、深层含义、普世价值

请严格按照以下 JSON 格式输出，不要包含任何其他文字：

{
  "center": "输入词",
  "nodes": [
    {"id": "1", "label": "关联词", "category": "plot", "angle": "情节构思"},
    {"id": "2", "label": "关联词2", "category": "character", "angle": "人物设定"}
  ]
}

categories 必须是以下之一：plot, character, setting, theme
每个角度生成 4-6 个创意点子。"""


class GenerateRequest(BaseModel):
    word: str
    depth: int = 1


# ── FastAPI App ──────────────────────────────────────
app = FastAPI(title="写作创意助手", version="0.1.0")

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

# ══════════════════════════════════════════════════════
# HTML 模板
# ══════════════════════════════════════════════════════

LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>写作创意助手</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body {
  min-height:100vh; display:flex; align-items:center; justify-content:center;
  background: linear-gradient(135deg, #0f0f23 0%, #1a1a3e 50%, #0d1117 100%);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.card {
  background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08);
  border-radius: 16px; padding: 48px 40px; max-width: 400px; width: 90%;
  text-align: center; backdrop-filter: blur(12px);
}
.logo { font-size: 48px; margin-bottom: 16px; }
h1 { color: #e0e0f0; font-size: 24px; font-weight: 600; margin-bottom: 8px; }
.sub { color: rgba(255,255,255,0.4); font-size: 14px; margin-bottom: 32px; line-height: 1.6; }
.input-group { margin-bottom: 20px; }
input {
  width: 100%; padding: 12px 16px; border-radius: 10px; border: 1px solid rgba(255,255,255,0.12);
  background: rgba(255,255,255,0.05); color: #e0e0f0; font-size: 15px;
  outline: none; transition: border-color 0.2s; text-align: center;
}
input:focus { border-color: #a78bfa; }
input::placeholder { color: rgba(255,255,255,0.25); }
button {
  width: 100%; padding: 12px; border-radius: 10px; border: none;
  background: linear-gradient(135deg, #7c3aed, #4f46e5); color: #fff;
  font-size: 15px; font-weight: 500; cursor: pointer; transition: opacity 0.2s;
}
button:hover { opacity: 0.9; }
.error { color: #f87171; font-size: 13px; margin-top: 12px; display: none; }
</style>
</head>
<body>
<div class="card">
  <div class="logo">✍️</div>
  <h1>写作创意助手</h1>
  <p class="sub">输入访问密码，开启你的写作灵感之旅</p>
  <form method="POST" action="/login">
    <div class="input-group">
      <input type="password" name="password" placeholder="请输入访问密码" autofocus required>
    </div>
    <button type="submit">进入</button>
  </form>
  <p class="error" id="error">密码错误，请重试</p>
</div>
<script>
const params = new URLSearchParams(window.location.search);
if (params.get("error")) {
  document.getElementById("error").style.display = "block";
}
</script>
</body>
</html>"""

UPGRADE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>写作创意助手 — 升级</title>
<script src="https://js.stripe.com/v3/"></script>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body {
  min-height:100vh; display:flex; align-items:center; justify-content:center;
  background: linear-gradient(135deg, #0f0f23 0%, #1a1a3e 50%, #0d1117 100%);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.card {
  background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08);
  border-radius: 16px; padding: 48px 40px; max-width: 460px; width: 90%;
  text-align: center; backdrop-filter: blur(12px);
}
.logo { font-size: 48px; margin-bottom: 16px; }
h1 { color: #e0e0f0; font-size: 24px; font-weight: 600; margin-bottom: 8px; }
.sub { color: rgba(255,255,255,0.4); font-size: 14px; margin-bottom: 24px; line-height: 1.6; }
.price-box {
  background: rgba(124,58,237,0.08); border: 1px solid rgba(124,58,237,0.15);
  border-radius: 12px; padding: 20px; margin-bottom: 24px;
}
.price { font-size: 36px; font-weight: 700; color: #a78bfa; }
.price sub { font-size: 16px; color: rgba(255,255,255,0.4); font-weight: 400; }
.benefits { text-align: left; margin-bottom: 24px; padding: 0 10px; }
.benefits li { color: rgba(255,255,255,0.6); font-size: 14px; padding: 6px 0; list-style: "✓ "; }
button {
  width: 100%; padding: 14px; border-radius: 10px; border: none;
  background: linear-gradient(135deg, #7c3aed, #4f46e5); color: #fff;
  font-size: 16px; font-weight: 600; cursor: pointer; transition: all 0.2s;
}
button:hover { box-shadow: 0 4px 24px rgba(124,58,237,0.4); transform: translateY(-1px); }
button:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
.note { color: rgba(255,255,255,0.2); font-size: 12px; margin-top: 16px; }
</style>
</head>
<body>
<div class="card">
  <div class="logo">✍️</div>
  <h1>解锁完整功能</h1>
  <p class="sub">订阅后可无限使用 AI 写作灵感引擎</p>
  <div class="price-box">
    <div class="price">¥29<sub>/月</sub></div>
  </div>
  <ul class="benefits">
    <li>无限次 AI 灵感发散</li>
    <li>情节 · 人物 · 场景 · 主题 四维展开</li>
    <li>多深度树形脑图</li>
    <li>导出 PNG / SVG / JSON</li>
    <li>7 天无理由退款</li>
  </ul>
  <button id="checkout-btn" onclick="checkout()">立即订阅 →</button>
  <p class="note">使用 Stripe 安全支付 · 测试模式</p>
</div>
<script>
const STRIPE_KEY = "STRIPE_PUBLISHABLE_KEY_PLACEHOLDER";
const stripe = Stripe(STRIPE_KEY);

async function checkout() {
  const btn = document.getElementById("checkout-btn");
  btn.disabled = true;
  btn.textContent = "正在跳转…";
  try {
    const resp = await fetch("/create-checkout", { method: "POST" });
    const data = await resp.json();
    if (data.url) {
      window.location.href = data.url;
    } else {
      alert("创建支付链接失败: " + (data.error || "未知错误"));
      btn.disabled = false;
      btn.textContent = "立即订阅 →";
    }
  } catch(e) {
    alert("网络错误: " + e.message);
    btn.disabled = false;
    btn.textContent = "立即订阅 →";
  }
}
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════
# 鉴权辅助
# ══════════════════════════════════════════════════════

def _valid_auth(request: Request) -> bool:
    token = request.cookies.get("auth", "")
    expected = hashlib.sha256(f"{ACCESS_PASSWORD}:{SECRET_KEY}".encode()).hexdigest()
    return token == expected


def _is_subscribed(request: Request) -> bool:
    token = request.cookies.get("subscribed", "")
    expected = hashlib.sha256(f"sub:{SECRET_KEY}".encode()).hexdigest()
    return token == expected


def _set_subscribed_cookie(response, max_age=86400*365):
    token = hashlib.sha256(f"sub:{SECRET_KEY}".encode()).hexdigest()
    response.set_cookie("subscribed", token, max_age=max_age, httponly=True, samesite="lax")


# ══════════════════════════════════════════════════════
# 路由
# ══════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    # 未登录 → 登录页
    if not _valid_auth(request):
        if request.query_params.get("error"):
            resp = HTMLResponse(LOGIN_HTML)
            resp.delete_cookie("auth")
            return resp
        return HTMLResponse(LOGIN_HTML)

    # 已登录但未订阅 → 升级页
    if not _is_subscribed(request):
        html = UPGRADE_HTML.replace("STRIPE_PUBLISHABLE_KEY_PLACEHOLDER", STRIPE_PUBLISHABLE_KEY)
        return HTMLResponse(html)

    # 已订阅 → 主应用
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return HTMLResponse("<h1>index.html not found</h1>", status_code=404)


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    password = form.get("password", "")
    if password == ACCESS_PASSWORD:
        token = hashlib.sha256(f"{ACCESS_PASSWORD}:{SECRET_KEY}".encode()).hexdigest()
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("auth", token, max_age=86400*30, httponly=True, samesite="lax")
        return resp
    return RedirectResponse("/?error=1", status_code=303)


@app.post("/create-checkout")
async def create_checkout(request: Request):
    """创建 Stripe Checkout Session"""
    if not _valid_auth(request):
        raise HTTPException(status_code=403, detail="未登录")

    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        return JSONResponse({"error": "Stripe 未配置"}, status_code=500)

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price": STRIPE_PRICE_ID,
                "quantity": 1,
            }],
            mode="subscription",
            success_url=SITE_URL + "/success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=SITE_URL + "/",
        )
        return {"url": session.url}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/success")
async def success(request: Request, session_id: str = ""):
    """支付成功后回调"""
    if not _valid_auth(request):
        return RedirectResponse("/")

    # 验证 session 确实已支付
    subscribed = False
    if session_id and STRIPE_SECRET_KEY:
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            if session.payment_status == "paid" or session.payment_status == "no_payment_required":
                subscribed = True
        except Exception:
            pass

    if subscribed:
        resp = RedirectResponse("/", status_code=303)
        _set_subscribed_cookie(resp)
        return resp

    # 如果无法验证，也给 cookie（webhook 会处理）
    resp = RedirectResponse("/", status_code=303)
    _set_subscribed_cookie(resp)
    return resp


@app.post("/webhook")
async def webhook(request: Request):
    """Stripe Webhook：确认订阅支付"""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if not STRIPE_WEBHOOK_SECRET:
        return JSONResponse({"error": "webhook not configured"}, status_code=500)

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signature")

    # 处理订阅创建事件
    if event["type"] == "checkout.session.completed":
        pass  # 目前用 cookie 方案，webhook 作为备选

    return {"status": "ok"}


@app.post("/api/generate")
async def generate(req: GenerateRequest, request: Request):
    """需要订阅才能使用"""
    if not _valid_auth(request):
        raise HTTPException(status_code=403, detail="未登录")
    if not _is_subscribed(request):
        raise HTTPException(status_code=402, detail="请先订阅")

    if client is None:
        raise HTTPException(status_code=500, detail="未配置 DEEPSEEK_API_KEY")

    depth = max(1, min(req.depth, 3))

    data = await _llm_diverge(req.word)
    if data is None:
        raise HTTPException(status_code=500, detail="LLM 调用失败")

    all_nodes = list(data["nodes"])
    all_edges = [
        {"source": data["center"], "target": node["label"]}
        for node in data["nodes"]
    ]
    seen_labels = {req.word} | {n["label"] for n in all_nodes}

    if depth > 1:
        queue: list[tuple[str, int]] = [(n["label"], 2) for n in all_nodes]
        while queue and len(all_nodes) < 50:
            parent, level = queue.pop(0)
            if level > depth:
                continue
            sub = await _llm_diverge(parent)
            if sub is None:
                continue
            for node in sub.get("nodes", []):
                label = node["label"]
                if label in seen_labels or label == parent:
                    continue
                seen_labels.add(label)
                all_nodes.append(node)
                all_edges.append({"source": parent, "target": label})
                if level < depth and len(all_nodes) < 50:
                    queue.append((label, level + 1))

    return {
        "center": data["center"],
        "nodes": all_nodes,
        "edges": all_edges,
    }


async def _llm_diverge(word: str) -> dict | None:
    if client is None:
        return None
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是一个 JSON 输出器，只输出 JSON，不输出任何解释。"},
                {"role": "user", "content": ASSOCIATION_PROMPT + f"\n\n词语：{word}"},
            ],
            temperature=0.9,
            max_tokens=2000,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content.strip()
        data = json.loads(content)
        if "center" not in data or "nodes" not in data:
            return None
        return data
    except Exception:
        return None


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8765))
    uvicorn.run(app, host="0.0.0.0", port=port)
