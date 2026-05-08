from __future__ import annotations

import gzip
import json
import math
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image
from rlottie_python import LottieAnimation


TILE_SIZE = 100
TELEGRAM_VIDEO_EMOJI_SAFE_BYTES = 240_000
TELEGRAM_TGS_EMOJI_MAX_BYTES = 64 * 1024
GRID_RE = re.compile(r"(?P<cols>\d{1,2})\s*[xх×]\s*(?P<rows>\d{1,2})", re.IGNORECASE)
TileProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True)
class Grid:
    cols: int
    rows: int

    @property
    def count(self) -> int:
        return self.cols * self.rows


@dataclass(frozen=True)
class StickerBatch:
    paths: list[Path]
    sticker_format: str
    grid: Grid
    source_width: int
    source_height: int
    padding: int


@dataclass(frozen=True)
class VideoProbe:
    width: int
    height: int
    duration: float


class MediaError(ValueError):
    pass


def parse_grid(text: str | None, max_tiles: int) -> Grid | None:
    if not text:
        return None
    match = GRID_RE.search(text)
    if not match:
        return None

    cols = int(match.group("cols"))
    rows = int(match.group("rows"))
    if cols < 1 or rows < 1:
        raise MediaError("Сетка должна быть больше нуля.")
    if cols * rows > max_tiles:
        raise MediaError(f"Слишком много плиток: максимум {max_tiles}.")
    return Grid(cols=cols, rows=rows)


def choose_grid(width: int, height: int, long_side: int, max_tiles: int) -> Grid:
    if width <= 0 or height <= 0:
        raise MediaError("Не удалось определить размер медиа.")

    long_side = max(1, long_side)
    if width >= height:
        cols = long_side
        rows = max(1, round(long_side * height / width))
    else:
        rows = long_side
        cols = max(1, round(long_side * width / height))

    while cols * rows > max_tiles:
        if cols >= rows and cols > 1:
            cols -= 1
        elif rows > 1:
            rows -= 1
        else:
            break

    return Grid(cols=cols, rows=rows)


def validate_padding(padding: int) -> None:
    if padding < 0 or padding * 2 >= TILE_SIZE:
        raise MediaError("Некорректный padding.")


def content_height_for_padding(padding: int) -> int:
    validate_padding(padding)
    return TILE_SIZE - padding * 2


def crop_visible_alpha_bounds(image: Image.Image) -> Image.Image:
    bbox = image.getchannel("A").getbbox()
    if bbox is None:
        return image
    return image.crop(bbox)


def fit_image_on_canvas(image: Image.Image, width: int, height: int) -> Image.Image:
    if width <= 0 or height <= 0:
        raise MediaError("Некорректный размер холста.")

    scale = min(width / image.width, height / image.height)
    scaled_width = max(1, int(round(image.width * scale)))
    scaled_height = max(1, int(round(image.height * scale)))
    resized = image.resize((scaled_width, scaled_height), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.alpha_composite(resized, ((width - scaled_width) // 2, (height - scaled_height) // 2))
    return canvas


def _marker_xy(marker_index: int) -> tuple[int, int]:
    return marker_index % TILE_SIZE, (marker_index // TILE_SIZE) % TILE_SIZE


def add_invisible_marker_if_empty(image: Image.Image, marker_index: int) -> None:
    if image.getchannel("A").getbbox() is None:
        image.putpixel(_marker_xy(marker_index), (0, 0, 0, 1))


def telegram_video_tile_byte_limit(max_tile_bytes: int) -> int:
    return min(max_tile_bytes, TELEGRAM_VIDEO_EMOJI_SAFE_BYTES)


def encode_video_tile(
    input_path: Path,
    output_path: Path,
    max_seconds: float,
    tile_filter_template: str,
    max_tile_bytes: int,
) -> None:
    max_tile_bytes = telegram_video_tile_byte_limit(max_tile_bytes)
    attempts = [
        (30, 32),
        (30, 38),
        (24, 42),
        (18, 46),
        (12, 50),
        (10, 54),
        (8, 58),
        (6, 63),
    ]
    last_size = 0
    for fps, crf in attempts:
        vf = tile_filter_template.format(fps=fps)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(input_path),
                "-an",
                "-t",
                str(max_seconds),
                "-vf",
                vf,
                "-c:v",
                "libvpx-vp9",
                "-pix_fmt",
                "yuva420p",
                "-b:v",
                "0",
                "-crf",
                str(crf),
                "-deadline",
                "good",
                "-cpu-used",
                "4",
                "-row-mt",
                "1",
                str(output_path),
            ],
            check=True,
        )
        last_size = output_path.stat().st_size
        if last_size <= max_tile_bytes:
            return

    raise MediaError(
        f"Видео-плитка получилась слишком тяжёлой ({last_size // 1024} КБ). "
        "Попробуйте сетку крупнее, например 6x6."
    )


def encode_frame_tile(
    frames_pattern: Path,
    output_path: Path,
    input_fps: float,
    crop_x: int,
    crop_y: int,
    max_tile_bytes: int,
    crop_height: int = TILE_SIZE,
    padding: int = 0,
) -> None:
    max_tile_bytes = telegram_video_tile_byte_limit(max_tile_bytes)
    attempts = [
        (30, 32),
        (30, 38),
        (24, 42),
        (18, 46),
        (12, 50),
        (10, 54),
        (8, 58),
        (6, 63),
    ]
    last_size = 0
    for fps, crf in attempts:
        target_fps = min(fps, input_fps)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-framerate",
                f"{input_fps:g}",
                "-start_number",
                "0",
                "-i",
                str(frames_pattern),
                "-an",
                "-vf",
                (
                    f"fps={target_fps:g},"
                    f"crop={TILE_SIZE}:{crop_height}:{crop_x}:{crop_y},"
                    f"pad={TILE_SIZE}:{TILE_SIZE}:0:{padding}:color=black@0,"
                    "format=yuva420p"
                ),
                "-c:v",
                "libvpx-vp9",
                "-pix_fmt",
                "yuva420p",
                "-b:v",
                "0",
                "-crf",
                str(crf),
                "-deadline",
                "good",
                "-cpu-used",
                "4",
                "-row-mt",
                "1",
                str(output_path),
            ],
            check=True,
        )
        last_size = output_path.stat().st_size
        if last_size <= max_tile_bytes:
            return

    raise MediaError(
        f"Animated-плитка получилась слишком тяжёлой ({last_size // 1024} КБ). "
        "Попробуйте сетку крупнее, например 6x6."
    )


