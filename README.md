# 写作创意助手 ✍️
输入一个主题，AI 从情节、人物、场景、主题四个维度发散写作灵感，可视化脑图展示。

## 功能
- 🎯 四维写作发散：情节构思 / 人物设定 / 场景氛围 / 主题联想
- 🌳 多深度递归展开（1-3 层）
- 🗺️ 可视化脑图 + 混合布局
- 📷 导出 PNG / SVG / JSON
- 💾 自动保存到浏览器
- ⌨️ 全键盘快捷键操作

## 技术栈
- 后端：Python FastAPI + DeepSeek API
- 前端：原生 HTML/CSS/JS + D3.js

## 本地运行
```bash
pip install -r requirements.txt
cp .env.example .env  # 填入 DEEPSEEK_API_KEY
python app.py
```
打开 http://localhost:8765