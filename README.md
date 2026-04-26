# SVG to draw.io Tools / SVGからdraw.ioへの変換ツール

This directory documents two Python tools for converting SVG files into editable draw.io (`.drawio`) files.

このドキュメントでは、SVGファイルを編集可能なdraw.io形式（`.drawio`）へ変換する2つのPythonツールの使い方を説明します。

- `tools/svg_to_drawio.py`: command-line converter / コマンドライン変換ツール
- `tools/svg_to_drawio_gui.py`: GUI wrapper for the converter / GUI操作用ツール

## Requirements / 必要環境

- Python 3.10 or later is recommended.
- No third-party Python packages are required.
- The GUI version uses `tkinter`, which is included in many standard Python installations.

日本語:

- Python 3.10以降を推奨します。
- 外部Pythonパッケージは不要です。
- GUI版は標準ライブラリの `tkinter` を使います。通常のPythonには含まれています。

## What It Converts / 変換できるもの

The converter reads SVG XML and creates draw.io `mxCell` elements. It supports common SVG elements such as:

- `rect`
- `circle`
- `ellipse`
- `line`
- `polyline`
- `polygon`
- `text`
- `path`
- `g`
- `use`

日本語:

このツールはSVG XMLを読み込み、draw.ioの `mxCell` 要素へ変換します。主に次のSVG要素に対応しています。

- `rect`
- `circle`
- `ellipse`
- `line`
- `polyline`
- `polygon`
- `text`
- `path`
- `g`
- `use`

## Command-Line Tool / コマンドライン版

File:

```text
tools/svg_to_drawio.py
```

Basic usage:

```powershell
python tools\svg_to_drawio.py input.svg
```

This creates:

```text
input.drawio
```

日本語:

基本的な使い方は次の通りです。

```powershell
python tools\svg_to_drawio.py input.svg
```

この場合、入力ファイルと同じ場所に `input.drawio` が作成されます。

### Specify Output File / 出力ファイルを指定する

```powershell
python tools\svg_to_drawio.py input.svg -o output.drawio
```

or:

```powershell
python tools\svg_to_drawio.py input.svg --output output.drawio
```

日本語:

出力先を明示したい場合は `-o` または `--output` を使います。

```powershell
python tools\svg_to_drawio.py input.svg -o output.drawio
```

### Adjust Curve Sampling / 曲線のサンプリング数を調整する

SVG paths that contain Bezier curves or arcs are sampled into points. The default value is `16`.

```powershell
python tools\svg_to_drawio.py input.svg --samples 32
```

Higher values can make curves smoother, but may create larger `.drawio` files.

日本語:

SVGのベジェ曲線や円弧は点列へ近似して変換されます。標準値は `16` です。

```powershell
python tools\svg_to_drawio.py input.svg --samples 32
```

値を大きくすると曲線が滑らかになりやすい一方で、`.drawio` ファイルサイズが大きくなる場合があります。

### CLI Help / ヘルプ表示

```powershell
python tools\svg_to_drawio.py --help
```

## GUI Tool / GUI版

File:

```text
tools/svg_to_drawio_gui.py
```

Start the GUI:

```powershell
python tools\svg_to_drawio_gui.py
```

日本語:

GUI版は次のコマンドで起動します。

```powershell
python tools\svg_to_drawio_gui.py
```

### GUI Workflow / GUIでの操作手順

English:

1. Click `Browse...` next to `Input SVG`.
2. Select the SVG file you want to convert.
3. Confirm or edit the `Output .drawio` path.
4. Set `Samples` if you want to change curve quality.
5. Click `Convert`.
6. Check the log area for conversion messages.

日本語:

1. `Input SVG` の横にある `Browse...` を押します。
2. 変換したいSVGファイルを選択します。
3. `Output .drawio` の出力先を確認、または編集します。
4. 曲線の変換品質を調整したい場合は `Samples` を変更します。
5. `Convert` を押します。
6. 変換結果や警告はログ欄で確認します。

## Output / 出力結果

The generated `.drawio` file can be opened with:

- draw.io desktop app
- diagrams.net
- VS Code extensions that support draw.io files

日本語:

生成された `.drawio` ファイルは、次のようなツールで開けます。

- draw.io デスクトップアプリ
- diagrams.net
- draw.io形式に対応したVS Code拡張機能

## Notes and Limitations / 注意点と制限

English:

- This tool converts SVG elements into editable draw.io shapes where possible.
- Some complex SVG features may not be reproduced exactly.
- Unsupported elements may be skipped with a warning.
- Embedded raster images in SVG are not the main target of this converter.
- Very complex paths may create many draw.io cells or large stencil data.

日本語:

- このツールは、可能な範囲でSVG要素を編集可能なdraw.io図形へ変換します。
- 複雑なSVG表現は完全には再現できない場合があります。
- 未対応の要素は警告を出してスキップされる場合があります。
- SVG内に埋め込まれたラスター画像は主な変換対象ではありません。
- 非常に複雑なpathは、多数のdraw.ioセルや大きなステンシルデータになる場合があります。

## Troubleshooting / トラブルシューティング

### `input not found`

English:

Check that the input SVG path is correct.

日本語:

入力SVGファイルのパスが正しいか確認してください。

### GUI does not start / GUIが起動しない

English:

Your Python installation may not include `tkinter`. Install a Python distribution that includes Tk support.

日本語:

Python環境に `tkinter` が含まれていない可能性があります。Tk対応のPythonを使用してください。

### Curves look rough / 曲線が粗く見える

English:

Increase the `--samples` value in the CLI, or increase `Samples` in the GUI.

日本語:

CLI版では `--samples` の値を上げてください。GUI版では `Samples` の値を上げてください。

Example:

```powershell
python tools\svg_to_drawio.py input.svg --samples 32
```

## Examples / 使用例

CLI:

```powershell
python tools\svg_to_drawio.py tools\sample\basic.svg
python tools\svg_to_drawio.py tools\sample\basic.svg -o tools\sample\basic.drawio
python tools\svg_to_drawio.py tools\sample\basic.svg --samples 32
```

GUI:

```powershell
python tools\svg_to_drawio_gui.py
```

## Recommended Use / おすすめの使い分け

English:

- Use `tools/svg_to_drawio_gui.py` when converting files manually.
- Use `tools/svg_to_drawio.py` when scripting, testing, or batch converting.

日本語:

- 手作業で変換する場合は `tools/svg_to_drawio_gui.py` が便利です。
- スクリプト化、テスト、まとめて変換する場合は `tools/svg_to_drawio.py` が便利です。
