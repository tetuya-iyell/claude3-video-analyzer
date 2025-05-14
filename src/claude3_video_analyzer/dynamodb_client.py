"""
DynamoDBクライアントクラス - スクリプト履歴の保存と取得
"""

import os
import boto3
import logging
import uuid
import json
import time
from datetime import datetime
from typing import Dict, Any, Optional, List
from .aws_credentials import with_aws_credential_refresh

# ロガー設定
logger = logging.getLogger(__name__)


class DynamoDBClient:
    """DynamoDBとの連携を処理するクライアントクラス"""

    def __init__(self):
        """初期化

        環境変数から設定を読み込み、DynamoDBクライアントを初期化します。
        """
        # 環境変数から設定を読み込む
        self.enabled = os.environ.get("DYNAMODB_ENABLED", "false").lower() in (
            "true",
            "yes",
            "1",
        )
        self.scripts_table_name = os.environ.get(
            "DYNAMODB_SCRIPTS_TABLE", "YukkuriScripts"
        )
        self.merged_scripts_table_name = os.environ.get(
            "DYNAMODB_MERGED_SCRIPTS_TABLE", "YukkuriMergedScripts"
        )
        self.region = os.environ.get("AWS_REGION", "us-east-1")

        # DynamoDBが有効でない場合は初期化を終了
        if not self.enabled:
            logger.info("DynamoDBは無効に設定されています。データは保存されません。")
            return

        # 認証情報マネージャーを初期化して使用
        from .aws_credentials import CredentialManager

        self.credential_manager = CredentialManager(region_name=self.region)

        # DynamoDBリソースを初期化
        try:
            # セッションから認証情報を取得してDynamoDBリソースを初期化
            self.dynamodb = self.credential_manager.get_resource("dynamodb")
            self.scripts_table = self.dynamodb.Table(self.scripts_table_name)
            self.merged_scripts_table = self.dynamodb.Table(
                self.merged_scripts_table_name
            )
            logger.info(
                f"DynamoDBクライアントを初期化しました: リージョン={self.region}, スクリプトテーブル={self.scripts_table_name}"
            )

            # テーブルが存在することを確認（必要に応じて作成）
            self._ensure_tables_exist()
        except Exception as e:
            logger.error(f"DynamoDBクライアントの初期化に失敗しました: {str(e)}")
            raise

    def _ensure_tables_exist(self):
        """必要なテーブルが存在することを確認し、なければ作成する"""
        if not self.enabled:
            return

        # スクリプトテーブルの確認
        try:
            self.scripts_table.table_status
            logger.info(f"スクリプトテーブル {self.scripts_table_name} は存在します")
        except Exception:
            logger.info(f"スクリプトテーブル {self.scripts_table_name} を作成します")
            self._create_scripts_table()

        # 結合スクリプトテーブルの確認
        try:
            self.merged_scripts_table.table_status
            logger.info(
                f"結合スクリプトテーブル {self.merged_scripts_table_name} は存在します"
            )
        except Exception:
            logger.info(
                f"結合スクリプトテーブル {self.merged_scripts_table_name} を作成します"
            )
            self._create_merged_scripts_table()

    def _create_scripts_table(self):
        """スクリプトテーブルを作成する"""
        if not self.enabled:
            return

        # 認証情報を更新してテーブルを作成
        try:
            self.dynamodb.create_table(
                TableName=self.scripts_table_name,
                KeySchema=[
                    {"AttributeName": "script_id", "KeyType": "HASH"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "script_id", "AttributeType": "S"},
                    {"AttributeName": "session_id", "AttributeType": "S"},
                ],
                GlobalSecondaryIndexes=[
                    {
                        "IndexName": "session_id-index",
                        "KeySchema": [
                            {"AttributeName": "session_id", "KeyType": "HASH"},
                        ],
                        "Projection": {"ProjectionType": "ALL"},
                        "ProvisionedThroughput": {
                            "ReadCapacityUnits": 5,
                            "WriteCapacityUnits": 5,
                        },
                    },
                ],
                ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
            )
            logger.info(f"スクリプトテーブル {self.scripts_table_name} を作成しました")

            # テーブルが使用可能になるまで待機
            self.scripts_table.meta.client.get_waiter("table_exists").wait(
                TableName=self.scripts_table_name
            )
        except Exception as e:
            logger.error(f"スクリプトテーブルの作成に失敗しました: {str(e)}")
            raise

    def _create_merged_scripts_table(self):
        """結合スクリプトテーブルを作成する"""
        if not self.enabled:
            return

        # 認証情報を更新してテーブルを作成
        try:
            self.dynamodb.create_table(
                TableName=self.merged_scripts_table_name,
                KeySchema=[
                    {"AttributeName": "merged_id", "KeyType": "HASH"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "merged_id", "AttributeType": "S"},
                    {"AttributeName": "session_id", "AttributeType": "S"},
                ],
                GlobalSecondaryIndexes=[
                    {
                        "IndexName": "session_id-index",
                        "KeySchema": [
                            {"AttributeName": "session_id", "KeyType": "HASH"},
                        ],
                        "Projection": {"ProjectionType": "ALL"},
                        "ProvisionedThroughput": {
                            "ReadCapacityUnits": 5,
                            "WriteCapacityUnits": 5,
                        },
                    },
                ],
                ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
            )
            logger.info(
                f"結合スクリプトテーブル {self.merged_scripts_table_name} を作成しました"
            )

            # テーブルが使用可能になるまで待機
            self.merged_scripts_table.meta.client.get_waiter("table_exists").wait(
                TableName=self.merged_scripts_table_name
            )
        except Exception as e:
            logger.error(f"結合スクリプトテーブルの作成に失敗しました: {str(e)}")
            raise

    @with_aws_credential_refresh
    def save_script(
        self, session_id: str, chapter_index: int, script_data: Dict[str, Any]
    ) -> str:
        """台本をDynamoDBに保存する

        Args:
            session_id: セッションID
            chapter_index: 章インデックス
            script_data: 台本データ

        Returns:
            保存されたスクリプトのID
        """
        if not self.enabled:
            logger.info(
                "DynamoDBは無効に設定されています。スクリプトは保存されません。"
            )
            return "dynamodb-disabled"

        try:
            # 新規生成フラグに基づいてスクリプトIDを生成
            is_new_script = script_data.get("is_newly_generated", False)

            if is_new_script or "script_id" not in script_data:
                # より安全なスクリプトIDを生成：セッションID + チャプターインデックス + タイムスタンプ + 完全なUUID
                timestamp = int(time.time())
                script_id = (
                    f"{session_id}_{chapter_index}_{timestamp}_{uuid.uuid4().hex}"
                )
                logger.info(f"新しいスクリプトIDを生成しました: {script_id}")
            else:
                # 既存のIDを使用（更新の場合）
                script_id = script_data.get("script_id")

            # 既存のデータがあるか確認
            existing_item = None
            try:
                # インデックスがある場合はクエリを使用
                response = self.scripts_table.query(
                    IndexName="session_id-index",
                    KeyConditionExpression=boto3.dynamodb.conditions.Key(
                        "session_id"
                    ).eq(session_id),
                    FilterExpression=boto3.dynamodb.conditions.Attr("chapter_index").eq(
                        chapter_index
                    ),
                )

                if "Items" in response and len(response["Items"]) > 0:
                    # 既存のアイテムが見つかった場合
                    existing_item = response["Items"][0]
                    script_id = existing_item["script_id"]
                    logger.info(
                        f"既存のスクリプトが見つかりました: script_id={script_id}"
                    )

                    # フィードバック数の確認（デバッグ用）
                    if "feedback" in existing_item and isinstance(
                        existing_item["feedback"], list
                    ):
                        logger.info(
                            f"既存のフィードバック数: {len(existing_item['feedback'])}件"
                        )
                    else:
                        logger.info("既存のフィードバックはありません")
            except Exception as query_error:
                # クエリ時のエラーは無視して新規作成
                logger.warning(f"既存スクリプト検索でエラー: {str(query_error)}")

            # 保存するデータを整形
            item = {
                "script_id": script_id,
                "session_id": session_id,
                "chapter_index": chapter_index,
                "chapter_title": script_data.get("chapter_title", ""),
                "chapter_summary": script_data.get("chapter_summary", ""),
                "script_content": script_data.get("script_content", ""),
                "status": script_data.get("status", "draft"),
                "created_at": script_data.get("created_at", datetime.now().isoformat()),
                "updated_at": datetime.now().isoformat(),
                "duration_minutes": script_data.get("duration_minutes", 3),
            }

            # フィードバック処理
            # 新しいスクリプトデータにフィードバックが含まれているか確認
            has_new_feedback = "feedback" in script_data and isinstance(
                script_data["feedback"], list
            )
            if has_new_feedback:
                logger.info(
                    f"新しいスクリプトデータにフィードバックが含まれています: {len(script_data['feedback'])}件"
                )
            else:
                logger.info("新しいスクリプトデータにフィードバックはありません")

            # 既存のスクリプトデータにフィードバックが含まれているか確認
            has_existing_feedback = (
                existing_item
                and "feedback" in existing_item
                and isinstance(existing_item["feedback"], list)
            )
            if has_existing_feedback:
                logger.info(
                    f"既存のスクリプトデータにフィードバックが含まれています: {len(existing_item['feedback'])}件"
                )
            else:
                logger.info("既存のスクリプトデータにフィードバックはありません")

            # フィードバックの統合処理
            if has_new_feedback and has_existing_feedback:
                # 両方にフィードバックがある場合は統合
                new_feedback = script_data["feedback"]
                existing_feedback = existing_item["feedback"]

                # 新しいフィードバックと既存のフィードバックを統合（重複排除）
                # 重複を避けるためにセットを使用して一度ユニークにしてから再びリストに変換
                merged_feedback = list(set(existing_feedback + new_feedback))

                logger.info(
                    f"フィードバックを統合: 既存={len(existing_feedback)}件 + 新規={len(new_feedback)}件 → 合計={len(merged_feedback)}件"
                )

                # 統合されたフィードバックを保存
                item["feedback"] = merged_feedback

            elif has_new_feedback:
                # 新しいフィードバックのみがある場合
                item["feedback"] = script_data["feedback"]
                logger.info(
                    f"新しいフィードバックのみを保存: {len(script_data['feedback'])}件"
                )

            elif has_existing_feedback:
                # 既存のフィードバックのみがある場合は維持
                item["feedback"] = existing_item["feedback"]
                logger.info(
                    f"既存のフィードバックを維持: {len(existing_item['feedback'])}件"
                )

            else:
                # どちらにもフィードバックがない場合は空配列
                item["feedback"] = []
                logger.info("フィードバックはありません。空配列を設定")

            # 分析結果があれば保存
            if "analysis" in script_data:
                item["analysis"] = script_data["analysis"]
                item["passed"] = script_data.get("passed", False)
            elif existing_item and "analysis" in existing_item:
                # 既存の分析結果を維持
                item["analysis"] = existing_item["analysis"]
                item["passed"] = existing_item.get("passed", False)

            # DynamoDBに保存
            self.scripts_table.put_item(Item=item)
            logger.info(
                f"DynamoDBにスクリプトを保存しました: script_id={script_id}, chapter_index={chapter_index}, フィードバック数={len(item.get('feedback', []))}件"
            )

            # スクリプトIDを返す
            return script_id
        except Exception as e:
            logger.error(f"スクリプトの保存に失敗しました: {str(e)}")
            raise

    @with_aws_credential_refresh
    def get_script_by_id(self, script_id: str) -> Optional[Dict[str, Any]]:
        """スクリプトIDを指定して台本を取得する

        Args:
            script_id: スクリプトID

        Returns:
            台本データ（見つからない場合はNone）
        """
        if not self.enabled:
            logger.info(
                "DynamoDBは無効に設定されています。スクリプトは取得できません。"
            )
            return None

        try:
            # DynamoDBからスクリプトを取得
            response = self.scripts_table.get_item(Key={"script_id": script_id})

            # 結果を確認
            if "Item" not in response:
                logger.warning(f"スクリプトが見つかりません: script_id={script_id}")
                return None

            return response["Item"]
        except Exception as e:
            logger.error(f"スクリプトの取得に失敗しました: {str(e)}")
            raise

    @with_aws_credential_refresh
    def get_scripts_by_session(self, session_id: str) -> List[Dict[str, Any]]:
        """セッションIDを指定して台本リストを取得する

        Args:
            session_id: セッションID

        Returns:
            台本データのリスト
        """
        if not self.enabled:
            logger.info(
                "DynamoDBは無効に設定されています。スクリプトは取得できません。"
            )
            return []

        try:
            # セッションIDのインデックスを使用して検索
            response = self.scripts_table.query(
                IndexName="session_id-index",
                KeyConditionExpression=boto3.dynamodb.conditions.Key("session_id").eq(
                    session_id
                ),
            )

            # 結果を確認
            if "Items" not in response or len(response["Items"]) == 0:
                logger.warning(
                    f"セッションのスクリプトが見つかりません: session_id={session_id}"
                )
                return []

            # 章番号でソート
            scripts = sorted(response["Items"], key=lambda x: x["chapter_index"])
            logger.info(
                f"セッションのスクリプトを取得しました: session_id={session_id}, スクリプト数={len(scripts)}"
            )

            return scripts
        except Exception as e:
            logger.error(f"セッションのスクリプト取得に失敗しました: {str(e)}")
            raise

    @with_aws_credential_refresh
    def get_merged_script(self, merged_id: str) -> Optional[Dict[str, Any]]:
        """結合されたスクリプトを取得する

        Args:
            merged_id: 結合されたスクリプトID

        Returns:
            結合されたスクリプトデータ（見つからない場合はNone）
        """
        if not self.enabled:
            logger.info(
                "DynamoDBは無効に設定されています。結合スクリプトは取得できません。"
            )
            return None

        try:
            # DynamoDBから結合スクリプトを取得
            response = self.merged_scripts_table.get_item(Key={"merged_id": merged_id})

            # 結果を確認
            if "Item" not in response:
                logger.warning(f"結合スクリプトが見つかりません: merged_id={merged_id}")
                return None

            return response["Item"]
        except Exception as e:
            logger.error(f"結合スクリプトの取得に失敗しました: {str(e)}")
            raise

    @with_aws_credential_refresh
    def save_merged_script(
        self, session_id: str, merged_script_data: Dict[str, Any]
    ) -> str:
        """結合された台本をDynamoDBに保存する

        Args:
            session_id: セッションID
            merged_script_data: 結合された台本データ

        Returns:
            保存された結合スクリプトのID
        """
        if not self.enabled:
            logger.info(
                "DynamoDBは無効に設定されています。結合スクリプトは保存されません。"
            )
            return "dynamodb-disabled"

        try:
            # 結合スクリプトIDを生成（または既存のIDを使用）
            merged_id = merged_script_data.get(
                "merged_id", f"{session_id}_merged_{uuid.uuid4().hex[:8]}"
            )

            # 保存するデータを整形
            item = {
                "merged_id": merged_id,
                "session_id": session_id,
                "title": merged_script_data.get("title", "結合スクリプト"),
                "content": merged_script_data.get("content", ""),
                "chapter_count": merged_script_data.get("chapter_count", 0),
                "script_ids": merged_script_data.get("script_ids", []),
                "created_at": merged_script_data.get(
                    "created_at", datetime.now().isoformat()
                ),
                "updated_at": datetime.now().isoformat(),
            }

            # DynamoDBに保存
            self.merged_scripts_table.put_item(Item=item)
            logger.info(
                f"DynamoDBに結合スクリプトを保存しました: merged_id={merged_id}"
            )

            # 結合スクリプトIDを返す
            return merged_id
        except Exception as e:
            logger.error(f"結合スクリプトの保存に失敗しました: {str(e)}")
            raise
