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

def check_agent(bedrock_client, agent_id):
    """指定されたエージェントIDが存在するか確認し、エイリアス情報を取得する"""
    logger.info(f"エージェントID {agent_id} の情報を確認中...")
    try:
        # エージェント情報の取得
        response = bedrock_client.get_agent(
            agentId=agent_id
        )
        
        logger.info(f"エージェント情報の取得に成功: {response['agentName']}")
        logger.info(f"ステータス: {response['agentStatus']}")
        
        # 3. エイリアス一覧の取得
        aliases_response = bedrock_client.list_agent_aliases(
            agentId=agent_id
        )
        
        if 'agentAliases' in aliases_response and aliases_response['agentAliases']:
            logger.info(f"エージェント {agent_id} のエイリアス一覧:")
            for alias in aliases_response['agentAliases']:
                logger.info(f"  エイリアス名: {alias['agentAliasName']}, ID: {alias['agentAliasId']}")
        else:
            logger.info(f"エージェント {agent_id} にはエイリアスがありません")
        
        return True, response, aliases_response.get('agentAliases', [])
    except Exception as e:
        logger.error(f"エージェント確認エラー: {type(e).__name__}: {str(e)}")
        return False, None, []

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
    target_agent_id = "MJAORXUJ01"
    target_alias_id = "M84J9HHVUS"
    
    if not agent_id:
        agent_id = target_agent_id
        logger.info(f"環境変数からエージェントIDが見つからないため、指定されたID {agent_id} を使用します")
    
    if not agent_alias_id:
        agent_alias_id = target_alias_id
        logger.info(f"環境変数からエイリアスIDが見つからないため、指定されたID {agent_alias_id} を使用します")

    # セッションの作成
    try:
        session = boto3.Session(
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=aws_region
        )
        
        # 1. まず指定されたエージェントが存在するか確認
        logger.info(f"指定されたエージェントID {target_agent_id} の存在を確認します")
        
        # BedrockのAPIクライアント（管理用）を作成
        bedrock_client = session.client(service_name="bedrock")
        
        # エージェント情報を確認
        success, agent_info, aliases = check_agent(bedrock_client, target_agent_id)
        
        if success:
            logger.info(f"エージェント {target_agent_id} は存在し、アクセス可能です")
            
            # エイリアスIDの確認
            alias_found = False
            for alias in aliases:
                if alias['agentAliasId'] == target_alias_id:
                    logger.info(f"指定されたエイリアスID {target_alias_id} は有効です")
                    alias_found = True
                    break
            
            if not alias_found and aliases:
                logger.info(f"指定されたエイリアスID {target_alias_id} は見つかりませんでした。最初のエイリアスを使用します: {aliases[0]['agentAliasId']}")
                agent_alias_id = aliases[0]['agentAliasId']
            elif not aliases:
                logger.error("エージェントにエイリアスがありません。エイリアスを作成する必要があります。")
                return
        else:
            logger.error(f"エージェントID {target_agent_id} は存在しないか、アクセスできません。")
            logger.info("利用可能なエージェントを確認します...")
            
            try:
                agents_response = bedrock_client.list_agents()
                if 'agents' in agents_response and agents_response['agents']:
                    logger.info("利用可能なエージェント一覧:")
                    for agent in agents_response['agents']:
                        logger.info(f"  エージェントID: {agent['agentId']}, 名前: {agent['agentName']}")
                        # このエージェントのエイリアスも確認
                        _, _, agent_aliases = check_agent(bedrock_client, agent['agentId'])
                else:
                    logger.info("利用可能なエージェントがありません")
            except Exception as e:
                logger.error(f"エージェント一覧取得エラー: {str(e)}")
        
        # Bedrock Agent Runtimeクライアントの作成
        agent_client = session.client(service_name="bedrock-agent-runtime")
        logger.info("Bedrock Agent Runtimeクライアントの作成に成功しました")
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
        except Exception as e:
            logger.error(f"代替呼び出しエラー: {e}")
        
        logger.info("レスポンスの処理を開始...")
        
        # レスポンスの処理 (EventStream)
        full_response = ""
        extracted_completion = ""
        i = 0
        for event in response:
            i += 1
            event_type = type(event).__name__
            logger.info(f"イベント #{i} タイプ: {event_type}")
            
            # イベントの内容をそのまま表示
            event_str = str(event)
            logger.info(f"イベント内容: {event_str[:1000]}")
            
            # イベントがdictのようなら、その内容を表示
            try:
                if isinstance(event, dict):
                    logger.info(f"Dict内容: {json.dumps(event)[:500]}")
                elif hasattr(event, '__dict__'):
                    logger.info(f"オブジェクト属性: {event.__dict__}")
            except:
                pass
                
            # 文字列として応答を蓄積
            full_response += event_str
            
            # システマティックにさまざまなパターンを試す
            try:
                # 1. オブジェクト属性アクセス - chunk.bytesパターン
                if hasattr(event, 'chunk') and hasattr(event.chunk, 'bytes'):
                    chunk_bytes = event.chunk.bytes
                    logger.info(f"chunk.bytes パターン検出: {chunk_bytes[:50] if isinstance(chunk_bytes, bytes) else 'Not bytes'}")
                    
                    # バイト列をJSONに変換
                    if isinstance(chunk_bytes, bytes):
                        chunk_data = json.loads(chunk_bytes.decode('utf-8'))
                        logger.info(f"デコード成功: {json.dumps(chunk_data)[:200]}")
                        
                        # completionキーがあれば抽出
                        if 'completion' in chunk_data:
                            completion_text = chunk_data['completion']
                            extracted_completion += completion_text
                            logger.info(f"抽出されたcompletion: {completion_text}")
                
                # 2. 辞書アクセスパターン
                if isinstance(event, dict) and 'chunk' in event:
                    logger.info("辞書アクセスパターン検出")
                    if 'bytes' in event['chunk']:
                        chunk_bytes = event['chunk']['bytes']
                        logger.info(f"チャンクデータ検出: {chunk_bytes[:100] if isinstance(chunk_bytes, (str, bytes)) else type(chunk_bytes)}")
                        
                        # バイト列または文字列をJSONに変換
                        if isinstance(chunk_bytes, bytes):
                            chunk_data = json.loads(chunk_bytes.decode('utf-8'))
                        elif isinstance(chunk_bytes, str):
                            chunk_data = json.loads(chunk_bytes)
                        else:
                            chunk_data = chunk_bytes
                            
                        logger.info(f"チャンクデータ解析: {json.dumps(chunk_data)[:200]}")
                        
                        # completionキーがあれば抽出
                        if 'completion' in chunk_data:
                            completion_text = chunk_data['completion']
                            extracted_completion += completion_text
                            logger.info(f"抽出されたcompletion: {completion_text}")
                
                # 3. completionプロパティを直接持つケース
                if hasattr(event, 'completion'):
                    completion_text = event.completion
                    extracted_completion += completion_text
                    logger.info(f"直接completionから抽出: {completion_text}")
                
                # 4. JSON文字列パターン
                try:
                    json_data = json.loads(event_str)
                    logger.info(f"JSONパース成功: {json.dumps(json_data)[:200]}")
                    
                    # completionキーがあれば抽出
                    if 'completion' in json_data:
                        completion_text = json_data['completion']
                        extracted_completion += completion_text
                        logger.info(f"JSON文字列からcompletion抽出: {completion_text}")
                except:
                    pass
            except Exception as e:
                logger.error(f"イベント処理エラー: {str(e)}")
                logger.exception("詳細:")
                pass
                
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
