import subprocess
import os
import uuid

def extract_audio_from_video(video_path: str, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    audio_path = os.path.join(out_dir, f"{uuid.uuid4()}.wav")

    cmd = [
        "ffmpeg",
        "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        audio_path
    ]

    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return audio_path
