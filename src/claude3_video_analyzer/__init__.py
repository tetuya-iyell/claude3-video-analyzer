import anthropic
import base64
import cv2
import os
import boto3
import json
import logging
from typing import List, Dict, Any, Optional, Tuple
from dotenv import load_dotenv
from .aws_retry import aws_api_retry

# ロガー設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 環境変数の読み込み
load_dotenv()


class ScriptGenerator:
    """台本生成のためのクラス"""
    
    def __init__(self, analyzer):
        """初期化
        
        Args:
            analyzer: VideoAnalyzerインスタンス
        """
        self.analyzer = analyzer
        self.script_prompt = analyzer.default_script_prompt
    
    def extract_chapters(self, analysis_text: str) -> List[Dict[str, str]]:
        """章立て解析結果から各章の情報を抽出する
        
        Args:
            analysis_text: 章立て解析結果のテキスト
            
        Returns:
            章情報のリスト（タイトルと概要）
        """
        logger.info("章構造の抽出を開始")
        chapters = []
        
        # 単純なテキスト解析でMarkdown形式から章を抽出
        try:
            lines = analysis_text.split('\n')
            current_chapter = None
            
            for line in lines:
                # 章タイトルの検出 (## から始まる行)
                if line.startswith('## '):
                    if current_chapter:
                        chapters.append(current_chapter)
                    
                    chapter_title = line.replace('## ', '').strip()
                    current_chapter = {
                        "chapter_num": len(chapters) + 1,
                        "chapter_title": chapter_title,
                        "chapter_summary": ""
                    }
                # 章の内容を蓄積
                elif current_chapter and line and not line.startswith('#'):
                    if current_chapter["chapter_summary"]:
                        current_chapter["chapter_summary"] += "\n" + line
                    else:
                        current_chapter["chapter_summary"] = line
            
            # 最後の章を追加
            if current_chapter:
                chapters.append(current_chapter)
                
            logger.info(f"章構造の抽出が完了しました（{len(chapters)}章）")
        except Exception as e:
            logger.error(f"章構造の抽出中にエラーが発生: {str(e)}")
            raise
            
        return chapters
    
    def generate_script_for_chapter(self, chapter: Dict[str, str], duration_minutes: int = 3) -> Dict[str, str]:
        """各章の台本を生成
        
        Args:
            chapter: 章情報（タイトルと概要を含む辞書）
            duration_minutes: 台本の対象動画時間（分単位、デフォルト3分）
            
        Returns:
            生成された台本
        """
        logger.info(f"章「{chapter['chapter_title']}」の台本生成を開始（目標時間: {duration_minutes}分）")
        
        # プロンプト生成（動画時間パラメータを追加）
        prompt = self.script_prompt.format(
            chapter_title=chapter["chapter_title"],
            chapter_summary=chapter["chapter_summary"],
            duration_minutes=duration_minutes
        )
        
        # Bedrockモードの場合はBedrockを使用
        if self.analyzer.use_bedrock:
            try:
                # Bedrockモデル呼び出し
                response = self.analyzer.bedrock_runtime.invoke_model(
                    modelId=self.analyzer.model,
                    body=json.dumps({
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 2000,
                        "messages": [
                            {"role": "user", "content": prompt}
                        ]
                    })
                )
                
                # レスポンスの解析
                response_body = json.loads(response.get('body').read())
                script_content = response_body['content'][0]['text']
                
                logger.info(f"章「{chapter['chapter_title']}」の台本生成が完了")
            except Exception as e:
                logger.error(f"台本生成中にエラーが発生: {str(e)}")
                raise
        else:
            # Anthropic APIの場合
            try:
                response = self.analyzer.client.messages.create(
                    model=self.analyzer.model,
                    max_tokens=2000,
                    messages=[{"role": "user", "content": prompt}]
                )
                script_content = response.content[0].text
                logger.info(f"章「{chapter['chapter_title']}」の台本生成が完了")
            except Exception as e:
                logger.error(f"台本生成中にエラーが発生: {str(e)}")
                raise
        
        # 台本データの作成
        script_data = {
            "chapter_title": chapter["chapter_title"],
            "chapter_summary": chapter["chapter_summary"],
            "script_content": script_content,
            "status": "review",
            "feedback": []
        }
        
        return script_data
        
    def analyze_script_quality(self, script_data: Dict[str, str]) -> Dict[str, Any]:
        """台本の品質を分析する
        
        Args:
            script_data: 分析する台本データ
            
        Returns:
            分析結果
        """
        logger.info(f"台本「{script_data['chapter_title']}」の品質分析を開始")
        
        # 分析用のプロンプト
        prompt = f"""
以下のゆっくり不動産の台本を分析し、その品質を評価してください。

# 章タイトル
{script_data['chapter_title']}

# 章の概要
{script_data['chapter_summary']}

# 台本
{script_data['script_content']}

以下の基準で評価してください：
1. ゆっくり実況の口調になっているか
2. 専門用語が適切に説明されているか
3. 重要なポイントが強調されているか
4. 具体的なアドバイスが含まれているか
5. 台本形式が適切か（「台詞:」で話者を示しているか）

この台本が基準を満たしていると思いますか？「はい」または「いいえ」で答え、その理由を具体的に説明してください。
改善点があれば具体的に指摘してください。
        """
        
        # Bedrockモードの場合はBedrockを使用
        if self.analyzer.use_bedrock:
            try:
                # Bedrockモデル呼び出し
                response = self.analyzer.bedrock_runtime.invoke_model(
                    modelId=self.analyzer.model,
                    body=json.dumps({
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 1000,
                        "messages": [
                            {"role": "user", "content": prompt}
                        ]
                    })
                )
                
                # レスポンスの解析
                response_body = json.loads(response.get('body').read())
                analysis = response_body['content'][0]['text']
                
                # 「はい」または「いいえ」を抽出
                passed = "はい" in analysis[:50]
                
                logger.info(f"台本「{script_data['chapter_title']}」の品質分析が完了")
            except Exception as e:
                logger.error(f"台本品質分析中にエラーが発生: {str(e)}")
                raise
        else:
            # Anthropic APIの場合
            try:
                response = self.analyzer.client.messages.create(
                    model=self.analyzer.model,
                    max_tokens=1000,
                    messages=[{"role": "user", "content": prompt}]
                )
                analysis = response.content[0].text
                passed = "はい" in analysis[:50]
                logger.info(f"台本「{script_data['chapter_title']}」の品質分析が完了")
            except Exception as e:
                logger.error(f"台本品質分析中にエラーが発生: {str(e)}")
                raise
        
        return {
            "passed": passed,
            "analysis": analysis
        }
    
    def improve_script(self, script_data: Dict[str, str], feedback: str) -> Dict[str, str]:
        """フィードバックに基づいて台本を改善する
        
        Args:
            script_data: 改善する台本データ
            feedback: フィードバック内容
            
        Returns:
            改善された台本
        """
        logger.info(f"台本「{script_data['chapter_title']}」の改善を開始")
        
        # 改善用のプロンプト
        prompt = f"""
あなたは不動産の解説動画「ゆっくり不動産」の台本編集アシスタントです。
以下の台本とフィードバックに基づいて、台本を改善してください。

# 現在の台本
{script_data['script_content']}

# フィードバック
{feedback}

フィードバックを踏まえて改善した台本を作成してください。台本形式は元の形式を維持してください。
        """
        
        # Bedrockモードの場合はBedrockを使用
        if self.analyzer.use_bedrock:
            try:
                # AI Agentクライアントを使用するかどうか
                if self.analyzer.bedrock_agent_client:
                    
                    logger.info(f"Bedrock AI Agentを使用して台本を改善します: {self.analyzer.bedrock_agent_id}")
                    
                    try:
                        # エージェント入力の準備 - 強化されたプロンプト
                        input_text = f"""
あなたは不動産の解説動画「ゆっくり不動産」の台本編集スペシャリストです。以下のフィードバックに基づいて台本を改善してください。

# 台本形式のガイドライン（重要）
- 元の台本では「話者1:」「話者2:」「ナレーション:」のような表記が使われています
- 改善版では「れいむ:」「まりさ:」「ナレーション:」のように明確なキャラクター名に変更してください
- 話者の変更は以下のルールに従ってください:
  * 話者1 → れいむ（女性キャラ、丁寧で説明が上手）
  * 話者2 → まりさ（女性キャラ、少し砕けた口調で質問や提案が得意）
  * ナレーション → そのままナレーション
- ゆっくり実況形式（「～です」「～ます」調）を維持してください
- 専門用語は噛み砕いて説明してください
- 重要ポイントには「！」マークを付けてください
- 台詞をリアルに聞こえるよう自然な会話調で書いてください
- フィードバックの内容を反映し、内容や長さを適切に調整してください
- 台本の終わりは次の章につながるような終わり方にしてください（例:「次の章では～について見ていきましょう」など）
- 台本の文字数は1分あたり200〜250文字を目安にしてください

# 現在の台本
{script_data['script_content']}

# ユーザーからのフィードバック
{feedback}

フィードバックを反映した改善版台本を作成してください。台本形式の基本は維持しながら、話者名を「れいむ」「まりさ」「ナレーション」に変更し、内容を改善してください。台本の終わりは次の章につながるように工夫してください。
返答は改善された台本のみをそのまま出力してください。
                        """
                        
                        # 環境変数からAgent ID/Aliasを取得
                        agent_id = self.analyzer.bedrock_agent_id
                        alias_id = self.analyzer.bedrock_agent_alias_id
                        
                        # バックアップとして固定値を使用（環境変数が空の場合）
                        if not agent_id or not alias_id:
                            logger.warning("環境変数からエージェントIDが取得できません。デフォルト値を使用します。")
                            agent_id = "QKIWJP7RL9" # テスト済みの既知のAgent ID
                            alias_id = "HMJDNE7YDR" # テスト済みの既知のAlias ID
                        
                        # APIリクエストのリトライ回数と待機時間の定義
                        max_retries = 3
                        retry_delay = 3  # 秒
                        
                        # リトライロジックを組み込んだBedrock AI Agentの呼び出し
                        logger.info(f"固定Agent ID {agent_id}とAlias ID {alias_id}を使用してBedrock AI Agentを呼び出し中...")
                        
                        # 専用のリトライデコレーターを使用してAPI呼び出しをラップ
                        @aws_api_retry(max_retries=3, base_delay=3, jitter=0.5)
                        def call_agent_with_retry():
                            return self.analyzer.bedrock_agent_client.invoke_agent(
                                agentId=agent_id,
                                agentAliasId=alias_id,
                                sessionId=f"script_improvement_{int(self.analyzer.time_module.time())}",
                                inputText=input_text
                            )
                            
                        try:
                            # リトライロジック付きで呼び出し
                            response = call_agent_with_retry()
                            logger.info("AI Agent呼び出し成功")
                        except Exception as e:
                            # すべてのリトライが失敗した場合
                            logger.error(f"すべてのAgentリトライが失敗しました: {e}")
                            raise
                        
                        # レスポンスの型を確認
                        logger.info(f"応答型: {type(response)}")
                        
                        # EventStreamかどうかを確認
                        try:
                            import botocore
                            if isinstance(response, botocore.eventstream.EventStream):
                                logger.info("EventStreamレスポンスを検出しました")
                                
                                # EventStreamを解析し、完全なレスポンスを構築
                                try:
                                    events_list = []
                                    for event in response:
                                        events_list.append(event)
                                        logger.info(f"イベント型: {type(event)}")
                                        
                                    logger.info(f"EventStreamから{len(events_list)}個のイベントを抽出")
                                    
                                    # completion値を見つける
                                    completion_found = False
                                    for event in events_list:
                                        # 辞書として直接アクセス
                                        if isinstance(event, dict) and 'completion' in event:
                                            response = event  # 応答を更新
                                            completion_found = True
                                            logger.info("dictイベントからcompletionを取得しました")
                                            break
                                            
                                        # 属性として確認
                                        if hasattr(event, 'completion'):
                                            response = {'completion': event.completion}
                                            completion_found = True
                                            logger.info("イベント属性からcompletionを取得しました")
                                            break
                                        
                                        # __dict__を使って確認
                                        if hasattr(event, '__dict__'):
                                            event_dict = event.__dict__
                                            if 'completion' in event_dict:
                                                response = {'completion': event_dict['completion']}
                                                completion_found = True
                                                logger.info("イベント__dict__からcompletionを取得しました")
                                                break
                                    
                                    if not completion_found:
                                        logger.warning("EventStreamからcompletionを抽出できませんでした")
                                    
                                except Exception as e:
                                    logger.error(f"EventStream処理エラー: {str(e)}")
                                    logger.exception("詳細:")
                        except ImportError:
                            logger.warning("botocoreモジュールをインポートできませんでした")
                        
                        # 辞書型の場合はキーを確認
                        if isinstance(response, dict):
                            logger.info(f"レスポンスキー: {response.keys()}")
                        else:
                            logger.info(f"辞書型ではないレスポンス: {type(response)}")
                        
                        # 辞書型のレスポンスからcompletionを取得
                        improved_script = ""
                        try:
                            if isinstance(response, dict) and 'completion' in response:
                                # completion値の処理
                                completion_value = response['completion']
                                # レスポンスの文字列表現を安全のために取得
                                response_repr = str(response)[:500] # 長すぎないようにする
                                logger.info(f"レスポンス文字列表現: {response_repr}")
                                
                                # EventStreamの特殊処理 - 大幅に簡素化された実装
                                import botocore
                                if isinstance(completion_value, botocore.eventstream.EventStream):
                                    logger.info(f"EventStreamを検出: 新しいバイナリデータ抽出アルゴリズムで処理します")
                                    
                                    # バイナリデータ処理の専用関数 - シンプル化
                                    def extract_text_from_binary(binary_data):
                                        """バイナリデータからテキストを抽出する関数 - 簡素化版"""
                                        if not binary_data or not isinstance(binary_data, bytes):
                                            return None
                                            
                                        logger.info(f"バイナリデータ抽出処理: 長さ={len(binary_data)}")
                                        
                                        # 主要なエンコーディングで試行
                                        for encoding in ["utf-8", "shift_jis", "latin-1"]:
                                            try:
                                                decoded = binary_data.decode(encoding, errors='ignore')
                                                # 日本語テキストが含まれているかチェック (キャラクター名を探す)
                                                script_markers = ["れいむ:", "まりさ:", "ナレーション:", "話者1:", "話者2:"]
                                                if any(marker in decoded for marker in script_markers):
                                                    logger.info(f"{encoding}で直接デコードに成功: 台本マーカーを検出")
                                                    return decoded
                                                
                                                # 一般的な日本語文字が含まれているかチェック
                                                if any(0x3040 <= ord(c) <= 0x30FF for c in decoded[:1000]):
                                                    logger.info(f"{encoding}で直接デコードに成功: 日本語文字を検出")
                                                    return decoded
                                            except Exception:
                                                pass
                                        
                                        # シンプルなJSONパターンチェック
                                        try:
                                            import re
                                            bytes_str = str(binary_data)
                                            
                                            # 最も一般的なパターンのみチェック
                                            json_pattern = r'completion":"([^"]+)"'
                                            match = re.search(json_pattern, bytes_str)
                                            if match:
                                                content = match.group(1)
                                                logger.info(f"JSONパターンで抽出成功: {len(content)}文字")
                                                return content
                                        except Exception as e:
                                            logger.warning(f"JSONパターンチェック失敗: {e}")
                                        
                                        # それ以外は失敗
                                        return None

                                    # イベントストリームを簡単に処理
                                    try:
                                        # 最も一般的なケースを処理
                                        for event in completion_value:
                                            logger.info(f"イベント型: {type(event)}")
                                            
                                            # ケース1: 最も一般的な形式 - 辞書型イベント内のchunk.bytes
                                            if isinstance(event, dict) and 'chunk' in event and 'bytes' in event['chunk']:
                                                binary_data = event['chunk']['bytes']
                                                if isinstance(binary_data, bytes) and len(binary_data) > 10:
                                                    logger.info(f"event['chunk']['bytes']検出: 長さ={len(binary_data)}")
                                                    
                                                    extracted_text = extract_text_from_binary(binary_data)
                                                    if extracted_text and len(extracted_text) > 300:
                                                        logger.info("辞書型イベントからテキストを抽出しました")
                                                        return extracted_text
                                            
                                            # ケース2: 辞書型イベント内のcompletion直接アクセス
                                            elif isinstance(event, dict) and 'completion' in event:
                                                completion_text = event['completion']
                                                if isinstance(completion_text, str) and len(completion_text) > 100:
                                                    logger.info(f"直接completionを取得: {len(completion_text)}文字")
                                                    return completion_text
                                            
                                            # ケース3: 属性アクセス
                                            elif hasattr(event, 'chunk') and hasattr(event.chunk, 'bytes'):
                                                binary_data = event.chunk.bytes
                                                if isinstance(binary_data, bytes):
                                                    extracted_text = extract_text_from_binary(binary_data)
                                                    if extracted_text:
                                                        logger.info("属性アクセスでテキストを抽出しました")
                                                        return extracted_text
                                    except Exception as e:
                                        logger.error(f"イベント処理エラー: {str(e)}")
                                    
                                    # シンプルなリトライ戦略
                                    try:
                                        # 新しいセッションIDでリトライ
                                        new_session_id = f"retry_script_{int(self.analyzer.time_module.time())}"
                                        
                                        logger.info(f"シンプルなリトライを実行: {new_session_id}")
                                        retry_response = self.analyzer.bedrock_agent_client.invoke_agent(
                                            agentId=agent_id,
                                            agentAliasId=alias_id,
                                            sessionId=new_session_id,
                                            inputText=input_text
                                        )
                                        
                                        if isinstance(retry_response, dict) and "completion" in retry_response:
                                            retry_text = retry_response["completion"]
                                            if isinstance(retry_text, str):
                                                logger.info(f"リトライ成功: {len(retry_text)}文字")
                                                return retry_text
                                    except Exception as retry_err:
                                        logger.error(f"リトライ失敗: {str(retry_err)}")
                                    
                                    # すべての処理が失敗した場合
                                    logger.warning("すべての処理が失敗したため、基盤モデルにフォールバックします")
                                    raise ValueError("Failed to process response, falling back to base model")
                                    
                                    # 注：以下のコードはEventStreamパースが正しく動作するようになったら有効化する
                                    """
                                    # EventStreamの内容をテキストとして結合
                                    event_texts = []
                                    try:
                                        for event in completion_value:
                                            logger.info(f"イベント型: {type(event)}")
                                            
                                            # 各種抽出方法を試す
                                            if hasattr(event, 'text'):
                                                event_texts.append(event.text)
                                                logger.info(f"イベントから .text 属性を抽出: {event.text[:30]}")
                                            elif hasattr(event, 'chunk'):
                                                if hasattr(event.chunk, 'bytes'):
                                                    # バイト列をデコード
                                                    try:
                                                        chunk_text = event.chunk.bytes.decode('utf-8')
                                                        event_texts.append(chunk_text)
                                                        logger.info(f"イベントからバイト列を抽出: {chunk_text[:30]}")
                                                    except:
                                                        logger.error("バイト列のデコードに失敗")
                                            elif isinstance(event, dict):
                                                # 辞書からtextフィールドを抽出
                                                if 'text' in event:
                                                    event_texts.append(event['text'])
                                                    logger.info(f"辞書からtextを抽出: {event['text'][:30]}")
                                                elif 'completion' in event:
                                                    event_texts.append(event['completion'])
                                                    logger.info(f"辞書からcompletionを抽出: {event['completion'][:30]}")
                                            
                                            # 文字列表現も試す
                                            event_str = str(event)
                                            if event_str and event_str != "None" and len(event_str) > 5:
                                                event_texts.append(event_str)
                                                logger.info(f"イベントの文字列表現を追加: {event_str[:30]}")
                                        
                                        # 結合してスクリプトを作成
                                        if event_texts:
                                            improved_script = "\n".join(event_texts)
                                            logger.info(f"EventStreamから取得したテキスト: {improved_script[:100]}...")
                                        else:
                                            logger.warning("EventStreamからテキストを取得できませんでした")
                                    except Exception as es_err:
                                        logger.error(f"EventStream解析エラー: {es_err}")
                                        logger.exception("詳細:")
                                    """
                                        
                                # 通常の文字列処理
                                elif isinstance(completion_value, str):
                                    improved_script = completion_value
                                    logger.info(f"文字列の完了テキストを取得: {improved_script[:100] if improved_script else '空'}...")
                                else:
                                    # その他の型の場合は文字列化
                                    logger.warning(f"completionが文字列ではなく{type(completion_value)}型です。文字列に変換します。")
                                    try:
                                        improved_script = str(completion_value)
                                    except:
                                        logger.error("文字列変換に失敗")
                            else:
                                logger.warning(f"completion キーが見つからないか、responseが辞書型ではありません: {type(response)}")
                                
                            # テキストが取得できたかチェック
                            if not improved_script or (isinstance(improved_script, str) and not improved_script.strip()):
                                logger.warning("Bedrock Agentからの有効な応答を取得できませんでした。標準モデルにフォールバックします。")
                                raise ValueError("Empty or invalid response from Bedrock Agent")
                                
                            logger.info(f"Bedrock AI Agentを使用して台本「{script_data['chapter_title']}」の改善が完了")
                        except Exception as stream_error:
                            logger.error(f"ストリーム解析エラー: {str(stream_error)}")
                            logger.exception("例外の詳細:")
                            
                            # 通常のBedrock基盤モデルにフォールバック
                            logger.info("通常のBedrock基盤モデルにフォールバックします")
                            
                            response = self.analyzer.bedrock_runtime.invoke_model(
                                modelId=self.analyzer.model,
                                body=json.dumps({
                                    "anthropic_version": "bedrock-2023-05-31",
                                    "max_tokens": 2000,
                                    "messages": [
                                        {"role": "user", "content": prompt}
                                    ]
                                })
                            )
                            
                            # レスポンスの解析
                            response_body = json.loads(response.get('body').read())
                            improved_script = response_body['content'][0]['text']
                            
                            logger.info(f"フォールバック: Bedrock基盤モデルを使用して台本「{script_data['chapter_title']}」の改善が完了")
                        
                        if not improved_script:
                            logger.warning("Bedrock AI Agentからの応答が空です。通常のモデル呼び出しに切り替えます。")
                            # 通常のBedrock基盤モデル呼び出しにフォールバック
                            raise ValueError("Empty response from AI Agent")
                        
                        logger.info(f"Bedrock AI Agentを使用して台本「{script_data['chapter_title']}」の改善が完了")
                    except Exception as agent_error:
                        logger.error(f"Bedrock AI Agent呼び出しエラー: {str(agent_error)}")
                        # 通常のBedrock基盤モデル呼び出しにフォールバック
                        raise ValueError(f"AI Agent error: {str(agent_error)}")
                        
                    # AIエージェントから受け取ったテキスト結果が文字列の場合の処理
                    if isinstance(improved_script, str) and improved_script:
                        logger.info(f"AIエージェントから文字列として受け取った改善台本を処理します（長さ: {len(improved_script)}）")
                        # この時点で improved_script は文字列型
                else:
                    # 通常のBedrock基盤モデル呼び出し（リトライ機能付き）
                    logger.info("通常のBedrock基盤モデルを使用します")
                    
                    @aws_api_retry(max_retries=2, base_delay=2)
                    def call_bedrock_model():
                        return self.analyzer.bedrock_runtime.invoke_model(
                            modelId=self.analyzer.model,
                            body=json.dumps({
                                "anthropic_version": "bedrock-2023-05-31",
                                "max_tokens": 2000,
                                "messages": [
                                    {"role": "user", "content": prompt}
                                ]
                            })
                        )
                    
                    # リトライ機能付きで呼び出し
                    response = call_bedrock_model()
                    
                    # レスポンスの解析
                    response_body = json.loads(response.get('body').read())
                    improved_script = response_body['content'][0]['text']
                    
                    logger.info(f"Bedrock基盤モデルを使用して台本「{script_data['chapter_title']}」の改善が完了")
            except Exception as e:
                logger.error(f"台本改善中にエラーが発生: {str(e)}")
                # エラーの場合は通常のモデル呼び出しを試みる
                try:
                    logger.info("エラー発生のため通常のBedrock基盤モデルにフォールバック")
                    response = self.analyzer.bedrock_runtime.invoke_model(
                        modelId=self.analyzer.model,
                        body=json.dumps({
                            "anthropic_version": "bedrock-2023-05-31",
                            "max_tokens": 2000,
                            "messages": [
                                {"role": "user", "content": prompt}
                            ]
                        })
                    )
                    
                    # レスポンスの解析
                    response_body = json.loads(response.get('body').read())
                    improved_script = response_body['content'][0]['text']
                    
                    logger.info(f"フォールバック: 通常のBedrock基盤モデルを使用して台本の改善が完了")
                except Exception as fallback_error:
                    logger.error(f"フォールバックにも失敗: {str(fallback_error)}")
                    raise
        else:
            # Anthropic APIの場合
            try:
                response = self.analyzer.client.messages.create(
                    model=self.analyzer.model,
                    max_tokens=2000,
                    messages=[{"role": "user", "content": prompt}]
                )
                improved_script = response.content[0].text
                logger.info(f"台本「{script_data['chapter_title']}」の改善が完了")
            except Exception as e:
                logger.error(f"台本改善中にエラーが発生: {str(e)}")
                raise
        
        # 元の台本データをコピー
        improved_script_data = script_data.copy()
        
        # 改善された台本が文字列型である場合の処理
        if isinstance(improved_script, str) and improved_script:
            logger.info(f"文字列型の改善台本（長さ: {len(improved_script)}）を処理して辞書型に変換します")
            improved_script_data["script_content"] = improved_script
            improved_script_data["status"] = "review"
        else:
            # 正常な処理（辞書または何らかのオブジェクトを返す場合）
            logger.info(f"既存の改善台本のフォーマットを使用: 型={type(improved_script)}")
            improved_script_data["script_content"] = improved_script
            improved_script_data["status"] = "review"
            
        return improved_script_data


