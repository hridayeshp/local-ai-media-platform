import cv2

def analyze_image(image_path: str):
    img = cv2.imread(image_path)

    if img is None:
        return {"error": "Could not read image"}

    height, width, channels = img.shape

    return {
        "width": width,
        "height": height,
        "channels": channels,
        "message": "Image loaded successfully"
    }
