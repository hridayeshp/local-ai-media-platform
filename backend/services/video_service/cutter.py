import subprocess
import os
import uuid

def cut_clip(video_path, start, end, output_dir):
    os.makedirs(output_dir, exist_ok=True)

    clip_name = f"clip_{uuid.uuid4().hex}.mp4"
    output_path = os.path.join(output_dir, clip_name)

    subprocess.run([
        "ffmpeg",
        "-y",
        "-i", video_path,
        "-ss", str(start),
        "-to", str(end),
        "-c", "copy",
        output_path
    ], check=True)

    return clip_name
