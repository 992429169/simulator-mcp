# Simulator MCP

通过 MCP 协议让 AI 操控 iOS 模拟器 —— 启动设备、截图、点击、输入文字、抓包、Mock 接口。

## 功能概览

| 分类 | 工具 | 说明 |
|------|------|------|
| 设备管理 | `list_devices` | 列出所有模拟器（状态、UDID、运行时） |
| | `boot_device` / `shutdown_device` | 启动 / 关闭模拟器 |
| | `install_app` | 安装 .app 包 |
| | `launch_app` | 启动 APP（支持 `proxy=true` 注入网络拦截） |
| | `open_url` | 打开 URL 或深链（自动选取已启动的模拟器） |
| 截图 | `take_screenshot` | 截图并以 base64 图片返回给 AI（支持视觉分析） |
| UI 交互 | `tap` | 点击指定 iOS 坐标（支持长按 `duration`） |
| | `swipe` | 从起点滑动到终点 |
| | `input_text` | 向当前焦点输入框输入文字 |
| | `press_button` | 按硬件键（`home` / `lock` / `siri`） |
| | `get_ui_hierarchy` | 获取 UI 无障碍树（JSON 格式） |
| | `tap_element` | 按文字匹配元素并点击其中心坐标 |
| 网络拦截 | `start_network_proxy` | 启动 mitmproxy 代理（默认 8080 端口） |
| | `stop_network_proxy` | 停止代理 |
| | `get_network_log` | 查看抓包记录（支持 URL、方法筛选） |
| | `add_mock_rule` | 添加 Mock 规则（URL 正则匹配） |
| | `remove_mock_rule` | 删除 Mock 规则 |

> 共 **18 个工具**，覆盖设备管理、UI 自动化、网络拦截三大场景。

## 前置依赖

| 依赖 | 用途 | 安装方式 |
|------|------|----------|
| Xcode | 提供 `xcrun simctl` 命令 | Mac App Store |
| idb-companion | fb-idb 的 native 守护进程（UI 自动化） | `brew install idb-companion` |
| Python >= 3.11 | 运行时 | `brew install python@3.11` 或更高版本 |
| uv（推荐） | 包管理 | `brew install uv` |

> 当前仓库内置的 `proxy-dylib/libproxy_inject.dylib` 是 `arm64` 预编译产物；Intel Mac 需要自行重新编译 `x86_64` 或通用版本。

## 安装

```bash
cd simulator-mcp

# 方式一：uv（推荐）
uv venv && uv pip install -e .

# 方式二：pip
python -m venv .venv && source .venv/bin/activate && pip install -e .
```

Python 依赖（自动安装）：
- `mcp >= 1.0` — MCP 协议 SDK（stdio JSON-RPC）
- `mitmproxy >= 10.0` — HTTP(S) 代理
- `fb-idb` — iOS 模拟器 UI 自动化

## 配置到 Claude Code

在 `~/.claude.json` 的 `mcpServers` 中添加：

```json
{
  "mcpServers": {
    "simulator": {
      "command": "/path/to/simulator-mcp/.venv/bin/simulator-mcp"
    }
  }
}
```

## 配置到 Claude Desktop

在 `~/Library/Application Support/Claude/claude_desktop_config.json` 中添加：

```json
{
  "mcpServers": {
    "simulator": {
      "command": "/path/to/simulator-mcp/.venv/bin/simulator-mcp"
    }
  }
}
```

> 将 `/path/to/simulator-mcp` 替换为实际的项目绝对路径。

## 使用示例

配置完成后，AI 可以直接操控模拟器：

```
用户: 打开模拟器，截图看看当前页面
AI:  调用 list_devices → boot_device → take_screenshot
     "当前页面显示的是 iOS 主屏幕..."

用户: 打开微博，点击注册按钮
AI:  调用 launch_app → take_screenshot → tap_element(text="注册")
     "已点击注册按钮，进入了注册页面"

用户: 抓包看看注册接口返回了什么
AI:  调用 start_network_proxy → launch_app(proxy=true) → get_network_log(url_pattern="register")
     "注册接口返回了 200，响应体是..."

用户: Mock 掉登录接口，返回自定义数据
AI:  调用 add_mock_rule(url_pattern="api/login", response_body='{"token":"fake"}')
     "已添加 Mock 规则 mock_1"
```

## 网络抓包说明

### 使用流程

1. **启动代理**：`start_network_proxy(port=8080)`
   - 自动在后台线程启动 mitmproxy
   - 如果当前已有 booted simulator，会先尝试安装 mitmproxy CA 证书

