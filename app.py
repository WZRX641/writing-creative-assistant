"""
写作创意助手 — Writing Creative Assistant
输入一个主题，AI 从情节、人物、场景、主题四个维度发散写作灵感。
支持 Stripe 付费订阅（测试模式）。
"""

import json
import os
import sqlite3
import uuid
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
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "price_1ThtvFCO83dWpJe0yxf7V532")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8765")

if not DEEPSEEK_API_KEY:
    print("⚠️ 未设置 DEEPSEEK_API_KEY")

client: OpenAI | None = None
if DEEPSEEK_API_KEY:
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# ── SQLite 持久化 ──────────────────────────────────────
DB_PATH = Path(__file__).parent / "data.db"

def _init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            free_uses INTEGER DEFAULT 10,
            is_pro INTEGER DEFAULT 0,
            stripe_customer_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def _get_user(user_id: str) -> dict:
    """获取用户数据，不存在则创建（10 次免费）"""
    _init_db()
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT free_uses, is_pro FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row is None:
        conn.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        row = (10, 0)
    conn.close()
    return {"free_uses": row[0], "is_pro": bool(row[1])}

def _use_free(user_id: str) -> int:
    """消耗一次免费次数，返回剩余次数"""
    user = _get_user(user_id)
    if user["is_pro"]:
        return -1
    remaining = max(0, user["free_uses"] - 1)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("UPDATE users SET free_uses = ? WHERE user_id = ?", (remaining, user_id))
    conn.commit()
    conn.close()
    return remaining

def _set_pro(user_id: str, customer_id: str = ""):
    """将用户设为 Pro"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "UPDATE users SET is_pro = 1, stripe_customer_id = ? WHERE user_id = ?",
        (customer_id, user_id),
    )
    if conn.rowcount == 0:
        conn.execute(
            "INSERT INTO users (user_id, is_pro, stripe_customer_id) VALUES (?, 1, ?)",
            (user_id, customer_id),
        )
    conn.commit()
    conn.close()

def _ensure_user(request: Request) -> str:
    """从 cookie 获取或创建 user_id"""
    uid = request.cookies.get("uid", "")
    if not uid:
        uid = uuid.uuid4().hex
    return uid

def _is_pro(uid: str) -> bool:
    return _get_user(uid)["is_pro"]

_init_db()

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

# ── 落地页（免密码） ──────────────────────────────────

LANDING_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>写作创意助手 — AI 驱动的写作灵感引擎</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body {
  min-height:100vh; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
  background: #0a0a1a; color: #e0e0f0; overflow-x: hidden;
  display:flex; flex-direction:column; align-items:center;
}
.bg {
  position:fixed; top:0; left:0; width:100%; height:100%; z-index:0; pointer-events:none;
  background: radial-gradient(ellipse at 20% 50%, rgba(124,58,237,0.08) 0%, transparent 60%),
              radial-gradient(ellipse at 80% 20%, rgba(79,70,229,0.06) 0%, transparent 50%),
              radial-gradient(ellipse at 50% 80%, rgba(244,114,182,0.05) 0%, transparent 50%);
}
.stars { position:absolute; width:100%; height:100%; }
.star { position:absolute; background:#fff; border-radius:50%; animation: twinkle 3s infinite alternate; }
@keyframes twinkle { 0% {opacity:0.2;} 100% {opacity:0.8;} }
.container { position:relative; z-index:1; max-width:600px; width:90%; padding: 60px 0; }
.hero { text-align:center; margin-bottom:40px; }
.hero-icon { font-size:56px; margin-bottom:20px; animation: float 3s ease-in-out infinite; }
@keyframes float { 0%,100% {transform:translateY(0);} 50% {transform:translateY(-8px);} }
.hero h1 {
  font-size:32px; font-weight:700; letter-spacing:-0.5px;
  background: linear-gradient(135deg, #a78bfa, #f472b6, #60a5fa);
  -webkit-background-clip:text; -webkit-text-fill-color:transparent;
  background-clip:text; margin-bottom:12px;
}
.hero .tagline { color: rgba(255,255,255,0.5); font-size:16px; line-height:1.7; }
.features { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:32px; }
.feat {
  background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06);
  border-radius:12px; padding:18px 16px; text-align:center;
  transition: all 0.3s;
}
.feat:hover { background: rgba(255,255,255,0.06); border-color: rgba(255,255,255,0.12); transform:translateY(-2px); }
.feat .icon { font-size:24px; margin-bottom:8px; }
.feat .title { font-size:14px; font-weight:600; color: #e0e0f0; margin-bottom:4px; }
.feat .desc { font-size:12px; color: rgba(255,255,255,0.35); line-height:1.5; }
.start-card {
  background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1);
  border-radius:16px; padding:36px 32px; text-align:center; backdrop-filter:blur(16px);
}
.start-card h2 { font-size:18px; font-weight:600; margin-bottom:8px; color: #e0e0f0; }
.start-card .hint { color: rgba(255,255,255,0.35); font-size:13px; margin-bottom:24px; }
.btn {
  display:inline-block; padding:14px 48px; border-radius:12px; border:none; text-decoration:none;
  background: linear-gradient(135deg, #7c3aed, #4f46e5); color:#fff;
  font-size:16px; font-weight:600; cursor:pointer; transition: all 0.2s;
}
.btn:hover { box-shadow:0 6px 24px rgba(124,58,237,0.4); transform:translateY(-1px); }
.footer { text-align:center; margin-top:40px; color: rgba(255,255,255,0.15); font-size:12px; }
@media (max-width:480px) {
  .features { grid-template-columns:1fr; }
  .hero h1 { font-size:26px; }
}
</style>
</head>
<body>
<div class="bg">
  <div class="stars" id="stars"></div>
</div>
<div class="container">
  <div class="hero">
    <div class="hero-icon">✍️</div>
    <h1>写作创意助手</h1>
    <p class="tagline">
      AI 驱动的灵感引擎<br>
      从情节、人物、场景、主题四个维度<br>
      为你的创作注入无限可能
    </p>
  </div>
  <div class="features">
    <div class="feat">
      <div class="icon">📖</div>
      <div class="title">情节构思</div>
      <div class="desc">故事线、冲突、转折</div>
    </div>
    <div class="feat">
      <div class="icon">👤</div>
      <div class="title">人物设定</div>
      <div class="desc">角色原型、关系网</div>
    </div>
    <div class="feat">
      <div class="icon">🌍</div>
      <div class="title">场景氛围</div>
      <div class="desc">环境、情绪、时代</div>
    </div>
    <div class="feat">
      <div class="icon">💡</div>
      <div class="title">主题联想</div>
      <div class="desc">隐喻、象征、哲思</div>
    </div>
  </div>
  <div class="start-card">
    <h2>🎁 免费体验 10 次</h2>
    <p class="hint">无需注册，点击即可开始创作</p>
    <a href="/start" class="btn">开始体验 →</a>
  </div>
  <div class="footer">Powered by DeepSeek · Stripe 测试模式</div>
</div>
<script>
const starsEl = document.getElementById("stars");
for (let i=0; i<60; i++) {
  const s = document.createElement("div");
  s.className = "star";
  s.style.cssText = `left:${Math.random()*100}%;top:${Math.random()*100}%;width:${Math.random()*3+1}px;height:${Math.random()*3+1}px;animation-delay:${Math.random()*3}s;animation-duration:${Math.random()*3+2}s`;
  starsEl.appendChild(s);
}
</script>
</body>
</html>"""


