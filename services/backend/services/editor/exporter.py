import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


def _safe_filename(name: str) -> str:
    base = os.path.basename(name or "asset")
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    return base[:120] or "asset.bin"


def _run_command(cmd: List[str], prefix: str) -> str:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        raise RuntimeError(f"{prefix}: {err[-700:]}")
    return proc.stdout


def _probe_media(path: str) -> Dict[str, Any]:
    raw = _run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            path,
        ],
        "ffprobe failed",
    )
    data = json.loads(raw)
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    duration = 0.0
    try:
        duration = float(fmt.get("duration", 0.0) or 0.0)
    except Exception:
        duration = 0.0

    width = None
    height = None
    for s in streams:
        if s.get("codec_type") == "video":
            width = int(s.get("width") or 0) or None
            height = int(s.get("height") or 0) or None
            break

    kind = "video" if has_video else "audio" if has_audio else "file"
    return {
        "kind": kind,
        "has_video": has_video,
        "has_audio": has_audio,
        "duration": duration,
        "width": width,
        "height": height,
    }


def _escape_drawtext(text: str) -> str:
    escaped = text.replace("\\", "\\\\")
    escaped = escaped.replace(":", "\\:")
    escaped = escaped.replace("'", "\\'")
    escaped = escaped.replace("%", "\\%")
    escaped = escaped.replace(",", "\\,")
    escaped = escaped.replace("[", "\\[")
    escaped = escaped.replace("]", "\\]")
    return escaped


@dataclass
class ExportResult:
    export_id: str
    output_path: str
    duration: float


