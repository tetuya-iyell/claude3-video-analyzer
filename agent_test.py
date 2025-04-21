#!/usr/bin/env python3
import boto3
import json
import os
import time
import sys
import pprint
from dotenv import load_dotenv
import logging

# ロギングの設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# .envファイルから環境変数を読み込む
load_dotenv()

def check_agent(agent_client, agent_id, alias_id=None):
    """指定されたエージェントIDとエイリアスIDの接続性をテストする"""
    logger.info(f"エージェントID {agent_id} の接続性をテストします...")
    # エイリアスIDが指定されていない場合は引数のIDを使用
    if alias_id is None:
        alias_id = agent_id  # 試験的に同じIDを使用
    
    try:
        # シンプルに接続テスト
        test_input = "こんにちは、接続テストです。"
        session_id = f"test_session_{int(time.time())}"
        
        logger.info(f"試験的な呼び出しを実行: agent={agent_id}, alias={alias_id}, session={session_id}")
        
        # エージェント呼び出しを試行
        response = agent_client.invoke_agent(
            agentId=agent_id,
            agentAliasId=alias_id,
            sessionId=session_id,
            inputText=test_input
        )
        
        # 応答型を確認
        logger.info(f"接続成功: レスポンスタイプ = {type(response)}")
        
        return True, {"status": "connected", "response_type": str(type(response))}, []
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)
        logger.error(f"エージェント確認エラー: {error_type}: {error_msg}")
        
        if 'ResourceNotFoundException' in error_type:
            logger.error(f"指定されたエージェントID {agent_id} または エイリアスID {alias_id} が存在しません")
            
            # エラーメッセージからエージェントIDのみで再試行するべきかを判断
            if "alias" in error_msg.lower():
                logger.info("エイリアスIDに問題がある可能性があります")
                return False, {"status": "alias_invalid", "error": error_msg}, []
            else:
                logger.error("エージェントIDが無効です")
                return False, {"status": "agent_invalid", "error": error_msg}, []
        elif 'ValidationException' in error_type:
            logger.error(f"バリデーションエラー: {error_msg}")
            return False, {"status": "validation_error", "error": error_msg}, []
        
        return False, {"status": "unknown_error", "error": error_msg}, []