PRICING_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>升级 Pro · 写作创意助手</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body {
  min-height:100vh; display:flex; align-items:center; justify-content:center;
  background: #0a0a1a; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
  color: #e0e0f0;
}
.card {
  background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1);
  border-radius:20px; padding:48px 40px; max-width:420px; width:90%;
  text-align:center; backdrop-filter:blur(16px);
}
.badge {
  display:inline-block; background: linear-gradient(135deg, #7c3aed, #4f46e5);
  color:#fff; font-size:12px; font-weight:600; padding:4px 14px; border-radius:20px;
  margin-bottom:20px; letter-spacing:1px;
}
h1 { font-size:28px; font-weight:700; margin-bottom:8px; }
.price { font-size:48px; font-weight:800; margin:20px 0 4px; }
.price span { font-size:18px; color: rgba(255,255,255,0.5); font-weight:400; }
.period { color: rgba(255,255,255,0.4); font-size:14px; margin-bottom:28px; }
.features { text-align:left; margin-bottom:32px; }
.features li {
  list-style:none; padding:8px 0; font-size:14px; color: rgba(255,255,255,0.7);
  display:flex; align-items:center; gap:8px;
}
.features li::before { content:"✓"; color:#34d399; font-weight:bold; }
.btn {
  display:block; width:100%; padding:14px; border-radius:12px; border:none;
  background: linear-gradient(135deg, #7c3aed, #4f46e5); color:#fff;
  font-size:16px; font-weight:600; cursor:pointer; text-decoration:none;
  transition: all 0.2s;
}
.btn:hover { box-shadow:0 6px 24px rgba(124,58,237,0.4); transform:translateY(-1px); }
.back { display:block; margin-top:16px; color: rgba(255,255,255,0.3); font-size:13px; text-decoration:none; }
.back:hover { color: rgba(255,255,255,0.6); }
</style>
</head>
<body>
<div class="card">
  <div class="badge">PRO</div>
  <h1>写作创意助手 Pro</h1>
  <p style="color:rgba(255,255,255,0.4);font-size:14px;margin-bottom:8px;">解锁全部创作能力</p>
  <div class="price">$5<span> 永久解锁</span></div>
  <div class="period">一次购买，终身使用</div>
  <ul class="features">
    <li>无限次 AI 灵感发散</li>
    <li>深度 1-3 层递归展开</li>
    <li>导出 PNG / SVG / JSON</li>
    <li>优先体验新功能</li>
  </ul>
  <a href="/create-checkout" class="btn">🚀 永久解锁 Pro</a>
  <a href="/" class="back">← 返回</a>
</div>
</body>
</html>"""


SUCCESS_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>订阅成功 · 写作创意助手</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body {
  min-height:100vh; display:flex; align-items:center; justify-content:center;
  background: #0a0a1a; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
  color: #e0e0f0;
}
.card {
  background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1);
  border-radius:20px; padding:48px 40px; max-width:420px; width:90%;
  text-align:center; backdrop-filter:blur(16px);
}
.icon { font-size:64px; margin-bottom:20px; }
h1 { font-size:24px; font-weight:700; margin-bottom:8px; color:#34d399; }
p { color: rgba(255,255,255,0.5); font-size:14px; margin-bottom:32px; line-height:1.6; }
.btn {
  display:block; width:100%; padding:14px; border-radius:12px; border:none;
  background: linear-gradient(135deg, #7c3aed, #4f46e5); color:#fff;
  font-size:16px; font-weight:600; cursor:pointer; text-decoration:none;
}
.btn:hover { box-shadow:0 6px 24px rgba(124,58,237,0.4); }
</style>
</head>
<body>
<div class="card">
  <div class="icon">🎉</div>
  <h1>解锁成功！</h1>
  <p>你已是 Pro 会员<br>所有高级功能已永久解锁</p>
  <a href="/" class="btn">开始创作 →</a>
</div>
</body>
</html>"""


# ── 路由 ──────────────────────────────────────────────

@app.get("/start")
async def start(request: Request):
    """设置 cookie 并进入主应用"""
    uid = uuid.uuid4().hex
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("uid", uid, max_age=86400*365, httponly=True, samesite="lax")
    return resp


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    uid = request.cookies.get("uid", "")
    if uid:
        html_path = STATIC_DIR / "index.html"
        if html_path.exists():
            return html_path.read_text(encoding="utf-8")
        return HTMLResponse("<h1>index.html not found</h1>", status_code=404)
    return HTMLResponse(LANDING_HTML)


# ── Stripe 付费 ──────────────────────────────────────

@app.get("/pricing", response_class=HTMLResponse)
async def pricing_page(request: Request):
    return HTMLResponse(PRICING_HTML)


@app.get("/create-checkout")
async def create_checkout(request: Request):
    if not STRIPE_SECRET_KEY:
        return HTMLResponse("<h2>Stripe 未配置</h2>", status_code=500)
    uid = request.cookies.get("uid", "")
    if not uid:
        uid = uuid.uuid4().hex
    try:
        checkout_session = stripe.checkout.Session.create(
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            mode="payment",
            client_reference_id=uid,
            success_url=f"{BASE_URL}/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{BASE_URL}/pricing",
        )
        resp = RedirectResponse(checkout_session.url, status_code=303)
        resp.set_cookie("uid", uid, max_age=86400*365, httponly=True, samesite="lax")
        return resp
    except Exception as e:
        return HTMLResponse(f"<h2>创建支付失败: {e}</h2>", status_code=500)


@app.get("/success", response_class=HTMLResponse)
async def success_page(request: Request):
    return HTMLResponse(SUCCESS_HTML)


@app.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    if not sig_header or not STRIPE_WEBHOOK_SECRET:
        return JSONResponse({"error": "missing signature"}, status_code=400)
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        return JSONResponse({"error": "invalid signature"}, status_code=400)
    except Exception:
        return JSONResponse({"error": "webhook error"}, status_code=400)
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        uid = session.get("client_reference_id", "")
        customer_id = session.get("customer", "")
        if uid:
            _set_pro(uid, customer_id)
            print(f"✅ Pro 已激活: {uid}")
        else:
            print(f"✅ 支付成功 (未关联用户): {session.get('id')}")
    elif event["type"] == "customer.subscription.deleted":
        subscription = event["data"]["object"]
        print(f"❌ 订阅取消: {subscription.get('id')}")
    return JSONResponse({"status": "ok"})


@app.get("/api/me")
async def get_user_info(request: Request):
    uid = request.cookies.get("uid", "")
    if not uid:
        return JSONResponse({"pro": False, "free_remaining": 0, "error": "no session"})
    user = _get_user(uid)
    return JSONResponse({
        "pro": user["is_pro"],
        "free_remaining": user["free_uses"],
    })


# ── API ────────────────────────────────

async def _llm_diverge(word: str) -> dict | None:
    if client is None:
        return None
    try:
        response = client.chat.completions.create(
            model="deepseek-v4-flash",
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


@app.post("/api/generate")
async def generate(req: GenerateRequest, request: Request):
    if client is None:
        raise HTTPException(status_code=500, detail="未配置 DEEPSEEK_API_KEY")

    uid = request.cookies.get("uid", "")
    if not uid:
        raise HTTPException(status_code=403, detail="请先进入首页获取访问凭证")

    is_pro = _is_pro(uid)

    # 免费用户：检查 + 消耗额度
    if not is_pro:
        user = _get_user(uid)
        if user["free_uses"] <= 0:
            raise HTTPException(status_code=402, detail="免费额度已用完，请升级 Pro")
        _use_free(uid)
        req.depth = min(req.depth, 1)  # 免费用户仅深度 1

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


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8765))
    uvicorn.run(app, host="0.0.0.0", port=port)