class EditorAssetStore:
    def __init__(self, runtime_dir: str) -> None:
        self.runtime_dir = runtime_dir
        self.assets_dir = os.path.join(runtime_dir, "assets")
        self.exports_dir = os.path.join(runtime_dir, "exports")
        self.index_path = os.path.join(runtime_dir, "assets_index.json")
        self.lock = threading.Lock()

        os.makedirs(self.assets_dir, exist_ok=True)
        os.makedirs(self.exports_dir, exist_ok=True)
        if not os.path.exists(self.index_path):
            self._save_index({"assets": [], "exports": []})

    def _load_index(self) -> Dict[str, Any]:
        with open(self.index_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_index(self, data: Dict[str, Any]) -> None:
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def add_asset(self, original_name: str, tmp_path: str) -> Dict[str, Any]:
        asset_id = uuid.uuid4().hex
        safe_name = _safe_filename(original_name)
        stored_name = f"{asset_id}_{safe_name}"
        target_path = os.path.join(self.assets_dir, stored_name)
        shutil.move(tmp_path, target_path)

        meta = _probe_media(target_path)
        size_bytes = os.path.getsize(target_path)
        asset = {
            "asset_id": asset_id,
            "original_name": safe_name,
            "stored_name": stored_name,
            "path": target_path,
            "size_bytes": size_bytes,
            **meta,
        }

        with self.lock:
            index = self._load_index()
            index.setdefault("assets", []).append(asset)
            self._save_index(index)
        return asset

    def list_assets(self) -> List[Dict[str, Any]]:
        with self.lock:
            index = self._load_index()
            return list(index.get("assets", []))

    def get_asset(self, asset_id: str) -> Optional[Dict[str, Any]]:
        assets = self.list_assets()
        for asset in assets:
            if asset.get("asset_id") == asset_id:
                return asset
        return None

    def add_export_record(self, export_id: str, output_path: str, duration: float, request_payload: Dict[str, Any]) -> None:
        record = {
            "export_id": export_id,
            "output_path": output_path,
            "duration": duration,
            "created_at": int(time.time()),
            "request_payload": request_payload,
        }
        with self.lock:
            index = self._load_index()
            index.setdefault("exports", []).append(record)
            self._save_index(index)

    def get_export_path(self, export_id: str) -> Optional[str]:
        with self.lock:
            index = self._load_index()
            for record in index.get("exports", []):
                if record.get("export_id") == export_id:
                    return record.get("output_path")
        return None


def _clip_end(clip: Dict[str, Any]) -> float:
    return float(clip.get("start", 0.0)) + float(clip.get("duration", 0.0))


def _collect_clips(payload: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    tracks = payload.get(key, []) or []
    clips: List[Dict[str, Any]] = []
    for t_index, track in enumerate(tracks):
        for c_index, clip in enumerate(track.get("clips", []) or []):
            item = dict(clip)
            item["_track_index"] = t_index
            item["_clip_index"] = c_index
            clips.append(item)
    return clips


def export_project(payload: Dict[str, Any], store: EditorAssetStore) -> ExportResult:
    width = int(payload.get("width", 1280))
    height = int(payload.get("height", 720))
    fps = int(payload.get("fps", 24))
    bg_color = str(payload.get("bg_color", "black"))

    video_clips = _collect_clips(payload, "video_tracks")
    audio_clips = _collect_clips(payload, "audio_tracks")
    text_clips = _collect_clips(payload, "text_tracks")

    if not video_clips and not text_clips:
        raise RuntimeError("Project must include at least one video or text clip")

    max_end = 1.0
    for clip in video_clips + audio_clips + text_clips:
        max_end = max(max_end, _clip_end(clip))
    duration = float(payload.get("duration") or max_end)
    duration = max(duration, max_end, 1.0)

    input_specs: List[Dict[str, Any]] = []
    for clip in video_clips + audio_clips:
        asset_id = clip.get("asset_id")
        if not asset_id:
            continue
        if any(spec["asset_id"] == asset_id for spec in input_specs):
            continue
        asset = store.get_asset(asset_id)
        if not asset:
            raise RuntimeError(f"Asset not found: {asset_id}")
        input_specs.append({"asset_id": asset_id, "asset": asset})

    input_map: Dict[str, int] = {}
    ffmpeg_cmd: List[str] = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={bg_color}:s={width}x{height}:r={fps}:d={duration:.3f}",
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=channel_layout=stereo:sample_rate=44100:d={duration:.3f}",
    ]

    input_index = 2
    for spec in input_specs:
        path = spec["asset"]["path"]
        ffmpeg_cmd.extend(["-i", path])
        input_map[spec["asset_id"]] = input_index
        input_index += 1

    filter_parts: List[str] = []
    current_video = "vbase"
    filter_parts.append(f"[0:v]setpts=PTS-STARTPTS,format=yuv420p[{current_video}]")

    audio_labels: List[str] = ["abase"]
    filter_parts.append("[1:a]atrim=duration={:.3f},asetpts=PTS-STARTPTS[abase]".format(duration))

    ordered_video = sorted(video_clips, key=lambda c: (c["_track_index"], c.get("start", 0), c["_clip_index"]))
    for idx, clip in enumerate(ordered_video):
        asset_id = clip.get("asset_id")
        if not asset_id:
            continue
        asset = store.get_asset(asset_id)
        if not asset:
            continue
        if not asset.get("has_video"):
            continue
        in_point = max(0.0, float(clip.get("in_point", 0.0)))
        clip_duration = max(0.1, float(clip.get("duration", 0.1)))
        start_time = max(0.0, float(clip.get("start", 0.0)))
        end_time = start_time + clip_duration
        transition_in = max(0.0, min(float(clip.get("transition_in", 0.0)), clip_duration / 2))
        transition_out = max(0.0, min(float(clip.get("transition_out", 0.0)), clip_duration / 2))

        source_idx = input_map[asset_id]
        vclip_label = f"vclip{idx}"
        vchain = f"[{source_idx}:v]trim=start={in_point:.3f}:duration={clip_duration:.3f},setpts=PTS-STARTPTS,scale={width}:{height},fps={fps}"
        if transition_in > 0:
            vchain += f",fade=t=in:st=0:d={transition_in:.3f}"
        if transition_out > 0:
            out_start = max(0.0, clip_duration - transition_out)
            vchain += f",fade=t=out:st={out_start:.3f}:d={transition_out:.3f}"
        vchain += f"[{vclip_label}]"
        filter_parts.append(vchain)

        out_label = f"vtmp{idx}"
        filter_parts.append(
            f"[{current_video}][{vclip_label}]overlay=shortest=0:enable='between(t,{start_time:.3f},{end_time:.3f})'[{out_label}]"
        )
        current_video = out_label

        if asset.get("has_audio"):
            volume = float(clip.get("volume", 1.0))
            delay_ms = int(start_time * 1000)
            audio_label = f"av{idx}"
            filter_parts.append(
                f"[{source_idx}:a]atrim=start={in_point:.3f}:duration={clip_duration:.3f},asetpts=PTS-STARTPTS,volume={volume:.3f},adelay={delay_ms}|{delay_ms}[{audio_label}]"
            )
            audio_labels.append(audio_label)

    ordered_audio = sorted(audio_clips, key=lambda c: (c["_track_index"], c.get("start", 0), c["_clip_index"]))
    for idx, clip in enumerate(ordered_audio):
        asset_id = clip.get("asset_id")
        if not asset_id:
            continue
        asset = store.get_asset(asset_id)
        if not asset or not asset.get("has_audio"):
            continue
        in_point = max(0.0, float(clip.get("in_point", 0.0)))
        clip_duration = max(0.1, float(clip.get("duration", 0.1)))
        start_time = max(0.0, float(clip.get("start", 0.0)))
        volume = float(clip.get("volume", 1.0))
        delay_ms = int(start_time * 1000)
        source_idx = input_map[asset_id]
        label = f"aa{idx}"
        filter_parts.append(
            f"[{source_idx}:a]atrim=start={in_point:.3f}:duration={clip_duration:.3f},asetpts=PTS-STARTPTS,volume={volume:.3f},adelay={delay_ms}|{delay_ms}[{label}]"
        )
        audio_labels.append(label)

    if len(audio_labels) == 1:
        filter_parts.append("[abase]anull[aout]")
    else:
        mixed_inputs = "".join(f"[{label}]" for label in audio_labels)
        filter_parts.append(f"{mixed_inputs}amix=inputs={len(audio_labels)}:normalize=0:dropout_transition=0[aout]")

    current_text_video = current_video
    ordered_text = sorted(text_clips, key=lambda c: (c.get("start", 0), c["_track_index"], c["_clip_index"]))
    for idx, clip in enumerate(ordered_text):
        text = str(clip.get("text", "")).strip()
        if not text:
            continue
        start_time = max(0.0, float(clip.get("start", 0.0)))
        end_time = max(start_time + 0.05, float(clip.get("end", start_time + 2.0)))
        fontsize = int(clip.get("font_size", 42))
        color = str(clip.get("color", "white"))
        x_raw = clip.get("x", 40)
        y_raw = clip.get("y", None)
        x = str(40 if x_raw is None else x_raw)
        y = str((height - 80) if y_raw is None else y_raw)
        label = f"vtxt{idx}"
        escaped = _escape_drawtext(text)
        filter_parts.append(
            f"[{current_text_video}]drawtext=text='{escaped}':fontcolor={color}:fontsize={fontsize}:x={x}:y={y}:enable='between(t,{start_time:.3f},{end_time:.3f})'[{label}]"
        )
        current_text_video = label

    filter_complex = ";".join(filter_parts)
    ffmpeg_cmd.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            f"[{current_text_video}]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
        ]
    )

    export_id = uuid.uuid4().hex
    output_path = os.path.join(store.exports_dir, f"{export_id}.mp4")
    ffmpeg_cmd.append(output_path)
    _run_command(ffmpeg_cmd, "Failed to export project")
    return ExportResult(export_id=export_id, output_path=output_path, duration=duration)