def main():
    # 設定を表示
    agent_id = os.getenv("BEDROCK_AGENT_ID", "")
    agent_alias_id = os.getenv("BEDROCK_AGENT_ALIAS_ID", "")
    aws_region = os.getenv("AWS_REGION", "us-east-1")

    logger.info(f"テスト設定:")
    logger.info(f"Agent ID: {agent_id}")
    logger.info(f"Agent Alias ID: {agent_alias_id}")
    logger.info(f"AWS Region: {aws_region}")
    
    # 指定されたエージェントIDとエイリアスID
    target_agent_id = "QKIWJP7RL9"  # my-simple-agent
    target_alias_id = "HMJDNE7YDR"  # prod-alias
    
    # AWS リージョンを us-east-1 に設定（エンドポイントが利用可能な可能性があるため）
    aws_region = "us-east-1"
    
    if not agent_id:
        agent_id = target_agent_id
        logger.info(f"環境変数からエージェントIDが見つからないため、指定されたID {agent_id} を使用します")
    
    if not agent_alias_id:
        agent_alias_id = target_alias_id
        logger.info(f"環境変数からエイリアスIDが見つからないため、指定されたID {agent_alias_id} を使用します")

    # セッションの作成
    try:
        aws_access_key = "AKIAXYTL6D6HUXSXERGJ"
        aws_secret_key = "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"
        
        # 直接指定された認証情報を使用
        session = boto3.Session(
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=aws_region
        )
        
        # 接続先エンドポイントを表示
        logger.info(f"利用可能なエンドポイント: {boto3.Session().get_available_regions('bedrock-agent-runtime')}")
        
        # Bedrock Agent Runtimeクライアントの作成
        agent_client = session.client(service_name="bedrock-agent-runtime")
        logger.info("Bedrock Agent Runtimeクライアントの作成に成功しました")
        
        # 1. まず指定されたエージェントが存在するか確認
        logger.info(f"指定されたエージェントID {target_agent_id} とエイリアスID {target_alias_id} の接続テスト")
        
        # 管理APIを使わず、直接エージェント呼び出しでテスト
        success, agent_info, aliases = check_agent(agent_client, target_agent_id, target_alias_id)
        
        # エージェントIDは正しいがエイリアスIDが不正な可能性
        if not success:
            if agent_info.get("status") == "alias_invalid":
                logger.info(f"エイリアスIDが不正です。BEDROCK_AGENT_ALIAS_IDを確認してください。")
            elif agent_info.get("status") == "agent_invalid":
                logger.info(f"エージェントIDが不正です。BEDROCK_AGENT_IDを確認してください。")
            else:
                logger.error(f"エージェント呼び出しに失敗しました。エラー: {agent_info.get('error', 'Unknown error')}")
        else:
            logger.info(f"エージェント {target_agent_id} とエイリアス {target_alias_id} への接続テストに成功しました")
    except Exception as e:
        logger.error(f"セッション作成エラー: {type(e).__name__}: {str(e)}")
        return

    # APIバージョンを確認
    try:
        logger.info("APIバージョンを確認中...")
        # バージョン情報やサポートされている操作を表示
        operations = agent_client._service_model.operation_names
        logger.info(f"利用可能なオペレーション: {operations}")
    except Exception as e:
        logger.error(f"API確認エラー: {str(e)}")

    # テストの実行
    try:
        # テスト用の入力テキスト
        sample_text = """
以下の台本を改善してください。より明るく前向きなトーンにしてください。

# 現在の台本
台詞: こんにちは、今日はコンテナハウスについて解説します。
台詞: コンテナハウスは安いですが、住み心地は良くありません。
台詞: でも予算が限られている方にはオプションの一つかもしれません。

# フィードバック
もっと前向きなトーンにしてください。
        """

        # セッションID (一意になるように現在時刻を使用)
        session_id = f"test_session_{int(time.time())}"
        logger.info(f"セッションID: {session_id}")
        
        logger.info("Bedrock Agentを呼び出し中...")
        # Agentの呼び出し
        logger.info("API呼び出し直前")
        response = agent_client.invoke_agent(
            agentId=agent_id,
            agentAliasId=agent_alias_id,
            sessionId=session_id,
            inputText=sample_text,
            enableTrace=True
        )
        logger.info(f"応答型: {type(response)}")
        logger.info(f"応答dir: {dir(response)}")
        
        # 標準的なテスト - 同期呼び出し
        try:
            # 直接APIをシンプルに呼び出し
            logger.info("代替API呼び出しを試行")
            direct_response = agent_client.invoke_agent(
                agentId=agent_id,
                agentAliasId=agent_alias_id,
                sessionId=f"{session_id}_direct",
                inputText="これは簡単なテストです。あなたは機能していますか？"
            )
            logger.info(f"直接応答オブジェクト: {type(direct_response)}")
            # 使用可能なメソッドを出力
            logger.info(f"応答メソッド: {[m for m in dir(direct_response) if not m.startswith('_')]}")
            
            # 応答の内容を詳細に表示
            logger.info(f"応答キー: {direct_response.keys()}")
            if 'completion' in direct_response:
                logger.info(f"完了テキスト: {direct_response['completion'][:200]}")
            if 'contentType' in direct_response:
                logger.info(f"コンテンツタイプ: {direct_response['contentType']}")
            if 'sessionId' in direct_response:
                logger.info(f"セッションID: {direct_response['sessionId']}")
        except Exception as e:
            logger.error(f"代替呼び出しエラー: {e}")
        
        logger.info("レスポンスの処理を開始...")
        
        # レスポンス型の診断
        logger.info(f"レスポンス型詳細チェック: {type(response)}")
        # レスポンスを文字列として初期化
        full_response = ""
        extracted_completion = ""
        
        # Boto3のEventStreamレスポンスかどうかチェック
        try:
            if hasattr(response, "get_available_events"):
                logger.info("Boto3 EventStreamレスポンス検出")
                full_response = str(response)
            elif isinstance(response, dict):
                logger.info("辞書型レスポンスを処理")
                logger.info(f"レスポンスキー: {response.keys()}")
                
                # 直接completion値を取得
                if 'completion' in response:
                    extracted_completion = response['completion']
                    logger.info(f"completion値を抽出: {extracted_completion[:200] if extracted_completion else 'なし'}")
                
                full_response = str(response)
            else:
                logger.info(f"その他のレスポンス型: {str(response)[:200]}")
                full_response = str(response)
        except Exception as check_err:
            logger.error(f"レスポンス型チェックエラー: {str(check_err)}")
        
        # EventStreamレスポンスのチェックと解析
        if hasattr(response, "__iter__") and not isinstance(response, dict):
            logger.info("EventStreamのようなイテラブルレスポンスの処理を開始")
            stream_full_response = ""
            stream_extracted_completion = ""
            i = 0
            
            try:
                for event in response:
                    i += 1
                    event_type = type(event).__name__
                    logger.info(f"イベント #{i} タイプ: {event_type}")
                    
                    # イベントの内容をそのまま表示
                    event_str = str(event)
                    logger.info(f"イベント内容: {event_str[:200]}")
                    
                    # イベントがdictのようなら、その内容を表示
                    try:
                        if isinstance(event, dict):
                            logger.info(f"Dict内容: {json.dumps(event)[:200]}")
                            if 'completion' in event:
                                completion_text = event['completion']
                                stream_extracted_completion += completion_text
                                logger.info(f"直接dictからcompletion抽出: {completion_text[:100]}")
                    except Exception as dict_err:
                        logger.error(f"辞書処理エラー: {str(dict_err)}")
                    
                    # 文字列として応答を蓄積
                    stream_full_response += event_str
                
                # イベントストリームから抽出された結果があれば更新
                if stream_extracted_completion:
                    logger.info("イベントストリームから抽出されたテキストで更新")
                    extracted_completion = stream_extracted_completion
                    full_response = stream_full_response
                    
            except Exception as stream_err:
                logger.error(f"EventStream処理エラー: {str(stream_err)}")
                logger.exception("詳細:")
                
        logger.info("\n===== 最終応答 =====")
        logger.info(full_response or "応答がありません")
        
        logger.info("\n===== 抽出されたコンテンツ =====")
        logger.info(extracted_completion or "抽出されたコンテンツはありません")
        
    except Exception as e:
        logger.error(f"テスト実行中のエラー: {type(e).__name__}: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    main()
