#!/usr/bin/env python3
"""SVG -> drawio (.drawio) 変換ツール.

使い方:
    python tools/svg_to_drawio.py input.svg [-o output.drawio] [--samples 16]

標準ライブラリのみで動作する単発スクリプト。SVG の rect/circle/ellipse/line/
polyline/polygon/text/path/g を draw.io の mxCell に変換し、draw.io デスクトップで
そのまま開ける .drawio ファイルを書き出す。
"""

from __future__ import (
    annotations,  # 型ヒントの前方参照を有効にする（Python 3.7 以降で文字列化不要）
)

import argparse  # コマンドライン引数のパースに使用
import base64  # stencil エンコードの base64 変換に使用
import math  # 三角関数・平方根などの数学関数に使用
import re  # 正規表現による文字列マッチングに使用
import sys  # 標準エラー出力・終了コードに使用
import uuid  # draw.io ダイアグラム ID のランダム生成に使用
import zlib  # stencil エンコードの raw deflate 圧縮に使用
from pathlib import Path  # ファイルパスのオブジェクト操作に使用
from xml.etree import ElementTree as ET  # XML の読み書きに使用


def warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)  # 警告メッセージを標準エラーに出力する


def local_tag(tag: str) -> str:
    # ElementTree は名前空間付きタグを "{namespace}localname" 形式で返すため、localname だけを取り出す
    return (
        tag.split("}", 1)[1] if "}" in tag else tag
    )  # "}" があれば後半を、なければそのまま返す


def strip_unit(s) -> float:
    if s is None:  # None の場合は 0.0 を返す
        return 0.0
    # SVG 属性値に含まれる px/em/% などの単位を除去して数値のみ返す
    m = re.match(
        r"^\s*([-+]?\d+\.?\d*(?:[eE][-+]?\d+)?)\s*[a-zA-Z%]*\s*$", str(s)
    )  # 先頭の数値部分にマッチ
    return (
        float(m.group(1)) if m else 0.0
    )  # マッチした数値を float に変換、失敗なら 0.0


def fmt(n: float) -> str:
    if n == int(n):  # 整数値と等しい場合は小数点なしで返す
        return str(int(n))
    # 末尾のゼロを除去して .drawio XML のサイズを削減する
    return f"{n:.4f}".rstrip("0").rstrip(
        "."
    )  # 小数4桁まで表示し、末尾の "0" と "." を除去


# ----------------------------------------------------------------------------
# 色
# ----------------------------------------------------------------------------

# SVG の名前付き色キーワードを16進数カラーコードにマッピングするテーブル
NAMED_COLORS = {
    "black": "#000000",
    "white": "#ffffff",
    "red": "#ff0000",
    "green": "#008000",
    "blue": "#0000ff",
    "yellow": "#ffff00",
    "cyan": "#00ffff",
    "magenta": "#ff00ff",
    "gray": "#808080",
    "grey": "#808080",
    "silver": "#c0c0c0",
    "maroon": "#800000",
    "olive": "#808000",
    "lime": "#00ff00",
    "aqua": "#00ffff",
    "teal": "#008080",
    "navy": "#000080",
    "fuchsia": "#ff00ff",
    "purple": "#800080",
    "orange": "#ffa500",
    "brown": "#a52a2a",
    "pink": "#ffc0cb",
    "gold": "#ffd700",
    "transparent": "none",
    "none": "none",  # SVG の "none" はそのまま "none" として扱う
}


def normalize_color(c):
    if c is None:  # None はそのまま None を返す（属性未設定を意味する）
        return None
    c = str(c).strip().lower()  # 前後の空白を除去し、小文字に統一する
    if not c:  # 空文字列の場合は None を返す
        return None
    if c == "none":  # 明示的な "none"（塗りなし・線なし）を返す
        return "none"
    if c.startswith("#"):  # # で始まる16進数カラーコードの処理
        if len(c) == 4:
            # #RGB → #RRGGBB 短縮形を展開
            return "#" + "".join(
                ch * 2 for ch in c[1:]
            )  # 各桁を2文字に繰り返して6桁に変換
        if len(c) == 7:  # 既に #RRGGBB 形式であればそのまま返す
            return c
    m = re.match(
        r"rgb\s*\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*\)", c
    )  # rgb(r,g,b) 形式にマッチ
    if m:
        # R・G・B それぞれを 0〜255 にクランプして2桁の16進数に変換し連結する
        return "#" + "".join(
            f"{max(0, min(255, int(float(v)))):02x}" for v in m.groups()
        )
    if c in NAMED_COLORS:  # テーブルに登録された名前付き色であれば変換する
        return NAMED_COLORS[c]
    warn(f"unknown color '{c}', using black")  # 未知の色名は警告して黒で代替する
    return "#000000"


# ----------------------------------------------------------------------------
# Transform 2D アフィン
# ----------------------------------------------------------------------------


class Transform:
    """2D アフィン変換 [[a c e],[b d f],[0 0 1]]."""

    __slots__ = ("a", "b", "c", "d", "e", "f")  # メモリ節約のためスロットを定義する

    def __init__(self, a=1.0, b=0.0, c=0.0, d=1.0, e=0.0, f=0.0):
        # 行列の各要素を初期化する（デフォルト値は単位行列）
        self.a = a
        self.b = b
        self.c = c  # a: X スケール・回転, b: Y 方向の回転成分, c: X 方向の回転成分
        self.d = d
        self.e = e
        self.f = f  # d: Y スケール・回転, e: X 平行移動, f: Y 平行移動

    @classmethod
    def identity(cls):
        return cls()  # すべてデフォルト値（単位行列）で生成する

    @classmethod
    def translate(cls, tx, ty=0):
        return cls(1, 0, 0, 1, tx, ty)  # 平行移動のみの変換行列を生成する

    @classmethod
    def scale(cls, sx, sy=None):
        if sy is None:  # sy が省略された場合は sx と同じ値を使い均等スケールにする
            sy = sx
        return cls(sx, 0, 0, sy, 0, 0)  # スケールのみの変換行列を生成する

    @classmethod
    def rotate(cls, deg, cx=0, cy=0):
        rad = math.radians(deg)  # 度数法をラジアンに変換する
        cs = math.cos(rad)
        sn = math.sin(rad)  # cos・sin を計算する
        if cx == 0 and cy == 0:
            return cls(cs, sn, -sn, cs, 0, 0)  # 原点中心の回転行列を生成する
        # 回転中心が原点以外のとき: 中心へ移動 → 回転 → 元に戻す、の合成
        return (
            cls.translate(cx, cy) @ cls(cs, sn, -sn, cs, 0, 0) @ cls.translate(-cx, -cy)
        )

    @classmethod
    def matrix(cls, a, b, c, d, e, f):
        return cls(
            a, b, c, d, e, f
        )  # SVG の matrix(a,b,c,d,e,f) を直接インスタンス化する

    def apply(self, x, y):
        # 点 (x, y) に変換を適用して変換後の座標を返す
        return (
            self.a * x + self.c * y + self.e,  # 変換後の X 座標
            self.b * x + self.d * y + self.f,
        )  # 変換後の Y 座標

    def __matmul__(self, other):
        # self @ other = self の後に other を適用する合成（SVG の transform スタックと一致）
        return Transform(
            self.a * other.a + self.c * other.b,  # 合成行列の a 成分
            self.b * other.a + self.d * other.b,  # 合成行列の b 成分
            self.a * other.c + self.c * other.d,  # 合成行列の c 成分
            self.b * other.c + self.d * other.d,  # 合成行列の d 成分
            self.a * other.e
            + self.c * other.f
            + self.e,  # 合成行列の e（X 平行移動）成分
            self.b * other.e
            + self.d * other.f
            + self.f,  # 合成行列の f（Y 平行移動）成分
        )

    def scale_factor(self):
        sx = math.hypot(self.a, self.b)  # X 軸方向のスケール係数（列ベクトルの長さ）
        sy = math.hypot(self.c, self.d)  # Y 軸方向のスケール係数（列ベクトルの長さ）
        return sx, sy  # (sx, sy) のタプルで返す

    def rotation_deg(self):
        return math.degrees(
            math.atan2(self.b, self.a)
        )  # a・b 成分から回転角を度数法で返す

    def is_axis_aligned(self, eps=1e-6):
        # b=0 かつ c=0 のとき回転もシアーもなく軸平行
        return (
            abs(self.b) < eps and abs(self.c) < eps
        )  # b と c が許容誤差内でゼロなら True


