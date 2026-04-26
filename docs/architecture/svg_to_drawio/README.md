# svg_to_drawio.py アーキテクチャ

このフォルダには、`tools/svg_to_drawio.py` の構造を説明するPlantUML図を置いています。

- `svg_to_drawio_class.puml`: 主要クラスと補助関数の関係
- `svg_to_drawio_activity.puml`: CLI実行から`.drawio`出力までの処理フロー
- `svg_to_drawio_sequence.puml`: 実行時の代表的な呼び出し順序

設計の中心は `Converter` です。`convert_file()` がSVGを読み込み、`Converter.walk()` がSVG要素を再帰的にたどり、要素ごとの `handle_*` メソッドが draw.io の `mxCell` を作ります。`Transform` はSVG座標変換、`PathParser` は `<path>` の点列化を担当します。
