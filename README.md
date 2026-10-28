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

# 首次运行：为现有演唱者生成账号（一次性密码在 data/initial_accounts.txt）
python tools/accounts.py init

python web/server.py --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>，会进入登录页。
登录页也支持本机创建练习账号；默认只允许回环地址注册，
局域网开放需显式设置 `SINGLENS_REGISTER=open`。

## 可选能力

上传歌曲的原唱/伴奏分离、Whisper 歌词识别、AI 歌声合成属于可选链路，
依赖较大的模型和独立环境（Demucs / faster-whisper / torch 等），
不在默认 `requirements.txt` 中。没有这些环境时，内置曲目、录音上传和评分仍可运行。

## 目录

```
analysis/   音高、对齐、节奏、评分、练习计划
web/        FastAPI 服务与前端（web/js、web/css、web/fonts）
tools/      前端绑定/契约检查脚本、账号管理 CLI
```

## 数据与隐私

- 原始音频、录音、上传歌曲、数据库、模型和账号文件都在本地 `data/`，**不进入版本库**；
- 唱后 AI 教练只接收报告文本（指标、问题片段、歌词、自述偏好），不接收音频；
- 生产使用请只绑定 `127.0.0.1`，或在 HTTPS 反向代理后设置
  `SINGLENS_COOKIE_SECURE=1`。局域网普通 HTTP 下密码不是加密传输。

## 字体

`web/fonts/` 使用 Saira Condensed、IBM Plex Mono、Outfit、得意黑子集，
均为 SIL OFL 1.1；许可证文件随字体一起保留。