# transform 属性中の "funcname(args)" パターンを抽出する正規表現
_TRANSFORM_RE = re.compile(r"(\w+)\s*\(([^)]*)\)")
# 数値（整数・小数・指数表記）にマッチする正規表現
_NUM_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def parse_transform(s):
    if not s:  # transform 属性がない場合は単位行列を返す
        return Transform.identity()
    result = Transform.identity()  # 結果を単位行列で初期化する
    # SVG は transform を左から右へ記述し、右から順に適用するため右から掛けていく
    for m in _TRANSFORM_RE.finditer(
        s
    ):  # "translate(10,20)" のような各変換関数を順に処理する
        op = m.group(1)  # 関数名（"translate", "scale" など）を取得する
        args = [
            float(x) for x in _NUM_RE.findall(m.group(2))
        ]  # 引数リストを float のリストに変換する
        if op == "translate":
            tx = args[0] if args else 0  # X 方向の移動量（引数がなければ 0）
            ty = args[1] if len(args) > 1 else 0  # Y 方向の移動量（省略時は 0）
            t = Transform.translate(tx, ty)  # 平行移動行列を生成する
        elif op == "scale":
            sx = args[0] if args else 1  # X スケール（引数がなければ 1）
            sy = (
                args[1] if len(args) > 1 else sx
            )  # Y スケール（省略時は X スケールと同値）
            t = Transform.scale(sx, sy)  # スケール行列を生成する
        elif op == "rotate":
            deg = args[0] if args else 0  # 回転角（度数法）
            if len(args) >= 3:
                t = Transform.rotate(
                    deg, args[1], args[2]
                )  # 回転中心 (cx, cy) が指定された回転
            else:
                t = Transform.rotate(deg)  # 原点中心の回転
        elif op == "matrix" and len(args) >= 6:
            t = Transform.matrix(*args[:6])  # 6要素の行列を直接使用する
        elif op in ("skewX", "skewY"):
            warn(
                f"transform '{op}' not supported, ignoring"
            )  # skew は未対応のためスキップ
            continue
        else:
            warn(f"unsupported transform: {op}")  # 未知の変換関数は警告してスキップ
            continue
        result = result @ t  # 右から掛けることで次の変換を後適用として合成する
    return result  # 合成された変換行列を返す


# ----------------------------------------------------------------------------
# style パース
# ----------------------------------------------------------------------------

# draw.io 変換に必要な SVG スタイルプロパティの集合
STYLE_KEYS = {
    "fill",
    "fill-opacity",
    "stroke",
    "stroke-opacity",
    "stroke-width",
    "stroke-dasharray",
    "font-size",
    "font-family",
    "text-anchor",
    "opacity",
    "visibility",
    "display",
}


def parse_style(elem, inherited=None):
    style = dict(inherited or {})  # 親から継承したスタイルをコピーして開始する
    s = elem.get("style")  # style="..." 属性の文字列を取得する
    if s:
        for pair in s.split(";"):  # ";" で区切られた各プロパティを処理する
            if ":" in pair:  # "key: value" 形式のペアのみ処理する
                k, v = pair.split(":", 1)  # キーと値を分割する
                k = k.strip()
                v = v.strip()  # 前後の空白を除去する
                if k in STYLE_KEYS:  # 必要なプロパティのみ保持する
                    style[k] = v
    for k in STYLE_KEYS:
        v = elem.get(k)  # style 属性ではなく直接属性として書かれた値も取得する
        if v is not None:
            style[k] = v  # 直接属性は style 属性より優先して上書きする
    return style  # マージされたスタイル辞書を返す


def style_to_drawio_parts(style, is_text=False):
    parts = []  # draw.io スタイル文字列の各パーツを格納するリスト
    fill = normalize_color(style.get("fill"))  # fill プロパティを正規化する
    if fill is None:  # fill が未指定の場合
        if not is_text:
            parts.append("fillColor=none")  # テキスト以外は塗りなしとして明示する
    elif fill == "none":  # fill=none が明示された場合
        parts.append("fillColor=none")  # 塗りなしを設定する
    else:
        if is_text:
            parts.append(
                f"fontColor={fill}"
            )  # テキストの場合は fontColor として設定する
        else:
            parts.append(f"fillColor={fill}")  # 図形の場合は fillColor として設定する
    if not is_text:  # 線のスタイルはテキスト以外にのみ適用する
        stroke = normalize_color(style.get("stroke"))  # stroke プロパティを正規化する
        if stroke is None or stroke == "none":
            parts.append("strokeColor=none")  # 線なしを設定する
        else:
            parts.append(f"strokeColor={stroke}")  # 線色を設定する
        sw = style.get("stroke-width")  # 線幅を取得する
        if sw:
            try:
                parts.append(
                    f"strokeWidth={fmt(strip_unit(sw))}"
                )  # 単位を除去して線幅を設定する
            except ValueError:
                pass  # 数値変換に失敗した場合は無視する
        da = style.get("stroke-dasharray")  # 破線パターンを取得する
        if da and da != "none":
            parts.append("dashed=1")  # 破線を有効にする
    op = style.get("opacity")  # 全体の不透明度を取得する
    if op:
        try:
            v = float(op)  # 文字列を数値に変換する
            parts.append(
                f"opacity={int(round(v * 100))}"
            )  # 0〜1 を 0〜100 のパーセンテージに変換する
        except ValueError:
            pass  # 数値変換に失敗した場合は無視する
    return parts  # draw.io スタイルのパーツリストを返す


# ----------------------------------------------------------------------------
# PathParser
# ----------------------------------------------------------------------------


