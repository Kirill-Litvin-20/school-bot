#!/usr/bin/env python3
from PIL import Image, ImageDraw, ImageFont
import os

# Canvas size matching the original (square-ish, social media friendly)
W, H = 1080, 1080
BRAND_BLUE = (45, 41, 180)       # dark navy/indigo for text
BRAND_INDIGO = (67, 56, 202)     # the purple-blue for logo/accent
ACCENT_LINE = (99, 84, 242)      # bright indigo for top border
BG = (246, 246, 252)             # very light lavender-grey background
BADGE_BG = (232, 228, 255)       # soft lavender for badge
BADGE_TEXT = (80, 70, 200)
DOT_COLOR = (99, 84, 242)
GRAY_TEXT = (110, 110, 140)
WHITE = (255, 255, 255)

# Fonts
FONT_DIR = "/System/Library/Fonts"
try:
    font_bold = ImageFont.truetype(f"{FONT_DIR}/HelveticaNeue.ttc", 72, index=1)
    font_semibold = ImageFont.truetype(f"{FONT_DIR}/HelveticaNeue.ttc", 44, index=1)
    font_body = ImageFont.truetype(f"{FONT_DIR}/HelveticaNeue.ttc", 38, index=0)
    font_badge = ImageFont.truetype(f"{FONT_DIR}/HelveticaNeue.ttc", 32, index=1)
    font_handle = ImageFont.truetype(f"{FONT_DIR}/HelveticaNeue.ttc", 34, index=1)
    font_logo = ImageFont.truetype(f"{FONT_DIR}/HelveticaNeue.ttc", 36, index=1)
    font_sub = ImageFont.truetype(f"{FONT_DIR}/HelveticaNeue.ttc", 20, index=0)
    font_emoji = ImageFont.truetype("/System/Library/Fonts/Apple Color Emoji.ttc", 64)
except Exception as e:
    print(e)
    font_bold = ImageFont.load_default()
    font_semibold = font_bold
    font_body = font_bold
    font_badge = font_bold
    font_handle = font_bold
    font_logo = font_bold
    font_sub = font_bold
    font_emoji = font_bold

img = Image.new("RGB", (W, H), WHITE)
draw = ImageDraw.Draw(img)

# Card background with rounded corners via paste
MARGIN = 40
RADIUS = 48
card = Image.new("RGB", (W - MARGIN*2, H - MARGIN*2), BG)
card_draw = ImageDraw.Draw(card)

def rounded_rect(d, xy, r, fill):
    x0, y0, x1, y1 = xy
    d.rectangle([x0+r, y0, x1-r, y1], fill=fill)
    d.rectangle([x0, y0+r, x1, y1-r], fill=fill)
    d.ellipse([x0, y0, x0+r*2, y0+r*2], fill=fill)
    d.ellipse([x1-r*2, y0, x1, y0+r*2], fill=fill)
    d.ellipse([x0, y1-r*2, x0+r*2, y1], fill=fill)
    d.ellipse([x1-r*2, y1-r*2, x1, y1], fill=fill)

CW = W - MARGIN*2
CH = H - MARGIN*2
rounded_rect(card_draw, (0, 0, CW-1, CH-1), RADIUS, BG)

# Top indigo border stripe (draw full-width stripe, then restore corners with BG)
card_draw.rectangle([0, 0, CW, 10], fill=ACCENT_LINE)

# Paste card onto white background
img.paste(card, (MARGIN, MARGIN))
draw = ImageDraw.Draw(img)

PAD = MARGIN + 60  # left content padding

# ── Logo area ──────────────────────────────────────────────────────────
# Circle icon
LOGO_X, LOGO_Y = PAD, MARGIN + 55
R_LOGO = 38
draw.ellipse([LOGO_X, LOGO_Y, LOGO_X + R_LOGO*2, LOGO_Y + R_LOGO*2],
             outline=BRAND_INDIGO, width=4)
# "И" letter in circle
draw.text((LOGO_X + R_LOGO - 10, LOGO_Y + R_LOGO - 16), "И",
          font=font_badge, fill=BRAND_INDIGO)

# Logo text
draw.text((LOGO_X + R_LOGO*2 + 16, LOGO_Y + 4), "ИНТЕГРАЛ",
          font=font_logo, fill=BRAND_INDIGO)
draw.text((LOGO_X + R_LOGO*2 + 16, LOGO_Y + 42), "онлайн школа",
          font=font_sub, fill=GRAY_TEXT)

# ── Badge "ШКОЛА ИНТЕГРАЛ" ─────────────────────────────────────────────
BADGE_Y = MARGIN + 175
badge_text = "ШКОЛА ИНТЕГРАЛ"
bbox = draw.textbbox((0, 0), badge_text, font=font_badge)
bw = bbox[2] - bbox[0] + 44
bh = bbox[3] - bbox[1] + 20
rounded_rect(draw, (PAD, BADGE_Y, PAD + bw, BADGE_Y + bh), 18, BADGE_BG)
draw.text((PAD + 22, BADGE_Y + 10), badge_text, font=font_badge, fill=BADGE_TEXT)

# ── Gift icon + main heading ───────────────────────────────────────────
HEADING_Y = BADGE_Y + bh + 40
draw.text((PAD, HEADING_Y), "🎁", font=font_emoji, embedded_color=True)

HEADING_Y2 = HEADING_Y + 82
draw.text((PAD, HEADING_Y2), "Бесплатная", font=font_bold, fill=BRAND_BLUE)
HEADING_Y3 = HEADING_Y2 + 80
draw.text((PAD, HEADING_Y3), "диагностика", font=font_bold, fill=BRAND_BLUE)
HEADING_Y4 = HEADING_Y3 + 80
draw.text((PAD, HEADING_Y4), "для новых учеников", font=font_semibold, fill=BRAND_BLUE)

# ── Body bullets ──────────────────────────────────────────────────────
BODY_Y = HEADING_Y4 + 80
bullets = [
    "смотрим текущий уровень",
    "находим слабые темы",
    "подсказываем подходящий формат",
    "подбираем преподавателя",
]
LINE_H = 54
for i, line in enumerate(bullets):
    y = BODY_Y + i * LINE_H
    # bullet dot
    draw.ellipse([PAD, y + 14, PAD + 14, y + 28], fill=GRAY_TEXT)
    draw.text((PAD + 26, y), line, font=font_body, fill=GRAY_TEXT)

# ── Bot handle pill ───────────────────────────────────────────────────
HANDLE_Y = BODY_Y + len(bullets) * LINE_H + 50
handle_text = "  @integral_school_ru_bot"
hbbox = draw.textbbox((0, 0), handle_text, font=font_handle)
hw = hbbox[2] - hbbox[0] + 44
hh = hbbox[3] - hbbox[1] + 22
rounded_rect(draw, (PAD, HANDLE_Y, PAD + hw, HANDLE_Y + hh), 22, BADGE_BG)
# dot before handle
dot_x = PAD + 18
dot_y = HANDLE_Y + hh // 2
draw.ellipse([dot_x - 6, dot_y - 6, dot_x + 6, dot_y + 6], fill=DOT_COLOR)
draw.text((PAD + 34, HANDLE_Y + 11), "@integral_school_ru_bot",
          font=font_handle, fill=BADGE_TEXT)

# Save
out = os.path.join(os.path.dirname(__file__), "diagnostic_preview.png")
img.save(out, "PNG")
print(f"Saved: {out}")
