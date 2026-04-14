# Simulator MCP 安装指南

通过 MCP 协议让 AI（Claude Code / Claude Desktop）操控 iOS 模拟器——截图、点击、输入、抓包、Mock 接口。

## 包内容

```
simulator-mcp.zip
├── pyproject.toml                  # Python 项目配置
├── README.md                       # 项目说明
├── ARCHITECTURE.md                 # 技术架构文档
├── diagrams/                       # 架构图（Mermaid 源文件 + PNG）
├── proxy-dylib/
│   ├── proxy_inject.m              # DYLD 注入源码（Objective-C）
│   └── libproxy_inject.dylib       # 预编译产物（arm64）
└── src/simulator_mcp/              # Python 源码（MCP Server）
```

## 系统要求

| 依赖 | 用途 | 安装方式 |
|------|------|----------|
| macOS | 必须，iOS 模拟器仅在 macOS 运行 | — |
| Xcode | 提供 `xcrun simctl` 命令 | Mac App Store |
| idb-companion | UI 自动化（点击、滑动、输入、获取 UI 树） | `brew install idb-companion` |
| Python >= 3.11 | 运行时 | `brew install python@3.11` 或更高版本 |
| uv（推荐） | Python 包管理 | `brew install uv` |

> 当前预编译的 `libproxy_inject.dylib` 为 `arm64`；Intel Mac 需要自行重新编译 `x86_64` 或通用版本。

## 安装步骤

### 1. 解压并安装 Python 依赖

```bash
# 解压到目标目录
unzip simulator-mcp.zip -d ~/simulator-mcp
cd ~/simulator-mcp

# 方式一：uv（推荐，速度快）
uv venv && uv pip install -e .

# 方式二：pip
python3 -m venv .venv && source .venv/bin/activate && pip install -e .
```

Python 依赖会自动安装：
- `mcp >= 1.0` — MCP 协议 SDK
- `mitmproxy >= 10.0` — HTTP(S) 代理（抓包 + Mock）
- `fb-idb` — iOS 模拟器 UI 自动化

### 2. 安装 idb-companion

```bash
brew install idb-companion
```

验证安装：
```bash
which idb_companion
# 应输出 /opt/homebrew/bin/idb_companion 或类似路径
```

### 3. 配置到 Claude Code

在 `~/.claude.json` 的 `mcpServers` 中添加（如果文件不存在则创建）：

```json
{
  "mcpServers": {
    "simulator": {
      "type": "stdio",
      "command": "INSTALL_PATH/.venv/bin/python",
      "args": ["-m", "simulator_mcp"]
    }
  }
}
```

> 将 `INSTALL_PATH` 替换为实际解压路径，如 `/Users/yourname/simulator-mcp`。

### 4. 配置到 Claude Desktop（可选）

编辑 `~/Library/Application Support/Claude/claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "simulator": {
      "type": "stdio",
      "command": "INSTALL_PATH/.venv/bin/python",
      "args": ["-m", "simulator_mcp"]
    }
  }
}
```

### 5. 验证安装

重启 Claude Code 后，输入 `/mcp` 查看 MCP 服务器状态，确认 `simulator` 显示为已连接。

然后试试：
```
你: 列出所有模拟器
AI: 调用 list_devices → 返回设备列表
```

## 18 个工具一览

| 分类 | 工具 | 说明 |
|------|------|------|
| 设备管理 | `list_devices` | 列出所有模拟器 |
| | `boot_device` / `shutdown_device` | 启动/关闭模拟器 |
| | `install_app` | 安装 .app 包 |
| | `launch_app` | 启动 APP（`proxy=true` 注入网络拦截） |
| | `open_url` | 打开 URL / 深链 |
| 截图 | `take_screenshot` | 截图返回 base64 图片给 AI |
| UI 交互 | `tap` / `swipe` | 坐标点击 / 滑动 |
| | `input_text` | 输入文字 |
| | `press_button` | 按硬件键（home/lock/siri） |
| | `get_ui_hierarchy` | 获取 UI 无障碍树（JSON） |
| | `tap_element` | 按文字匹配元素并点击 |
| 网络拦截 | `start_network_proxy` / `stop_network_proxy` | 启动/停止 mitmproxy |
| | `get_network_log` | 查看抓包记录 |
| | `add_mock_rule` / `remove_mock_rule` | 管理 Mock 规则 |

## 网络抓包使用流程

1. `start_network_proxy()` — 启动代理；如果当前已有 booted simulator，会尝试安装 CA 证书
2. `launch_app(udid, bundle_id, proxy=true)` — 带代理启动 APP，并对目标模拟器再次执行 CA 证书安装
3. `get_network_log(url_pattern="api/xxx")` — 查看请求
4. `add_mock_rule(url_pattern="api/login", response_body='{"token":"fake"}')` — Mock 接口

> 当前仅支持 regular 抓包流程：先 `start_network_proxy()`，再通过 `launch_app(..., proxy=true)` 启动目标 APP。
> 抓包原理：通过 DYLD_INSERT_LIBRARIES 注入 dylib，在 APP 启动前 swizzle NSURLSessionConfiguration，将所有 HTTP(S) 流量转发到 mitmproxy。

## 重新编译 dylib（可选）

如果修改了 `proxy-dylib/proxy_inject.m`：

```bash
cd proxy-dylib
clang -dynamiclib -framework Foundation \
  -arch arm64 -arch x86_64 \
  -o libproxy_inject.dylib \
  proxy_inject.m
```

## 常见问题

| 问题 | 解决方案 |
|------|---------|
| `idb_companion` 未找到 | `brew install idb-companion` |
| 代理启动后 APP 报错/白屏 | 停代理 → 杀 APP → 不带 proxy 重新 launch |
| HTTPS 抓不到包 | 确认 CA 证书已安装（start_network_proxy 会自动安装） |
| `take_screenshot` 超时 | 确认模拟器已 boot，`xcrun simctl list devices` 检查状态 |
| MCP 连接失败 | 检查 `~/.claude.json` 中路径是否正确，Python 路径是否指向 .venv |