class PathParser:
    CMD_RE = re.compile(
        r"([MmLlHhVvCcSsQqTtAaZz])"
    )  # SVG パスコマンド文字にマッチする正規表現

    def __init__(self, samples=16):
        self.samples = max(2, samples)  # ベジェ曲線・弧のサンプリング点数（最低2点）

    def tokenize(self, d):
        tokens = []  # (コマンド文字, [引数リスト]) のリストを格納する
        i = 0  # 現在の文字位置
        n = len(d)  # パス文字列の長さ
        while i < n:
            ch = d[i]
            if ch.isspace() or ch == ",":  # 空白・カンマは区切り文字としてスキップする
                i += 1
                continue
            if ch in "MmLlHhVvCcSsQqTtAaZz":  # コマンド文字を検出した場合
                cmd = ch  # コマンド文字を保存する
                i += 1  # コマンド文字の次から引数を読む
                args = []  # このコマンドの引数リストを初期化する
                while i < n:
                    ch2 = d[i]
                    if ch2.isspace() or ch2 == ",":  # 区切り文字はスキップする
                        i += 1
                        continue
                    if (
                        ch2 in "MmLlHhVvCcSsQqTtAaZz"
                    ):  # 次のコマンド文字が来たら引数読み取りを終了する
                        break
                    m = _NUM_RE.match(d, i)  # 現在位置から数値にマッチを試みる
                    if not m:  # 数値以外の文字は1文字スキップする
                        i += 1
                        continue
                    args.append(
                        float(m.group(0))
                    )  # マッチした数値を引数リストに追加する
                    i = m.end()  # 数値の終端に位置を進める
                tokens.append(
                    (cmd, args)
                )  # コマンドと引数のペアをトークンリストに追加する
            else:
                i += 1  # 未知の文字は1文字スキップする
        return tokens  # トークンリストを返す

    def parse(self, d):
        try:
            tokens = self.tokenize(d)  # パス文字列をトークン列に変換する
        except Exception as e:
            warn(
                f"failed to tokenize path: {e}"
            )  # トークン化に失敗した場合は警告して空リストを返す
            return []
        subpaths = []  # 完成したサブパスを格納するリスト
        current = []  # 現在構築中のサブパスの点リスト
        cx = cy = 0.0  # 現在の描画カーソル位置
        sx = sy = 0.0  # 現在のサブパスの開始点（Z コマンド用）
        prev_c = None  # 直前のキュービックベジェの第2制御点（S コマンドの反射用）
        prev_q = None  # 直前の二次ベジェの制御点（T コマンドの反射用）

        def flush():
            nonlocal current
            # M または Z で現在のサブパスを確定する
            if len(current) >= 2:  # 点が2個以上あるサブパスのみ有効とする
                subpaths.append(current)
            current = []  # 新しいサブパス用にリストをリセットする

        for cmd, args in tokens:
            absolute = cmd.isupper()  # 大文字なら絶対座標、小文字なら相対座標
            c = cmd.upper()  # コマンドの種類を大文字で判定する
            if c == "M":  # Move To: 新しいサブパスを開始する
                flush()  # 前のサブパスを確定する
                if len(args) < 2:  # 引数が不足している場合はスキップする
                    continue
                x, y = args[0], args[1]  # 移動先の座標を取得する
                if not absolute:
                    x += cx
                    y += cy  # 相対座標を絶対座標に変換する
                current = [(x, y)]  # 新しいサブパスの開始点を設定する
                cx, cy = x, y  # カーソルを移動先に更新する
                sx, sy = x, y  # サブパスの開始点を記録する
                i = 2
                while i + 1 < len(
                    args
                ):  # M の後に続く暗黙の L コマンドの引数を処理する
                    nx, ny = args[i], args[i + 1]
                    if not absolute:
                        nx += cx
                        ny += cy  # 相対座標を絶対座標に変換する
                    current.append((nx, ny))  # 暗黙の L として点を追加する
                    cx, cy = nx, ny  # カーソルを更新する
                    i += 2
                prev_c = prev_q = None  # 制御点の履歴をリセットする
            elif c == "L":  # Line To: 直線を引く
                i = 0
                while i + 1 < len(args):
                    nx, ny = args[i], args[i + 1]  # 終点の座標を取得する
                    if not absolute:
                        nx += cx
                        ny += cy  # 相対座標を絶対座標に変換する
                    current.append((nx, ny))  # 直線の終点を点リストに追加する
                    cx, cy = nx, ny  # カーソルを更新する
                    i += 2
                prev_c = prev_q = None  # 制御点の履歴をリセットする
            elif c == "H":  # Horizontal Line To: 水平線を引く
                for nx in args:
                    if not absolute:
                        nx += cx  # 相対 X を絶対 X に変換する
                    current.append((nx, cy))  # Y 座標は現在のカーソルのまま点を追加する
                    cx = nx  # X カーソルを更新する
                prev_c = prev_q = None  # 制御点の履歴をリセットする
            elif c == "V":  # Vertical Line To: 垂直線を引く
                for ny in args:
                    if not absolute:
                        ny += cy  # 相対 Y を絶対 Y に変換する
                    current.append((cx, ny))  # X 座標は現在のカーソルのまま点を追加する
                    cy = ny  # Y カーソルを更新する
                prev_c = prev_q = None  # 制御点の履歴をリセットする
            elif c == "C":  # Cubic Bezier: キュービックベジェ曲線
                i = 0
                while i + 5 < len(args):
                    x1, y1, x2, y2, x, y = args[
                        i : i + 6
                    ]  # 2つの制御点と終点を取得する
                    if not absolute:
                        x1 += cx
                        y1 += cy  # 第1制御点を絶対座標に変換する
                        x2 += cx
                        y2 += cy  # 第2制御点を絶対座標に変換する
                        x += cx
                        y += cy  # 終点を絶対座標に変換する
                    current.extend(
                        self._sample_cubic((cx, cy), (x1, y1), (x2, y2), (x, y))
                    )  # 曲線を折れ線近似する
                    cx, cy = x, y  # カーソルを終点に更新する
                    prev_c = (x2, y2)  # 第2制御点を S コマンド用に保存する
                    i += 6
                prev_q = None  # 二次ベジェ制御点の履歴をリセットする
            elif (
                c == "S"
            ):  # Smooth Cubic Bezier: 前のキュービックベジェに滑らかに繋がるベジェ曲線
                i = 0
                while i + 3 < len(args):
                    x2, y2, x, y = args[i : i + 4]  # 第2制御点と終点を取得する
                    if not absolute:
                        x2 += cx
                        y2 += cy  # 第2制御点を絶対座標に変換する
                        x += cx
                        y += cy  # 終点を絶対座標に変換する
                    if prev_c is None:
                        x1, y1 = (
                            cx,
                            cy,
                        )  # 直前がキュービックベジェでなければ第1制御点をカーソルに設定する
                    else:
                        x1, y1 = (
                            2 * cx - prev_c[0],
                            2 * cy - prev_c[1],
                        )  # 前の第2制御点をカーソルで反転して第1制御点を求める
                    current.extend(
                        self._sample_cubic((cx, cy), (x1, y1), (x2, y2), (x, y))
                    )  # 曲線を折れ線近似する
                    cx, cy = x, y  # カーソルを終点に更新する
                    prev_c = (x2, y2)  # 第2制御点を次の S コマンド用に保存する
                    i += 4
                prev_q = None  # 二次ベジェ制御点の履歴をリセットする
            elif c == "Q":  # Quadratic Bezier: 二次ベジェ曲線
                i = 0
                while i + 3 < len(args):
                    x1, y1, x, y = args[i : i + 4]  # 制御点と終点を取得する
                    if not absolute:
                        x1 += cx
                        y1 += cy  # 制御点を絶対座標に変換する
                        x += cx
                        y += cy  # 終点を絶対座標に変換する
                    current.extend(
                        self._sample_quad((cx, cy), (x1, y1), (x, y))
                    )  # 曲線を折れ線近似する
                    cx, cy = x, y  # カーソルを終点に更新する
                    prev_q = (x1, y1)  # 制御点を T コマンド用に保存する
                    i += 4
                prev_c = None  # キュービックベジェ制御点の履歴をリセットする
            elif (
                c == "T"
            ):  # Smooth Quadratic Bezier: 前の二次ベジェに滑らかに繋がるベジェ曲線
                i = 0
                while i + 1 < len(args):
                    x, y = args[i : i + 2]  # 終点を取得する
                    if not absolute:
                        x += cx
                        y += cy  # 終点を絶対座標に変換する
                    if prev_q is None:
                        x1, y1 = (
                            cx,
                            cy,
                        )  # 直前が二次ベジェでなければ制御点をカーソルに設定する
                    else:
                        x1, y1 = (
                            2 * cx - prev_q[0],
                            2 * cy - prev_q[1],
                        )  # 前の制御点をカーソルで反転して制御点を求める
                    current.extend(
                        self._sample_quad((cx, cy), (x1, y1), (x, y))
                    )  # 曲線を折れ線近似する
                    cx, cy = x, y  # カーソルを終点に更新する
                    prev_q = (x1, y1)  # 制御点を次の T コマンド用に保存する
                    i += 2
                prev_c = None  # キュービックベジェ制御点の履歴をリセットする
            elif c == "A":  # Arc: 楕円弧
                i = 0
                while i + 6 < len(args):
                    rx, ry, xrot, large, sweep, x, y = args[
                        i : i + 7
                    ]  # 半径・回転・フラグ・終点を取得する
                    if not absolute:
                        x += cx
                        y += cy  # 終点を絶対座標に変換する
                    current.extend(
                        self._sample_arc(
                            cx, cy, rx, ry, xrot, int(large) != 0, int(sweep) != 0, x, y
                        )
                    )  # 弧を折れ線近似する
                    cx, cy = x, y  # カーソルを終点に更新する
                    i += 7
                prev_c = prev_q = None  # 制御点の履歴をリセットする
            elif c == "Z":  # Close Path: サブパスを閉じる
                if current and (cx, cy) != (sx, sy):
                    current.append(
                        (sx, sy)
                    )  # カーソルが開始点と異なれば開始点に戻る直線を追加する
                cx, cy = sx, sy  # カーソルをサブパスの開始点に戻す
                flush()  # サブパスを確定する
                prev_c = prev_q = None  # 制御点の履歴をリセットする

        flush()  # 最後のサブパスを確定する
        return subpaths  # すべてのサブパスのリストを返す

    def _sample_cubic(self, p0, p1, p2, p3):
        n = self.samples  # サンプリング点数を取得する
        pts = []  # サンプリング点を格納するリスト
        for i in range(1, n + 1):
            t = i / n  # パラメータ t を 0 より大きく 1 以下の範囲で均等に割り当てる
            it = 1 - t  # (1-t) を計算しておく（ベルンシュタイン基底の共通因子）
            # キュービックベジェ曲線の点を計算する: B(t) = (1-t)^3*p0 + 3(1-t)^2*t*p1 + 3(1-t)*t^2*p2 + t^3*p3
            x = (
                it * it * it * p0[0]
                + 3 * it * it * t * p1[0]
                + 3 * it * t * t * p2[0]
                + t * t * t * p3[0]
            )
            y = (
                it * it * it * p0[1]
                + 3 * it * it * t * p1[1]
                + 3 * it * t * t * p2[1]
                + t * t * t * p3[1]
            )
            pts.append((x, y))  # 計算した点をリストに追加する
        return pts  # サンプリング点のリストを返す

    def _sample_quad(self, p0, p1, p2):
        n = self.samples  # サンプリング点数を取得する
        pts = []  # サンプリング点を格納するリスト
        for i in range(1, n + 1):
            t = i / n  # パラメータ t を均等に割り当てる
            it = 1 - t  # (1-t) を計算しておく
            # 二次ベジェ曲線の点を計算する: B(t) = (1-t)^2*p0 + 2(1-t)*t*p1 + t^2*p2
            x = it * it * p0[0] + 2 * it * t * p1[0] + t * t * p2[0]
            y = it * it * p0[1] + 2 * it * t * p1[1] + t * t * p2[1]
            pts.append((x, y))  # 計算した点をリストに追加する
        return pts  # サンプリング点のリストを返す

    def _sample_arc(self, x1, y1, rx, ry, phi_deg, large_arc, sweep, x2, y2):
        # SVG 仕様 F.6.5 のエンドポイント→中心パラメータ変換に従う
        if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
            return [(x2, y2)]  # 半径が 0 または始点と終点が同じなら終点のみ返す
        rx, ry = abs(rx), abs(ry)  # 半径は絶対値を使う（SVG 仕様）
        phi = math.radians(phi_deg)  # 楕円の傾き角をラジアンに変換する
        cs = math.cos(phi)
        sn = math.sin(phi)  # 楕円傾きの cos・sin を計算する
        dx = (x1 - x2) / 2.0  # 始点と終点の X 差の半値
        dy = (y1 - y2) / 2.0  # 始点と終点の Y 差の半値
        x1p = (
            cs * dx + sn * dy
        )  # 楕円のローカル座標系での始点の X（傾きを除去した座標）
        y1p = -sn * dx + cs * dy  # 楕円のローカル座標系での始点の Y
        rad_check = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (
            ry * ry
        )  # 半径が点間距離に対して十分か確認する
        if rad_check > 1:
            scale = math.sqrt(
                rad_check
            )  # 半径が不足している場合はスケールアップして補正する
            rx *= scale
            ry *= scale
        sign = (
            -1.0 if large_arc == sweep else 1.0
        )  # large-arc-flag と sweep-flag の組み合わせで中心の向きを決める
        num = (
            (rx * rx) * (ry * ry) - (rx * rx) * (y1p * y1p) - (ry * ry) * (x1p * x1p)
        )  # 中心座標の計算の分子
        den = (rx * rx) * (y1p * y1p) + (ry * ry) * (x1p * x1p)  # 中心座標の計算の分母
        coef = (
            sign * math.sqrt(max(0.0, num / den)) if den else 0.0
        )  # 中心座標の係数（分母 0 は保護）
        cxp = coef * (rx * y1p) / ry if ry else 0.0  # ローカル座標系での楕円中心の X
        cyp = -coef * (ry * x1p) / rx if rx else 0.0  # ローカル座標系での楕円中心の Y
        cx = cs * cxp - sn * cyp + (x1 + x2) / 2.0  # 元の座標系での楕円中心の X
        cy = sn * cxp + cs * cyp + (y1 + y2) / 2.0  # 元の座標系での楕円中心の Y

        def angle(ux, uy, vx, vy):
            n_u = math.hypot(ux, uy)
            n_v = math.hypot(vx, vy)  # 2つのベクトルの長さを計算する
            if n_u == 0 or n_v == 0:
                return 0.0  # ゼロベクトルに対しては 0 を返す
            cos_a = max(
                -1.0, min(1.0, (ux * vx + uy * vy) / (n_u * n_v))
            )  # cos を -1〜1 にクランプして数値誤差を防ぐ
            a = math.acos(cos_a)  # ベクトル間の角度を求める
            if (ux * vy - uy * vx) < 0:  # 外積の符号で角度の向きを決める
                a = -a
            return a  # 符号付き角度を返す

        theta1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)  # 弧の開始角を求める
        delta = angle(
            (x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry
        )  # 弧の角度変化量を求める
        if not sweep and delta > 0:
            delta -= 2 * math.pi  # sweep=0 で delta が正の場合、反時計回りに修正する
        elif sweep and delta < 0:
            delta += 2 * math.pi  # sweep=1 で delta が負の場合、時計回りに修正する
        n = self.samples  # サンプリング点数を取得する
        pts = []  # サンプリング点を格納するリスト
        for i in range(1, n + 1):
            t = theta1 + delta * (i / n)  # 弧上の各サンプリング角度を計算する
            xp = rx * math.cos(t)  # ローカル座標系での X 座標
            yp = ry * math.sin(t)  # ローカル座標系での Y 座標
            x = (
                cs * xp - sn * yp + cx
            )  # 元の座標系での X 座標（楕円傾きを戻して中心を加算）
            y = sn * xp + cs * yp + cy  # 元の座標系での Y 座標
            pts.append((x, y))  # 計算した点をリストに追加する
        return pts  # サンプリング点のリストを返す


# ----------------------------------------------------------------------------
# Stencil エンコード（複合パス用）
# ----------------------------------------------------------------------------


def _encode_stencil(xml: str) -> str:
    """スタンシル XML を raw deflate + base64 でエンコードする.

    draw.io クライアントは pako.inflateRaw(atob(data)) でデコードするため、
    zlib ヘッダ・チェックサムなしの raw deflate が必要。
    Python の wbits=-15 が raw deflate に対応する。
    """
    data = xml.encode("utf-8")  # UTF-8 バイト列に変換する
    comp = zlib.compressobj(
        level=9, wbits=-15
    )  # raw deflate（zlib ヘッダなし）で圧縮オブジェクト生成
    compressed = comp.compress(data) + comp.flush()  # データを圧縮してフラッシュする
    return base64.b64encode(compressed).decode("ascii")  # base64 文字列として返す


def _build_stencil_xml(local_subpaths: list, bw: float, bh: float, style: dict) -> str:
    """複数のサブパス（バウンディングボックス原点基準の座標）を draw.io stencil XML に変換する.

    stencil の w/h はバウンディングボックスの寸法と一致させることで、
    draw.io がセルに配置したときスケーリングなしで描画される。
    """
    fill = normalize_color(style.get("fill"))  # 塗り色を正規化する
    stroke = normalize_color(style.get("stroke"))  # 線色を正規化する
    # 塗り・線の有無に応じてスタンシルの描画コマンドを選ぶ
    if fill == "none" or fill is None:
        render_cmd = "stroke"  # 塗りなし：線のみ描画する
    elif stroke is None or stroke == "none":
        render_cmd = "fill"  # 線なし：塗りのみ描画する
    else:
        render_cmd = "fillstroke"  # 塗りと線の両方を描画する
    cmds = []
    for sub in local_subpaths:
        if not sub:  # 空のサブパスはスキップする
            continue
        cmds.append(
            f'<move x="{fmt(sub[0][0])}" y="{fmt(sub[0][1])}"/>'
        )  # サブパスの開始点に移動する
        for px, py in sub[1:]:
            cmds.append(f'<line x="{fmt(px)}" y="{fmt(py)}"/>')  # 各頂点へ直線を引く
        cmds.append("<close/>")  # サブパスを閉じる
    inner = "".join(cmds)  # 全コマンドを結合する
    # stencil XML を組み立てる（strokewidth="inherit" でセルスタイルの線幅を引き継ぐ）
    return (
        f'<shape w="{fmt(bw)}" h="{fmt(bh)}" aspect="variable" strokewidth="inherit">'
        f"<foreground><path>{inner}</path><{render_cmd}/></foreground>"
        f"</shape>"
    )


# ----------------------------------------------------------------------------
# Converter
# ----------------------------------------------------------------------------


class Converter:
    def __init__(self, samples=16):
        self.cells = []  # 生成した mxCell 要素を蓄積するリスト
        self.next_id = 2  # 次に発行する ID の番号（0 と 1 は draw.io の予約済み）
        self.samples = samples  # 曲線サンプリング点数
        self.path_parser = PathParser(
            samples
        )  # path 要素の解析に使う PathParser インスタンス
        # viewBox がない場合のページサイズ算出に使う
        self.bbox = [
            float("inf"),
            float("inf"),
            float("-inf"),
            float("-inf"),
        ]  # [min_x, min_y, max_x, max_y]
        self.id_index = {}  # SVG の id 属性をキーに要素を引く辞書（<use> の参照先解決用）
        self._use_depth = 0  # <use> 要素のネスト深さカウンタ（無限再帰防止用）

    def _id(self):
        i = self.next_id  # 現在の ID 番号を取得する
        self.next_id += 1  # 次回のために番号をインクリメントする
        return f"c{i}"  # "c2", "c3", ... 形式の文字列を返す

    def _track(self, x, y):
        if x < self.bbox[0]:
            self.bbox[0] = x  # X の最小値を更新する
        if y < self.bbox[1]:
            self.bbox[1] = y  # Y の最小値を更新する
        if x > self.bbox[2]:
            self.bbox[2] = x  # X の最大値を更新する
        if y > self.bbox[3]:
            self.bbox[3] = y  # Y の最大値を更新する

    def add_vertex(self, style_str, x, y, w, h, value=""):
        if w <= 0:
            w = 1  # 幅が 0 以下の場合は最小値 1 にする（draw.io が 0 サイズをサポートしないため）
        if h <= 0:
            h = 1  # 高さが 0 以下の場合も同様に最小値 1 にする
        self._track(x, y)
        self._track(x + w, y + h)  # バウンディングボックスを更新する
        cell = ET.Element(
            "mxCell",
            {
                "id": self._id(),  # ユニークな ID を割り当てる
                "value": value,  # セルに表示するテキスト
                "style": style_str,  # draw.io のスタイル文字列
                "vertex": "1",  # vertex=1 で図形（頂点）として登録する
                "parent": "1",  # 親をデフォルトレイヤー（id=1）に設定する
            },
        )
        ET.SubElement(
            cell,
            "mxGeometry",
            {
                "x": fmt(x),
                "y": fmt(y),  # 図形の左上座標
                "width": fmt(w),
                "height": fmt(h),  # 図形の幅と高さ
                "as": "geometry",  # draw.io が geometry として認識するための属性
            },
        )
        self.cells.append(cell)  # 生成したセルをリストに追加する

    def add_edge(self, style_str, points):
        if len(points) < 2:  # 点が1個以下では線を引けないためスキップする
            return
        for x, y in points:
            self._track(x, y)  # すべての点でバウンディングボックスを更新する
        cell = ET.Element(
            "mxCell",
            {
                "id": self._id(),  # ユニークな ID を割り当てる
                "value": "",  # エッジにテキストなし
                "style": style_str,  # draw.io のスタイル文字列
                "edge": "1",  # edge=1 で線（エッジ）として登録する
                "parent": "1",  # 親をデフォルトレイヤーに設定する
            },
        )
        geom = ET.SubElement(
            cell, "mxGeometry", {"relative": "1", "as": "geometry"}
        )  # エッジのジオメトリ要素
        ET.SubElement(
            geom,
            "mxPoint",
            {
                "x": fmt(points[0][0]),
                "y": fmt(points[0][1]),
                "as": "sourcePoint",  # 始点を sourcePoint として設定する
            },
        )
        ET.SubElement(
            geom,
            "mxPoint",
            {
                "x": fmt(points[-1][0]),
                "y": fmt(points[-1][1]),
                "as": "targetPoint",  # 終点を targetPoint として設定する
            },
        )
        if len(points) > 2:  # 中間点がある場合は Array として格納する
            arr = ET.SubElement(
                geom, "Array", {"as": "points"}
            )  # 中間点を格納する Array 要素
            for x, y in points[1:-1]:
                ET.SubElement(
                    arr, "mxPoint", {"x": fmt(x), "y": fmt(y)}
                )  # 各中間点を mxPoint として追加する
        self.cells.append(cell)  # 生成したエッジをリストに追加する

    def index_ids(self, elem):
        eid = elem.get("id")  # 要素の id 属性を取得する
        if eid:
            self.id_index[eid] = elem  # id をキーに要素を辞書へ登録する
        for child in elem:
            self.index_ids(child)  # 子要素を再帰的に処理してすべての id を収集する

    def walk(self, elem, transform, parent_style):
        tag = local_tag(elem.tag)  # 名前空間を除いたタグ名を取得する
        if tag in (
            "metadata",
            "title",
            "desc",
            "style",
            "clipPath",
            "mask",
            "filter",
            "linearGradient",
            "radialGradient",
            "pattern",
        ):
            return  # 変換対象外のメタデータ・定義要素はスキップする
        if tag in ("defs", "symbol"):
            return  # defs・symbol は参照先の定義であり直接描画しない
        my_t = parse_transform(
            elem.get("transform")
        )  # この要素自身の transform を解析する
        cur_t = transform @ my_t  # 親から受け継いだ変換に自分の変換を合成する
        cur_style = parse_style(
            elem, parent_style
        )  # 親スタイルを継承しつつこの要素のスタイルを解析する
        vis = cur_style.get("visibility")  # visibility プロパティを取得する
        disp = cur_style.get("display")  # display プロパティを取得する
        if vis == "hidden" or disp == "none":
            return  # 非表示要素はスキップする
        if tag in ("svg", "g", "a"):
            for child in elem:
                self.walk(
                    child, cur_t, cur_style
                )  # コンテナ要素は子要素を再帰的に処理する
            return
        if tag == "use":
            self.handle_use(
                elem, cur_t, cur_style
            )  # <use> 要素は参照先を展開して処理する
            return
        handler = {
            "rect": self.handle_rect,
            "circle": self.handle_circle,
            "ellipse": self.handle_ellipse,
            "line": self.handle_line,
            "polyline": lambda e, t, s: self.handle_polyline(
                e, t, s, closed=False
            ),  # 開いた折れ線
            "polygon": lambda e, t, s: self.handle_polyline(
                e, t, s, closed=True
            ),  # 閉じた折れ線（ポリゴン）
            "text": self.handle_text,
            "path": self.handle_path,
        }.get(tag)  # タグ名に対応するハンドラ関数を取得する
        if handler:
            handler(elem, cur_t, cur_style)  # 対応するハンドラで図形を変換する
        elif tag == "image":
            warn("<image> not supported, skipping")  # <image> は未対応として警告する
        else:
            warn(f"unsupported element: <{tag}>")  # その他の未知タグも警告する

    def handle_use(self, elem, transform, style):
        # 循環参照による無限再帰を防ぐ深さ制限
        if self._use_depth > 8:
            warn(
                "<use> nesting too deep, skipping"
            )  # ネストが深すぎる場合は警告してスキップ
            return
        href = (
            elem.get("href")  # SVG2 形式の href を優先する
            or elem.get("{http://www.w3.org/1999/xlink}href")  # xlink 名前空間付き href
            or elem.get("xlink:href")
        )  # 名前空間プレフィックス形式 href
        if not href or not href.startswith("#"):
            warn(f"<use> with unsupported href: {href}")  # ファイル外参照などは未対応
            return
        target = self.id_index.get(
            href[1:]
        )  # "#id" の "#" を除いた id で参照先を検索する
        if target is None:
            warn(
                f"<use> target not found: {href}"
            )  # 参照先が見つからない場合は警告してスキップ
            return
        ux = strip_unit(elem.get("x", 0))  # <use> 要素の x オフセットを取得する
        uy = strip_unit(elem.get("y", 0))  # <use> 要素の y オフセットを取得する
        use_t = transform @ Transform.translate(
            ux, uy
        )  # オフセット分の平行移動を変換に追加する
        self._use_depth += 1  # ネスト深さを増やす
        try:
            tag = local_tag(target.tag)
            if tag in ("svg", "g", "symbol"):
                for child in target:
                    self.walk(
                        child, use_t, style
                    )  # コンテナ型の参照先は子要素を展開して処理する
            else:
                self.walk(target, use_t, style)  # 単一要素の参照先はそのまま処理する
        finally:
            self._use_depth -= 1  # 処理終了後にネスト深さを戻す（例外時も確実に）

    # ---- shape handlers ----------------------------------------------------

    def handle_rect(self, elem, transform, style):
        x = strip_unit(elem.get("x", 0))  # 矩形の左上 X 座標を取得する
        y = strip_unit(elem.get("y", 0))  # 矩形の左上 Y 座標を取得する
        w = strip_unit(elem.get("width", 0))  # 矩形の幅を取得する
        h = strip_unit(elem.get("height", 0))  # 矩形の高さを取得する
        if w <= 0 or h <= 0:
            return  # 幅または高さが 0 以下の場合は描画しない
        rx = strip_unit(elem.get("rx", 0))  # 角丸の X 半径を取得する
        ry = strip_unit(elem.get("ry", 0))  # 角丸の Y 半径を取得する
        # 変換後の4隅の座標を計算する（回転やスケールが含まれる場合に必要）
        corners = [
            transform.apply(x, y),
            transform.apply(x + w, y),
            transform.apply(x + w, y + h),
            transform.apply(x, y + h),
        ]
        xs = [p[0] for p in corners]
        ys = [p[1] for p in corners]  # 変換後の X・Y 座標一覧
        bx, by = min(xs), min(ys)  # バウンディングボックスの左上座標を求める
        bw, bh = max(xs) - bx, max(ys) - by  # バウンディングボックスの幅・高さを求める
        rot = (
            transform.rotation_deg() if not transform.is_axis_aligned() else 0.0
        )  # 回転がある場合のみ角度を取得する
        parts = [
            "rounded=1" if (rx or ry) else "rounded=0",
            "whiteSpace=wrap",
            "html=1",
        ]  # 角丸の有無を設定する
        if rx or ry:
            sx, _ = transform.scale_factor()
            # draw.io の arcSize は短辺に対するパーセンテージ
            arc_pct = max(
                0, min(100, (max(rx, ry) * sx * 2) / max(bw, 1) * 100)
            )  # 角丸半径をパーセンテージに変換する
            parts.append(f"arcSize={fmt(arc_pct)}")  # arcSize パラメータを追加する
        parts.extend(style_to_drawio_parts(style))  # 塗り・線のスタイルを追加する
        if abs(rot) > 0.5:
            parts.append(
                f"rotation={fmt(rot)}"
            )  # 0.5 度以上の回転がある場合に rotation を追加する
        self.add_vertex(
            ";".join(parts), bx, by, bw, bh
        )  # スタイルと座標で頂点セルを登録する

    def handle_circle(self, elem, transform, style):
        cx = strip_unit(elem.get("cx", 0))  # 円の中心 X 座標を取得する
        cy = strip_unit(elem.get("cy", 0))  # 円の中心 Y 座標を取得する
        r = strip_unit(elem.get("r", 0))  # 円の半径を取得する
        if r <= 0:
            return  # 半径が 0 以下の場合は描画しない
        self._emit_ellipse(
            cx, cy, r, r, transform, style
        )  # rx=ry=r として楕円として処理する

    def handle_ellipse(self, elem, transform, style):
        cx = strip_unit(elem.get("cx", 0))  # 楕円の中心 X 座標を取得する
        cy = strip_unit(elem.get("cy", 0))  # 楕円の中心 Y 座標を取得する
        rx = strip_unit(elem.get("rx", 0))  # 楕円の X 半径を取得する
        ry = strip_unit(elem.get("ry", 0))  # 楕円の Y 半径を取得する
        if rx <= 0 or ry <= 0:
            return  # どちらかの半径が 0 以下の場合は描画しない
        self._emit_ellipse(cx, cy, rx, ry, transform, style)  # 共通の楕円出力処理を呼ぶ

    def _emit_ellipse(self, cx, cy, rx, ry, transform, style):
        sx, sy = transform.scale_factor()  # 変換のスケール係数を取得する
        tcx, tcy = transform.apply(cx, cy)  # 楕円の中心を変換後の座標に変換する
        w = 2 * rx * sx  # 変換後の楕円の幅を計算する
        h = 2 * ry * sy  # 変換後の楕円の高さを計算する
        x = tcx - w / 2  # バウンディングボックスの左端を求める
        y = tcy - h / 2  # バウンディングボックスの上端を求める
        parts = ["ellipse", "whiteSpace=wrap", "html=1"]  # 楕円スタイルを設定する
        parts.extend(style_to_drawio_parts(style))  # 塗り・線のスタイルを追加する
        rot = (
            transform.rotation_deg() if not transform.is_axis_aligned() else 0.0
        )  # 回転角を取得する
        if abs(rot) > 0.5:
            parts.append(f"rotation={fmt(rot)}")  # 回転がある場合に rotation を追加する
        self.add_vertex(";".join(parts), x, y, w, h)  # 楕円セルを登録する

    def handle_line(self, elem, transform, style):
        x1 = strip_unit(elem.get("x1", 0))  # 始点の X 座標を取得する
        y1 = strip_unit(elem.get("y1", 0))  # 始点の Y 座標を取得する
        x2 = strip_unit(elem.get("x2", 0))  # 終点の X 座標を取得する
        y2 = strip_unit(elem.get("y2", 0))  # 終点の Y 座標を取得する
        p1 = transform.apply(x1, y1)  # 始点を変換後の座標に変換する
        p2 = transform.apply(x2, y2)  # 終点を変換後の座標に変換する
        parts = ["endArrow=none", "html=1"]  # 矢印なしの線スタイルを設定する
        parts.extend(
            p for p in style_to_drawio_parts(style) if not p.startswith("fillColor")
        )  # 線には fillColor を含めない
        self.add_edge(";".join(parts), [p1, p2])  # 始点・終点の2点でエッジを登録する

    def handle_polyline(self, elem, transform, style, closed):
        pts_str = elem.get("points", "")  # points 属性の文字列を取得する
        nums = [
            float(x) for x in _NUM_RE.findall(pts_str)
        ]  # 数値を全て float に変換する
        pts = [
            transform.apply(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)
        ]  # 2個ずつ座標ペアとして変換する
        if len(pts) < 2:
            return  # 2点未満では線を引けないためスキップする
        if closed:
            pts.append(pts[0])  # ポリゴンの場合は最初の点を末尾に追加して閉じる
        parts = ["endArrow=none", "html=1"]  # 矢印なしの線スタイルを設定する
        sparts = style_to_drawio_parts(style)
        if closed:
            parts.extend(
                sparts
            )  # ポリゴンは fillColor を含むすべてのスタイルを適用する
        else:
            parts.extend(
                p for p in sparts if not p.startswith("fillColor")
            )  # ポリラインには fillColor を含めない
        self.add_edge(";".join(parts), pts)  # 変換後の点列でエッジを登録する

    def handle_text(self, elem, transform, style):
        x = strip_unit(elem.get("x", 0))  # テキストの基準 X 座標を取得する
        y = strip_unit(elem.get("y", 0))  # テキストの基準 Y 座標を取得する
        lines = []  # テキスト行を収集するリスト
        if elem.text and elem.text.strip():
            lines.append(elem.text.strip())  # <text> 要素の直接テキストを追加する
        for child in elem:
            if local_tag(child.tag) == "tspan" and child.text and child.text.strip():
                lines.append(child.text.strip())  # <tspan> 子要素のテキストを追加する
            if child.tail and child.tail.strip():
                lines.append(child.tail.strip())  # <tspan> の後続テキストも追加する
        text = "\n".join(lines)  # 各行を改行で結合する
        if not text:
            return  # テキストが空の場合はスキップする
        font_size = (
            strip_unit(style.get("font-size", "12")) or 12
        )  # フォントサイズを取得する（デフォルト 12px）
        sx, sy = transform.scale_factor()  # 変換のスケール係数を取得する
        font_size *= max(sx, sy)  # スケールに合わせてフォントサイズを拡大する
        tx, ty = transform.apply(x, y)  # テキスト基準点を変換後の座標に変換する
        line_count = text.count("\n") + 1  # テキストの行数を数える
        # 外部ライブラリなしではグリフ幅が取得できないため文字数×係数で近似する
        w = max(
            len(max(text.split("\n"), key=len)) * font_size * 0.6, font_size * 2
        )  # 最長行の幅を推定する
        h = (
            font_size * 1.4 * line_count
        )  # フォントサイズ × 行間係数 × 行数で高さを推定する
        anchor = style.get(
            "text-anchor", "start"
        )  # text-anchor でテキスト配置を取得する
        align = {"start": "left", "middle": "center", "end": "right"}.get(
            anchor, "left"
        )  # draw.io の align 値に変換する
        if align == "center":
            x_pos = tx - w / 2  # 中央揃えは幅の半分だけ左にオフセットする
        elif align == "right":
            x_pos = tx - w  # 右揃えは幅分だけ左にオフセットする
        else:
            x_pos = tx  # 左揃えは基準点をそのまま使う
        y_pos = (
            ty - font_size
        )  # SVG のテキスト y はベースライン位置なのでフォントサイズ分上に補正する
        parts = [
            "text",
            "html=1",
            f"align={align}",
            "verticalAlign=middle",
            f"fontSize={fmt(font_size)}",
        ]  # テキストセルのスタイルを設定する
        ff = style.get("font-family")  # font-family を取得する
        if ff:
            ff = (
                ff.replace('"', "").replace("'", "").split(",")[0].strip()
            )  # 引用符を除去し最初のフォント名だけ使う
            if ff:
                parts.append(f"fontFamily={ff}")  # fontFamily パラメータを追加する
        parts.extend(
            style_to_drawio_parts(style, is_text=True)
        )  # テキスト用の色スタイルを追加する
        rot = (
            transform.rotation_deg() if not transform.is_axis_aligned() else 0.0
        )  # 回転角を取得する
        if abs(rot) > 0.5:
            parts.append(f"rotation={fmt(rot)}")  # 回転がある場合に rotation を追加する
        value = text.replace(
            "\n", "<br>"
        )  # draw.io の HTML モードで改行を <br> に変換する
        self.add_vertex(
            ";".join(parts), x_pos, y_pos, w, h, value=value
        )  # テキストセルを登録する

    def handle_path(self, elem, transform, style):
        d = elem.get("d")  # path の d 属性（コマンド列）を取得する
        if not d:
            return  # d 属性がない場合はスキップする
        subpaths = self.path_parser.parse(d)  # d 属性をサブパスのリストに解析する
        if not subpaths:
            return  # サブパスが得られなかった場合はスキップする
        fill = normalize_color(style.get("fill"))
        # fill の有無でポリゴン（塗りあり）とポリライン（線のみ）を切り替える
        is_filled = fill is not None and fill != "none"
        # 複数サブパスかつ塗りありの場合は stencil として1セルに束ねて出力する
        # （evenodd ドーナツ等、サブパスを個別に塗ると穴が再現できないため）
        if len(subpaths) > 1 and is_filled:
            self._emit_compound_path(
                subpaths, transform, style
            )  # stencil として複合出力する
            return
        for sub in subpaths:
            tpts = [
                transform.apply(x, y) for x, y in sub
            ]  # サブパスの各点に変換を適用する
            if is_filled and len(tpts) >= 3:
                self._emit_filled_polygon(
                    tpts, style
                )  # 塗りありで3点以上なら塗りつぶしポリゴンとして出力する
            else:
                parts = ["endArrow=none", "html=1"]
                parts.extend(
                    p
                    for p in style_to_drawio_parts(style)
                    if not p.startswith("fillColor")
                )  # 塗りなしパスは fillColor を除いて出力する
                self.add_edge(";".join(parts), tpts)  # 折れ線エッジとして登録する

    def _emit_filled_polygon(self, pts, style):
        # draw.io はネイティブの塗りつぶしポリゴンを持たないため閉じた edge で代替する
        if pts[0] != pts[-1]:
            pts = pts + [pts[0]]  # 末尾に先頭点を追加してパスを閉じる
        parts = ["endArrow=none", "html=1"]  # 矢印なしのスタイルを設定する
        parts.extend(
            style_to_drawio_parts(style)
        )  # 塗り・線のすべてのスタイルを追加する
        self.add_edge(";".join(parts), pts)  # 閉じたエッジとして登録する

    def _emit_compound_path(self, subpaths, transform, style):
        """複数サブパスを1つの stencil vertex として出力する.

        各サブパスを単独の塗りつぶし図形として出力すると重なりが正しく消えず、
        evenodd ドーナツ型などが再現できない。stencil として1セルに束ねることで
        draw.io のブラウザ SVG レンダラが複合パスとして正しく描画する。
        """
        # 変換後の座標をサブパスごとに計算する
        t_subpaths = [[transform.apply(x, y) for x, y in sub] for sub in subpaths]
        all_pts = [p for sub in t_subpaths for p in sub]  # 全点を一列に並べる
        if not all_pts:
            return
        xs = [p[0] for p in all_pts]
        ys = [p[1] for p in all_pts]
        bx, by = min(xs), min(ys)  # バウンディングボックスの左上座標
        bw = max(xs) - bx  # バウンディングボックスの幅
        bh = max(ys) - by  # バウンディングボックスの高さ
        if bw <= 0:
            bw = 1  # 幅ゼロを防ぐ
        if bh <= 0:
            bh = 1  # 高さゼロを防ぐ
        # 各点をバウンディングボックス原点基準のローカル座標に変換する
        local_subpaths = [[(px - bx, py - by) for px, py in sub] for sub in t_subpaths]
        stencil_xml = _build_stencil_xml(
            local_subpaths, bw, bh, style
        )  # stencil XML を生成する
        encoded = _encode_stencil(stencil_xml)  # raw deflate + base64 でエンコードする
        # stencil シェイプとして draw.io スタイルを組み立てる
        parts = [f"shape=stencil({encoded})"]
        parts.extend(style_to_drawio_parts(style))  # 塗り・線のスタイルを追加する
        self.add_vertex(";".join(parts), bx, by, bw, bh)  # stencil vertex を登録する

    # ---- output ------------------------------------------------------------

    def build_mxfile(self, page_w, page_h):
        mxfile = ET.Element(
            "mxfile", {"host": "svg-to-drawio"}
        )  # ルート要素 <mxfile> を生成する
        diagram = ET.SubElement(
            mxfile,
            "diagram",
            {
                "id": str(uuid.uuid4()),  # ダイアグラムごとにユニークな UUID を発行する
                "name": "Page-1",  # ページ名を設定する
            },
        )
        model = ET.SubElement(
            diagram,
            "mxGraphModel",
            {
                "dx": "800",
                "dy": "600",  # 初期ビューポートオフセット
                "grid": "1",
                "gridSize": "10",
                "guides": "1",  # グリッド表示を有効にする
                "tooltips": "1",
                "connect": "1",
                "arrows": "1",  # ツールチップ・接続・矢印を有効にする
                "fold": "1",
                "page": "1",
                "pageScale": "1",  # 折りたたみ・ページ表示を有効にする
                "pageWidth": fmt(max(page_w, 100)),  # ページ幅（最低 100px）
                "pageHeight": fmt(max(page_h, 100)),  # ページ高さ（最低 100px）
                "math": "0",
                "shadow": "0",  # 数式レンダリングとシャドウを無効にする
            },
        )
        root = ET.SubElement(model, "root")  # セルを格納する <root> 要素を生成する
        # id=0 はルートセル、id=1 はデフォルトレイヤー。draw.io フォーマットで必須
        ET.SubElement(root, "mxCell", {"id": "0"})  # ルートセル（親なし）
        ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})  # デフォルトレイヤー
        for c in self.cells:
            root.append(c)  # 変換済みの各セルを root に追加する
        return mxfile  # 完成した <mxfile> ツリーを返す


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------


