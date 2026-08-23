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
     "description": "Read-only metadata inspector. Returns the image's absolute path, format, width, height, color mode, and file size in bytes. Use it first to learn an image's dimensions or mode before resizing, cropping, or converting. Never writes to or modifies the source file.",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string", "description": "Absolute or relative path to the source image to inspect."}},
                     "required": ["path"]}},
    {"name": "resize",
     "description": "Resize an image and write the result to a NEW file (the source is never overwritten). Provide width and/or height; if only one is given the other is computed to preserve aspect ratio. 'fit' controls how the image maps into the target box: contain fits inside (no crop), cover fills the box and crops the overflow, fill/stretch forces the exact width x height (aspect ratio may change). Returns the output path and resulting dimensions.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "path": {"type": "string", "description": "Source image path."},
                         "out": {"type": "string", "description": "Output image path. A new file is created; the source is left untouched."},
                         "width": {"type": "integer", "description": "Target width in pixels. Omit to derive from height and preserve aspect ratio."},
                         "height": {"type": "integer", "description": "Target height in pixels. Omit to derive from width and preserve aspect ratio."},
                         "fit": {"type": "string", "enum": ["contain", "cover", "fill", "stretch"], "description": "Mapping mode: contain (fit inside, keep ratio), cover (fill box, crop overflow), fill/stretch (exact size, ratio may change). Default contain."}},
                     "required": ["path", "out"]}},
    {"name": "crop",
     "description": "Extract a rectangular sub-region and write it to a NEW file (source unchanged). The region is defined by its top-left corner (x, y) and size (width, height); coordinates are automatically clamped to the image bounds, so partial/out-of-bounds boxes are safe. Returns the output path and the actual box used.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "path": {"type": "string", "description": "Source image path."},
                         "out": {"type": "string", "description": "Output image path. A new file is created; the source is left untouched."},
                         "x": {"type": "integer", "description": "Left edge of the crop region, in pixels (clamped to image bounds)."},
                         "y": {"type": "integer", "description": "Top edge of the crop region, in pixels (clamped to image bounds)."},
                         "width": {"type": "integer", "description": "Width of the crop region, in pixels."},
                         "height": {"type": "integer", "description": "Height of the crop region, in pixels."}},
                     "required": ["path", "out", "x", "y", "width", "height"]}},
    {"name": "convert",
     "description": "Change an image's file format and write it to a NEW file (source unchanged). The target format is inferred from the output file extension (e.g. .webp) unless 'format' is given explicitly; supported targets include PNG, JPEG, WEBP, GIF, BMP, TIFF. When saving to a format without transparency (e.g. JPEG), any alpha channel is flattened onto a white background. Returns the output path and final format.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "path": {"type": "string", "description": "Source image path."},
                         "out": {"type": "string", "description": "Output image path. A new file is created; the source is left untouched."},
                         "format": {"type": "string", "description": "Optional explicit format (PNG/JPEG/WEBP/GIF/BMP/TIFF). If omitted, it is inferred from the output extension."}},
                     "required": ["path", "out"]}},
    {"name": "compress",
     "description": "Re-encode an image at a chosen quality to reduce file size, writing the result to a NEW file (source unchanged). Quality is 1-100 (default 80); lower values produce smaller files. The output format defaults to JPEG but can be set via 'format'. Returns the output path plus bytes_before, bytes_after, and saved_percent so you can see how much space was recovered.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "path": {"type": "string", "description": "Source image path."},
                         "out": {"type": "string", "description": "Output image path. A new file is created; the source is left untouched."},
                         "quality": {"type": "integer", "description": "Quality 1-100 (higher = larger, better). Default 80."},
                         "format": {"type": "string", "description": "Optional output format (defaults to JPEG). Use PNG/WEBP to keep transparency."}},
                     "required": ["path", "out"]}},
    {"name": "rotate",
     "description": "Rotate an image clockwise by a number of degrees and write it to a NEW file (source unchanged). With expand=true (the default) the canvas is enlarged so no corners are clipped; set expand=false to keep the original canvas size and crop the overflow. Returns the output path and the angle applied.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "path": {"type": "string", "description": "Source image path."},
                         "out": {"type": "string", "description": "Output image path. A new file is created; the source is left untouched."},
                         "degrees": {"type": "number", "description": "Rotation angle in degrees, clockwise."},
                         "expand": {"type": "boolean", "description": "If true (default), grow the canvas to fit the rotated image; if false, keep original size and crop overflow."}},
                     "required": ["path", "out", "degrees"]}},
    {"name": "flip",
     "description": "Mirror an image and write it to a NEW file (source unchanged). 'axis' selects horizontal (left-right mirror) or vertical (top-bottom flip). Returns the output path and the axis used.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "path": {"type": "string", "description": "Source image path."},
                         "out": {"type": "string", "description": "Output image path. A new file is created; the source is left untouched."},
                         "axis": {"type": "string", "enum": ["horizontal", "vertical"], "description": "Mirror direction: horizontal (left-right) or vertical (top-bottom). Default horizontal."}},
                     "required": ["path", "out", "axis"]}},
    {"name": "thumbnail",
     "description": "Create a downscaled preview whose longest side is at most 'size' pixels (aspect ratio preserved), writing it to a NEW file (source unchanged). Ideal for generating small previews or contact sheets. Returns the output path and the max-side constraint applied.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "path": {"type": "string", "description": "Source image path."},
                         "out": {"type": "string", "description": "Output image path. A new file is created; the source is left untouched."},
                         "size": {"type": "integer", "description": "Maximum length, in pixels, of the longest side of the thumbnail."}},
                     "required": ["path", "out", "size"]}},
    {"name": "watermark",
     "description": "Burn a text label onto an image and write it to a NEW file (source unchanged). Configure placement (position), color (hex, e.g. #ffffff), opacity (0-1, default 0.6), font size, and padding. The label is drawn with an alpha channel so it blends over the image. Returns the output path and the effective position.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "path": {"type": "string", "description": "Source image path."},
                         "out": {"type": "string", "description": "Output image path. A new file is created; the source is left untouched."},
                         "text": {"type": "string", "description": "The watermark text to draw."},
                         "position": {"type": "string", "description": "Anchor: top-left, top-right, bottom-left, bottom-right, or center. Default bottom-right."},
                         "font_size": {"type": "integer", "description": "Font size in pixels (auto-sized if omitted)."},
                         "opacity": {"type": "number", "description": "Text opacity 0-1 (default 0.6)."},
                         "color": {"type": "string", "description": "Text color as hex, e.g. #ffffff (default white)."},
                         "padding": {"type": "integer", "description": "Margin in pixels between the text and the chosen edge (default 16)."}},
                     "required": ["path", "out", "text"]}},
    {"name": "effects",
     "description": "Apply a single visual effect and write the result to a NEW file (source unchanged). Effects: greyscale, sepia, invert, blur (uses 'radius'), sharpen, contrast (uses 'factor'), brightness (uses 'factor'). Returns the output path and the effect applied. Unknown effect names raise an error.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "path": {"type": "string", "description": "Source image path."},
                         "out": {"type": "string", "description": "Output image path. A new file is created; the source is left untouched."},
                         "effect": {"type": "string", "description": "One of: greyscale, sepia, invert, blur, sharpen, contrast, brightness."},
                         "radius": {"type": "number", "description": "Blur radius in pixels (used only by the blur effect; default 2)."},
                         "factor": {"type": "number", "description": "Intensity multiplier for contrast/brightness (e.g. 1.3 = brighter, 0.8 = darker; defaults apply if omitted)."}},
                     "required": ["path", "out", "effect"]}},
    {"name": "placeholder",
     "description": "Generate a brand-new solid-color image (optionally with a centered label) and write it to 'out'. Useful for mockups, spacer images, or test fixtures. Provide width, height, background color, and optional text with its color and font size. Returns the output path and the generated dimensions.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "out": {"type": "string", "description": "Output image path. A new image file is created."},
                         "width": {"type": "integer", "description": "Image width in pixels."},
                         "height": {"type": "integer", "description": "Image height in pixels."},
                         "color": {"type": "string", "description": "Background color as hex, e.g. #e5e7eb (default light grey)."},
                         "text": {"type": "string", "description": "Optional centered label text."},
                         "text_color": {"type": "string", "description": "Label color as hex, e.g. #6b7280 (default grey)."},
                         "font_size": {"type": "integer", "description": "Label font size in pixels (auto-sized if omitted)."}},
                     "required": ["out", "width", "height"]}},
    {"name": "overlay",
     "description": "Composite one image on top of another and write the result to a NEW file (sources unchanged). The 'overlay' image is placed at offset (x, y) on the 'base' image; its alpha channel is respected, and an 'opacity' of 0-1 lets you fade it in. Use it for logos, badges, or layering. Returns the output path and the offset used.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "base": {"type": "string", "description": "Path to the bottom (base) image."},
                         "overlay": {"type": "string", "description": "Path to the top image to composite, whose alpha channel is honored."},
                         "out": {"type": "string", "description": "Output image path. A new file is created; both sources are left untouched."},
                         "x": {"type": "integer", "description": "Horizontal offset of the overlay's top-left corner on the base (default 0)."},
                         "y": {"type": "integer", "description": "Vertical offset of the overlay's top-left corner on the base (default 0)."},
                         "opacity": {"type": "number", "description": "Opacity of the overlay, 0-1 (default 1.0 = fully opaque)."}},
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
