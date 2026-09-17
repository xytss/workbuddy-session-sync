# WorkBuddy 会话同步

在同一台 Windows 电脑上，将同一套本地会话接管给当前登录的 WorkBuddy 账号。
账号 A → B → N → A，保留会话 ID、原始消息和工作区引用，不生成多个聊天副本。

**当前是 0.3.1：支持在 WorkBuddy 运行中检测账号切换并自动接管，无需退出或重启客户端。**
尚未完成真实账号往返续聊验收，因此仍不承诺服务端模型会接受跨账号沿用的全部上下文。

## 启动

要求 Windows 11、uv，首次启动需要联网安装依赖。项目使用 Python 3.12。

最简单的启动方式是双击项目根目录的 `启动GUI.bat`。脚本会自动切换到项目目录并启动 GUI；
无论从桌面快捷方式还是资源管理器启动，都不依赖当前工作目录。

```powershell
Set-Location -LiteralPath 'F:\Code\workbuddy-session-sync'
uv sync --locked
uv run workbuddy-sync
```

也可以在 PowerShell 中运行项目里的 `start.ps1`。

## GUI 页面

- 「同步概览」在顶部显示当前登录账号名称和账号 ID，并用摘要卡片展示可用会话数与共享范围。
  表格优先显示会话标题；账号和会话 ID 会缩短显示，鼠标悬停可以查看完整值。
- 「账号历史」左侧列出当前账号及 WorkBuddy 留下的历史登录账号名称、ID 和会话数量；右侧显示
  所选账号当前拥有的本地历史会话，并可直接在 WorkBuddy 中打开。这里展示的是当前归属视图，不是固定快照：
  会话同步给新账号后，会从原账号移到新账号的列表中。
- 「设置与恢复」集中放置数据位置、备份目录和恢复操作。恢复归属使用警示样式，并在执行前要求确认。

同步范围默认使用「全部会话」，切换账号后自动把全部普通会话同步给新账号。改为「仅选中的会话」后，
列表会显示逐行勾选列、「全选当前会话」「清空选择」和「已选 x / y」摘要；不再需要按住 Ctrl 或
Shift 多选。保存设置和同步操作使用不同的视觉层级，降低误触恢复操作的风险。

## 自动同步的使用方式

1. 打开本工具，检查数据目录和登录文件，刷新预览。
2. 默认候选范围为所有账号的普通会话，包含以后新建的会话。选择「仅选中的会话」后，可逐项选择，
   也可使用「全选当前会话」或「清空选择」。
3. 自动同步默认开启。保持工具窗口运行，可以最小化；关闭窗口即停止监测。
4. 在 WorkBuddy 内正常切换账号并完成登录。
5. 等待本工具提示「同步完成」或「所选会话已归当前账号」。
6. 在 WorkBuddy 中打开原来的对话继续。

工具不会切换登录账号，也不会结束或重启 WorkBuddy 进程。切号期间登录文件尚未写完整时，
工具会等待下一轮；WorkBuddy 正在写数据库时，也会等待下一轮，不会因此关闭自动同步。

WorkBuddy 在切号时会立即读取一次会话列表，而本工具默认每 3 秒检测一次，因此接管完成后，
原会话可能还没有出现在新账号的侧栏。此时在本工具列表中只选中该会话，点击
「在 WorkBuddy 中打开」；工具会使用 WorkBuddy 自带的 `workbuddy://chat/<会话ID>` 深链
直接打开它，无需重启客户端。

自动同步默认开启。启用全量共享意味着这个本地数据目录内不同账号的普通会话都会归当前账号，
这也包括新账号自身已有的普通会话。一次只供一个 WorkBuddy 客户端使用。
已软删除的会话、后台自动化会话不参与同步；MCP 凭据、记忆、额度、远端资源授权不会随会话迁移。

## 命令行

```powershell
# 只读诊断；不会创建或修改 WorkBuddy 数据库
uv run workbuddy-sync diagnose

# 一次接管；WorkBuddy 可以保持运行
uv run workbuddy-sync sync

# 开启所有普通会话的自动同步
uv run workbuddy-sync configure --auto-sync 'on' --scope 'all'
uv run workbuddy-sync watch

# 关闭自动同步（运行中的 watch 下次轮询会读取设置）
uv run workbuddy-sync configure --auto-sync 'off'

# 选择会话；使用 diagnose 返回的真实 ID
uv run workbuddy-sync configure --scope 'selected' --session-id '实际会话ID'

# 修改数据位置
uv run workbuddy-sync configure --data-dir 'C:\Users\25821\.workbuddy-ai'
```

