#!/usr/bin/env python3
"""DSH local image-processing MCP server (Pillow-based, offline, no API key).

Stdio MCP server exposing image tools for DeepSeek Harness:
  info / resize / crop / convert / compress / rotate / flip / thumbnail /
  watermark / effects / placeholder / overlay

Wire it into DSH via @deepseek-ai/dsh-mcp-client in the profile patch:
    - id: mcp-images
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: images
        transport: stdio
        command: python3
        args: ['<PATH-TO>/image_mcp.py']

Model-visible tool names: mcp__images__<tool>.
JSON-RPC 2.0, newline-delimited, one JSON object per line on stdin/stdout.
All logs go to stderr (stdout is reserved for the protocol).
"""

import json
import os
import sys
import traceback

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

SERVER_NAME = "dsh-image-mcp"
SERVER_VERSION = "0.1.0"
DEFAULT_PROTOCOL = "2025-03-26"


def log(*args):
    print("[image-mcp]", *args, file=sys.stderr, flush=True)


def respond(msg_id, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.buffer.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def open_image(path):
    p = os.path.abspath(path)
    if not os.path.isfile(p):
        raise FileNotFoundError("file not found: %s" % p)
    return p, Image.open(p)


def ensure_out(out):
    o = os.path.abspath(out)
    d = os.path.dirname(o)
    if d:
        os.makedirs(d, exist_ok=True)
    return o


def info_of(img, path):
    return {
        "path": os.path.abspath(path),
        "format": (img.format or "unknown"),
        "width": img.width,
        "height": img.height,
        "mode": img.mode,
        "size_bytes": os.path.getsize(path) if os.path.isfile(path) else None,
    }


def save(img, out, fmt=None, quality=None, **kw):
    o = ensure_out(out)
    f = (fmt or img.format or os.path.splitext(o)[1].lstrip(".") or "PNG").upper()
    if f == "JPG":
        f = "JPEG"
    save_kw = {}
    if f == "JPEG":
        save_kw["quality"] = quality if quality is not None else 85
    elif f == "WEBP":
        save_kw["quality"] = quality if quality is not None else 85
    elif f == "PNG":
        save_kw["optimize"] = True
    img.save(o, format=f, **save_kw)
    return o


def flatten_alpha(img, fmt):
    """Composite alpha onto a white background when saving to a format without alpha."""
    if fmt in ("JPEG", "JPG") and img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    return img


def parse_color(spec, fallback):
    try:
        s = str(spec).lstrip("#")
        if len(s) == 6:
            return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        pass
    return fallback


def load_font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # older Pillow
        return ImageFont.load_default()


# --------------------------------------------------------------------------
# tool handlers
# --------------------------------------------------------------------------

def h_info(a):
    p, img = open_image(a["path"])
    with img:
        return info_of(img, p)


def h_resize(a):
    p, img = open_image(a["path"])
    w = a.get("width")
    h = a.get("height")
    fit = (a.get("fit") or "contain").lower()
    if not w and not h:
        raise ValueError("width or height required (at least one)")
    with img:
        iw, ih = img.size
        if w and h:
            if fit == "contain":
                s = min(w / iw, h / ih)
                img = img.resize((max(1, round(iw * s)), max(1, round(ih * s))), Image.LANCZOS)
            elif fit == "cover":
                s = max(w / iw, h / ih)
                nw, nh = round(iw * s), round(ih * s)
                img = img.resize((nw, nh), Image.LANCZOS)
                l, t = (nw - w) // 2, (nh - h) // 2
                img = img.crop((l, t, l + w, t + h))
            else:  # fill / stretch
                img = img.resize((w, h), Image.LANCZOS)
        else:
            nw = w or max(1, round(iw * h / ih))
            nh = h or max(1, round(ih * w / iw))
            img = img.resize((nw, nh), Image.LANCZOS)
        out = save(img, a["out"])
        return {"out": out, "fit": fit, **info_of(img, out)}


def h_crop(a):
    p, img = open_image(a["path"])
    x, y = int(a["x"]), int(a["y"])
    w, h = int(a["width"]), int(a["height"])
    with img:
        box = (max(0, x), max(0, y), min(img.width, x + w), min(img.height, y + h))
        img2 = img.crop(box)
        out = save(img2, a["out"])
        return {"out": out, "box": list(box), **info_of(img2, out)}


def h_convert(a):
    p, img = open_image(a["path"])
    fmt = (a.get("format") or os.path.splitext(a["out"])[1].lstrip(".") or "PNG").upper()
    with img:
        img = flatten_alpha(img, fmt)
        out = save(img, a["out"], fmt=fmt)
        return {"out": out, "format": fmt, **info_of(img, out)}


def h_compress(a):
    p, img = open_image(a["path"])
    q = max(1, min(100, int(a.get("quality") or 80)))
    fmt = (a.get("format") or img.format or "JPEG").upper()
    before = os.path.getsize(p)
    with img:
        img = flatten_alpha(img, fmt)
        out = save(img, a["out"], fmt=fmt, quality=q)
        after = os.path.getsize(out)
        return {
            "out": out,
            "format": fmt,
            "quality": q,
            "bytes_before": before,
            "bytes_after": after,
            "saved_percent": round(100 * (1 - after / before), 1) if before else None,
            **info_of(img, out),
        }


def h_rotate(a):
    p, img = open_image(a["path"])
    deg = float(a.get("degrees") or 0)
    expand = bool(a.get("expand", True))
    with img:
        img = img.rotate(deg, expand=expand, resample=Image.BICUBIC)
        out = save(img, a["out"])
        return {"out": out, "degrees": deg, **info_of(img, out)}


def h_flip(a):
    p, img = open_image(a["path"])
    axis = (a.get("axis") or "horizontal").lower()
    if axis not in ("horizontal", "vertical"):
        raise ValueError("axis must be horizontal or vertical")
    with img:
        img = ImageOps.mirror(img) if axis == "horizontal" else ImageOps.flip(img)
        out = save(img, a["out"])
        return {"out": out, "axis": axis, **info_of(img, out)}


def h_thumbnail(a):
    p, img = open_image(a["path"])
    size = int(a["size"])
    with img:
        img.thumbnail((size, size), Image.LANCZOS)
        out = save(img, a["out"])
        return {"out": out, "max_size": size, **info_of(img, out)}


def h_watermark(a):
    p, img = open_image(a["path"])
    text = str(a["text"])
    with img:
        base = img.convert("RGBA")
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        font_size = int(a.get("font_size") or max(16, base.height // 40))
        font = load_font(font_size)
        bbox = d.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad = int(a.get("padding") or 16)
        pos = (a.get("position") or "bottom-right").lower()
        W, H = base.size
        x = {"top-left": pad, "top-right": W - tw - pad,
             "bottom-left": pad, "bottom-right": W - tw - pad,
             "center": (W - tw) // 2}.get(pos, W - tw - pad)
        y = {"top-left": pad, "top-right": pad,
             "bottom-left": H - th - pad, "bottom-right": H - th - pad,
             "center": (H - th) // 2}.get(pos, H - th - pad)
        opacity = max(0.0, min(1.0, float(a.get("opacity") or 0.6)))
        rgb = parse_color(a.get("color"), (255, 255, 255))
        fill = tuple(rgb) + (int(255 * opacity),)
        d.text((x, y), text, font=font, fill=fill)
        out_img = Image.alpha_composite(base, overlay)
        out_img = out_img.convert("RGB") if img.mode in ("P", "1") else out_img
        out = save(out_img, a["out"])
        return {"out": out, "position": pos, "font_size": font_size, **info_of(out_img, out)}


def h_effects(a):
    p, img = open_image(a["path"])
    fx = (a.get("effect") or "").lower()
    with img:
        if fx in ("greyscale", "grayscale"):
            img = ImageOps.grayscale(img).convert("RGB")
        elif fx == "sepia":
            g = ImageOps.grayscale(img)
            img = Image.merge("RGB", [g.point(lambda v: min(255, int(v * 1.07))),
                                      g.point(lambda v: int(v * 0.74)),
                                      g.point(lambda v: int(v * 0.43))])
        elif fx == "invert":
            img = ImageOps.invert(img.convert("RGB"))
        elif fx == "blur":
            img = img.filter(ImageFilter.GaussianBlur(float(a.get("radius") or 2)))
        elif fx == "sharpen":
            img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150))
        elif fx == "contrast":
            img = ImageEnhance.Contrast(img).enhance(float(a.get("factor") or 1.5))
        elif fx == "brightness":
            img = ImageEnhance.Brightness(img).enhance(float(a.get("factor") or 1.3))
        else:
            raise ValueError("unknown effect: %s (use greyscale/sepia/invert/blur/sharpen/contrast/brightness)" % fx)
        out = save(img, a["out"])
        return {"out": out, "effect": fx, **info_of(img, out)}


def h_placeholder(a):
    w, h = int(a["width"]), int(a["height"])
    bg = parse_color(a.get("color"), (229, 231, 235)) + (255,)
    img = Image.new("RGBA", (w, h), bg)
    text = a.get("text")
    if text:
        d = ImageDraw.Draw(img)
        fs = int(a.get("font_size") or max(12, min(w, h) // 8))
        font = load_font(fs)
        bbox = d.textbbox((0, 0), str(text), font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tc = parse_color(a.get("text_color"), (107, 114, 128)) + (255,)
        d.text(((w - tw) // 2, (h - th) // 2), str(text), font=font, fill=tc)
    out = save(img, a["out"], fmt="PNG")
    return {"out": out, **info_of(img, out)}


def h_overlay(a):
    bp, base = open_image(a["base"])
    op_, ov = open_image(a["overlay"])
    with base, ov:
        base = base.convert("RGBA")
        ov = ov.convert("RGBA")
        x, y = int(a.get("x") or 0), int(a.get("y") or 0)
        opacity = max(0.0, min(1.0, float(a.get("opacity") or 1.0)))
        if opacity < 1.0:
            ov.putalpha(ov.split()[-1].point(lambda v: int(v * opacity)))
        base.alpha_composite(ov, (x, y))
        out = save(base, a["out"])
        return {"out": out, "offset": [x, y], **info_of(base, out)}


HANDLERS = {
    "info": h_info,
    "resize": h_resize,
    "crop": h_crop,
    "convert": h_convert,
    "compress": h_compress,
    "rotate": h_rotate,
    "flip": h_flip,
    "thumbnail": h_thumbnail,
    "watermark": h_watermark,
    "effects": h_effects,
    "placeholder": h_placeholder,
    "overlay": h_overlay,
}

STR = {"type": "string"}
INT = {"type": "integer"}
NUM = {"type": "number"}
BOOL = {"type": "boolean"}

TOOLS = [
    {"name": "info",
     "description": "Inspect an image: format, dimensions, color mode, file size.",
     "inputSchema": {"type": "object", "properties": {"path": STR}, "required": ["path"]}},
    {"name": "resize",
     "description": "Resize an image. Provide width and/or height; when only one is given the other keeps aspect ratio. fit: contain (fit inside box, keep ratio), cover (fill box, crop overflow), fill (stretch to exact box).",
     "inputSchema": {"type": "object",
                     "properties": {"path": STR, "out": STR, "width": INT, "height": INT,
                                    "fit": {"type": "string", "enum": ["contain", "cover", "fill", "stretch"]}},
                     "required": ["path", "out"]}},
    {"name": "crop",
     "description": "Crop a rectangular region (x, y, width, height) from an image.",
     "inputSchema": {"type": "object",
                     "properties": {"path": STR, "out": STR, "x": INT, "y": INT, "width": INT, "height": INT},
                     "required": ["path", "out", "x", "y", "width", "height"]}},
    {"name": "convert",
     "description": "Convert an image to another format (PNG/JPEG/WEBP/GIF/BMP/TIFF). Format is inferred from the output extension unless 'format' is given. Alpha is flattened onto white when saving to JPEG.",
     "inputSchema": {"type": "object",
                     "properties": {"path": STR, "out": STR, "format": STR},
                     "required": ["path", "out"]}},
    {"name": "compress",
     "description": "Compress an image by re-encoding with a quality setting (1-100, default 80). Reports bytes saved.",
     "inputSchema": {"type": "object",
                     "properties": {"path": STR, "out": STR, "quality": INT, "format": STR},
                     "required": ["path", "out"]}},
    {"name": "rotate",
     "description": "Rotate an image by degrees (clockwise). expand=true enlarges the canvas to fit the rotated image.",
     "inputSchema": {"type": "object",
                     "properties": {"path": STR, "out": STR, "degrees": NUM, "expand": BOOL},
                     "required": ["path", "out", "degrees"]}},
    {"name": "flip",
     "description": "Mirror (horizontal) or flip (vertical) an image.",
     "inputSchema": {"type": "object",
                     "properties": {"path": STR, "out": STR,
                                    "axis": {"type": "string", "enum": ["horizontal", "vertical"]}},
                     "required": ["path", "out", "axis"]}},
    {"name": "thumbnail",
     "description": "Create a thumbnail whose longest side is at most 'size' pixels, keeping aspect ratio.",
     "inputSchema": {"type": "object",
                     "properties": {"path": STR, "out": STR, "size": INT},
                     "required": ["path", "out", "size"]}},
    {"name": "watermark",
     "description": "Draw a text watermark on an image. position: top-left/top-right/bottom-left/bottom-right/center. color: hex like '#ffffff'. opacity: 0-1.",
     "inputSchema": {"type": "object",
                     "properties": {"path": STR, "out": STR, "text": STR,
                                    "position": STR, "font_size": INT, "opacity": NUM,
                                    "color": STR, "padding": INT},
                     "required": ["path", "out", "text"]}},
    {"name": "effects",
     "description": "Apply a visual effect: greyscale, sepia, invert, blur (radius), sharpen, contrast (factor), brightness (factor).",
     "inputSchema": {"type": "object",
                     "properties": {"path": STR, "out": STR, "effect": STR, "radius": NUM, "factor": NUM},
                     "required": ["path", "out", "effect"]}},
    {"name": "placeholder",
     "description": "Create a new placeholder image: solid color with optional centered label text.",
     "inputSchema": {"type": "object",
                     "properties": {"out": STR, "width": INT, "height": INT, "color": STR,
                                    "text": STR, "text_color": STR, "font_size": INT},
                     "required": ["out", "width", "height"]}},
    {"name": "overlay",
     "description": "Composite one image on top of another at offset (x, y), honoring the overlay's alpha channel; opacity 0-1.",
     "inputSchema": {"type": "object",
                     "properties": {"base": STR, "overlay": STR, "out": STR, "x": INT, "y": INT, "opacity": NUM},
                     "required": ["base", "overlay", "out"]}},
]


# --------------------------------------------------------------------------
# main loop
# --------------------------------------------------------------------------

def main():
    log("started", SERVER_VERSION, "cwd=%s" % os.getcwd())
    stdin = sys.stdin.buffer
    for raw in stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw.decode("utf-8"))
        except Exception as e:
            log("bad json:", e)
            continue
        method = msg.get("method")
        mid = msg.get("id")
        try:
            if method == "initialize":
                p = msg.get("params") or {}
                respond(mid, {
                    "protocolVersion": p.get("protocolVersion") or DEFAULT_PROTOCOL,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                })
            elif method == "ping":
                respond(mid, {})
            elif method == "tools/list":
                respond(mid, {"tools": TOOLS})
            elif method == "tools/call":
                params = msg.get("params") or {}
                name = params.get("name")
                args = params.get("arguments") or {}
                if name not in HANDLERS:
                    raise ValueError("unknown tool: %s" % name)
                result = HANDLERS[name](args)
                respond(mid, {"content": [{"type": "text",
                                           "text": json.dumps(result, ensure_ascii=False, indent=2)}]})
            elif method and method.startswith("notifications/"):
                continue  # no response to notifications
            else:
                if mid is not None:
                    respond(mid, error={"code": -32601, "message": "method not found: %s" % method})
        except Exception as e:
            log(traceback.format_exc())
            if mid is not None:
                respond(mid, {"content": [{"type": "text",
                                           "text": "ERROR: %s: %s" % (type(e).__name__, e)}],
                              "isError": True})
    log("eof, exiting")


if __name__ == "__main__":
    main()
