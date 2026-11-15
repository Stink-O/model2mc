"""model2mc logo: an isometric block (yellow top, stone sides) and a pixel wordmark with the "2" in yellow.
Colours from the Tripo-to-Minecraft key art (#F9CF00, #101114, #FAFAFA, the giant's stone greys).
Usage: python docs/branding/make_logo.py docs/branding"""
import math
import sys
from pathlib import Path

YELLOW = "#F9CF00"
INK = {"light": "#101114", "dark": "#FAFAFA"}
SIDE_L = {"light": "#6E7178", "dark": "#8A8E96"}
SIDE_R = {"light": "#45484E", "dark": "#5C6067"}

G = {
    "m": ["#####", "#.#.#", "#.#.#", "#.#.#", "#.#.#"],
    "o": [".###.", "#...#", "#...#", "#...#", ".###."],
    "d": ["....#", "....#", ".####", "#...#", "#...#", "#...#", ".####"],
    "e": [".###.", "#...#", "#####", "#....", ".####"],
    "l": ["##.", ".#.", ".#.", ".#.", ".#.", ".#.", ".##"],
    "2": [".###.", "#...#", "....#", "..##.", ".#...", "#....", "#####"],
    "c": [".####", "#....", "#....", "#....", ".####"],
}


def outline(cells, x0, y0, s):
    """SVG path for the union of unit cells (set of (col,row)), traced as closed loops (no seams)."""
    edges = {}
    for c, r in cells:
        for a, b, n in (((c, r), (c + 1, r), (c, r - 1)), ((c + 1, r), (c + 1, r + 1), (c + 1, r)),
                        ((c + 1, r + 1), (c, r + 1), (c, r + 1)), ((c, r + 1), (c, r), (c - 1, r))):
            if n not in cells:
                edges.setdefault(a, []).append(b)
    d = []
    while edges:
        start = next(iter(edges))
        loop = [start]
        p = start
        while True:
            q = edges[p].pop()
            if not edges[p]:
                del edges[p]
            if q == start:
                break
            loop.append(q)
            p = q
        # drop collinear points
        pts = [loop[i] for i in range(len(loop))
               if (loop[i - 1][0] - loop[i][0]) * (loop[(i + 1) % len(loop)][1] - loop[i][1])
               != (loop[i - 1][1] - loop[i][1]) * (loop[(i + 1) % len(loop)][0] - loop[i][0])]
        d.append("M" + "L".join(f"{x0 + x * s:g},{y0 + y * s:g}" for x, y in pts) + "Z")
    return "".join(d)


def wordmark(x0, s, ink):
    ink_cells, acc_cells = set(), set()
    x = 0
    for ch in "model2mc":
        g = G[ch]
        top = 7 - len(g)
        for r, row in enumerate(g):
            for c, v in enumerate(row):
                if v == "#":
                    (acc_cells if ch == "2" else ink_cells).add((x + c, top + r))
        x += len(g[0]) + 1
    w = (x - 1) * s
    svg = (f'<path fill="{ink}" d="{outline(ink_cells, x0, 0, s)}"/>'
           f'<path fill="{YELLOW}" d="{outline(acc_cells, x0, 0, s)}"/>')
    return w, svg


def block(cx, cy, e, mode):
    k = e * math.cos(math.radians(30))
    h = e / 2
    f = lambda pts: " ".join(f"{x:.2f},{y:.2f}" for x, y in pts)
    top = [(cx, cy - e), (cx + k, cy - h), (cx, cy), (cx - k, cy - h)]
    left = [(cx - k, cy - h), (cx, cy), (cx, cy + e), (cx - k, cy + h)]
    right = [(cx, cy), (cx + k, cy - h), (cx + k, cy + h), (cx, cy + e)]
    return (f'<polygon fill="{SIDE_L[mode]}" points="{f(left)}"/>'
            f'<polygon fill="{SIDE_R[mode]}" points="{f(right)}"/>'
            f'<polygon fill="{YELLOW}" points="{f(top)}"/>')


def logo(mode, s=20):
    H = 7 * s                  # wordmark height, 140
    e = H / 2 + 2              # block edge so the hexagon matches the text height
    k = e * math.cos(math.radians(30))
    icon = block(k, H / 2, e, mode)
    tx = 2 * k + 2.2 * s
    w, word = wordmark(tx, s, INK[mode])
    W = tx + w
    y0 = H / 2 - e
    VH = 2 * e
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 {y0:g} {W:g} {VH:g}" width="{W:g}" height="{VH:g}">'
            f'<title>model2mc</title>{icon}{word}</svg>\n')


def icon_only(mode, pad=0.08):
    e = 100
    k = e * math.cos(math.radians(30))
    S = 2 * e * (1 + 2 * pad)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {S:g} {S:g}" width="{S:g}" height="{S:g}">'
            f'<title>model2mc</title>{block(S / 2, S / 2, e, mode)}</svg>\n')


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for mode in ("light", "dark"):
        (out / f"model2mc-logo-{mode}.svg").write_text(logo(mode))
    (out / "model2mc-icon.svg").write_text(icon_only("dark"))
