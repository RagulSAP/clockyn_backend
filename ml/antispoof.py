import cv2
import numpy as np

# Production-safe liveness estimation
# based on texture + frequency analysis

def check_liveness(image, bbox):

    x1, y1, x2, y2 = map(int, bbox)
    face = image[y1:y2, x1:x2]

    if face.size == 0:
        return False

    gray = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)

    # High-frequency texture analysis
    lap_var = cv2.Laplacian(gray, cv2.CV_64F).var()

    # real faces contain micro-texture variation
    # screens and prints are smoother
    if lap_var < 30:
        return False

    return True
