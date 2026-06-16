"""
写作创意助手 — Writing Creative Assistant
输入一个主题，AI 从情节、人物、场景、主题四个维度发散写作灵感。
"""

import json
import os
import sqlite3
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel

# ── 配置 ──────────────────────────────────────────────
load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
ADMIN_KEY = os.getenv("ADMIN_KEY", "admin123")

if not DEEPSEEK_API_KEY:
    print("⚠️ 未设置 DEEPSEEK_API_KEY")

client: OpenAI | None = None
if DEEPSEEK_API_KEY:
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

# ── SQLite ──────────────────────────────────────────
DB_PATH = Path(__file__).parent / "data.db"

def _init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY, free_uses INTEGER DEFAULT 10,
        is_pro INTEGER DEFAULT 0, nickname TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS pending (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT,
        nickname TEXT, txn_id TEXT, txn_platform TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'pending')""")
    conn.commit()
    conn.close()

def _get_user(user_id: str) -> dict:
    _init_db()
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute("SELECT free_uses, is_pro FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        row = (10, 0)
    conn.close()
    return {"free_uses": row[0], "is_pro": bool(row[1])}

def _use_free(user_id: str) -> int:
    user = _get_user(user_id)
    if user["is_pro"]:
        return -1
    remaining = max(0, user["free_uses"] - 1)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("UPDATE users SET free_uses = ? WHERE user_id = ?", (remaining, user_id))
    conn.commit()
    conn.close()
    return remaining

def _set_pro(user_id: str, nickname: str = ""):
    conn = sqlite3.connect(str(DB_PATH))
    if nickname:
        conn.execute("INSERT INTO users (user_id, is_pro, nickname) VALUES (?, 1, ?) ON CONFLICT(user_id) DO UPDATE SET is_pro=1, nickname=excluded.nickname", (user_id, nickname))
    else:
        cur = conn.execute("UPDATE users SET is_pro = 1 WHERE user_id = ?", (user_id,))
        if cur.rowcount == 0:
            conn.execute("INSERT INTO users (user_id, is_pro) VALUES (?, 1)", (user_id,))
    conn.commit()
    conn.close()

def _is_pro(uid: str) -> bool:
    return _get_user(uid)["is_pro"]

def _all_users() -> list:
    _init_db()
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute("SELECT user_id, free_uses, is_pro, nickname, created_at FROM users ORDER BY created_at DESC").fetchall()
    conn.close()
    return rows

def _pending_requests() -> list:
    _init_db()
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute("SELECT id, user_id, nickname, txn_id, txn_platform, created_at FROM pending WHERE status='pending' ORDER BY created_at DESC").fetchall()
    conn.close()
    return rows

def _approve_pending(req_id: int) -> tuple | None:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute("SELECT user_id, nickname FROM pending WHERE id=? AND status='pending'", (req_id,)).fetchone()
    if not row:
        conn.close()
        return None
    uid, nickname = row
    _set_pro(uid, nickname)
    conn.execute("UPDATE pending SET status='approved' WHERE id=?", (req_id,))
    conn.commit()
    conn.close()
    return (uid, nickname)

_init_db()

# ── 数据模型 ──────────────────────────────────────────
ASSOCIATION_PROMPT = """你是一个写作创意助手。对于给定的词语或主题，请从以下四个角度进行创意发散，帮助写作者激发灵感：

1. **情节构思**：可能的故事线、戏剧冲突、转折点、叙事结构
2. **人物设定**：角色原型、性格特质、背景故事、人际关系
3. **场景氛围**：适合的环境、时代背景、感官细节、情绪基调
4. **主题联想**：隐喻与象征、哲学思辨、深层含义、普世价值