def render_video_content_frames(
    input_path: Path,
    frames_dir: Path,
    max_seconds: float,
    fps: float,
    canvas_w: int,
    canvas_h: int,
) -> Path:
    frames_dir.mkdir(parents=True, exist_ok=True)
    frames_pattern = frames_dir / "frame_%05d.png"
    vf = (
        f"trim=duration={max_seconds},setpts=PTS-STARTPTS,"
        f"fps={fps:g},"
        f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease:flags=lanczos,"
        "format=rgba,"
        f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2:color=black@0,"
        "format=rgba"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(input_path),
            "-an",
            "-t",
            str(max_seconds),
            "-vf",
            vf,
            str(frames_pattern),
        ],
        check=True,
    )
    if not any(frames_dir.glob("frame_*.png")):
        raise MediaError("Не удалось подготовить кадры видео/GIF.")
    return frames_pattern


def make_static_tiles(
    input_path: Path,
    output_dir: Path,
    padding: int,
    grid: Grid | None,
    default_long_side: int,
    max_tiles: int,
    progress_callback: TileProgressCallback | None = None,
) -> StickerBatch:
    validate_padding(padding)
    output_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(input_path) as source:
        image = source.convert("RGBA")

    if grid is None:
        content_image = crop_visible_alpha_bounds(image)
        grid = choose_grid(content_image.width, content_image.height, default_long_side, max_tiles)
    else:
        content_image = image
    if grid.count > max_tiles:
        raise MediaError(f"Слишком много плиток: максимум {max_tiles}.")

    content_h = content_height_for_padding(padding)
    content_canvas = fit_image_on_canvas(
        content_image,
        grid.cols * TILE_SIZE,
        grid.rows * content_h,
    )

    paths: list[Path] = []
    for row in range(grid.rows):
        for col in range(grid.cols):
            tile = content_canvas.crop(
                (
                    col * TILE_SIZE,
                    row * content_h,
                    (col + 1) * TILE_SIZE,
                    (row + 1) * content_h,
                )
            )

            canvas = Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0))
            canvas.alpha_composite(tile, (0, padding))
            add_invisible_marker_if_empty(canvas, len(paths))

            path = output_dir / f"tile_{len(paths):03d}.webp"
            canvas.save(path, "WEBP", lossless=True, quality=95, method=6)
            paths.append(path)
            if progress_callback:
                progress_callback(len(paths), grid.count)

    return StickerBatch(
        paths=paths,
        sticker_format="static",
        grid=grid,
        source_width=image.width,
        source_height=image.height,
        padding=padding,
    )


