import cv2
import os
import uuid

def extract_frames(video_path: str, out_dir: str, every_n_seconds: int = 2):
    cap = cv2.VideoCapture(video_path)

    if not cap.isOpened():
        return {"error": "Could not open video"}

    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_interval = int(fps * every_n_seconds)

    os.makedirs(out_dir, exist_ok=True)

    frames = []
    frame_count = 0
    saved = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_count % frame_interval == 0:
            filename = f"frame_{uuid.uuid4().hex}.jpg"
            path = os.path.join(out_dir, filename)
            cv2.imwrite(path, frame)
            h, w, c = frame.shape

            frames.append({
                "file": filename,
                "width": w,
                "height": h,
                "channels": c
            })
            saved += 1

        frame_count += 1

    cap.release()

    return {
        "frames_extracted": saved,
        "frames": frames[:5]  # return sample only
    }