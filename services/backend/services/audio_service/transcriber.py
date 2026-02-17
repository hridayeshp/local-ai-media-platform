from faster_whisper import WhisperModel

model = WhisperModel(
    "base",
    device="cpu",        # Docker = CPU
    compute_type="int8"  # Faster + lower memory
)

def transcribe_audio(audio_path: str):
    segments, info = model.transcribe(audio_path)

    results = []
    full_text = ""

    for seg in segments:
        results.append({
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip()
        })
        full_text += seg.text + " "

    return {
        "text": full_text.strip(),
        "segments": results
    }