请严格按照以下 JSON 格式输出，不要包含任何其他文字：
{"center": "输入词", "nodes": [{"id": "1", "label": "关联词", "category": "plot", "angle": "情节构思"}]}
categories 必须是以下之一：plot, character, setting, theme
每个角度生成 4-6 个创意点子。"""

class GenerateRequest(BaseModel):
    word: str
    depth: int = 1

class RegisterRequest(BaseModel):
    nickname: str
    txn_id: str = ""
    txn_platform: str = "wechat"

# ── FastAPI App ──────────────────────────────────────
app = FastAPI(title="写作创意助手", version="0.1.0")
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)


# ── 落地页 ──────────────────────────────────────────
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
.bg { position:fixed; top:0; left:0; width:100%; height:100%; z-index:0; pointer-events:none;
  background: radial-gradient(ellipse at 20% 50%, rgba(124,58,237,0.08) 0%, transparent 60%),
              radial-gradient(ellipse at 80% 20%, rgba(79,70,229,0.06) 0%, transparent 50%),
              radial-gradient(ellipse at 50% 80%, rgba(244,114,182,0.05) 0%, transparent 50%); }
.stars { position:absolute; width:100%; height:100%; }
.star { position:absolute; background:#fff; border-radius:50%; animation: twinkle 3s infinite alternate; }
@keyframes twinkle { 0% {opacity:0.2;} 100% {opacity:0.8;} }
.container { position:relative; z-index:1; max-width:600px; width:90%; padding: 60px 0; }
.hero { text-align:center; margin-bottom:40px; }
.hero-icon { font-size:56px; margin-bottom:20px; animation: float 3s ease-in-out infinite; }
@keyframes float { 0%,100% {transform:translateY(0);} 50% {transform:translateY(-8px);} }
.hero h1 { font-size:32px; font-weight:700; letter-spacing:-0.5px;
  background: linear-gradient(135deg, #a78bfa, #f472b6, #60a5fa);
  -webkit-background-clip:text; -webkit-text-fill-color:transparent;
  background-clip:text; margin-bottom:12px; }
.hero .tagline { color: rgba(255,255,255,0.5); font-size:16px; line-height:1.7; }
.features { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:32px; }
.feat { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06);
  border-radius:12px; padding:18px 16px; text-align:center; transition: all 0.3s; }
.feat:hover { background: rgba(255,255,255,0.06); border-color: rgba(255,255,255,0.12); transform:translateY(-2px); }
.feat .icon { font-size:24px; margin-bottom:8px; }
.feat .title { font-size:14px; font-weight:600; color: #e0e0f0; margin-bottom:4px; }
.feat .desc { font-size:12px; color: rgba(255,255,255,0.35); line-height:1.5; }
.start-card { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1);
  border-radius:16px; padding:36px 32px; text-align:center; backdrop-filter:blur(16px); }
.start-card h2 { font-size:18px; font-weight:600; margin-bottom:8px; color: #e0e0f0; }
.start-card .hint { color: rgba(255,255,255,0.35); font-size:13px; margin-bottom:24px; }
.btn { display:inline-block; padding:14px 48px; border-radius:12px; border:none; text-decoration:none;
  background: linear-gradient(135deg, #7c3aed, #4f46e5); color:#fff;
  font-size:16px; font-weight:600; cursor:pointer; transition: all 0.2s; }
.btn:hover { box-shadow:0 6px 24px rgba(124,58,237,0.4); transform:translateY(-1px); }
.footer { text-align:center; margin-top:40px; color: rgba(255,255,255,0.15); font-size:12px; }
@media (max-width:480px) { .features { grid-template-columns:1fr; } .hero h1 { font-size:26px; } }
</style>
</head>
<body>
<div class="bg"><div class="stars" id="stars"></div></div>
<div class="container">
  <div class="hero">
    <div class="hero-icon">✍️</div>
    <h1>写作创意助手</h1>
    <p class="tagline">AI 驱动的灵感引擎<br>从情节、人物、场景、主题四个维度<br>为你的创作注入无限可能</p>
  </div>
  <div class="features">
    <div class="feat"><div class="icon">📖</div><div class="title">情节构思</div><div class="desc">故事线、冲突、转折</div></div>
    <div class="feat"><div class="icon">👤</div><div class="title">人物设定</div><div class="desc">角色原型、关系网</div></div>
    <div class="feat"><div class="icon">🌍</div><div class="title">场景氛围</div><div class="desc">环境、情绪、时代</div></div>
    <div class="feat"><div class="icon">💡</div><div class="title">主题联想</div><div class="desc">隐喻、象征、哲思</div></div>
  </div>
  <div class="start-card">
    <h2>🎁 免费体验 10 次</h2>
    <p class="hint">无需注册，点击即可开始创作</p>
    <a href="/start" class="btn">开始体验 →</a>
  </div>
  <div class="footer">Powered by DeepSeek</div>
</div>
<script>
const starsEl=document.getElementById("stars");
for(let i=0;i<60;i++){const s=document.createElement("div");s.className="star";
s.style.cssText=`left:${Math.random()*100}%;top:${Math.random()*100}%;width:${Math.random()*3+1}px;height:${Math.random()*3+1}px;animation-delay:${Math.random()*3}s;animation-duration:${Math.random()*3+2}s`;
starsEl.appendChild(s);}
</script>
</body>
</html>"""