def parse_viewbox(svg_root):
    vb = svg_root.get("viewBox")  # viewBox 属性を取得する
    if vb:
        nums = [float(x) for x in _NUM_RE.findall(vb)]  # viewBox の4つの数値を取得する
        if len(nums) >= 4:
            return (
                nums[0],
                nums[1],
                nums[2],
                nums[3],
            )  # (min-x, min-y, width, height) を返す
    # viewBox がない場合は width/height 属性からページサイズを取得する
    w = strip_unit(svg_root.get("width", 0))  # SVG の幅を取得する
    h = strip_unit(svg_root.get("height", 0))  # SVG の高さを取得する
    return 0.0, 0.0, w, h  # 原点 (0, 0) に幅・高さを返す


def convert_file(input_path: Path, output_path: Path, samples: int) -> int:
    try:
        tree = ET.parse(input_path)  # SVG ファイルを XML として解析する
    except ET.ParseError as e:
        print(
            f"error: failed to parse SVG: {e}", file=sys.stderr
        )  # XML 構文エラーを報告する
        return 2  # 終了コード 2 で失敗を示す
    except OSError as e:
        print(
            f"error: cannot read {input_path}: {e}", file=sys.stderr
        )  # ファイル読み取りエラーを報告する
        return 1  # 終了コード 1 で失敗を示す

    svg_root = tree.getroot()  # XML ツリーのルート要素を取得する
    if local_tag(svg_root.tag) != "svg":
        print(
            f"error: root element is not <svg>: {svg_root.tag}", file=sys.stderr
        )  # SVG 以外のファイルを拒否する
        return 2

    vb_x, vb_y, vb_w, vb_h = parse_viewbox(svg_root)  # viewBox の原点と寸法を取得する
    # viewBox の原点を draw.io の (0,0) に合わせるオフセット
    base_t = Transform.translate(
        -vb_x, -vb_y
    )  # viewBox の min-x, min-y を引いて原点を合わせる

    conv = Converter(samples=samples)  # Converter インスタンスを生成する
    conv.index_ids(svg_root)  # <use> 参照解決用に全要素の id を収集する
    conv.walk(svg_root, base_t, {})  # SVG ツリーを再帰的に走査して変換する

    # ページサイズの決定: viewBox が有効な場合はその値を、なければ変換後のバウンディングボックスを使う
    page_w = (
        vb_w
        if vb_w > 0
        else (conv.bbox[2] - conv.bbox[0] if conv.bbox[0] != float("inf") else 800)
    )
    page_h = (
        vb_h
        if vb_h > 0
        else (conv.bbox[3] - conv.bbox[1] if conv.bbox[1] != float("inf") else 600)
    )

    mxfile = conv.build_mxfile(page_w, page_h)  # draw.io の XML ツリーを構築する
    ET.indent(mxfile, space="  ")  # XML を2スペースインデントで整形する
    out_tree = ET.ElementTree(mxfile)  # ElementTree でラップする
    output_path.parent.mkdir(
        parents=True, exist_ok=True
    )  # 出力先ディレクトリを必要に応じて作成する
    out_tree.write(
        output_path, encoding="utf-8", xml_declaration=True
    )  # UTF-8 の XML 宣言付きで書き出す
    print(
        f"wrote {output_path} ({len(conv.cells)} cells)"
    )  # 書き出し完了と生成セル数を表示する
    return 0  # 終了コード 0 で成功を示す


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Convert SVG to draw.io (.drawio) file."
    )  # 引数パーサを生成する
    p.add_argument(
        "input", help="input SVG file"
    )  # 変換元 SVG ファイルの引数を定義する
    p.add_argument(
        "-o", "--output", help="output .drawio file (default: input with .drawio ext)"
    )  # 出力ファイルオプション
    p.add_argument(
        "--samples",
        type=int,
        default=16,
        help="sampling points for Bezier/arc curves (default: 16)",
    )  # 曲線サンプリング点数オプション
    args = p.parse_args(argv)  # コマンドライン引数を解析する

    input_path = Path(args.input)  # 入力パスを Path オブジェクトに変換する
    if not input_path.exists():
        print(
            f"error: input not found: {input_path}", file=sys.stderr
        )  # 入力ファイルが存在しない場合はエラー
        return 1
    # 出力パスが指定されていれば使い、なければ入力パスの拡張子を .drawio に替えて使う
    output_path = (
        Path(args.output) if args.output else input_path.with_suffix(".drawio")
    )
    return convert_file(
        input_path, output_path, args.samples
    )  # ファイル変換を実行して終了コードを返す


if __name__ == "__main__":
    sys.exit(
        main()
    )  # スクリプトとして実行された場合に main() を呼び出し、終了コードをシェルに返す
