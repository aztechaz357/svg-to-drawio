"""GUI wrapper for svg_to_drawio.py."""  # このファイルがSVGからdraw.ioへ変換するGUIラッパーであることを示す

from __future__ import annotations  # 型ヒントの評価を遅延して前方参照を扱いやすくする

import contextlib  # 標準出力と標準エラー出力を一時的に差し替えるために使う
import io  # 例外の詳細文字列をメモリ上に組み立てるために使う
import queue  # ワーカースレッドからGUIスレッドへログや完了通知を渡すために使う
import sys  # プログラム終了時に終了コードを返すために使う
import threading  # 変換処理中もGUIが固まらないように別スレッドを使う
import traceback  # 予期しない例外のスタックトレースをログに出すために使う
from pathlib import Path  # ファイルパスを安全に組み立てるために使う
from tkinter import (  # tkinterの変数、ルート画面、ファイル選択、メッセージ表示を使う
    BooleanVar,
    IntVar,
    StringVar,
    Tk,
    filedialog,
    messagebox,
    ttk,  # tkinterの見た目が整った標準ウィジェットを使う
)

import svg_to_drawio  # 同じフォルダの変換本体モジュールを呼び出す


class QueueWriter:  # print出力をGUIログ用キューへ流すための簡易ライターを定義する
    def __init__(
        self, messages: queue.Queue[tuple[str, str]]
    ):  # ログ受け渡し用キューを受け取って初期化する
        self.messages = messages  # 受け取ったキューをインスタンスに保存する

    def write(self, text: str) -> int:  # stdout/stderr互換のwriteメソッドを用意する
        if text:  # 空文字でなければログとして扱う
            self.messages.put(("log", text))  # GUIスレッドへ表示用ログを送る
        return len(text)  # 書き込んだ文字数として受け取った文字数を返す

    def flush(self) -> None:  # stdout/stderr互換のflushメソッドを用意する
        pass  # キューへ即時投入しているのでflushでは何もしない


