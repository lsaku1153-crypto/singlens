# SingLens

本地运行的演唱 AI 陪练：选歌/上传歌曲 → 跟着伴奏唱 → 音准、节奏与表现力评分 → 生成下一遍的练习建议。

## 技术栈

- Python 3.11 + FastAPI + WebSocket
- librosa / numpy / scipy / soundfile 音频分析
- 原生 HTML / CSS / JavaScript + Canvas，无前端构建步骤
- SQLite 保存本地会话、报告、模板与设置

## 运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python web/server.py --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>。

## 可选能力

上传歌曲的原唱/伴奏分离、Whisper 歌词识别、AI 歌声合成属于可选链路，
依赖较大的模型和独立环境（Demucs / faster-whisper / torch 等），
不在默认 `requirements.txt` 中。没有这些环境时，内置曲目、录音上传和评分仍可运行。

## 目录

```
analysis/   音高、对齐、节奏、评分、练习计划
web/        FastAPI 服务与前端（web/js、web/css、web/fonts）
tools/      前端绑定/契约检查脚本
```

## 数据与隐私

`data/` 下是本地数据库、录音、上传歌曲、分离音轨、模型和操作者令牌，
**不进入版本库**。

## 字体

`web/fonts/` 使用 Saira Condensed、IBM Plex Mono、Outfit、得意黑子集，
均为 SIL OFL 1.1；许可证文件随字体一起保留。