独立配置可用 `uv run workbuddy-sync --config '配置路径' diagnose`。
不要同时运行 GUI 和 watch：GUI 将自己的设置保存在内存中，CLI watch 每轮读取磁盘设置。
`switching`、`busy` 和 `logged_out` 表示本轮尚未接管，命令行单次同步的退出码为 2；
`watch` 会继续轮询。其他错误会报告并退出；GUI 遇到其他错误会暂停自动同步，修正后可再次开启。

## 数据与恢复

默认应用状态存放在项目的 `state/`：

```text
state/
  settings.json
  backups/
    操作时间-随机标识/
      before.db          SQLite 完整备份，包含已提交的 WAL 数据
      operation.json     数据库位置及每个会话修改前后的归属
```

每次实际修改前都通过 SQLite Backup API 备份数据库，再在单个事务中更新 `sessions.user_id`。
无变化时不重复备份。备份目录按操作创建，全部保留，不执行清理或文件删除。
消息文件不在此备份内：本工具完全不修改它们。若需要整机灾难恢复，应另行备份完整 WorkBuddy 数据目录及工作区。

界面「恢复归属」选择包含 `operation.json` 的具体备份目录。也可运行：

```powershell
uv run workbuddy-sync restore 'F:\Code\workbuddy-session-sync\state\backups\实际操作目录'
```

恢复会先关闭自动同步，备份当前数据库，再仅恢复该操作涉及的 `user_id`。
新消息、新标题和其他表不被覆盖；同一操作重复恢复无变化。
会话已被接管给其他账号时停止恢复，应先恢复较新的操作。
备份元数据在事务执行前保存，因此它表示操作计划；中断后是否写入以数据库实际归属为准。

## 本机适配依据

2026-09-16 对当前安装做过只读检查：

- WorkBuddy AI 5.5.2，主进程 `WorkBuddyAI.exe`。
- 数据库：`C:\Users\25821\.workbuddy-ai\workbuddy.db`。
- 登录文件：`%USERPROFILE%\AppData\Local\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop-ai.info`。
- 使用登录文件的 `account.uid`；出现同名 `.logged-out` 标记时视为退出登录。
- 为显示账号名称，会从当前登录文件和 WorkBuddy 自己留下的历史 `.info` 文件中仅读取
  `uid` 与 `nickname` 等显示字段；不读取或保存 token，也不将完整登录文件写入日志或备份。
- WorkBuddy 的会话列表按当前 `user_id` 过滤；本工具只改变所选会话的本地归属。
- WorkBuddy 在应用内切号时会重置活跃会话连接，使下一条消息使用新账号认证。
- WorkBuddy 保存会话时会保留数据库里已有的非空归属，不会用旧内存值覆盖本工具的新归属。
- WorkBuddy 的侧栏不监听外部 SQLite 写入；工具提供会话深链按钮绕过切号后的陈旧列表。
- 完整消息历史保存在本地 JSONL，工具不会复制、改写或删除这些消息文件。

本项目独立实现，没有复制执行第三方迁移脚本；`work/` 中的安装包片段仅用于本地适配调查，不发布。
当前只支持这套已观察到的 Windows 数据格式；必要列不存在时停止操作，不尝试改造数据库。

## 验证

```powershell
$env:PYTHONUTF8 = '1'
uv run pytest -q
uv run ruff check .
```

测试使用自行创建的模拟数据库和消息文件，不修改真实账号。
测试运行目录保留在 `work/test-runs/`，不自动删除。

真实验收还需要：用测试会话在 A 下写入独特信息，在 WorkBuddy 内切 B，接管后核对上下文并追加消息，
再切回 A 验证新增消息与上下文。进一步核对附件、工作区、工具调用历史能否继续使用。
自动化测试证明本地归属变更和文件保留行为，不等价于 WorkBuddy 真实服务端续聊成功。
