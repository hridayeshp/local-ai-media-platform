import subprocess
import os
import uuid

def build_slideshow(image_paths: list, output_dir: str, duration_per_image=3):
    """
    image_paths: full paths to images
    duration_per_image: seconds each image stays on screen
    """

    os.makedirs(output_dir, exist_ok=True)

    list_file = os.path.join(output_dir, "images.txt")

    # FFmpeg concat format
    with open(list_file, "w") as f:
        for img in image_paths:
            f.write(f"file '{img}'\n")
            f.write(f"duration {duration_per_image}\n")

        # Required: repeat last image
        f.write(f"file '{image_paths[-1]}'\n")

    output_name = f"slideshow_{uuid.uuid4().hex}.mp4"
    output_path = os.path.join(output_dir, output_name)

    subprocess.run([
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,
        "-vsync", "vfr",
        "-pix_fmt", "yuv420p",
        output_path
    ], check=True)

    return output_name, output_path
