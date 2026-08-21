# image-mcp

> 中文 | English

![GitHub stars](https://img.shields.io/github/stars/TOBYCAI/image-mcp?style=flat-square&color=facc15)
![Downloads](https://img.shields.io/github/downloads/TOBYCAI/image-mcp/total?style=flat-square&color=14b8a6)
![Downloads@latest](https://img.shields.io/github/downloads/TOBYCAI/image-mcp/latest/total?style=flat-square&color=14b8a6)
![License](https://img.shields.io/badge/license-MIT-3b82f6?style=flat-square)
![MCP](https://img.shields.io/badge/MCP-stdio-4d6bfe?style=flat-square)

A local, **Pillow-based** image-processing MCP server (offline, no API key). Via MCP it exposes a set of image tools to DeepSeek Harness (or any stdio-MCP client): `info / resize / crop / convert / compress / rotate / flip / thumbnail / watermark / effects / placeholder / overlay` — **12** tools, model-visible as `mcp__images__*`.

## Features

- **Offline & zero-cost**: local Pillow processing only, no online API or key required.
- **12 image tools**: info / resize / crop / convert / compress / rotate / flip / thumbnail / watermark / effects / placeholder / overlay.
- **Standard protocol**: JSON-RPC 2.0, newline-delimited stdio MCP; all logs go to stderr (stdout is reserved for the protocol).
- **Easy wiring**: one config entry via `@deepseek-ai/dsh-mcp-client` in a DSH profile.

## Install & wire in (DSH)

```bash
pip install -r requirements.txt   # or: pip install Pillow
```

In a DSH profile patch (e.g. `cordis.patch.yml`):

```yaml
- id: mcp-images
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: images
    transport: stdio
    command: python3
    args: ['<PATH-TO>/image_mcp.py']
```

Restart DSH and the agent can call the `mcp__images__*` tools.

## Run standalone (no DSH)

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | python3 image_mcp.py
# returns the schema of the 12 tools
```

## Tools

| Tool | Description |
|---|---|
| `info` | image format / dimensions / mode / size |
| `resize` | scale by width/height or ratio (contain / cover / fill / stretch) |
| `crop` | crop a rectangle region |
| `convert` | convert format (PNG/JPEG/WEBP/GIF/BMP/TIFF) |
| `compress` | re-encode with a quality setting |
| `rotate` | rotate (optionally expand canvas) |
| `flip` | mirror horizontally / vertically |
| `thumbnail` | thumbnail with longest side at most `size` |
| `watermark` | text watermark (position/color/opacity/size) |
| `effects` | greyscale / sepia / invert / blur / sharpen / contrast / brightness |
| `placeholder` | solid-color placeholder (optionally with text) |
| `overlay` | composite one image over another (with alpha) |

## Protocol

- Transport: stdio, one JSON object per line.
- Version: JSON-RPC 2.0, Protocol `2025-03-26`.
- `SERVER_NAME=dsh-image-mcp`, `SERVER_VERSION=0.1.0`.

## License

[MIT](./LICENSE) © TOBYCAI