# ── 付费页（收款码 + 交易号验证）──────────────────
PRICING_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>永久解锁 Pro · 写作创意助手</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body {
  min-height:100vh; display:flex; align-items:center; justify-content:center;
  background: #0a0a1a; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
  color: #e0e0f0;
}
.card {
  background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1);
  border-radius:20px; padding:40px 36px; max-width:480px; width:90%;
  text-align:center; backdrop-filter:blur(16px);
}
.badge { display:inline-block; background: linear-gradient(135deg, #7c3aed, #4f46e5);
  color:#fff; font-size:12px; font-weight:600; padding:4px 14px; border-radius:20px; margin-bottom:20px; letter-spacing:1px; }
h1 { font-size:24px; font-weight:700; margin-bottom:6px; }
.sub { color: rgba(255,255,255,0.4); font-size:14px; margin-bottom:20px; }
.price { font-size:42px; font-weight:800; margin:16px 0 4px; color: #fbbf24; }
.price span { font-size:16px; color: rgba(255,255,255,0.5); font-weight:400; }
.period { color: rgba(255,255,255,0.4); font-size:13px; margin-bottom:24px; }
.qr-section { display:flex; gap:24px; justify-content:center; margin-bottom:20px; flex-wrap:wrap; }
.qr-box { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08);
  border-radius:16px; padding:20px 16px; width:160px; }
.qr-box .label { font-size:14px; font-weight:600; margin-bottom:12px; }
.qr-box .label.wx { color: #34d399; }
.qr-box .label.alipay { color: #60a5fa; }
.qr-box .qr-img { width:140px; height:140px; border-radius:8px; margin:0 auto 10px; display:block; object-fit:contain; }
.qr-note { color: rgba(255,255,255,0.25); font-size:11px; }
.steps { text-align:left; margin:24px 0; padding:16px; background: rgba(255,255,255,0.02); border-radius:12px; }
.steps h3 { font-size:13px; color: rgba(255,255,255,0.6); margin-bottom:10px; }
.steps ol { padding-left:20px; }
.steps li { color: rgba(255,255,255,0.5); font-size:13px; padding:4px 0; line-height:1.6; }
.btn-paid { display:block; width:100%; padding:14px; border-radius:12px; border:none;
  background: linear-gradient(135deg, #f59e0b, #ef4444); color:#fff; font-size:16px; font-weight:600;
  cursor:pointer; margin-bottom:12px; transition: all 0.2s; }
.btn-paid:hover { box-shadow:0 6px 24px rgba(245,158,11,0.4); transform:translateY(-1px); }
.modal-overlay { display:none; position:fixed; top:0; left:0; width:100%; height:100%;
  background: rgba(0,0,0,0.7); z-index:100; align-items:center; justify-content:center; }
.modal-overlay.show { display:flex; }
.modal { background: #1a1a2e; border:1px solid rgba(255,255,255,0.1); border-radius:16px;
  padding:36px 30px; width:90%; max-width:380px; text-align:center; }
.modal h2 { font-size:20px; margin-bottom:6px; }
.modal .hint { color:rgba(255,255,255,0.35); font-size:13px; margin-bottom:8px; }
.modal .tutorial { color:rgba(255,255,255,0.3); font-size:11px; margin-bottom:16px; line-height:1.5; }
.modal input, .modal select { width:100%; padding:12px 16px; border-radius:10px;
  border:1px solid rgba(255,255,255,0.1); background: rgba(255,255,255,0.04);
  color:#e0e0f0; font-size:15px; outline:none; margin-bottom:16px; text-align:center; }
.modal select { -webkit-appearance:none; appearance:none; cursor:pointer; }
.modal input:focus, .modal select:focus { border-color: #7c3aed; }
.modal .btn-submit { width:100%; padding:12px; border-radius:10px; border:none;
  background: linear-gradient(135deg, #34d399, #10b981); color:#111;
  font-size:15px; font-weight:600; cursor:pointer; }
.modal .btn-submit:hover { box-shadow:0 4px 16px rgba(52,211,153,0.3); }
.modal .btn-submit:disabled { opacity:0.5; cursor:not-allowed; }
.modal .close { margin-top:12px; color:rgba(255,255,255,0.25); font-size:12px; cursor:pointer; }
.back { display:block; margin-top:12px; color: rgba(255,255,255,0.3); font-size:13px; text-decoration:none; }
.back:hover { color: rgba(255,255,255,0.6); }
.success-icon { font-size:48px; margin-bottom:12px; }
</style>
</head>
<body>
<div class="card">
  <div class="badge">PRO</div>
  <h1>写作创意助手 Pro</h1>
  <p class="sub">解锁全部创作能力</p>
  <div class="price">¥5<span> 永久解锁</span></div>
  <div class="period">一次购买，终身使用</div>

  <div class="qr-section">
    <div class="qr-box">
      <div class="label wx">💚 微信支付</div>
      <img src="/static/微信5元收款.png" class="qr-img" alt="微信收款 ¥5">
    </div>
    <div class="qr-box">
      <div class="label alipay">💙 支付宝</div>
      <img src="/static/支付宝5元收款.jpg" class="qr-img" alt="支付宝收款 ¥5">
    </div>
  </div>

  <div class="steps">
    <h3>📋 开通步骤</h3>
    <ol>
      <li>选择微信或支付宝扫码支付 ¥5</li>
      <li>支付完成后，复制交易单号</li>
      <li>点击下方按钮，填写昵称和交易单号</li>
      <li>管理员核对后即刻开通 ✅</li>
    </ol>
  </div>

  <button class="btn-paid" onclick="showRegister()">💰 我已付款，提交开通申请</button>

  <a href="/" class="back">← 返回</a>
</div>

<!-- 注册弹窗 -->
<div class="modal-overlay" id="modalOverlay">
  <div class="modal" id="modalContent">
    <h2 id="modalTitle">📝 提交开通申请</h2>
    <p class="hint">请填写以下信息，管理员核对后会为你开通</p>
    <p class="tutorial" id="modalTutorial">在微信/支付宝账单中找到本次支付 ¥5 的记录，复制交易单号</p>
    <select id="platformSelect">
      <option value="wechat">微信支付</option>
      <option value="alipay">支付宝</option>
    </select>
    <input type="text" id="txnInput" placeholder="粘贴交易单号" maxlength="50">
    <input type="text" id="nicknameInput" placeholder="你的创作昵称" maxlength="20">
    <button class="btn-submit" id="btnSubmit" onclick="doRegister()">✅ 提交申请</button>
    <div class="close" onclick="closeModal()">取消</div>
  </div>
</div>

<script>
function showRegister() { document.getElementById('modalOverlay').classList.add('show'); }
function closeModal() { document.getElementById('modalOverlay').classList.remove('show'); }

async function doRegister() {
  const nickname = document.getElementById('nicknameInput').value.trim();
  const txnId = document.getElementById('txnInput').value.trim();
  const platform = document.getElementById('platformSelect').value;
  if (!nickname) { alert('请输入昵称'); return; }
  if (!txnId) { alert('请粘贴交易单号（用于管理员核对付款）'); return; }
  const btn = document.getElementById('btnSubmit');
  btn.disabled = true; btn.textContent = '提交中…';
  try {
    const resp = await fetch('/api/register', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ nickname, txn_id: txnId, txn_platform: platform })
    });
    if (!resp.ok) {
      const err = await resp.json().catch(()=>({detail:'未知错误'}));
      alert('提交失败: '+err.detail); btn.disabled = false; btn.textContent = '✅ 提交申请';
      return;
    }
    // 成功 - 显示等待审核
    document.getElementById('modalTitle').textContent = '📬 申请已提交';
    document.getElementById('modalTutorial').textContent = '';
    document.getElementById('platformSelect').style.display = 'none';
    document.getElementById('txnInput').style.display = 'none';
    document.getElementById('nicknameInput').style.display = 'none';
    document.querySelector('.modal .hint').innerHTML = '管理员核对付款后将为你开通 Pro<br>请耐心等待，通常几分钟内完成';
    btn.textContent = '关闭';
    btn.onclick = closeModal;
  } catch(e) {
    alert('网络错误，请重试');
    btn.disabled = false; btn.textContent = '✅ 提交申请';
  }
}
</script>
</body>
</html>"""


# ── 管理后台 ────────────────────────────────────────
ADMIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>管理后台 · 写作创意助手</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { min-height:100vh; display:flex; align-items:center; justify-content:center;
  background: #0a0a1a; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
  color: #e0e0f0; }
.card { background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1);
  border-radius:20px; padding:36px 32px; max-width:620px; width:90%; backdrop-filter:blur(16px); }
h1 { font-size:22px; font-weight:700; margin-bottom:6px; }
.sub { color: rgba(255,255,255,0.35); font-size:13px; margin-bottom:20px; }
.section { margin-bottom:24px; }
.section h2 { font-size:14px; color: rgba(255,255,255,0.5); margin-bottom:10px; border-bottom:1px solid rgba(255,255,255,0.05); padding-bottom:6px; }
.form-group { margin-bottom:10px; }
.form-group label { display:block; font-size:13px; color: rgba(255,255,255,0.5); margin-bottom:4px; }
.form-group input { width:100%; padding:10px 14px; border-radius:10px;
  border:1px solid rgba(255,255,255,0.1); background: rgba(255,255,255,0.04);
  color:#e0e0f0; font-size:14px; outline:none; }
.form-group input:focus { border-color: #7c3aed; }
.btn-row { display:flex; gap:8px; }
.btn { padding:10px 20px; border-radius:10px; border:none; font-size:14px; font-weight:600; cursor:pointer; }
.btn-green { background: linear-gradient(135deg, #34d399, #10b981); color:#111; }
.btn-green:hover { box-shadow:0 4px 16px rgba(52,211,153,0.3); }
.btn-red { background: linear-gradient(135deg, #ef4444, #dc2626); color:#fff; }
.btn-red:hover { box-shadow:0 4px 16px rgba(239,68,68,0.3); }
.msg { margin-top:10px; padding:10px; border-radius:8px; font-size:13px; display:none; }
.msg.success { display:block; background: rgba(52,211,153,0.1); color:#34d399; border:1px solid rgba(52,211,153,0.2); }
.msg.error { display:block; background: rgba(239,68,68,0.1); color:#f87171; border:1px solid rgba(239,68,68,0.2); }
.table { width:100%; margin-top:12px; border-collapse:collapse; font-size:12px; }
.table th,.table td { padding:8px 10px; text-align:left; border-bottom:1px solid rgba(255,255,255,0.05); }
.table th { color: rgba(255,255,255,0.4); font-weight:500; }
.table td { color: rgba(255,255,255,0.7); font-family:monospace; }
.table .pro { color:#34d399; } .table .free { color:#fbbf24; } .table .pending { color:#f59e0b; }
.approve-btn { padding:4px 12px; border-radius:6px; border:none; background:#34d399; color:#111;
  font-size:11px; font-weight:600; cursor:pointer; }
.approve-btn:hover { background:#10b981; }
.reject-btn { padding:4px 12px; border-radius:6px; border:none; background:rgba(239,68,68,0.3); color:#f87171;
  font-size:11px; font-weight:600; cursor:pointer; margin-left:4px; }
.reject-btn:hover { background:rgba(239,68,68,0.5); }
.back { display:block; margin-top:16px; color: rgba(255,255,255,0.25); font-size:12px; text-align:center; text-decoration:none; }
</style>
</head>
<body>
<div class="card">
  <h1>🛠️ 管理后台</h1>
  <p class="sub">审核开通申请 · 管理用户</p>

  <div class="form-group">
    <label>管理员密钥</label>
    <input type="password" id="adminKey" placeholder="输入 ADMIN_KEY">
  </div>

  <!-- 待审核申请 -->
  <div class="section">
    <h2>📩 待审核申请</h2>
    <table class="table" id="pendingTable">
      <thead><tr><th>昵称</th><th>平台</th><th>交易单号</th><th>时间</th><th>操作</th></tr></thead>
      <tbody id="pendingBody"><tr><td colspan="5" style="text-align:center;color:rgba(255,255,255,0.2);">输入密钥后自动加载</td></tr></tbody>
    </table>
    <div class="msg" id="msg"></div>
  </div>

  <!-- 已开通用户 -->
  <div class="section">
    <h2>👤 用户列表</h2>
    <table class="table" id="userTable">
      <thead><tr><th>昵称</th><th>用户 ID</th><th>状态</th><th>剩余</th><th>时间</th></tr></thead>
      <tbody id="userBody"><tr><td colspan="5" style="text-align:center;color:rgba(255,255,255,0.2);">输入密钥后自动加载</td></tr></tbody>
    </table>
  </div>

  <a href="/" class="back">← 返回主页</a>
</div>

<script>
async function loadAll() {
  const key = document.getElementById('adminKey').value;
  if (!key) return;
  // load pending
  try {
    const resp = await fetch(`/admin/pending?key=${encodeURIComponent(key)}`);
    const data = await resp.json();
    if (!data.ok) { document.getElementById('pendingBody').innerHTML='<tr><td colspan="5" style="text-align:center;color:#f87171;">密钥错误</td></tr>'; return; }
    if (data.pending.length===0) {
      document.getElementById('pendingBody').innerHTML='<tr><td colspan="5" style="text-align:center;color:rgba(255,255,255,0.2);">暂无待审核申请</td></tr>';
    } else {
      document.getElementById('pendingBody').innerHTML = data.pending.map(p =>
        `<tr><td>${p[2]}</td><td>${p[4]==='wechat'?'微信':'支付宝'}</td><td style="font-size:11px;">${p[3]}</td><td>${p[5]}</td><td><button class="approve-btn" onclick="approve(${p[0]})">✅ 开通</button><button class="reject-btn" onclick="reject(${p[0]})">✕</button></td></tr>`
      ).join('');
    }
    // load users
    const resp2 = await fetch(`/admin/users?key=${encodeURIComponent(key)}`);
    const data2 = await resp2.json();
    if (data2.ok) {
      document.getElementById('userBody').innerHTML = data2.users.map(u =>
        `<tr><td>${u[3]||'-'}</td><td>${u[0].slice(0,14)}…</td><td class="${u[2]?'pro':'free'}">${u[2]?'Pro':'免费'}</td><td>${u[1]}</td><td>${u[4]}</td></tr>`
      ).join('');
    }
  } catch(e) { console.error(e); }
}

async function approve(id) {
  const key = document.getElementById('adminKey').value;
  const msg = document.getElementById('msg');
  try {
    const resp = await fetch(`/admin/approve?key=${encodeURIComponent(key)}&id=${id}`);
    const data = await resp.json();
    msg.className = data.ok ? 'msg success' : 'msg error';
    msg.textContent = data.ok ? `✅ 已开通` : `❌ ${data.error}`;
    if (data.ok) loadAll();
  } catch(e) {
    msg.className='msg error'; msg.textContent='操作失败';
  }
}

async function reject(id) {
  const key = document.getElementById('adminKey').value;
  const msg = document.getElementById('msg');
  if (!confirm('确定拒绝此申请？')) return;
  try {
    const resp = await fetch(`/admin/reject?key=${encodeURIComponent(key)}&id=${id}`);
    const data = await resp.json();
    msg.className = data.ok ? 'msg success' : 'msg error';
    msg.textContent = data.ok ? '已拒绝' : data.error;
    if (data.ok) loadAll();
  } catch(e) {
    msg.className='msg error'; msg.textContent='操作失败';
  }
}

document.getElementById('adminKey').addEventListener('input', loadAll);
</script>
</body>
</html>"""


# ── 路由 ──────────────────────────────────────────
@app.get("/start")
async def start(request: Request):
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

@app.get("/pricing", response_class=HTMLResponse)
async def pricing_page(request: Request):
    uid = request.cookies.get("uid", "")
    resp = HTMLResponse(PRICING_HTML)
    if not uid:
        resp.set_cookie("uid", uuid.uuid4().hex, max_age=86400*365, httponly=True, samesite="lax")
    return resp

# ── 注册 API ─────────────────────────────────────
@app.post("/api/register")
async def register(req: RegisterRequest, request: Request):
    uid = request.cookies.get("uid", "")
    if not uid:
        raise HTTPException(status_code=400, detail="请先从首页进入")
    if not req.nickname.strip():
        raise HTTPException(status_code=400, detail="请输入昵称")
    if not req.txn_id.strip():
        raise HTTPException(status_code=400, detail="请填写交易单号")
    # 保存为待审核申请
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("INSERT INTO pending (user_id, nickname, txn_id, txn_platform) VALUES (?, ?, ?, ?)",
                 (uid, req.nickname.strip(), req.txn_id.strip(), req.txn_platform))
    conn.commit()
    conn.close()
    return {"ok": True, "message": "申请已提交，管理员核对后将为你开通 Pro"}

# ── 管理后台 API ──────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return HTMLResponse(ADMIN_HTML)

@app.get("/admin/pending")
async def admin_pending(key: str):
    if key != ADMIN_KEY:
        return JSONResponse({"ok": False, "error": "密钥错误"}, status_code=403)
    return JSONResponse({"ok": True, "pending": _pending_requests()})

@app.get("/admin/approve")
async def admin_approve(key: str, id: int):
    if key != ADMIN_KEY:
        return JSONResponse({"ok": False, "error": "密钥错误"}, status_code=403)
    result = _approve_pending(id)
    if result is None:
        return JSONResponse({"ok": False, "error": "申请不存在或已处理"})
    return JSONResponse({"ok": True, "uid": result[0], "nickname": result[1]})

@app.get("/admin/reject")
async def admin_reject(key: str, id: int):
    if key != ADMIN_KEY:
        return JSONResponse({"ok": False, "error": "密钥错误"}, status_code=403)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("UPDATE pending SET status='rejected' WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True})

@app.get("/admin/users")
async def admin_users(key: str):
    if key != ADMIN_KEY:
        return JSONResponse({"ok": False, "error": "密钥错误"}, status_code=403)
    return JSONResponse({"ok": True, "users": _all_users()})

# ── API ──────────────────────────────────────────
@app.get("/api/me")
async def get_user_info(request: Request):
    uid = request.cookies.get("uid", "")
    if not uid:
        return JSONResponse({"pro": False, "free_remaining": 0, "error": "no session"})
    user = _get_user(uid)
    return JSONResponse({"pro": user["is_pro"], "free_remaining": user["free_uses"]})

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
            temperature=0.9, max_tokens=2000,
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
    if not is_pro:
        user = _get_user(uid)
        if user["free_uses"] <= 0:
            raise HTTPException(status_code=402, detail="免费额度已用完，请升级 Pro")
        _use_free(uid)
        req.depth = min(req.depth, 1)
    depth = max(1, min(req.depth, 3))
    data = await _llm_diverge(req.word)
    if data is None:
        raise HTTPException(status_code=500, detail="LLM 调用失败")
    all_nodes = list(data["nodes"])
    all_edges = [{"source": data["center"], "target": node["label"]} for node in data["nodes"]]
    seen_labels = {req.word} | {n["label"] for n in all_nodes}
    if depth > 1:
        queue: list[tuple[str, int]] = [(n["label"], 2) for n in all_nodes]
        while queue and len(all_nodes) < 50:
            parent, level = queue.pop(0)
            if level > depth: continue
            sub = await _llm_diverge(parent)
            if sub is None: continue
            for node in sub.get("nodes", []):
                label = node["label"]
                if label in seen_labels or label == parent: continue
                seen_labels.add(label)
                all_nodes.append(node)
                all_edges.append({"source": parent, "target": label})
                if level < depth and len(all_nodes) < 50:
                    queue.append((label, level + 1))
    return {"center": data["center"], "nodes": all_nodes, "edges": all_edges}


# ── 静态文件 ───────────────────────────────────
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8765)))
