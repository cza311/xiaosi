# 小四 SakuraMaid

本地优先的桌面 AI 女仆：语音对话 + 桌面立绘 + 手机远程 + 自动翻唱。

> 她不是聊天窗口，是一个住在你桌面上的角色——会等你回来、提醒你休息、
> 自己去 B 站找歌学着翻唱、也能在你上班时用手机指挥她干活。

## 功能亮点

- **语音交互**：离线语音识别（vosk 中文大模型），说「小四」唤醒；插话打断；日语声线 + 中文字幕
- **智能体工具**：待办 / 日程提醒 / 天气 / 打开应用 / 搜索 / 桌面整理 / 便签 / 截图理解屏幕 / 长期记忆
- **自动翻唱**：说「唱《歌名》」→ 她自己去 B 站找歌 → UVR5 分离人声 → RVC 换成她的声线 → 成品归档并汇报（全后台，进度可问）
- **手机控制台**：局域网/Tailscale 用手机发消息、看状态、语音对话（PWA，可加主屏）
- **桌面立绘**：12 帧动作（挥手/端茶/散步/打瞌睡…），呼吸浮动、随机散步、无人时滑到屏幕边缘待命
- **主动关怀**：久坐提醒、晨昏问候、空闲搭话（台词由 LLM 现编，不是固定话术）
- **状态机**：思考/唱歌/工作中状态全程可查，手机端实时可见

## 系统要求

| 项目 | 要求 |
|---|---|
| 系统 | Windows 10/11 |
| Python | 3.10+（3.14 实测通过） |
| 内存 | 8G 可跑，16G 舒服 |
| 显卡 | 有 NVIDIA 显卡体验最佳（RVC 唱歌需要）；无显卡可用 CPU 慢速模式 |
| LLM | 智谱 API Key（[open.bigmodel.cn](https://open.bigmodel.cn) 免费申请，glm-4.5-flash 免费） |

## 安装（三步）

```bat
git clone https://github.com/cza311/xiaosi.git D:\maid
cd /d D:\maid
py install.py
```

`install.py` 会自动完成：安装 Python 依赖 → 下载 vosk 语音模型 →
引导你放置 GPT-SoVITS 整合包 → 生成 config.json → 创建开机自启。

### 手动安装（等价流程）

1. **依赖**：`pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`
2. **语音识别模型**：下载 [vosk-model-cn-0.22](https://alphacephei.com/vosk/models)（1.9GB），解压到仓库根 `vosk-model-cn-0.22/`
3. **语音引擎**：下载 [GPT-SoVITS 官方整合包](https://github.com/RVC-Boss/GPT-SoVITS/releases)（v2pro，约 20GB），解压到 `gptsovits/` 下，然后把 `启动女仆酱.bat` 里的目录名 `GPT-SoVITS-v2pro-20250604` 改成你实际的目录名
4. **配置**：复制 `config.example.json` 为 `config.json`，填入你的智谱 `api_key`
5. **启动**：双击 `启动女仆酱.bat`（自动拉起 TTS 引擎 + 主程序）

## 声音与立绘（自备素材）

小四是「壳 + 声音 + 皮」分离的设计，仓库不带版权素材，**你的女仆你做主**：

- **声音**：在 GPT-SoVITS 里用你喜欢的角色/自己的声音微调（官方 webui 一键训练），
  然后在 `config.json` 里把 `gptsovits_ref` 指向你的参考音频。
  不训练也能用：`config.json` 里 `tts_engine` 改为 `edge`，直接用微软 Edge-TTS 免费声线（零配置）
- **立绘**：把你的角色 PNG 放到仓库根，命名 `avatar.png`（默认）、`avatar_wave.png`（挥手）、
  `avatar_tea.png`（端茶）等，见 [FEATURES.md](FEATURES.md) 的 12 帧清单。最低只需一张 `avatar.png`
- **Live2D**（可选实验功能）：把 Live2D 模型放进 `l2d/model/`，`config.json` 里 `avatar_engine` 改为 `l2d`

## 项目结构

```
main.py             主循环：立绘 + 唤醒 + 对话调度
maid_brain.py       LLM 智能体：工具调用 / 确定性意图路由
maid_voice.py       TTS 引擎封装（GPT-SoVITS / Edge-TTS 双引擎）
voice_worker.py     离线语音识别（vosk）
maid_avatar.py      桌面立绘（Tk，零 GPU 依赖）
maid_remote.py      手机控制台 HTTP 服务（含 PWA 前端 remote_ui/）
maid_status.py      状态机（idle/thinking/singing/working/waiting/done）
maid_extras.py      记忆 / 技能自学 / 主动关怀 / 翻唱调度
maid_talk.py        台词工厂（LLM 现编 + 本地词库兜底）
watchdog.py         看门狗（崩溃自动拉起，征得同意才重启）
song/               翻唱流水线（B 站找歌 + UVR5 + RVC）
tuwen/              小说推文视频流水线
```

## 已知限制

- 语音识别只用中文模型，英文/日文口语唤醒未适配
- RVC 翻唱依赖 NVIDIA 显卡（CPU 能跑但一首歌要很久）
- GPT-SoVITS 训练部分请用官方工具，本仓库只做推理接入

## 许可

代码以 [AGPL-3.0](LICENSE) 开源，永久免费。
GPT-SoVITS / vosk / RVC 等第三方组件遵循其各自的开源协议。
请遵守当地法律法规，自备的声音与立绘素材的版权责任由使用者自行承担。

## 致谢

- [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) —— 少样本声音克隆
- [RVC](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI) —— 歌声声线转换
- [vosk](https://alphacephei.com/vosk/) —— 离线语音识别
- [智谱 AI](https://open.bigmodel.cn) —— GLM 大模型
- [Shinsekai / 七海](https://github.com/) —— 剧本式多段输出协议的思想来源
