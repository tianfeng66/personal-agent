# 私人 AI 工作助理

用自然语言整理日常工作：待办任务、定时提醒、日历、邮件、微信推送、上网搜索、笔记、工作日志、日报、长期记忆。数据存在本地 SQLite，零第三方依赖（Python 3.9+）。

设计参考了 GitHub 上的开源项目：[personal-os](https://github.com/amanaiproduct/personal-os)、[CowAgent](https://github.com/zhayujie/CowAgent)、[talorys](https://github.com/rociiu/talorys)。

[下载 Windows x64 便携版](https://github.com/tianfeng66/personal-agent/releases/latest/download/Personal-Agent-Windows-x64.zip) · [查看更新记录](CHANGELOG.md)

## Windows 快速开始

将 Windows x64 便携包完整解压到可写文件夹，双击 **Start.cmd**。包内附带 Python，启动后默认浏览器会打开工作台。前台使用时保留命令窗口，按 Ctrl+C 可以退出。

首次启动会生成同目录的 `.env`。任务、笔记、日志和提醒可以直接使用。要启用 AI 对话，用记事本打开 `.env`，按模型服务商填写 `LLM_API_KEY`、`LLM_BASE_URL` 和 `LLM_MODEL`，保存后重新启动。Windows 日程保存在应用的本地 SQLite 数据库里，可通过 AI 对话新建，在「日程」页查看。桌面提醒的可见性取决于 Windows 通知设置。

如需在登录 Windows 后自动运行，先关闭前台服务，在解压目录打开 PowerShell，执行：

```powershell
.\runtime\python.exe -X utf8 -m agent install
.\runtime\python.exe -X utf8 -m agent status
.\runtime\python.exe -X utf8 -m agent uninstall
```

以上命令分别用于启用、查询和取消自启。启用后，双击 Start.cmd 即可打开正在运行的工作台。移动文件夹前请先取消自启，移动后重新启用。备份或迁移时保留整个 `data` 文件夹和 `.env`。仅下载源码的用户可安装 Python 3.9 或更高版本，并将命令中的 `.\runtime\python.exe` 换成 `py -3`。

## macOS 快速开始

```bash
cd personal-agent
cp .env.example .env      # 至少填写 LLM_API_KEY
python3 -m agent install  # 设为开机自动在后台运行，然后浏览器打开 http://127.0.0.1:8765
```

后台运行相关命令：

```bash
python3 -m agent status     # 查看是否在运行
python3 -m agent install    # 修改 .env 或更新代码后，重新执行一次即可重启
python3 -m agent uninstall  # 取消开机自动运行
```

不想后台运行的话，也可以用 `python3 -m agent web` 手动启动（终端窗口要保持打开）。

运行日志在 `data/logs/server.log`，数据库每天自动备份到 `data/backups/`（保留最近 14 天）。
对话记录会保存下来，重启后可以接着聊；点「清空对话」会删除对话记录，任务和笔记不受影响。

## 在对话中开发工具

展开 AI 对话下方的「开发与管理工具」，勾选「在对话中设计工具」。例如输入「做一个会议纪要工具，调用时填写标题和正文，每条笔记固定加上会议标签」。AI 会提交草案。核对名称、用途、基础动作和固定参数后，点击「批准启用」。关闭开发面板即可在普通对话中使用，例如「用会议纪要记录今天的评审结论」。

每个工具可封装一个基础动作，支持创建或查询任务、保存或搜索笔记、记录工作日志、读取日报数据。调用时只需补充未固定的参数。重新设计同名工具可以替换旧版本，拒绝草案会保留现有版本。已启用的工具可随时撤销。工具配置随本机数据库备份。开发模式和自然语言调用需要配置支持工具调用的模型。

## 多智能体协作

在「团队」标签页创建智能体（可以从研究员、秘书、写作助手、审核员模板开始），给每个成员设定职责、工作要求和可用工具。

- 总助理统筹：判断任务需要谁，把任务交给成员，最后汇总。可以同时开工的成员会并行执行。
- 成员之间可以互相请求帮忙，委托链最多 3 层（总助理 → A → B），不允许循环委托。
- 每个成员只能使用授权给它的工具；删除类操作和长期记忆只有总助理能用。
- 对话框输入「@名字 任务」可以直接点名某个成员。
- AI 回答时，对话框里会实时显示每位成员在做什么；回答下方可以展开「协作过程」。
- 搜索量大时 DuckDuckGo 容易拦截，建议配置 `TAVILY_API_KEY`。

## 测试

```bash
python3 -m unittest discover -s tests -t .
```

## 功能与配置

| 功能 | 说明 | 需要配置 |
| --- | --- | --- |
| 任务 / 笔记 / 日志 / 日报 / 记忆 | 对话或右侧面板手动管理 | 无 |
| 定时提醒 | 「下午3点提醒我开会」，到点弹系统通知 + 网页提示 | 无 |
| Windows 日历 | 应用内本地日程 | 无 |
| Mac 日历 | 查看、新建日程（iCloud/Google/Exchange 等在「日历」App 里登录过的都行） | 首次使用在系统弹窗点「好」 |
| 上网搜索 | 搜索并读取网页 | 无（可选 `TAVILY_API_KEY`） |
| 邮件 | 查看收件箱；AI 只能写草稿，要你在「邮件」页点「确认发送」 | `EMAIL_*`，密码填授权码 |
| 微信推送 | 提醒、日报推送到你的微信 | `SERVERCHAN_KEY` 或 `WECOM_WEBHOOK_URL` |

各项的具体填法见 `.env.example`。

## 限制

- 微信只能推送消息给自己，不能读取聊天记录（微信没有给个人号开放接口，第三方工具有封号风险）。
- 提醒只在程序运行时触发；程序关着时错过的提醒，下次启动会补发。
- 日历里的重复事件只能读到第一次。
- Outlook/Hotmail 个人邮箱已不支持密码登录，暂时用不了。

## 安全

- 网页只在本机可访问，没有登录功能，请不要改成对外开放。
- 网页读取功能禁止访问本机和内网地址。
- `.env` 和 `data/` 已加入 `.gitignore`，不要推到公开仓库。
