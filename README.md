# Claude3 Video Analyzer

Claude3 Video Analyzerは、Anthropic社のClaude-3モデルのマルチモーダル機能を利用して、MP4形式の動画をプロンプトに基づいて解析するPythonプロジェクトです。Webブラウザから動画をアップロードして簡単に解析できるインターフェースを備えています。

## 主な機能

- MP4動画からフレームを抽出し、base64エンコードされた画像データに変換
- プロンプトとエンコードされた画像データをClaude-3モデルに送信し、動画の内容を解析
- 解析結果をテキストとして出力
- Webインターフェースでの動画アップロード、プロンプトの編集と解析機能
- ドラッグ＆ドロップによる簡単な動画選択
- **章立て解析機能** で動画の内容を構造化された形式で出力（[詳細はこちら](docs/chapter-analysis-feature.md)）
- **ゆっくり不動産台本生成機能** で章立て解析結果から台本を自動生成（[詳細はこちら](docs/goose-script-generator.md)）
- **DynamoDB統合機能** で台本データをAWSクラウドと同期（[詳細はこちら](docs/dynamodb-integration.md)）

## 必要条件

- Python 3.10.13以上
- AnthropicのAPIキーまたはAWS認証情報（Bedrockアクセス権限付き）

## インストール

1. リポジトリをクローンします:
```
git clone https://github.com/Olemi-llm-apprentice/claude3-video-analyzer.git
```
2. プロジェクトディレクトリに移動します:
```
cd claude3-video-analyzer
```

3. 必要なPythonパッケージをインストールします:
```
pip install -r requirements.txt
```

4. 環境変数を設定します:
- `.env.example` ファイルを `.env` にコピーし、必要な認証情報を記入します。
- Anthropic APIを直接利用する場合は、`MODE=anthropic`とし、`ANTHROPIC_API_KEY`を設定します。
- AWS Bedrockを利用する場合は、`MODE=bedrock`とし、AWS認証情報を設定します。
  - AWS_ACCESS_KEY_ID と AWS_SECRET_ACCESS_KEY を設定
  - AWS_REGION は必要に応じて変更（デフォルト: us-east-1）
  - MODEL_ID を使用したいモデルに設定（例: anthropic.claude-3-7-sonnet-20240229-v1:0）
  - IAMユーザーがBedrock APIへのアクセス権を持っていることを確認
- DynamoDB統合機能を利用する場合は、以下の設定を行います:
  - `DYNAMODB_ENABLED=true` に設定
  - `DYNAMODB_SCRIPTS_TABLE` と `DYNAMODB_MERGED_SCRIPTS_TABLE` でテーブル名を指定（デフォルト値のままでも可）
  - IAMユーザーがDynamoDBへのアクセス権を持っていることを確認

## 使用方法

### Webインターフェースを使用

1. 以下のコマンドでWebサーバーを起動します:

```
python main.py
```

2. ブラウザで `http://localhost:5000/` にアクセスします。

3. 以下の操作が可能です:
   - 動画ファイルをドラッグ＆ドロップ、またはファイル選択ダイアログから選択
   - 解析用のプロンプトをカスタマイズ（デフォルトプロンプトが初期表示されます）
   - 「解析開始」ボタンをクリックして動画解析を実行
   - 解析結果はリアルタイムに表示され、完了後にコピーや新規解析が可能

### 詳細なアプリケーション使用ガイド

1. **環境設定**
   - `.env.example` ファイルを `.env` にコピーします：`cp .env.example .env`
   - `.env` ファイルを編集して、Anthropic APIキーまたはAWS Bedrock認証情報を設定します
   - 使用するモデルを変更したい場合は、`MODEL_ID`の値を編集します

2. **サーバー起動**
   - `python main.py` コマンドを実行してWebサーバーを起動します
   - コンソールにサーバー情報や使用モデル、アクセスURLが表示されます

3. **Webインターフェースの使い方**
   - ブラウザで `http://localhost:5000/` にアクセスします
   - アップロードエリアをクリックするか、MP4ファイルを直接ドラッグ＆ドロップします
   - アップロードした動画が表示され、ファイル名が確認できます
   - 解析タイプを選択します（「通常解析」または「章立て解析」）
   - 必要に応じてプロンプトを編集します（各解析タイプごとにデフォルトプロンプトが用意されています）
   - 「解析開始」ボタンをクリックして処理を開始します

4. **解析プロセス**
   - 動画がサーバーにアップロードされ、フレームが抽出されます
   - 抽出されたフレームはClaude-3モデルに送信され、指定したプロンプトに基づいて解析されます
   - 解析結果はリアルタイムで画面に表示されます（テキストが徐々に生成されます）

5. **結果の活用**
   - 解析が完了したら「結果をコピー」ボタンを押して、テキストをクリップボードにコピーできます
   - 「新しい解析」ボタンをクリックして別の動画を解析できます

6. **注意事項**
   - 大きなサイズの動画ファイルはフレーム抽出に時間がかかる場合があります
   - 解析されるフレーム数は最大20枚に制限されています（パフォーマンス向上のため）
   - 解析結果はサーバーに保存されず、セッション終了時に破棄されます

### コマンドライン使用（従来の方法）

`resources` ディレクトリに動画ファイルを配置し、以下のようにコードを編集して実行することもできます:

```python
from src.claude3_video_analyzer import VideoAnalyzer

analyzer = VideoAnalyzer()
video_file_path = "resources/your_video.mp4"
prompt = "これは動画のフレーム画像です。動画の最初から最後の流れ、動作を微分して日本語で解説してください。"

# 解析を実行
result = analyzer.analyze_video(video_file_path, prompt)
print(result)
```

- `video_file_path` に解析したい動画ファイルのパスを指定します。
- `prompt` に動画解析のためのプロンプトを指定します。


## トラブルシューティング

### よくある問題と解決方法

1. **サーバー起動エラー**
   - `ModuleNotFoundError`: 必要なパッケージがインストールされていません。`pip install -r requirements.txt` を実行してください。
   - `ValueError: ANTHROPIC_API_KEY not found`: `.env` ファイルにAPIキーが設定されていません。正しい認証情報を追加してください。

2. **動画解析エラー**
   - 「ビデオファイルを開けませんでした」: ファイル形式がMP4であることを確認してください。
   - 「フレームを抽出できませんでした」: 動画ファイルが破損していないか確認してください。
   - Bedrock APIエラー: AWS認証情報と指定したモデルIDが正しいことを確認してください。

3. **ブラウザの問題**
   - アップロードが機能しない: 最新のブラウザを使用しているか確認してください。
   - ページが表示されない: サーバーが正常に起動していることを確認し、`http://localhost:5000/` にアクセスしてください。

## サポートとフィードバック

問題や提案がある場合は、GitHub上でIssueを作成するか、Pull Requestを送信してください。

## ライセンス
このプロジェクトはMITライセンスの下で公開されています。詳細については、LICENSEファイルを参照してください。