class VideoAnalyzer:
    def __init__(self):
        # モードを取得
        self.mode = os.getenv("MODE", "anthropic")  # デフォルトはAnthropicクライアント
        self.use_bedrock = False
        self.bedrock_client = None
        self.bedrock_agent_client = None  # Bedrock Agent用クライアント
        
        # 時間モジュールのインポート
        import time
        self.time_module = time

        # Anthropicクライアント用の設定
        if self.mode == "anthropic":
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if api_key is None:
                raise ValueError(
                    "ANTHROPIC_API_KEY not found in environment variables or .env file."
                )

            # Anthropicクライアントの初期化
            self.client = anthropic.Anthropic(api_key=api_key)

        # AWS Bedrockクライアント用の設定
        elif self.mode == "bedrock":
            aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
            aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
            aws_region = os.getenv("AWS_REGION", "us-east-1")
            
            # 認証情報がない場合はデフォルト認証情報チェーンを使用
            # (~/.aws/credentials や環境変数など)
            if aws_access_key is None or aws_secret_key is None:
                logger.info("環境変数に認証情報がないため、AWS認証情報チェーンを使用します (~/.aws/credentials など)")
            

            # Bedrockクライアントの初期化
            try:
                # AWS認証情報の設定を確認
                # Bedrockが利用可能な特定のリージョンを使用
                # まずは環境変数のリージョンを使用し、なければus-east-1をデフォルトとする
                aws_region = os.getenv("AWS_REGION", "us-east-1")
                # Bedrockモデルが利用可能な他のリージョンも検討
                available_regions = ["us-east-1", "us-west-2", "eu-central-1", "ap-northeast-1"]
                logger.info(f"設定されたリージョン: {aws_region}")
                
                # デフォルト認証情報チェーンを使用
                if aws_access_key and aws_secret_key:
                    # 環境変数に認証情報がある場合はそれを使用
                    logger.info("AWS環境変数の認証情報を使用します")
                    logger.info(f"使用するリージョン: {aws_region}")
                    boto3_session = boto3.Session(
                        aws_access_key_id=aws_access_key,
                        aws_secret_access_key=aws_secret_key,
                        region_name=aws_region,
                    )
                else:
                    # AWS認証情報チェーンを使用 (~/.aws/credentials を含む)
                    logger.info("AWSデフォルト認証情報チェーンを使用します (~/.aws/credentials)")
                    try:
                        # デフォルト認証情報プロバイダーチェーンをデバッグ
                        home_dir = os.path.expanduser("~")
                        aws_creds_file = os.path.join(home_dir, ".aws", "credentials")
                        if os.path.exists(aws_creds_file):
                            logger.info(f"AWS認証情報ファイルが存在します: {aws_creds_file}")
                        else:
                            logger.warning(f"AWS認証情報ファイルが見つかりません: {aws_creds_file}")
                    except Exception as e:
                        logger.warning(f"AWS認証情報ファイルのチェック中にエラーが発生しました: {str(e)}")
                        
                    boto3_session = boto3.Session(region_name=aws_region)
                    sts = boto3_session.client('sts')
                    try:
                        identity = sts.get_caller_identity()
                        logger.info(f"AWS認証情報チェーン使用中のIAMユーザー: {identity.get('Arn')}")
                    except Exception as e:
                        logger.warning(f"IAMユーザー情報の取得に失敗しました: {str(e)}")

                # Bedrockランタイムクライアントの作成
                self.bedrock_runtime = boto3_session.client(
                    service_name="bedrock-runtime",
                )
                
                # Bedrock Agentクライアントの作成 - リージョン指定を明示的に設定
                self.bedrock_agent_client = boto3_session.client(
                    service_name="bedrock-agent-runtime",
                    region_name="us-east-1",  # 明示的にus-east-1を指定
                )
                
                logger.info("Bedrock Agentクライアントの初期化に成功しました")
                self.use_bedrock = True
            except Exception as e:
                logger.error(f"Bedrockクライアントの初期化エラー: {str(e)}")
                raise ConnectionError(f"Bedrockクライアントの初期化エラー: {str(e)}")
        else:
            raise ValueError(
                f"Unsupported mode '{self.mode}'. Use 'anthropic' or 'bedrock'."
            )

        # モードに応じたモデルIDを環境変数から取得
        if self.use_bedrock:
            # Bedrockモードの場合はBEDROCK_MODEL_IDを使用
            # 使用可能なClaudeモデルを使用する
            # まずはClaudeの基本モデル（Claude 3 Sonnet）に切り替え
            default_model = "anthropic.claude-3-sonnet-20240229-v1:0"
            self.model = os.getenv("BEDROCK_MODEL_ID", default_model)
            # IAMのアクセス権限がない場合は明確なエラーメッセージを表示するための準備
            print(f"Bedrock mode: Using model {self.model} (IAM権限が必要です)")
        else:
            # Anthropicモードの場合はANTHROPIC_MODEL_IDを使用
            default_model = "claude-3-sonnet-20240229"  # フォールバック値
            self.model = os.getenv("ANTHROPIC_MODEL_ID", default_model)
            print(f"Anthropic mode: Using model {self.model}")

        # 後方互換性のために、MODEL_IDがあれば最優先で使用
        self.model = os.getenv("MODEL_ID", self.model)

        # デフォルトプロンプト
        self.default_prompt = "これは動画のフレーム画像です。動画の最初から最後の流れ、動作を微分して日本語で解説してください。"

        # 章立て解析用のデフォルトプロンプト
        self.default_chapters_prompt = "これは動画のフレーム画像です。以下の形式で動画を章立てして解説してください。\n\n【形式】\n# 動画の概要\n（50-100文字程度で動画全体の概要を簡潔に説明）\n\n## 章1：タイトル\n（章の内容を説明）\n\n## 章2：タイトル\n（章の内容を説明）\n\n（必要に応じて章を追加）\n\n# まとめ\n（動画全体のポイントを簡潔にまとめる）"
        
        # Bedrock Agent用のパラメータ
        self.bedrock_agent_id = os.getenv("BEDROCK_AGENT_ID", "")
        self.bedrock_agent_alias_id = os.getenv("BEDROCK_AGENT_ALIAS_ID", "")
        
        # 有効なエージェントIDとエイリアスIDがあるか検証
        if self.bedrock_agent_id and self.bedrock_agent_alias_id:
            # サンプル値は使用しない
            if self.bedrock_agent_id in ['abcde12345fghi67890j', 'YOUR_AGENT_ID']:
                logger.warning(f"無効なBEDROCK_AGENT_IDが設定されています: {self.bedrock_agent_id}")
                self.bedrock_agent_id = ""
                self.bedrock_agent_alias_id = ""
            else:
                logger.info(f"Bedrock Agentの設定を検出: Agent ID={self.bedrock_agent_id}, Alias ID={self.bedrock_agent_alias_id}")
        
        # 台本生成用のデフォルトプロンプト
        self.default_script_prompt = """あなたは不動産の解説動画「ゆっくり不動産」の台本作成専門のAIアシスタントです。
以下の章タイトルと概要に基づいて、ゆっくり不動産の台本を作成してください。

# 章タイトル
{chapter_title}

# 章の概要
{chapter_summary}

# 台本の長さ
{duration_minutes}分程度の動画向け台本（目安: 1分あたり約200〜250字）

以下の点に注意して台本を作成してください：
1. ゆっくり実況の口調で書く（「～です」「～ます」調）
2. 専門用語は噛み砕いて説明する
3. 重要なポイントは繰り返して強調する
4. 読者が実際に行動できる具体的なアドバイスを含める
5. 台本形式は「話者1:」「話者2:」「ナレーション:」で話者を示し、その後に台詞内容を記載する
6. 必ず「話者1:」「話者2:」「ナレーション:」のみを使用し、キャラクター名は使わないでください
7. 指定された動画時間に合わせて、適切な台本の長さになるよう調整してください（1分あたり200〜250文字が目安）
8. 台本の終わりは次の章につながる終わり方にしてください（例:「次の章では～について見ていきましょう」など）

台本を作成してください："""

    def get_frames_from_video(self, file_path, max_images=20):
        """ビデオからフレームを抽出してbase64にエンコード"""
        video = cv2.VideoCapture(file_path)
        if not video.isOpened():
            raise FileNotFoundError(
                f"ビデオファイル '{file_path}' を開けませんでした。"
            )

        base64_frames = []
        buffer = None

        while video.isOpened():
            success, frame = video.read()
            if not success:
                break
            _, buffer = cv2.imencode(".jpg", frame)
            base64_frame = base64.b64encode(buffer).decode("utf-8")
            base64_frames.append(base64_frame)
        video.release()

        # フレームがない場合はエラー
        if not base64_frames:
            raise ValueError("ビデオからフレームを抽出できませんでした。")

        # 選択する画像の数を制限する
        num_frames = len(base64_frames)
        if num_frames <= max_images:
            # フレーム数がmax_images以下の場合はすべて使用
            return base64_frames, buffer
        else:
            # フレーム数が多い場合は均等に抽出
            step = max(num_frames // max_images, 1)  # ゼロ除算を避ける
            selected_frames = base64_frames[0::step][:max_images]
            return selected_frames, buffer

    def analyze_video(
        self, file_path, prompt=None, model=None, max_images=20, stream_callback=None
    ):
        """ビデオを解析してテキスト結果を返す"""
        # パラメータの設定
        if prompt is None:
            prompt = self.default_prompt

        if model is None:
            model = self.model

        # ビデオからフレームを取得
        base64_frames, _ = self.get_frames_from_video(file_path, max_images)

        # 結果を保存する変数
        result_text = ""

        # Anthropic APIかBedrock APIかによって処理を分岐
        if not self.use_bedrock:
            # Claude APIにリクエストを送信（Anthropicクライアント）
            with self.client.messages.stream(
                model=model,  # モデル指定
                max_tokens=1024,  # 最大トークン数
                messages=[
                    {
                        "role": "user",
                        "content": [
                            *map(
                                lambda x: {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/jpeg",
                                        "data": x,
                                    },
                                },
                                base64_frames,
                            ),
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            ) as stream:
                for text in stream.text_stream:
                    result_text += text
                    if stream_callback:
                        stream_callback(text)
        else:
            # Bedrock APIにリクエストを送信
            # Bedrockのリクエストボディを作成
            body = json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 1024,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                *map(
                                    lambda x: {
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": "image/jpeg",
                                            "data": x,
                                        },
                                    },
                                    base64_frames,
                                ),
                                {"type": "text", "text": prompt},
                            ],
                        }
                    ],
                }
            )

            try:
                # ストリーミングAPIが拒否されているため、通常の同期APIを使用
                logger.info("ストリーミングAPIが利用できないため、通常のAPIを使用します")
                
                # 通常のinvoke_modelを使用
                # Claude 3.5 Sonnetモデル用のリクエスト形式に戻す
                try:
                    # Anthropicモデル用（標準）- 仕様通りClaudeモデルを使用
                    response = self.bedrock_runtime.invoke_model(
                        modelId=model, body=body
                    )
                except Exception as e:
                    error_msg = str(e)
                    if "AccessDeniedException" in error_msg and "is not authorized to perform" in error_msg:
                        # IAM権限エラーの場合、詳細なエラーメッセージを提供
                        logger.error("AWS IAM権限エラー: Bedrock APIへのアクセス権限がありません")
                        logger.error("必要な権限: bedrock:InvokeModel")
                        logger.error("AWS管理者に以下の権限を要求してください:")
                        logger.error("1. AWS IAM コンソールでユーザーのポリシーを確認")
                        logger.error("2. Bedrock APIへのアクセス権限を追加 (bedrock:InvokeModel)")
                        logger.error("3. 特に anthropic.claude-3-5-sonnet-20240620-v1:0 へのアクセスを確保")
                        raise ConnectionError("AWS Bedrock API権限エラー: AWS IAM権限の設定が必要です") from e
                    else:
                        # その他のエラーはそのまま伝播
                        raise
                
                # 応答本体から結果を抽出
                response_body = json.loads(response.get('body').read())
                
                # Anthropicモデル用のレスポンス処理（仕様に従いClaudeモデルのみサポート）
                if 'content' in response_body and len(response_body['content']) > 0:
                    for content_item in response_body['content']:
                        if content_item.get('type') == 'text':
                            text = content_item.get('text', '')
                            result_text += text
                            
                            # コールバックがあれば呼び出し (ストリーミングをシミュレート)
                            if stream_callback:
                                # テキストを小さな部分に分割して疑似ストリーミング
                                chunk_size = 20  # 20文字ずつ送信
                                for i in range(0, len(text), chunk_size):
                                    text_chunk = text[i:i+chunk_size]
                                    stream_callback(text_chunk)
                                    import time
                                    time.sleep(0.05)  # 少し待機して疑似ストリーミング
            except Exception as e:
                raise RuntimeError(f"Bedrock API error: {str(e)}")

        return result_text

    def analyze_video_with_chapters(
        self, file_path, prompt=None, model=None, max_images=20, stream_callback=None
    ):
        """ビデオを章立て形式で解析してテキスト結果を返す"""
        # パラメータの設定
        if prompt is None:
            prompt = self.default_chapters_prompt

        if model is None:
            model = self.model

        # ビデオからフレームを取得
        base64_frames, _ = self.get_frames_from_video(file_path, max_images)

        # 結果を保存する変数
        result_text = ""

        # Anthropic APIかBedrock APIかによって処理を分岐
        if not self.use_bedrock:
            # Claude APIにリクエストを送信（Anthropicクライアント）
            with self.client.messages.stream(
                model=model,  # モデル指定
                max_tokens=2048,  # 章立て形式は長くなるので最大トークン数を増やす
                messages=[
                    {
                        "role": "user",
                        "content": [
                            *map(
                                lambda x: {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/jpeg",
                                        "data": x,
                                    },
                                },
                                base64_frames,
                            ),
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            ) as stream:
                for text in stream.text_stream:
                    result_text += text
                    if stream_callback:
                        stream_callback(text)
        else:
            # Bedrock APIにリクエストを送信
            # Bedrockのリクエストボディを作成
            body = json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 2048,  # 章立て形式は長くなるので最大トークン数を増やす
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                *map(
                                    lambda x: {
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": "image/jpeg",
                                            "data": x,
                                        },
                                    },
                                    base64_frames,
                                ),
                                {"type": "text", "text": prompt},
                            ],
                        }
                    ],
                }
            )

            try:
                # ストリーミングAPIが拒否されているため、通常の同期APIを使用
                logger.info("ストリーミングAPIが利用できないため、通常のAPIを使用します")
                
                # 通常のinvoke_modelを使用
                # Claude 3.5 Sonnetモデル用のリクエスト形式に戻す
                try:
                    # Anthropicモデル用（標準）- 仕様通りClaudeモデルを使用
                    response = self.bedrock_runtime.invoke_model(
                        modelId=model, body=body
                    )
                except Exception as e:
                    error_msg = str(e)
                    if "AccessDeniedException" in error_msg and "is not authorized to perform" in error_msg:
                        # IAM権限エラーの場合、詳細なエラーメッセージを提供
                        logger.error("AWS IAM権限エラー: Bedrock APIへのアクセス権限がありません")
                        logger.error("必要な権限: bedrock:InvokeModel")
                        logger.error("AWS管理者に以下の権限を要求してください:")
                        logger.error("1. AWS IAM コンソールでユーザーのポリシーを確認")
                        logger.error("2. Bedrock APIへのアクセス権限を追加 (bedrock:InvokeModel)")
                        logger.error("3. 特に anthropic.claude-3-5-sonnet-20240620-v1:0 へのアクセスを確保")
                        raise ConnectionError("AWS Bedrock API権限エラー: AWS IAM権限の設定が必要です") from e
                    else:
                        # その他のエラーはそのまま伝播
                        raise
                
                # 応答本体から結果を抽出
                response_body = json.loads(response.get('body').read())
                
                # Anthropicモデル用のレスポンス処理（仕様に従いClaudeモデルのみサポート）
                if 'content' in response_body and len(response_body['content']) > 0:
                    for content_item in response_body['content']:
                        if content_item.get('type') == 'text':
                            text = content_item.get('text', '')
                            result_text += text
                            
                            # コールバックがあれば呼び出し (ストリーミングをシミュレート)
                            if stream_callback:
                                # テキストを小さな部分に分割して疑似ストリーミング
                                chunk_size = 20  # 20文字ずつ送信
                                for i in range(0, len(text), chunk_size):
                                    text_chunk = text[i:i+chunk_size]
                                    stream_callback(text_chunk)
                                    import time
                                    time.sleep(0.05)  # 少し待機して疑似ストリーミング
            except Exception as e:
                raise RuntimeError(f"Bedrock API error: {str(e)}")

        return result_text