def probe_video(input_path: Path) -> VideoProbe:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(input_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    if not streams:
        raise MediaError("В файле не найден видеопоток.")

    stream = streams[0]
    duration_raw = (data.get("format") or {}).get("duration") or stream.get("duration") or "0"
    return VideoProbe(
        width=int(stream["width"]),
        height=int(stream["height"]),
        duration=float(duration_raw),
    )


def make_video_tiles(
    input_path: Path,
    output_dir: Path,
    padding: int,
    grid: Grid | None,
    default_long_side: int,
    max_tiles: int,
    max_seconds: float,
    max_tile_bytes: int = 256 * 1024,
    progress_callback: TileProgressCallback | None = None,
    tile_concurrency: int = 1,
) -> StickerBatch:
    validate_padding(padding)
    output_dir.mkdir(parents=True, exist_ok=True)

    probe = probe_video(input_path)

    if grid is None:
        grid = choose_grid(probe.width, probe.height, default_long_side, max_tiles)
    if grid.count > max_tiles:
        raise MediaError(f"Слишком много плиток: максимум {max_tiles}.")

    content_h = content_height_for_padding(padding)
    canvas_w = grid.cols * TILE_SIZE
    canvas_h = grid.rows * content_h

    paths: list[Path | None] = [None] * grid.count
    frames_dir = output_dir / "_frames"
    render_fps = 30.0

    try:
        frames_pattern = render_video_content_frames(
            input_path,
            frames_dir,
            max_seconds,
            render_fps,
            canvas_w,
            canvas_h,
        )

        def encode_tile(index: int, row: int, col: int) -> tuple[int, Path]:
            output_path = output_dir / f"tile_{index:03d}.webm"
            encode_frame_tile(
                frames_pattern,
                output_path,
                render_fps,
                col * TILE_SIZE,
                row * content_h,
                max_tile_bytes,
                crop_height=content_h,
                padding=padding,
            )
            return index, output_path

        completed = 0
        worker_count = min(grid.count, max(1, tile_concurrency))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(encode_tile, row * grid.cols + col, row, col)
                for row in range(grid.rows)
                for col in range(grid.cols)
            ]
            for future in as_completed(futures):
                index, output_path = future.result()
                paths[index] = output_path
                completed += 1
                if progress_callback:
                    progress_callback(completed, grid.count)
    finally:
        shutil.rmtree(frames_dir, ignore_errors=True)

    return StickerBatch(
        paths=[path for path in paths if path is not None],
        sticker_format="video",
        grid=grid,
        source_width=probe.width,
        source_height=probe.height,
        padding=padding,
    )


def _sample_frame_indices(total_frames: int, max_samples: int = 12) -> list[int]:
    if total_frames <= 1:
        return [0]
    sample_count = min(total_frames, max_samples)
    return sorted(
        {
            min(total_frames - 1, round(index * (total_frames - 1) / (sample_count - 1)))
            for index in range(sample_count)
        }
    )


def _tgs_visible_bounds(animation: LottieAnimation, total_frames: int) -> tuple[int, int, int, int] | None:
    bounds: tuple[int, int, int, int] | None = None
    for frame_index in _sample_frame_indices(total_frames):
        frame = animation.render_pillow_frame(frame_index).convert("RGBA")
        bbox = frame.getchannel("A").getbbox()
        if bbox is None:
            continue
        if bounds is None:
            bounds = bbox
        else:
            bounds = (
                min(bounds[0], bbox[0]),
                min(bounds[1], bbox[1]),
                max(bounds[2], bbox[2]),
                max(bounds[3], bbox[3]),
            )
    return bounds


def _fit_tgs_frame_on_grid(
    frame: Image.Image,
    bounds: tuple[int, int, int, int] | None,
    grid: Grid,
    padding: int,
    mark_empty: bool = True,
) -> Image.Image:
    content_h = content_height_for_padding(padding)
    source = frame.crop(bounds) if bounds else frame
    content_canvas = fit_image_on_canvas(
        source,
        grid.cols * TILE_SIZE,
        grid.rows * content_h,
    )
    canvas = Image.new("RGBA", (grid.cols * TILE_SIZE, grid.rows * TILE_SIZE), (0, 0, 0, 0))
    for row in range(grid.rows):
        strip = content_canvas.crop(
            (
                0,
                row * content_h,
                grid.cols * TILE_SIZE,
                (row + 1) * content_h,
            )
        )
        canvas.alpha_composite(strip, (0, row * TILE_SIZE + padding))

    tile_index = 0
    for row in range(grid.rows):
        for col in range(grid.cols):
            tile = canvas.crop(
                (
                    col * TILE_SIZE,
                    row * TILE_SIZE,
                    (col + 1) * TILE_SIZE,
                    (row + 1) * TILE_SIZE,
                )
            )
            if mark_empty and tile.getchannel("A").getbbox() is None:
                canvas.putpixel(
                    (col * TILE_SIZE + _marker_xy(tile_index)[0], row * TILE_SIZE + _marker_xy(tile_index)[1]),
                    (0, 0, 0, 1),
                )
            tile_index += 1
    return canvas


