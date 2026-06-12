"""
灵感发散 — Inspire Mind
输入一个词，AI 从多个角度发散联想，生成可视化脑图。
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from openai import OpenAI
from pydantic import BaseModel

# ── 配置 ──────────────────────────────────────────────
load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

if not DEEPSEEK_API_KEY:
    print("⚠️ 未设置 DEEPSEEK_API_KEY，请创建 .env 文件或设置环境变量")
    print("   内容: DEEPSEEK_API_KEY=sk-***")

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


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return HTMLResponse("<h1>index.html not found</h1>", status_code=404)


async def _llm_diverge(word: str) -> dict | None:
    """调用 LLM 对单个词发散。返回 {"center", "nodes"} 或 None（失败时）。"""
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


@app.post("/api/generate")
async def generate(req: GenerateRequest):
    """调用 LLM 对一个词进行多角度发散，可指定深度 1-3"""
    if client is None:
        raise HTTPException(
            status_code=500,
            detail="未配置 DEEPSEEK_API_KEY，请在 .env 文件中设置",
        )

    depth = max(1, min(req.depth, 3))  # 限制 1-3

    # 第一层
    data = await _llm_diverge(req.word)
    if data is None:
        raise HTTPException(status_code=500, detail="LLM 调用失败")

    all_nodes = list(data["nodes"])
    all_edges = [
        {"source": data["center"], "target": node["label"]}
        for node in data["nodes"]
    ]
    seen_labels = {req.word} | {n["label"] for n in all_nodes}

    # BFS 逐层发散
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
                # 去重
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

    uvicorn.run(app, host="0.0.0.0", port=8765)
