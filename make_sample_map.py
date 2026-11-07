"""Generate a synthetic layout map for testing (known ground truth).
Scale: 1 px = 0.05 m  ->  20 px = 1 m.
Plots are 10 m x 20 m (= 200 m2 = 7.91 marla)."""
import cv2, numpy as np

mpp = 0.05
W, H = 1400, 1000
img = np.full((H, W, 3), 255, np.uint8)
t = 3  # line thickness px
pw, ph = int(10 / mpp), int(20 / mpp)   # 200 x 400 px
x0, y0 = 100, 80
cols, rows = 6, 2
gap_y = 120  # road between rows

n = 0
for r in range(rows):
    for c in range(cols):
        x = x0 + c * pw
        y = y0 + r * (ph + gap_y)
        cv2.rectangle(img, (x, y), (x + pw, y + ph), (0, 0, 0), t)
        n += 1
        cv2.putText(img, str(n), (x + 70, y + 200), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2)
# one irregular (corner) plot
pts = np.array([[1350, 80], [1350, 480], [1300, 480], [1300, 300], [1330, 80]], np.int32)
cv2.polylines(img, [pts], True, (0, 0, 0), t)
# small simulated scan gap in a line
cv2.line(img, (500, 80), (500, 90), (255, 255, 255), 5)
# light noise
noise = np.random.default_rng(1).normal(0, 6, img.shape)
img = np.clip(img + noise, 0, 255).astype(np.uint8)
cv2.imwrite("sample_layout.png", img)
print("rectangular plots: 12 x 200 m2; plus 1 irregular plot")