def _load_tgs_json(input_path: Path) -> dict:
    try:
        with gzip.open(input_path, "rt", encoding="utf-8") as file:
            data = json.load(file)
    except Exception as error:
        raise MediaError("Не удалось прочитать .TGS-анимацию.") from error
    if not isinstance(data, dict) or not isinstance(data.get("layers"), list):
        raise MediaError("Не удалось прочитать .TGS-анимацию.")
    return data


def _tiny_tgs_marker(tile_index: int, op: float) -> dict:
    x, y = _marker_xy(tile_index)
    return {
        "ddd": 0,
        "ind": 2,
        "ty": 4,
        "ks": {
            "o": {"k": 1},
            "r": {"k": 0},
            "p": {"k": [x * 512 / TILE_SIZE, y * 512 / TILE_SIZE, 0]},
            "a": {"k": [0, 0, 0]},
            "s": {"k": [100, 100, 100]},
        },
        "shapes": [
            {"ty": "rc", "d": 1, "s": {"k": [1, 1]}, "p": {"k": [0, 0]}, "r": {"k": 0}},
            {"ty": "fl", "c": {"k": [0, 0, 0, 1]}, "o": {"k": 1}, "r": 1},
            {
                "ty": "tr",
                "p": {"k": [0, 0]},
                "a": {"k": [0, 0]},
                "s": {"k": [100, 100]},
                "r": {"k": 0},
                "o": {"k": 100},
            },
        ],
        "ip": 0,
        "op": op,
        "st": 0,
        "bm": 0,
    }


def _write_marker_tgs_tile(source_data: dict, output_path: Path, tile_index: int) -> None:
    op = source_data.get("op", 180)
    data = {
        "v": source_data.get("v", "5.7.4"),
        "fr": source_data.get("fr", 60),
        "ip": source_data.get("ip", 0),
        "op": op,
        "w": 512,
        "h": 512,
        "nm": "empty emoji tile",
        "ddd": 0,
        "assets": [],
        "layers": [_tiny_tgs_marker(tile_index, op)],
    }
    packed = gzip.compress(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        compresslevel=9,
    )
    output_path.write_bytes(packed)


def _source_asset_id(data: dict) -> str:
    used = {
        str(asset.get("id"))
        for asset in data.get("assets", [])
        if isinstance(asset, dict) and asset.get("id") is not None
    }
    index = 0
    while True:
        candidate = f"emoji_picture_source_{index}"
        if candidate not in used:
            return candidate
        index += 1


def _write_tgs_tile(
    source_data: dict,
    output_path: Path,
    ref_id: str,
    position: tuple[float, float],
    scale: float,
    tile_index: int,
) -> None:
    op = source_data.get("op", 180)
    data = {
        "v": source_data.get("v", "5.7.4"),
        "fr": source_data.get("fr", 60),
        "ip": source_data.get("ip", 0),
        "op": op,
        "w": 512,
        "h": 512,
        "nm": "emoji tile",
        "ddd": 0,
        "assets": [
            *deepcopy(source_data.get("assets") or []),
            {
                "id": ref_id,
                "w": source_data.get("w", 512),
                "h": source_data.get("h", 512),
                "layers": deepcopy(source_data.get("layers") or []),
            },
        ],
        "layers": [
            {
                "ddd": 0,
                "ind": 1,
                "ty": 0,
                "refId": ref_id,
                "sr": 1,
                "ks": {
                    "o": {"k": 100},
                    "r": {"k": 0},
                    "p": {"k": [position[0], position[1], 0]},
                    "a": {"k": [0, 0, 0]},
                    "s": {"k": [scale, scale, 100]},
                },
                "ao": 0,
                "w": source_data.get("w", 512),
                "h": source_data.get("h", 512),
                "ip": source_data.get("ip", 0),
                "op": op,
                "st": source_data.get("st", 0),
                "bm": 0,
            },
            _tiny_tgs_marker(tile_index, op),
        ],
    }
    packed = gzip.compress(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        compresslevel=9,
    )
    output_path.write_bytes(packed)
    if output_path.stat().st_size > TELEGRAM_TGS_EMOJI_MAX_BYTES:
        raise MediaError(
            f"TGS-плитка получилась слишком тяжёлой ({output_path.stat().st_size // 1024} КБ). "
            "Попробуйте сетку крупнее, например 6x6."
        )


