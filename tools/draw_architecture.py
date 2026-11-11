"""Architecture diagram with product icons (icons from the `diagrams` package wheel; no Graphviz needed)."""
from PIL import Image, ImageDraw, ImageFont
import sys
R = sys.argv[1] + "/resources/"
OUT = sys.argv[2]  # usage: python tools/draw_architecture.py <unzipped diagrams wheel> <out.png>
S = 2  # supersample
W, H = 1100 * S, 1290 * S
im = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(im)
B = lambda n: ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf", n * S)
Rg = lambda n: ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf", n * S)
INK, MUTED, LINE, BORDER = (30, 34, 42), (95, 102, 115), (90, 96, 110), (205, 210, 220)

def doc_icon(size):
    ic = Image.new("RGBA", (size, size), (0, 0, 0, 0)); g = ImageDraw.Draw(ic)
    for k, off in enumerate((18, 9, 0)):
        x0 = size * 0.18 + off * S; y0 = size * 0.08 + off * S
        g.rounded_rectangle((x0, y0, x0 + size * 0.55, y0 + size * 0.75), 6 * S, fill=(255, 255, 255), outline=(60, 110, 200), width=3 * S)
        for j in range(4):
            yy = y0 + size * (0.2 + j * 0.13)
            g.line((x0 + size * 0.08, yy, x0 + size * 0.45, yy), fill=(150, 170, 210), width=3 * S)
    return ic

nodes = {}
def node(key, cx, cy, icons, title, sub, w=300):
    w *= S; h = 132 * S; cx *= S; cy *= S
    box = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
    d.rounded_rectangle(box, 14 * S, fill=(250, 251, 253), outline=BORDER, width=2 * S)
    size = 54 * S
    total = len(icons) * size + (len(icons) - 1) * 8 * S
    x = cx - total / 2
    for p in icons:
        ic = doc_icon(size) if p == "DOC" else Image.open(R + p).convert("RGBA")
        if p != "DOC":
            ic.thumbnail((size, size), Image.LANCZOS)
        im.paste(ic, (int(x + (size - ic.width) / 2), int(box[1] + 10 * S + (size - ic.height) / 2)), ic)
        x += size + 8 * S
    d.text((cx, box[1] + 76 * S), title, font=B(19), fill=INK, anchor="mm")
    d.text((cx, box[1] + 104 * S), sub, font=Rg(15), fill=MUTED, anchor="mm")
    nodes[key] = box

def arrow(p, q, label=None, dashed=False, lpos=0.5, lside="right"):
    pts = [(x * S, y * S) for x, y in [p] + ([] if not isinstance(q, list) else q[:-1]) + [q[-1] if isinstance(q, list) else q]]
    for a, b in zip(pts, pts[1:]):
        if dashed:
            import math
            L = math.dist(a, b); n = int(L / (12 * S))
            for i in range(0, n, 2):
                t0, t1 = i / n, min((i + 1) / n, 1)
                d.line((a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0, a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1), fill=LINE, width=3 * S)
        else:
            d.line((a, b), fill=LINE, width=3 * S)
    (x1, y1), (x2, y2) = pts[-2], pts[-1]
    import math
    ang = math.atan2(y2 - y1, x2 - x1); L = 14 * S
    d.polygon([(x2, y2), (x2 - L * math.cos(ang - 0.4), y2 - L * math.sin(ang - 0.4)), (x2 - L * math.cos(ang + 0.4), y2 - L * math.sin(ang + 0.4))], fill=LINE)
    if label:
        a, b = pts[len(pts) // 2 - 1], pts[len(pts) // 2]
        mx, my = a[0] + (b[0] - a[0]) * lpos, a[1] + (b[1] - a[1]) * lpos
        f = Rg(15); tw = d.textlength(label, font=f)
        x = mx + 10 * S if lside == "right" else mx - 10 * S - tw
        d.rectangle((x - 4 * S, my - 12 * S, x + tw + 4 * S, my + 12 * S), fill="white")
        d.text((x, my), label, font=f, fill=(40, 90, 170), anchor="lm")

node("in", 390, 80, ["DOC"], "Letters & scans", "real mail + synthetic letters", 290)
node("ai", 760, 80, ["azure/aimachinelearning/ai-studio.png"], "Azure AI Foundry", "writes synthetic wording", 290)
node("blob", 550, 260, ["azure/storage/blob-storage.png"], "Azure Blob Storage", "files stored by content hash")
node("di", 550, 440, ["azure/aimachinelearning/form-recognizers.png"], "AI Document Intelligence", "custom model: values + confidence", 340)
node("mon", 940, 440, ["azure/analytics/log-analytics-workspaces.png", "azure/devops/application-insights.png"], "Log Analytics + App Insights", "logs + review-app usage", 280)
node("py", 550, 620, ["programming/language/python.png"], "Python checks", "one schema · range + ratio rules")
node("lang", 175, 830, ["azure/aimachinelearning/language.png"], "AI Language", "remove personal info", 270)
node("pa", 560, 830, ["azure/integration/power-platform.png"], "Power Apps review", "accept · correct · not on doc", 300)
node("dbx", 940, 830, ["onprem/analytics/databricks.png", "onprem/mlops/mlflow.png"], "Databricks + MLflow", "bronze, silver, gold layers", 280)
node("sql", 560, 1030, ["azure/database/sql-databases.png"], "Azure SQL", "clean values + full history")
node("pbi", 560, 1210, ["onprem/analytics/powerbi.png"], "Power BI", "homeowner · operations · finance")

arrow((390, 146), [(470, 194)])
arrow((760, 146), [(630, 194)], "synthetic", lside="right")
arrow((550, 326), [(550, 374)])
arrow((550, 506), [(550, 554)])
arrow((720, 440), [(800, 440)], dashed=True)
arrow((470, 686), [(175, 764)])
arrow((620, 686), [(620, 764)], "unsure: to a person", lside="left")
arrow((630, 686), [(940, 764)])
arrow((450, 686), [(370, 740), (370, 1000), (410, 1000)])
d.text((380*S, 935*S), "confident", font=Rg(15), fill=(40, 90, 170), anchor="lm")
d.text((380*S, 955*S), "+ valid", font=Rg(15), fill=(40, 90, 170), anchor="lm")
arrow((560, 896), [(560, 964)])
arrow((560, 1096), [(560, 1144)])
im = im.resize((W // S, H // S), Image.LANCZOS)
im.save(OUT)
