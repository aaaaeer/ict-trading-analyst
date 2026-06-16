"""Generate a sample EUR/USD 1H candlestick chart with ICT structure."""
from PIL import Image, ImageDraw, ImageFont
import random

W, H = 1200, 700
BG = (18, 18, 30)
GRID = (40, 40, 60)
UP = (38, 166, 154)
DOWN = (239, 83, 80)
TEXT = (200, 200, 220)
WHITE = (240, 240, 255)

random.seed(42)

candles = []
price = 1.08500
for i in range(60):
    # Simulate a bullish trending market with pullbacks
    if i < 15:
        drift = random.uniform(-0.0005, 0.0012)
    elif i < 25:
        drift = random.uniform(-0.0015, 0.0003)  # pullback
    elif i < 45:
        drift = random.uniform(-0.0003, 0.0015)  # rally
    else:
        drift = random.uniform(-0.0008, 0.0008)  # ranging

    o = price
    c = o + drift
    h = max(o, c) + random.uniform(0.0002, 0.0008)
    l = min(o, c) - random.uniform(0.0002, 0.0008)
    candles.append((o, h, l, c))
    price = c

prices = [p for ohcl in candles for p in ohcl]
p_min = min(prices) - 0.0010
p_max = max(prices) + 0.0010
p_range = p_max - p_min

PAD_L, PAD_R, PAD_T, PAD_B = 80, 40, 60, 60
chart_w = W - PAD_L - PAD_R
chart_h = H - PAD_T - PAD_B

def to_y(p):
    return PAD_T + chart_h - int((p - p_min) / p_range * chart_h)

def to_x(i):
    return PAD_L + int((i + 0.5) * chart_w / len(candles))

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)

# Grid
for i in range(6):
    y = PAD_T + int(i * chart_h / 5)
    d.line([(PAD_L, y), (W - PAD_R, y)], fill=GRID, width=1)
    p = p_max - i * p_range / 5
    d.text((5, y - 8), f"{p:.5f}", fill=TEXT)

# Draw candles
cw = max(6, int(chart_w / len(candles)) - 3)
for i, (o, h, l, c) in enumerate(candles):
    x = to_x(i)
    color = UP if c >= o else DOWN
    d.line([(x, to_y(h)), (x, to_y(l))], fill=color, width=2)
    y1, y2 = sorted([to_y(o), to_y(c)])
    d.rectangle([(x - cw//2, y1), (x + cw//2, max(y1+1, y2))], fill=color)

# Mark key ICT levels
pdh = max(candles[0][1], candles[1][1], candles[2][1])
pdl = min(candles[0][2], candles[1][2], candles[2][2])
fvg_low = candles[20][3]
fvg_high = candles[22][0]
ob_low = candles[18][2]
ob_high = candles[18][1]

d.line([(PAD_L, to_y(pdh)), (W - PAD_R, to_y(pdh))], fill=(255, 200, 0), width=1)
d.text((PAD_L + 4, to_y(pdh) - 14), "PDH", fill=(255, 200, 0))

d.line([(PAD_L, to_y(pdl)), (W - PAD_R, to_y(pdl))], fill=(255, 150, 0), width=1)
d.text((PAD_L + 4, to_y(pdl) + 2), "PDL", fill=(255, 150, 0))

fvg_y_top = min(to_y(fvg_high), to_y(fvg_low))
fvg_y_bot = max(to_y(fvg_high), to_y(fvg_low))
d.rectangle([(to_x(20), fvg_y_top), (to_x(22), fvg_y_bot)],
            outline=(38, 166, 154), width=1)
d.text((to_x(20) + 2, fvg_y_top - 14), "Bullish FVG", fill=UP)

ob_y_top = min(to_y(ob_high), to_y(ob_low))
ob_y_bot = max(to_y(ob_high), to_y(ob_low))
d.rectangle([(to_x(18) - cw, ob_y_top), (to_x(20), ob_y_bot)],
            outline=(180, 100, 255), width=1)
d.text((to_x(18) - cw, ob_y_bot + 2), "Bull OB", fill=(180, 100, 255))

# BSL
bsl_y = to_y(max(c[1] for c in candles[30:40]))
d.line([(to_x(30), bsl_y), (to_x(40), bsl_y)], fill=(100, 200, 255), width=1)
d.text((to_x(35), bsl_y - 14), "BSL (equal highs)", fill=(100, 200, 255))

# Title
d.text((PAD_L, 10), "EUR/USD  1H  —  ICT Sample Chart", fill=WHITE)
d.text((W - 200, 10), f"Price: {candles[-1][3]:.5f}", fill=WHITE)

out = "/Users/user/ict-trading-analyst/sample_eurusd_1h.png"
img.save(out)
print(f"Saved: {out}")