class SvgToDrawioApp:  # SVGからdraw.ioへ変換するGUIアプリ本体を定義する
    def __init__(self, root: Tk):  # Tkのルート画面を受け取ってアプリを初期化する
        self.root = root  # ルート画面を後続メソッドから使えるように保存する
        self.root.title("SVG to draw.io")  # ウィンドウタイトルを設定する
        self.root.minsize(680, 420)  # 操作しやすい最小ウィンドウサイズを設定する
        self.input_var = StringVar()  # 入力SVGパスを保持するtkinter文字列変数を作る
        self.output_var = StringVar()  # 出力drawioパスを保持するtkinter文字列変数を作る
        self.samples_var = IntVar(
            value=16
        )  # 曲線近似のサンプル数を保持する整数変数を作る
        self.auto_output_var = BooleanVar(
            value=True
        )  # 出力先を入力ファイル名から自動補完するかを保持する真偽値変数を作る
        self.status_var = StringVar(
            value="Ready"
        )  # 画面下部へ表示する状態文字列を保持する変数を作る
        self.messages: queue.Queue[tuple[str, str]] = (
            queue.Queue()
        )  # ワーカースレッドからGUIへ渡すメッセージキューを作る
        self.worker: threading.Thread | None = (
            None  # 実行中の変換スレッドを保持する変数を初期化する
        )
        self._build_ui()  # GUI部品を作成して画面へ配置する
        self._poll_messages()  # キュー監視を開始してログや完了通知を画面へ反映する

    def _build_ui(self) -> None:  # 画面上の入力欄、ボタン、ログ欄を構築する
        self.root.columnconfigure(
            0, weight=1
        )  # ウィンドウ横方向の拡大をメイン領域へ割り当てる
        self.root.rowconfigure(
            0, weight=1
        )  # ウィンドウ縦方向の拡大をメイン領域へ割り当てる
        main = ttk.Frame(self.root, padding=12)  # 余白付きのメインフレームを作る
        main.grid(
            row=0, column=0, sticky="nsew"
        )  # メインフレームをウィンドウ全体へ広げて配置する
        main.columnconfigure(1, weight=1)  # パス入力欄の列が横方向に伸びるようにする
        main.rowconfigure(5, weight=1)  # ログ欄の行が縦方向に伸びるようにする
        ttk.Label(main, text="Input SVG").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=4
        )  # 入力SVG欄のラベルを配置する
        input_entry = ttk.Entry(
            main, textvariable=self.input_var
        )  # 入力SVGパスを表示編集する入力欄を作る
        input_entry.grid(
            row=0, column=1, sticky="ew", pady=4
        )  # 入力欄を横に伸びる形で配置する
        ttk.Button(main, text="Browse...", command=self.choose_input).grid(
            row=0, column=2, padx=(8, 0), pady=4
        )  # 入力ファイル選択ボタンを配置する
        ttk.Label(main, text="Output .drawio").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=4
        )  # 出力ファイル欄のラベルを配置する
        output_entry = ttk.Entry(
            main, textvariable=self.output_var
        )  # 出力drawioパスを表示編集する入力欄を作る
        output_entry.grid(
            row=1, column=1, sticky="ew", pady=4
        )  # 出力欄を横に伸びる形で配置する
        ttk.Button(main, text="Browse...", command=self.choose_output).grid(
            row=1, column=2, padx=(8, 0), pady=4
        )  # 出力先選択ボタンを配置する
        options = ttk.Frame(main)  # 自動出力とsamples設定を並べるフレームを作る
        options.grid(
            row=2, column=1, sticky="w", pady=6
        )  # オプションフレームを入力欄の下へ配置する
        ttk.Checkbutton(  # 出力先自動補完のチェックボックスを作り始める
            options,  # チェックボックスをオプションフレーム内へ置く
            text="Use input filename when output is empty",  # チェックボックスに表示する文言を指定する
            variable=self.auto_output_var,  # チェック状態を保持する変数を指定する
            command=self.update_default_output,  # チェック操作時に出力先候補を更新する
        ).grid(row=0, column=0, sticky="w")  # チェックボックスを左寄せで配置する
        ttk.Label(options, text="Samples").grid(
            row=0, column=1, padx=(24, 8)
        )  # samples入力欄のラベルを配置する
        ttk.Spinbox(
            options, from_=2, to=128, width=6, textvariable=self.samples_var
        ).grid(row=0, column=2)  # samples値を上下ボタンで指定できる欄を配置する
        actions = ttk.Frame(main)  # 実行ボタン類を並べるフレームを作る
        actions.grid(
            row=3, column=1, sticky="w", pady=(4, 10)
        )  # 実行ボタン類をオプション欄の下に配置する
        self.convert_button = ttk.Button(
            actions, text="Convert", command=self.convert
        )  # 変換開始ボタンを作る
        self.convert_button.grid(row=0, column=0)  # 変換開始ボタンを左端に配置する
        ttk.Button(actions, text="Clear Log", command=self.clear_log).grid(
            row=0, column=1, padx=(8, 0)
        )  # ログ消去ボタンを変換ボタンの右へ配置する
        ttk.Label(main, textvariable=self.status_var).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(0, 4)
        )  # 現在状態を表示するラベルを配置する
        log_frame = ttk.Frame(main)  # ログ表示欄とスクロールバーを入れるフレームを作る
        log_frame.grid(
            row=5, column=0, columnspan=3, sticky="nsew"
        )  # ログフレームを画面下部いっぱいへ配置する
        log_frame.columnconfigure(0, weight=1)  # ログ本文の列が横方向に伸びるようにする
        log_frame.rowconfigure(0, weight=1)  # ログ本文の行が縦方向に伸びるようにする
        self.log = self._make_log(
            log_frame
        )  # ログ用テキストウィジェットを作成して保持する

    def _make_log(
        self, parent: ttk.Frame
    ):  # ログ表示用テキスト欄とスクロールバーを作る
        from tkinter import (
            Text,  # 複数行テキスト表示用ウィジェットを必要な場所で読み込む
        )

        text = Text(
            parent, height=12, wrap="word", state="disabled"
        )  # 編集不可の複数行ログ欄を作る
        scrollbar = ttk.Scrollbar(
            parent, orient="vertical", command=text.yview
        )  # ログ欄を縦スクロールするバーを作る
        text.configure(
            yscrollcommand=scrollbar.set
        )  # ログ欄のスクロール位置をスクロールバーへ同期する
        text.grid(
            row=0, column=0, sticky="nsew"
        )  # ログ欄を親フレームいっぱいへ配置する
        scrollbar.grid(
            row=0, column=1, sticky="ns"
        )  # スクロールバーをログ欄の右に縦いっぱいへ配置する
        return text  # 作成したログ欄を呼び出し元へ返す

    def choose_input(self) -> None:  # 入力SVGファイルを選択するダイアログを開く
        filename = filedialog.askopenfilename(  # ファイルを開くダイアログを表示して選択結果を受け取る
            title="Select SVG file",  # ダイアログのタイトルを指定する
            filetypes=[
                ("SVG files", "*.svg"),
                ("All files", "*.*"),
            ],  # SVGを優先して選べるファイル種類を指定する
        )  # ファイル選択ダイアログの呼び出しを終える
        if not filename:  # キャンセルされた場合を判定する
            return  # キャンセル時は何も変更せず戻る
        self.input_var.set(filename)  # 選ばれた入力ファイルパスを画面の入力欄へ反映する
        self.update_default_output()  # 入力ファイルに合わせて出力先候補を更新する

    def choose_output(self) -> None:  # 出力drawioファイルを選択するダイアログを開く
        initial = self._default_output_path()  # 入力ファイル名から初期出力先候補を作る
        filename = filedialog.asksaveasfilename(  # 保存先を選ぶダイアログを表示して結果を受け取る
            title="Save draw.io file",  # 保存ダイアログのタイトルを指定する
            defaultextension=".drawio",  # 拡張子未指定時に.drawioを付ける
            initialfile=initial.name
            if initial
            else "",  # 初期ファイル名を入力ファイル由来の名前にする
            initialdir=str(initial.parent)
            if initial
            else "",  # 初期フォルダを入力ファイルの場所にする
            filetypes=[
                ("draw.io files", "*.drawio"),
                ("XML files", "*.xml"),
                ("All files", "*.*"),
            ],  # 保存候補のファイル種類を指定する
        )  # 保存先選択ダイアログの呼び出しを終える
        if filename:  # 保存先が選択された場合を判定する
            self.output_var.set(filename)  # 選ばれた出力パスを画面の出力欄へ反映する

    def update_default_output(
        self,
    ) -> None:  # 入力SVGから標準の出力drawioパスを更新する
        if (
            self.auto_output_var.get() and self.input_var.get()
        ):  # 自動補完が有効で入力パスがある場合だけ処理する
            default = (
                self._default_output_path()
            )  # 入力パスの拡張子を.drawioにした候補を作る
            if default:  # 候補パスが作れた場合を判定する
                self.output_var.set(str(default))  # 候補パスを出力欄へ設定する

    def _default_output_path(
        self,
    ) -> Path | None:  # 入力ファイルに対応する標準出力パスを返す
        if not self.input_var.get():  # 入力欄が空かどうかを判定する
            return None  # 入力がない場合は候補なしを返す
        return Path(self.input_var.get()).with_suffix(
            ".drawio"
        )  # 入力パスの拡張子だけ.drawioへ置き換えて返す

    def convert(self) -> None:  # 画面入力を検証して変換処理を開始する
        if (
            self.worker and self.worker.is_alive()
        ):  # すでに変換スレッドが動いているか確認する
            return  # 実行中なら二重起動せず戻る
        input_path = Path(
            self.input_var.get().strip()
        )  # 入力欄の文字列を前後空白除去してPathに変換する
        output_text = (
            self.output_var.get().strip()
        )  # 出力欄の文字列を前後空白除去して取得する
        output_path = (
            Path(output_text) if output_text else input_path.with_suffix(".drawio")
        )  # 出力欄が空なら入力名由来の.drawioパスを使う
        if not input_path.exists():  # 入力ファイルが存在するか確認する
            messagebox.showerror(
                "Input not found", f"Input SVG was not found:\n{input_path}"
            )  # 入力ファイルがないことをエラーダイアログで知らせる
            return  # 入力が不正なので変換を中止する
        if (
            input_path.suffix.lower() != ".svg"
        ):  # 入力ファイルの拡張子が.svgではないか確認する
            if not messagebox.askyesno(
                "Confirm input", "The input file does not end with .svg. Continue?"
            ):  # SVG以外でも続行するか確認する
                return  # 利用者が続行しない場合は変換を中止する
        try:  # samples値の整数変換で起きる例外を捕捉する
            samples = int(
                self.samples_var.get()
            )  # 画面上のsamples値を整数として取得する
        except Exception:  # 整数として読めなかった場合を処理する
            messagebox.showerror(
                "Invalid samples", "Samples must be an integer."
            )  # samplesが整数でないことを知らせる
            return  # samplesが不正なので変換を中止する
        if samples < 2:  # samplesが最小値を満たすか確認する
            messagebox.showerror(
                "Invalid samples", "Samples must be 2 or greater."
            )  # samplesが小さすぎることを知らせる
            return  # samplesが不正なので変換を中止する
        self.convert_button.configure(
            state="disabled"
        )  # 変換中の二重クリックを防ぐため実行ボタンを無効化する
        self.status_var.set("Converting...")  # 状態表示を変換中に更新する
        self.append_log(
            f"Input : {input_path}\nOutput: {output_path}\nSamples: {samples}\n"
        )  # 変換開始時の入力、出力、samplesをログへ出す
        self.worker = threading.Thread(  # 変換処理をGUIとは別のスレッドとして作成する
            target=self._convert_worker,  # スレッドで実行するメソッドを指定する
            args=(
                input_path,
                output_path,
                samples,
            ),  # スレッドへ入力パス、出力パス、samples値を渡す
            daemon=True,  # アプリ終了時にワーカースレッドが残っても終了できるようにする
        )  # スレッドオブジェクトの作成を終える
        self.worker.start()  # 変換処理のスレッドを開始する

    def _convert_worker(
        self, input_path: Path, output_path: Path, samples: int
    ) -> None:  # バックグラウンドでSVG変換を実行する
        stdout = QueueWriter(self.messages)  # 標準出力をGUIログへ流すライターを作る
        stderr = QueueWriter(
            self.messages
        )  # 標準エラー出力をGUIログへ流すライターを作る
        try:  # 変換中の通常エラーと予期しない例外を分けて扱う
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):  # 変換処理のprint出力をログキューへ差し替える
                code = svg_to_drawio.convert_file(
                    input_path, output_path, samples
                )  # 既存の変換関数を呼び出して終了コードを受け取る
            if code == 0:  # 変換関数が正常終了したか確認する
                self.messages.put(
                    ("done", f"Done: {output_path}")
                )  # 完了メッセージをGUIスレッドへ送る
            else:  # 変換関数がエラーコードを返した場合を処理する
                self.messages.put(
                    ("error", f"Conversion failed with exit code {code}.")
                )  # エラーコード付きの失敗通知をGUIスレッドへ送る
        except Exception:  # 変換関数外も含めた予期しない例外を捕捉する
            buf = (
                io.StringIO()
            )  # スタックトレースを書き込むメモリ上の文字列バッファを作る
            traceback.print_exc(file=buf)  # 捕捉した例外の詳細をバッファへ書き込む
            self.messages.put(
                ("log", buf.getvalue())
            )  # 例外の詳細をログとしてGUIスレッドへ送る
            self.messages.put(
                ("error", "Conversion failed with an unexpected error.")
            )  # 予期しない失敗としてGUIスレッドへ通知する

    def _poll_messages(
        self,
    ) -> None:  # ワーカースレッドから届いたメッセージを定期的に処理する
        try:  # キューが空になるまで取り出す処理で空キュー例外を捕捉する
            while True:  # 現時点で届いているメッセージをすべて処理する
                kind, text = (
                    self.messages.get_nowait()
                )  # キューから待たずにメッセージ種別と本文を取り出す
                if kind == "log":  # メッセージがログ本文か確認する
                    self.append_log(text)  # ログ本文をログ欄へ追記する
                elif kind == "done":  # メッセージが正常完了通知か確認する
                    self.append_log(text + "\n")  # 完了内容をログ欄へ追記する
                    self.status_var.set(text)  # 状態表示を完了内容へ更新する
                    self.convert_button.configure(
                        state="normal"
                    )  # 次の変換ができるように実行ボタンを有効化する
                    messagebox.showinfo(
                        "Conversion complete", text
                    )  # 完了ダイアログを表示する
                elif kind == "error":  # メッセージが失敗通知か確認する
                    self.append_log(text + "\n")  # 失敗内容をログ欄へ追記する
                    self.status_var.set(text)  # 状態表示を失敗内容へ更新する
                    self.convert_button.configure(
                        state="normal"
                    )  # 再実行できるように実行ボタンを有効化する
                    messagebox.showerror(
                        "Conversion failed", text
                    )  # 失敗ダイアログを表示する
        except queue.Empty:  # キューに処理対象がなくなった場合を処理する
            pass  # 空キューは正常なので何もしない
        self.root.after(100, self._poll_messages)  # 100ミリ秒後に再びキューを確認する

    def append_log(self, text: str) -> None:  # ログ欄へ文字列を追記する
        self.log.configure(
            state="normal"
        )  # 追記できるようにログ欄を一時的に編集可能へする
        self.log.insert("end", text)  # ログ欄の末尾へ文字列を挿入する
        self.log.see("end")  # 追記した末尾が見える位置までスクロールする
        self.log.configure(
            state="disabled"
        )  # 利用者が編集できないようにログ欄を再び無効化する

    def clear_log(self) -> None:  # ログ欄の内容を消去する
        self.log.configure(
            state="normal"
        )  # 削除できるようにログ欄を一時的に編集可能へする
        self.log.delete("1.0", "end")  # ログ欄の先頭から末尾までを削除する
        self.log.configure(
            state="disabled"
        )  # 利用者が編集できないようにログ欄を再び無効化する


def main() -> int:  # アプリ起動時のエントリーポイントを定義する
    root = Tk()  # tkinterのルートウィンドウを作成する
    SvgToDrawioApp(root)  # ルートウィンドウ上にアプリ画面を構築する
    root.mainloop()  # GUIイベントループを開始して操作を待ち受ける
    return 0  # GUI終了後に正常終了コードを返す


if __name__ == "__main__":  # このファイルが直接実行された場合だけ起動処理を行う
    sys.exit(main())  # mainの終了コードをプロセス終了コードとして返す
