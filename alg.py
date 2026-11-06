#!/usr/bin/env python3
"""ピラミンクスの手順から、その手順で揃う配置の SVG を作る (単体出力 / Markdown 一括置換)。

使い方:
  # 1枚の SVG を出力
  python pyraminx.py svg "R U R' U'" -o out.svg [--scramble] [--bg "#f3d3a8"] [--width 420] [--caption]

  # Markdown 中の手順を画像に置き換える
  python pyraminx.py md note.md [more.md ...] [--scramble] [--bg "#f3d3a8"] [--fg white] [--width 100]

記法:
  U L R B = その頂点を含む層 (頂点側から見て時計回り)、' = 反時計回り、2 = 2回 (R2 = R R、R2' = R' R')
  小文字 u l r b = 頂点のチップだけ
  S = R' L R L'、H = L R' L' R に展開される (S' H' は逆手順)
視点: U 頂点を真上から見た図 (中央が U、上が B、左下が L、右下が R)
色:   F(U-L-R)=緑、L(U-L-B)=赤、R(U-R-B)=青、D(L-R-B)=黄

md サブコマンドの対象:
  - 表のセルが `...` だけのもの
  - 行全体が `R U R' U'` のようなインラインコードだけの行
  - 中身が手順だけのコードブロック (```)
  展開後2手以上の手順のみ対象 (`R` 単体などは対象外。`R2` は2手なので対象)。
  対象を、ピラミンクスの下に手順が中央揃えで入った SVG (![手順](imgs/pyraminx-<hash>.svg)) に置換する。
  画像は Markdown と同じ階層の imgs/ に保存。手順は alt に残るので何度実行しても増えず、
  alt を書き換えて再実行すれば画像も更新される。
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import re
import sys
from pathlib import Path
from typing import NamedTuple

# ============================================================
# 手順の記法 (パース / 展開)
# ============================================================

# 1手の記法: R, R', R2, R2', R'2 / S, S', H, H'  (S, H に 2 は付けない)
TOKEN = r"(?:[URLBurlb](?:2['’]?|['’]2?)?|[SH]['’]?)"
ALG_RE = re.compile(rf"\s*(?:{TOKEN}\s*)*")
TOKEN_RE = re.compile(TOKEN)

MACROS = {"S": "R' L R L'", "H": "L R' L' R"}

Move = tuple[str, bool]  # (層名, 反時計回りか)


class Token(NamedTuple):
    letter: str
    double: bool = False
    prime: bool = False

    def __str__(self) -> str:
        return self.letter + ("2" if self.double else "") + ("'" if self.prime else "")


def tokenize(text: str) -> list[Token]:
    """文字列を記法の単位に分ける。使えない文字があれば ValueError。"""
    if not ALG_RE.fullmatch(text):
        raise ValueError("手順に使えない文字があります: " + text)
    return [
        Token(t[0], "2" in t, "'" in t or "’" in t)
        for t in TOKEN_RE.findall(text)
    ]


def invert(moves: list[Move]) -> list[Move]:
    return [(n, not p) for n, p in reversed(moves)]


def expand(tokens: list[Token]) -> list[Move]:
    """S, H などの略記と 2 回転を基本手順 (1回転ずつ) に展開する。"""
    out: list[Move] = []
    for t in tokens:
        if t.letter in MACROS:
            seq = expand(tokenize(MACROS[t.letter]))
            out.extend(invert(seq) if t.prime else seq)
        else:
            out.extend([(t.letter, t.prime)] * (2 if t.double else 1))
    return out


def parse(text: str) -> list[Move]:
    return expand(tokenize(text))


def normalize(text: str) -> str | None:
    """手順なら正規化した文字列 ("R U R' U'" 形式)、対象外なら None。"""
    s = text.strip()
    if len(s) >= 2 and s[0] == s[-1] == "`":
        s = s.strip("`").strip()
    try:
        tokens = tokenize(s)
    except ValueError:
        return None
    if len(expand(tokens)) < 2:  # 展開後2手未満は対象外 (S, H 単体は展開すると4手)
        return None
    return " ".join(map(str, tokens))


# ============================================================
# ピラミンクスのモデル
# ============================================================

NAMES = "ULRB"
P = [(0, 1, 0), (-0.8165, -1 / 3, 0.4714), (0.8165, -1 / 3, 0.4714), (0, -1 / 3, -0.9428)]
FACES = {
    "F": ((0, 1, 2), "#3de03d"),
    "L": ((0, 1, 3), "#e8402a"),
    "R": ((0, 2, 3), "#1f2fe0"),
    "D": ((1, 2, 3), "#fff23a"),
}
# 描画: 中央 U、上 B、左下 L、右下 R
SCREEN = {0: (150, 156), 1: (27, 227), 2: (273, 227), 3: (150, 14)}
VIEW = ["L", "R", "F"]

State = dict[tuple[int, ...], str]  # ステッカー位置 (4頂点に対する重み) -> 色


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


@functools.cache
def subtriangles():
    """1面を9個の小三角形に分割。各頂点は面の3頂点に対する重み (合計3)。"""
    def g(i, j):
        return (3 - i - j, i, j)

    out = []
    for i in range(3):
        for j in range(3 - i):
            out.append((g(i, j), g(i + 1, j), g(i, j + 1)))
    for i in range(2):
        for j in range(2 - i):
            out.append((g(i + 1, j), g(i, j + 1), g(i + 1, j + 1)))
    return out


def slot_key(fv, tri):
    """ステッカー位置を、4頂点に対する重み (合計9) で表す。"""
    w = [0] * 4
    for t in tri:
        for k, vi in enumerate(fv):
            w[vi] += t[k]
    return tuple(w)


def solved_state() -> State:
    return {
        slot_key(fv, tri): color
        for fv, color in FACES.values()
        for tri in subtriangles()
    }


@functools.cache
def cw_map(v: int) -> dict[int, int]:
    """頂点 v から見て時計回りに、他の3頂点がどう入れ替わるか。"""
    o = [i for i in range(4) if i != v]
    a, b, c = (P[i] for i in o)
    n = _cross(_sub(b, a), _sub(c, a))
    cen = tuple(sum(x) / 3 for x in zip(a, b, c))
    if _dot(n, _sub(P[v], cen)) > 0:  # o[0]->o[1]->o[2] が反時計回り
        return {o[0]: o[2], o[2]: o[1], o[1]: o[0]}
    return {o[0]: o[1], o[1]: o[2], o[2]: o[0]}


def apply_move(st: State, name: str, prime: bool) -> State:
    v = NAMES.index(name.upper())
    threshold = 6 if name.islower() else 3  # チップのみ / チップ+中層
    m = cw_map(v)
    if prime:
        m = {b: a for a, b in m.items()}
    new: State = {}
    for pos, col in st.items():
        if pos[v] > threshold:
            q = list(pos)
            for a, b in m.items():
                q[b] = pos[a]
            pos = tuple(q)
        new[pos] = col
    return new


def arrangement(alg: str, scramble: bool = False) -> State:
    """scramble=False: この手順で揃う配置 / True: 揃った状態に手順を適用した配置。"""
    moves = parse(alg)
    if not scramble:
        moves = invert(moves)
    st = solved_state()
    for name, prime in moves:
        st = apply_move(st, name, prime)
    return st


# ============================================================
# SVG 描画
# ============================================================

STROKE = 1.8  # 黒線の太さ (全ての線で共通)
CAPTION_HEIGHT = 44


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(st: State, bg: str = "none", width: int = 420,
           caption: str | None = None, fg: str = "#000") -> str:
    """caption を渡すと、ピラミンクスの下に中央揃えで手順の文字列を入れる。"""
    def pts(tri, fv):
        return [
            (sum(t[k] / 3 * SCREEN[fv[k]][0] for k in range(3)),
             sum(t[k] / 3 * SCREEN[fv[k]][1] for k in range(3)))
            for t in tri
        ]

    def fmt(ps):
        return " ".join(f"{x:.2f},{y:.2f}" for x, y in ps)

    height = 250 + (CAPTION_HEIGHT if caption else 0)
    px_h = round(width * height / 300)
    poly = f'stroke="#000" stroke-width="{STROKE}" stroke-linejoin="round"'
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 {height}" '
           f'width="{width}" height="{px_h}">']
    if bg != "none":
        out.append(f'<rect width="300" height="{height}" fill="{bg}"/>')
    for f in VIEW:
        fv = FACES[f][0]
        for tri in subtriangles():
            out.append(f'<polygon points="{fmt(pts(tri, fv))}" '
                       f'fill="{st[slot_key(fv, tri)]}" {poly}/>')
    for f in VIEW:
        fv = FACES[f][0]
        out.append(f'<polygon points="{fmt([SCREEN[i] for i in fv])}" fill="none" {poly}/>')
    if caption:
        size = min(22, 280 / (len(caption) * 0.62))
        out.append(f'<text x="150" y="{250 + 28}" text-anchor="middle" font-size="{size:.1f}" '
                   f'fill="{fg}" font-family="ui-monospace, Menlo, Consolas, monospace">'
                   f'{_esc(caption)}</text>')
    out.append("</svg>")
    return "\n".join(out)


# ============================================================
# Markdown 変換
# ============================================================

IMG_RE = re.compile(r"^(\s*)!\[([^\]]*)\]\(imgs/pyraminx-[0-9a-f]{10}\.svg\)\s*$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
INLINE_RE = re.compile(r"\s*`([^`]+)`\s*")


class Style(NamedTuple):
    scramble: bool = False
    bg: str = "none"
    width: int = 100
    fg: str = "white"


def alg_in(text: str) -> str | None:
    """`手順` のインラインコード、または生成済み画像 (alt が手順) から手順を取り出す。"""
    if m := INLINE_RE.fullmatch(text):
        return normalize(m.group(1))
    if m := IMG_RE.match(text):
        return normalize(m.group(2))
    return None


class MarkdownConverter:
    def __init__(self, imgdir: Path, style: Style):
        self.imgdir = imgdir
        self.style = style

    def make_svg(self, alg: str) -> str:
        """SVG を (内容が変わったときだけ) 保存し、ファイル名を返す。"""
        s = self.style
        key = f"{s.scramble}|{s.bg}|{s.width}|{s.fg}|{alg}"
        name = "pyraminx-" + hashlib.sha1(key.encode()).hexdigest()[:10] + ".svg"
        svg = render(arrangement(alg, s.scramble), s.bg, s.width, caption=alg, fg=s.fg)
        path = self.imgdir / name
        old = path.read_text(encoding="utf-8") if path.exists() else None
        if old != svg:
            self.imgdir.mkdir(parents=True, exist_ok=True)
            path.write_text(svg, encoding="utf-8")
        return name

    def image(self, alg: str) -> str:
        return f"![{alg}](imgs/{self.make_svg(alg)})"

    def convert(self, text: str) -> str:
        lines = text.split("\n")
        out: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if fm := FENCE_RE.match(line):
                i = self._fence(lines, i, fm.group(1), out)
            elif line.lstrip().startswith("|"):
                out.append(self._table_row(line))
                i += 1
            else:
                if alg := alg_in(line):
                    self._emit(out, line, alg)
                else:
                    out.append(line)
                i += 1
        return "\n".join(out)

    def _fence(self, lines: list[str], start: int, fence: str, out: list[str]) -> int:
        """コードブロックを処理し、次に読む行番号を返す。"""
        end = start + 1
        while end < len(lines):
            s = lines[end].strip()
            if s.startswith(fence[0] * len(fence)) and not s.strip(fence[0]):
                break
            end += 1
        body = " ".join(lines[start + 1:end])
        alg = normalize(body) if body.strip() else None
        if alg:
            self._emit(out, lines[start], alg)
        else:
            out.extend(lines[start:end + 1])
        return end + 1

    def _table_row(self, line: str) -> str:
        cells = line.split("|")
        for i, cell in enumerate(cells):
            if alg := alg_in(cell):
                cells[i] = f" {self.image(alg)} "
        return "|".join(cells)

    def _emit(self, out: list[str], first_line: str, alg: str) -> None:
        indent = re.match(r"\s*", first_line).group(0)
        # 旧形式 (画像+コード) からの移行: 直前に同じ手順の画像があれば取り除く
        k = len(out) - 1
        while k >= 0 and not out[k].strip():
            k -= 1
        if k >= 0 and (pm := IMG_RE.match(out[k])) and normalize(pm.group(2)) == alg:
            del out[k:]
        out.append(indent + self.image(alg))


# ============================================================
# CLI
# ============================================================

def cmd_svg(args) -> None:
    st = arrangement(args.alg, args.scramble)
    caption = normalize(args.alg) or args.alg if args.caption else None
    Path(args.output).write_text(
        render(st, args.bg, args.width, caption=caption, fg=args.fg), encoding="utf-8")
    print("wrote", args.output, file=sys.stderr)


def cmd_md(args) -> None:
    style = Style(args.scramble, args.bg, args.width, args.fg)
    for name in args.files:
        path = Path(name)
        text = path.read_text(encoding="utf-8")
        new = MarkdownConverter(path.resolve().parent / "imgs", style).convert(text)
        if new != text:
            path.write_text(new, encoding="utf-8")
            print("updated", path, file=sys.stderr)
        else:
            print("unchanged", path, file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p, width, fg):
        p.add_argument("--scramble", action="store_true", help="手順を揃った状態にそのまま適用する")
        p.add_argument("--bg", default="none", help="背景色 (既定: 透明)")
        p.add_argument("--width", type=int, default=width)
        p.add_argument("--fg", default=fg, help=f"手順の文字色 (既定: {fg})")

    p = sub.add_parser("svg", help="手順から SVG を1枚出力")
    p.add_argument("alg", help="手順 (例: \"R U R' U'\")")
    p.add_argument("-o", "--output", default="pyraminx.svg")
    p.add_argument("--caption", action="store_true", help="下に手順の文字列を入れる")
    common(p, width=420, fg="#000")
    p.set_defaults(func=cmd_svg)

    p = sub.add_parser("md", help="Markdown 中の手順を画像に置換")
    p.add_argument("files", nargs="+")
    common(p, width=100, fg="white")
    p.set_defaults(func=cmd_md)
    return ap


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except ValueError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()