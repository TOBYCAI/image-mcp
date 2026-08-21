# image-mcp

> 中文 | [English](./README.en.md)

![GitHub stars](https://img.shields.io/github/stars/TOBYCAI/image-mcp?style=flat-square&color=facc15)
![Downloads](https://img.shields.io/github/downloads/TOBYCAI/image-mcp/total?style=flat-square&color=14b8a6)
![Downloads@latest](https://img.shields.io/github/downloads/TOBYCAI/image-mcp/latest/total?style=flat-square&color=14b8a6)
![License](https://img.shields.io/badge/license-MIT-3b82f6?style=flat-square)
![MCP](https://img.shields.io/badge/MCP-stdio-4d6bfe?style=flat-square)

基于 **Pillow** 的本地图片处理 MCP Server（离线、无需 API key）。通过 MCP 暴露一组图片工具给 DeepSeek Harness（或其他支持 stdio MCP 的客户端）：`info / resize / crop / convert / compress / rotate / flip / thumbnail / watermark / effects / placeholder / overlay`，共 **12** 个工具，模型可见工具名为 `mcp__images__*`。

## 特性

- **离线零成本**：纯本地 Pillow 处理，不需要任何线上 API/密钥。
- **12 个图片工具**：信息 / 缩放 / 裁剪 / 转换 / 压缩 / 旋转 / 翻转 / 缩略图 / 水印 / 特效 / 占位图 / 叠加。
- **协议标准**：JSON-RPC 2.0、按行分隔的 stdio MCP；所有日志走 stderr（stdout 专用于协议）。
- **易于接入**：经 `@deepseek-ai/dsh-mcp-client` 一行配置即可挂进 DSH profile。

## 安装与接入（DSH）

```bash
pip install -r requirements.txt   # 或 pip install Pillow
```

在 DSH profile 的补丁（如 `cordis.patch.yml`）里加入：

```yaml
- id: mcp-images
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: images
    transport: stdio
    command: python3
    args: ['<PATH-TO>/image_mcp.py']
```

重启 DSH 后，agent 即可调用 `mcp__images__*` 工具。

## 独立运行（无 DSH）

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | python3 image_mcp.py
# 返回 12 个工具的 schema
```

## 工具一览

| 工具 | 说明 |
|---|---|
| `info` | 查看图片格式 / 尺寸 / 模式 / 大小 |
| `resize` | 按宽高/比例缩放（contain / cover / fill / stretch） |
| `crop` | 裁剪矩形区域 |
| `convert` | 转换格式（PNG/JPEG/WEBP/GIF/BMP/TIFF） |
| `compress` | 按质量重新编码压缩 |
| `rotate` | 旋转（可扩展画布） |
| `flip` | 水平/垂直镜像 |
| `thumbnail` | 长边不超过指定尺寸的缩略图 |
| `watermark` | 文字水印（位置/颜色/透明度/字号） |
| `effects` | 特效：灰度/棕褐/反相/模糊/锐化/对比/亮度 |
| `placeholder` | 生成纯色占位图（可带文字） |
| `overlay` | 在底图上叠加另一张图（支持透明度） |

## 协议

- 传输：stdio，一行一个 JSON 对象。
- 版本：JSON-RPC 2.0，Protocol `2025-03-26`。
- `SERVER_NAME=dsh-image-mcp`，`SERVER_VERSION=0.1.0`。

## 适配系统

- **适配系统**：macOS / Windows / Linux（Python 3 + Pillow，跨平台）。
- Windows 注意：命令用 `python`（而非 `python3`）；`args` 里的路径用绝对路径或反斜杠。

## License

[MIT](./LICENSE) © TOBYCAI