def _tgs_visible_tiles(
    animation: LottieAnimation,
    bounds: tuple[int, int, int, int] | None,
    grid: Grid,
    padding: int,
    total_frames: int,
) -> list[bool]:
    visible = [False] * grid.count
    for frame_index in _sample_frame_indices(total_frames):
        frame = animation.render_pillow_frame(frame_index).convert("RGBA")
        canvas = _fit_tgs_frame_on_grid(frame, bounds, grid, padding, mark_empty=False)
        tile_index = 0
        for row in range(grid.rows):
            for col in range(grid.cols):
                if visible[tile_index]:
                    tile_index += 1
                    continue
                tile = canvas.crop(
                    (
                        col * TILE_SIZE,
                        row * TILE_SIZE,
                        (col + 1) * TILE_SIZE,
                        (row + 1) * TILE_SIZE,
                    )
                )
                visible[tile_index] = tile.getchannel("A").getbbox() is not None
                tile_index += 1
        if all(visible):
            break
    return visible


def make_tgs_tiles(
    input_path: Path,
    output_dir: Path,
    padding: int,
    grid: Grid | None,
    default_long_side: int,
    max_tiles: int,
    max_seconds: float,
    max_tile_bytes: int = 256 * 1024,
    progress_callback: TileProgressCallback | None = None,
) -> StickerBatch:
    validate_padding(padding)
    output_dir.mkdir(parents=True, exist_ok=True)
    source_data = _load_tgs_json(input_path)

    try:
        animation = LottieAnimation.from_tgs(str(input_path))
    except Exception as error:
        raise MediaError("Не удалось прочитать .TGS-анимацию.") from error

    source_width, source_height = animation.lottie_animation_get_size()
    total_frames = max(1, int(animation.lottie_animation_get_totalframe()))
    source_fps = float(animation.lottie_animation_get_framerate() or 30)
    source_fps = source_fps if source_fps > 0 else 30.0
    duration = min(max_seconds, total_frames / source_fps)
    render_fps = min(30.0, source_fps)
    frame_count = max(1, int(math.ceil(duration * render_fps)))

    bounds = _tgs_visible_bounds(animation, min(total_frames, max(1, int(math.ceil(duration * source_fps)))))
    bounds_width = (bounds[2] - bounds[0]) if bounds else source_width
    bounds_height = (bounds[3] - bounds[1]) if bounds else source_height

    if grid is None:
        grid = choose_grid(bounds_width, bounds_height, default_long_side, max_tiles)
    if grid.count > max_tiles:
        raise MediaError(f"Слишком много плиток: максимум {max_tiles}.")

    paths: list[Path] = []
    content_h = content_height_for_padding(padding)
    canvas_w = grid.cols * TILE_SIZE
    canvas_h = grid.rows * content_h
    scale_factor = min(canvas_w / bounds_width, canvas_h / bounds_height)
    offset_x = (canvas_w - bounds_width * scale_factor) / 2
    offset_y = (canvas_h - bounds_height * scale_factor) / 2
    bounds_left, bounds_top = (bounds[0], bounds[1]) if bounds else (0, 0)
    lottie_scale = scale_factor * 512
    visible_tiles = _tgs_visible_tiles(
        animation,
        bounds,
        grid,
        padding,
        min(total_frames, max(1, int(math.ceil(duration * source_fps)))),
    )
    ref_id = _source_asset_id(source_data)

    for row in range(grid.rows):
        for col in range(grid.cols):
            tile_index = len(paths)
            output_path = output_dir / f"tile_{tile_index:03d}.tgs"
            position = (
                (offset_x - col * TILE_SIZE - bounds_left * scale_factor) * 512 / TILE_SIZE,
                (offset_y - row * content_h + padding - bounds_top * scale_factor) * 512 / TILE_SIZE,
            )
            if visible_tiles[tile_index]:
                _write_tgs_tile(
                    source_data,
                    output_path,
                    ref_id,
                    position,
                    lottie_scale,
                    tile_index,
                )
            else:
                _write_marker_tgs_tile(source_data, output_path, tile_index)
            paths.append(output_path)
            if progress_callback:
                progress_callback(len(paths), grid.count)

    return StickerBatch(
        paths=paths,
        sticker_format="animated",
        grid=grid,
        source_width=source_width,
        source_height=source_height,
        padding=padding,
    )