2. **regular 模式：带代理启动 APP**：`launch_app(udid="xxx", bundle_id="com.example.app", proxy=true)`
   - 通过 `DYLD_INSERT_LIBRARIES` 注入 `libproxy_inject.dylib`
   - Swizzle `NSURLSessionConfiguration`，将所有 HTTP(S) 流量转发到代理
   - 会再次对目标模拟器执行 CA 证书安装，避免“先开代理、后启动模拟器”时 HTTPS 抓包失效

3. **local 模式：只抓当前前台 APP，不重启**：
   - `start_network_proxy(mode="local", udid="xxx", capture_frontmost_app=true)`
   - 通过 mitmproxy 的 macOS local capture 只拦截当前前台 simulator app 的 host PID
   - 不需要 `launch_app(proxy=true)`，适合已经在运行的 app
   - 自动 reset 已有的外部 TCP 连接（通过 lldb shutdown），强制 App 重连经过代理
   - 后台 PID 监控：App 重启后自动检测新 PID 并重新绑定，无需手动重启代理

4. **查看/Mock 请求**：
   - `get_network_log(url_pattern="api/user")` — 按 URL 子串筛选
   - `add_mock_rule(url_pattern="api/login", response_body="...")` — URL 正则匹配

### 技术原理

**regular 模式**:
```
App 启动 → dylib 注入 → swizzle NSURLSessionConfiguration
  → 所有 NSURLSession 流量 → mitmproxy (127.0.0.1:8080)
    → MockEngine 检查规则 → 命中则返回假数据 / 未命中则转发真实服务器
    → NetworkLog 记录请求/响应（内存环形缓冲，最多 1000 条）
    → 同时落盘：
      - `/tmp/proxy_requests.log`：摘要日志
      - `/tmp/proxy_requests.jsonl`：结构化明细日志
      - `/tmp/proxy_request_bodies/`：超大 body 的分流文件
```

**local 模式**:
```
start_network_proxy(mode="local", capture_frontmost_app=true)
  → 检测前台 App PID + 关联进程 PID
  → mitmproxy local redirector 按 PID 拦截新建 TCP 连接
  → lldb shutdown() 已有外部连接 → App 自动重连经过代理
  → 后台 PID 监控线程（每 3 秒）：
      App 重启 → 检测旧 PID 消失 → 查找新 PID
      → options.update(mode=["local:新PID"]) 动态更新拦截规则
      → reset 新进程已有连接
```

## 项目结构

```
simulator-mcp/
├── pyproject.toml                 # 项目配置、依赖、入口定义
├── README.md                      # 本文件
├── ARCHITECTURE.md                # 技术架构详细文档
├── diagrams/                      # Mermaid 源文件 + PNG 架构图
├── proxy-dylib/
│   ├── proxy_inject.m             # DYLD 注入源码 (Objective-C, 95 行)
│   └── libproxy_inject.dylib      # 编译产物 (当前为 arm64)
└── src/simulator_mcp/
    ├── __init__.py                # 入口: main() → asyncio.run(server.main())
    ├── __main__.py                # python -m simulator_mcp 入口
    ├── server.py                  # MCP Server: 18 个工具注册 + call_tool 分发
    ├── simulator/
    │   ├── simctl.py              # xcrun simctl 异步封装 (设备管理 + 截图)
    │   └── idb_client.py          # fb-idb 异步封装 (UI 交互 + tap_element)
    ├── tools/
    │   ├── device.py              # 设备管理工具 (参数解析 + proxy 注入逻辑)
    │   ├── screenshot.py          # 截图工具 (PNG → base64)
    │   ├── ui.py                  # UI 交互工具 (参数解析 → idb_client)
    │   └── network.py             # 网络工具 (参数解析 → proxy_server)
    └── proxy/
        ├── proxy_server.py        # mitmproxy 生命周期 + ProxyAddon + CA 证书安装
        ├── network_log.py         # 请求日志: 内存环形缓冲 + 结构化落盘
        └── mock_engine.py         # Mock 规则引擎: 正则匹配, 首条命中
```

## 重新编译 dylib（可选）

如果修改了 `proxy_inject.m`：

```bash
cd proxy-dylib
clang -dynamiclib -framework Foundation \
  -arch arm64 -arch x86_64 \
  -o libproxy_inject.dylib \
  proxy_inject.m
```

## 架构文档

详细的技术架构设计见 [ARCHITECTURE.md](./ARCHITECTURE.md)。
