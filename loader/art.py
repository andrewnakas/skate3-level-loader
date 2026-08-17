"""Cover art for a map.

Almost no community pack ships artwork -- an exhaustive scan of every downloaded
pack turned up exactly one thumbnail -- and this machine cannot take screenshots
(the in-game capture is Windows-only and GNOME's D-Bus capture is blocked). So
the default is a generated card: a colour derived from the world id, so each map
looks consistent and distinct every time you see it.

Drop a file at art/<world-id>.png (or .jpg) to override any of them.

**Why this renders to a pixbuf instead of drawing in a GTK handler.** PyGObject
can only pass a cairo.Context into a `draw` signal if the `python3-gi-cairo`
package is installed; without it, draw handlers do not run at all -- they fail
with "Couldn't find foreign struct converter for 'cairo.Context'" and widgets
simply never paint. Rather than depend on a root-installed package, everything
here is drawn with *pure pycairo* (which needs no GTK integration) and handed to
GTK as a GdkPixbuf. Text is left to real GTK widgets, which do it better anyway.
"""

from __future__ import annotations

import colorsys
import hashlib
import io
import math
from pathlib import Path

import cairo
import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

from . import config  # noqa: E402

SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

# Rendered cards are pure functions of (world_id, size), so cache them.
_CACHE: dict[tuple[str, int, int], GdkPixbuf.Pixbuf] = {}


def art_path(world_id: str) -> Path | None:
    slug = world_id.lower().replace(" ", "-")
    for suffix in SUFFIXES:
        candidate = config.ART_DIR / f"{slug}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def hue_for(world_id: str) -> float:
    """The 0..1 hue a world's card uses. Shared with the in-game overlay so the
    loading screen and the library tile are visibly the same map."""
    return hashlib.sha1(world_id.encode("utf-8")).digest()[0] / 255.0


def palette(world_id: str) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """A stable two-tone palette for a world id."""
    hue = hue_for(world_id)
    # Keep lightness in a band that stays legible under white text.
    top = colorsys.hls_to_rgb(hue, 0.34, 0.55)
    bottom = colorsys.hls_to_rgb((hue + 0.08) % 1.0, 0.14, 0.62)
    return top, bottom


def _surface_to_pixbuf(surface: cairo.ImageSurface) -> GdkPixbuf.Pixbuf:
    """cairo -> GdkPixbuf without the gi cairo bridge, via an in-memory PNG."""
    buffer = io.BytesIO()
    surface.write_to_png(buffer)
    loader = GdkPixbuf.PixbufLoader.new_with_type("png")
    loader.write(buffer.getvalue())
    loader.close()
    return loader.get_pixbuf()


def _draw_generated(width: int, height: int, world_id: str) -> cairo.ImageSurface:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    context = cairo.Context(surface)

    top, bottom = palette(world_id)
    gradient = cairo.LinearGradient(0, 0, width * 0.35, height)
    gradient.add_color_stop_rgb(0.0, *top)
    gradient.add_color_stop_rgb(1.0, *bottom)
    context.set_source(gradient)
    context.rectangle(0, 0, width, height)
    context.fill()

    _stripes(context, width, height, world_id)
    _scrim(context, width, height)
    return surface


def _stripes(context, width, height, world_id: str) -> None:
    """A few angled bands, seeded by the id, so cards are distinguishable."""
    digest = hashlib.sha1(world_id.encode("utf-8")).digest()
    context.save()
    context.rectangle(0, 0, width, height)
    context.clip()
    angle = math.radians(18 + digest[1] % 24)
    context.translate(width / 2, height / 2)
    context.rotate(angle)
    span = max(width, height) * 1.6
    for index in range(4):
        offset = ((digest[2 + index] / 255.0) - 0.5) * span
        thickness = span * (0.02 + (digest[6 + index] % 40) / 900.0)
        context.set_source_rgba(1, 1, 1, 0.05 + (index % 2) * 0.03)
        context.rectangle(-span / 2, offset, span, thickness)
        context.fill()
    context.restore()


def _scrim(context, width, height) -> None:
    """Darken the lower half so overlaid text stays readable."""
    gradient = cairo.LinearGradient(0, height * 0.3, 0, height)
    gradient.add_color_stop_rgba(0.0, 0, 0, 0, 0.0)
    gradient.add_color_stop_rgba(1.0, 0, 0, 0, 0.72)
    context.set_source(gradient)
    context.rectangle(0, 0, width, height)
    context.fill()


def _load_user_art(world_id: str, width: int, height: int) -> GdkPixbuf.Pixbuf | None:
    path = art_path(world_id)
    if path is None:
        return None
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            str(path), width, height, preserve_aspect_ratio=False
        )
    except Exception:
        return None

    # Composite the same scrim over user art so text stays readable on it too.
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    context = cairo.Context(surface)
    stride = pixbuf.get_rowstride()
    data = pixbuf.get_pixels()
    fmt = cairo.FORMAT_ARGB32 if pixbuf.get_has_alpha() else cairo.FORMAT_RGB24
    try:
        source = cairo.ImageSurface.create_for_data(
            bytearray(_to_cairo_order(data, pixbuf)), fmt,
            pixbuf.get_width(), pixbuf.get_height(), stride,
        )
        context.set_source_surface(source, 0, 0)
        context.paint()
    except Exception:
        return pixbuf
    _scrim(context, width, height)
    return _surface_to_pixbuf(surface)


def _to_cairo_order(data: bytes, pixbuf: GdkPixbuf.Pixbuf) -> bytes:
    """GdkPixbuf is RGBA byte order; cairo wants native-endian ARGB32."""
    channels = pixbuf.get_n_channels()
    out = bytearray(data)
    for i in range(0, len(out) - channels + 1, channels):
        r, g, b = out[i], out[i + 1], out[i + 2]
        out[i], out[i + 1], out[i + 2] = b, g, r
    return bytes(out)


def card_pixbuf(world_id: str, width: int, height: int) -> GdkPixbuf.Pixbuf:
    """The background image for a map, user-supplied or generated."""
    key = (world_id, width, height)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    pixbuf = _load_user_art(world_id, width, height)
    if pixbuf is None:
        pixbuf = _surface_to_pixbuf(_draw_generated(width, height, world_id))
    _CACHE[key] = pixbuf
    return pixbuf


def clear_cache() -> None:
    _CACHE.clear()
