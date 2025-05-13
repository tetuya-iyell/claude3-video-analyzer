# DynamoDB統合機能

## 概要
Claude3 Video Analyzerに「DynamoDB統合」機能を追加しました。この機能により、生成された台本データをAWS DynamoDBに保存し、異なるセッション間でデータを共有・同期することが可能になりました。環境変数による簡単な設定で、DynamoDB機能のオン/オフを切り替えることができます。

## 主な機能

### 1. 台本データの永続化
- 生成されたスクリプトをAWS DynamoDBに保存
- セッション間でのデータ共有と同期
- フィードバック履歴の保持と統合

### 2. 認証情報の自動リフレッシュ
- AWS認証トークンの有効期限切れを自動検出
- 認証情報の透過的な更新処理
- 安定したAWSサービス連携

### 3. テーブル自動作成機能
- 必要なDynamoDBテーブルの自動検出と生成
- スクリプトテーブルとマージドスクリプトテーブルの管理
- インデックス付きテーブル構造による高速検索

### 4. セッション管理
- セッションIDによるユーザーデータの管理
- 異なる環境間でのデータ同期
- ローカルセッションとクラウドデータの統合

## 技術実装

### 1. モジュール構成
- `dynamodb_client.py`: DynamoDB操作の中核機能を提供
- `aws_credentials.py`: AWS認証情報の管理と自動リフレッシュ
- `api_controller.py`: APIエンドポイント処理とDynamoDB連携
- `api_routes.py`: BluprintパターンによるAPI定義

### 2. AWS認証設計
- `CredentialManager`クラスによるAWS認証情報の一元管理
- セッションベースのクライアント・リソース提供
- `with_aws_credential_refresh`デコレーターによる透過的なエラー処理と再試行

### 3. データモデル
- スクリプトテーブル: 個別章の台本データを保存
  - プライマリーキー: `script_id`
  - インデックス: `session_id-index`
- マージドスクリプトテーブル: 結合された台本データを保存
  - プライマリーキー: `merged_id` 
  - インデックス: `session_id-index`

### 4. APIエンドポイント
- `/api/bedrock-scripts/sync-with-dynamodb`: DynamoDBとのデータ同期

## 設定方法
`.env`ファイルで以下の項目を設定可能:

```
# DynamoDB統合設定
DYNAMODB_ENABLED=true
DYNAMODB_SCRIPTS_TABLE=YukkuriScripts
DYNAMODB_MERGED_SCRIPTS_TABLE=YukkuriMergedScripts

# AWS認証情報
AWS_ACCESS_KEY_ID=your_key
AWS_SECRET_ACCESS_KEY=your_secret
AWS_SESSION_TOKEN=your_token  # 必要な場合のみ
AWS_REGION=us-east-1
```

## 使用方法

1. 環境変数で`DYNAMODB_ENABLED=true`を設定
2. アプリケーションを起動すると、テーブル存在確認と必要に応じた作成が行われる
3. 台本生成・分析・フィードバックなどの操作を通常通り実行
4. 各操作でデータが自動的にDynamoDBと同期される
5. 異なるセッションでも「DynamoDB同期」ボタンで最新データを取得可能

## エラー処理

- DynamoDB接続エラー時: ローカルファイルシステムに自動フォールバック
- 認証エラー時: 認証情報の自動リフレッシュを試行
- ネットワークエラー時: 適切なエラーメッセージを表示し、ローカルデータで継続

## 今後の展望

1. **複数ユーザー対応**:
   - ユーザー認証システムとの連携
   - ユーザーごとのデータ分離とアクセス制御

2. **データ共有機能の強化**:
   - 台本の共同編集機能
   - チームでの台本レビュー・承認ワークフロー

3. **検索・フィルター機能**:
   - 保存された台本の全文検索
   - メタデータによるフィルタリングとソート

4. **バージョン管理**:
   - 台本の変更履歴追跡
   - 特定バージョンへのロールバック機能

5. **S3連携による大容量データ対応**:
   - 長い台本や追加リソースのS3保存
   - スケーラブルなデータ管理