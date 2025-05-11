"""
台本生成を担当するクラス
"""

import json
import logging
import re
import boto3
import botocore
import botocore.config
from typing import Dict, List, Optional, Any, Union

from ..aws_retry import aws_api_retry
from ..aws_credentials import with_aws_credential_refresh
from .models import ChapterData, ScriptData

# ロガー設定
logger = logging.getLogger(__name__)


def sanitize_script(script_text: str) -> str:
    """台本テキストをサニタイズし、不要なデータを除去する
    
    Args:
        script_text: サニタイズ対象の台本テキスト
        
    Returns:
        サニタイズされた台本テキスト
    """
    # テキストがない場合は空文字を返す
    if not script_text:
        return ""
        
    # 1. AIが追加した説明/前書きを削除
    first_character_idx = -1
    for character in ["ナレーション:", "れいむ:", "まりさ:"]:
        pos = script_text.find(character)
        if pos >= 0 and (first_character_idx == -1 or pos < first_character_idx):
            first_character_idx = pos
    
    # 説明文が検出された場合は削除
    if first_character_idx > 0:
        original_length = len(script_text)
        script_text = script_text[first_character_idx:]
        logger.info(f"台本の前書き/説明文を削除しました（{original_length - len(script_text)}文字）")
    
    # 2. 行単位での厳格なフィルタリング
    lines = script_text.split('\n')
    clean_lines = []
    removed_lines = 0
    
    for line in lines:
        # EventStreamやオブジェクト参照を含む行は完全に除外
        if any(marker in line for marker in 
              ['EventStream', 'botocore', 'object at 0x', 'at 0x']):
            removed_lines += 1
            logger.warning(f"サニタイズ: 問題のある行を完全に削除「{line[:30]}...」")
            continue
            
        # キャラクター発言行での特別チェック
        if any(char_prefix in line for char_prefix in ["れいむ:", "まりさ:", "ナレーション:"]):
            # 不審なパターンを持つキャラクター行を除外
            if '<' in line and '>' in line:
                removed_lines += 1
                logger.warning(f"サニタイズ: 問題のあるキャラクター行を削除「{line[:30]}...」")
                continue
        
        # 安全な行のみを追加
        clean_lines.append(line)
    
    # 3. 正規表現によるさらなるサニタイズ
    sanitized_text = '\n'.join(clean_lines)
    
    # あらゆる形式のオブジェクト参照パターンを対象に
    patterns = [
        # 一般的なオブジェクト参照
        r'<[^>]*?(EventStream|botocore|object at|at 0x)[^>]*?>',
        
        # キャラクター発言行内のオブジェクト参照
        r'(れいむ|まりさ|ナレーション):.*?<.*?(EventStream|object|botocore).*?>.*',
        
        # 完全なEventStream行
        r'.*EventStream.*',
        r'.*botocore.*',
        r'.*object at 0x.*'
    ]
    
    # パターン適用
    for pattern in patterns:
        old_len = len(sanitized_text)
        sanitized_text = re.sub(pattern, '', sanitized_text)
        if len(sanitized_text) != old_len:
            logger.info(f"サニタイズ: '{pattern}'パターンで{old_len - len(sanitized_text)}文字を削除")
    
    # 4. 台本の整形 - 話者の間に改行を挿入して可読性を向上
    lines = sanitized_text.split('\n')
    
    # 空行を削除して基本クリーニング
    clean_lines = [line for line in lines if line.strip()]  
    
    # 話者間に改行を入れる処理
    formatted_lines = []
    prev_speaker = None
    
    for line in clean_lines:
        # 話者の判定（行頭が「れいむ:」「まりさ:」「ナレーション:」で始まるか）
        current_speaker = None
        for speaker in ["れいむ:", "まりさ:", "ナレーション:"]:
            if line.startswith(speaker):
                current_speaker = speaker
                break
        
        # 前の行と今の行の話者が異なる場合、改行を挿入
        if current_speaker and prev_speaker and current_speaker != prev_speaker:
            formatted_lines.append("")  # 空行を挿入
        
        formatted_lines.append(line)
        prev_speaker = current_speaker
    
    if removed_lines > 0 or len(lines) != len(clean_lines):
        logger.info(f"サニタイズ完了: 合計{removed_lines}行を削除、{len(lines) - len(clean_lines)}件の空行を削除")
    
    logger.info(f"台本フォーマット調整: 話者間に改行を挿入して可読性を向上({len(formatted_lines)}行)")
    
    return '\n'.join(formatted_lines)


class ScriptGenerator:
    """台本生成のためのクラス"""
    
    def __init__(self, analyzer):
        """初期化
        
        Args:
            analyzer: VideoAnalyzerインスタンス
        """
        self.analyzer = analyzer
        self.script_prompt = analyzer.default_script_prompt
        
    def calculate_expected_length(self, duration_minutes: int) -> int:
        """動画の長さに基づいて必要な文字数を計算する
        
        Args:
            duration_minutes: 動画の長さ（分）
            
        Returns:
            目標文字数
        """
        # 基本計算: 1分あたり200〜250文字
        min_chars = duration_minutes * 200
        max_chars = duration_minutes * 250
        
        # 目標文字数は範囲の中間値
        target_chars = int((min_chars + max_chars) / 2)
        
        logger.info(f"動画時間{duration_minutes}分に対する目標文字数: {min_chars}〜{max_chars}文字（目標: {target_chars}文字）")
        
        return target_chars
        
    def ensure_minimum_length(self, script_content: str, target_chars: int, script_data: Dict[str, Any]) -> str:
        """台本が指定された文字数に達していない場合、不足分を補う

        Args:
            script_content: 現在の台本内容
            target_chars: 目標文字数
            script_data: 台本データ(contextとして利用)
            
        Returns:
            拡充された台本内容
        """
        current_length = len(script_content)
        
        if current_length >= target_chars:
            logger.info("台本は既に目標文字数に達しています")
            return script_content
            
        # 不足している文字数を計算
        missing_chars = target_chars - current_length
        logger.info(f"台本の文字数が不足しています: 不足={missing_chars}文字")
        
        # 台本の拡充をいくつかのセクションに分けて処理
        try:
            # 台本の末尾を取得して、どのように終わっているかを把握
            last_lines = "\n".join(script_content.split('\n')[-5:])
            
            # フィードバックスタイルを解析
            style_hint = ""
            if 'feedback' in script_data and isinstance(script_data['feedback'], list):
                for fb in script_data['feedback']:
                    if 'ギャル' in fb:
                        style_hint = "ギャル風の口調（「～だよね～」「マジ」「ヤバイ」などの言葉を使う）で"
                        break
                    if 'お笑い' in fb:
                        style_hint = "お笑い風（ボケとツッコミの掛け合い、面白い例え話を含める）で"
                        break
            
            # 動画時間を取得（目標文字数の計算に必要）
            duration_minutes = script_data.get('duration_minutes', 3)
            chapter_title = script_data.get('chapter_title', '不動産解説')
            
            # 拡張用の基本データを準備
            supplemental_text = f"""

れいむ: では、{chapter_title}についてもう少し詳しく説明しましょう。

まりさ: はい、お願いします！

れいむ: {chapter_title}に関して重要なポイントをさらに掘り下げますと、まず施工品質と材料選びが重要です。特に断熱性能は快適な居住環境を左右するため、押さえておくべきポイントです。

まりさ: なるほど！それって具体的にどういうことなの？

れいむ: 例えば、断熱材の種類によって性能が大きく異なります。高性能なグラスウールやウレタンフォームなどは、冬は暖かく夏は涼しい空間を作り出すのに役立ちます。また、窓の仕様も重要で、ペアガラスや樹脂サッシの採用で大幅に断熱性能が向上します。

まりさ: へぇ〜、そういう細かいところまで考えるんだね！

れいむ: はい、そうですね。また、コスト面でも工夫ができます。例えば、施工時期や材料の選択、施工方法の工夫によって、予算内でより高品質な結果を得ることができるのです。

まりさ: すごい！そういう情報って、これから{chapter_title}を検討している人には本当に役立つね！

れいむ: その通りです。次の章では、より具体的な事例も交えて解説していきましょう。
"""
            
            # 追加テキストの長さをチェック
            supplement_length = len(supplemental_text)
            
            # 必要な文字数に達するまで補足テキストを追加
            if current_length + supplement_length < target_chars:
                # より多くのテキストが必要な場合は、さらに内容を追加
                additional_text = f"""

れいむ: また、{chapter_title}に関して見落としがちなのが、法的な側面です。建築基準法や地域ごとの条例など、様々な規制があります。

まりさ: え？そんな複雑なことまで考えなきゃいけないの？

れいむ: はい、事前に確認しておかないと、後々問題になることもあります。例えば、建ぺい率や容積率の制限、防火地域の指定などによって、できることとできないことが変わってきます。

まりさ: なるほど！専門家に相談するのが大事なんだね。

れいむ: その通りです。また、資金計画も重要です。初期費用だけでなく、維持費や修繕費なども含めたライフサイクルコストで考えることをおすすめします。

まりさ: 長い目で見るってことだね！でも、そういう計算って難しそう...

れいむ: 心配いりません。最近では様々なシミュレーションツールも充実していますし、専門家のアドバイスを受けることで適切な計画を立てることができます。

まりさ: そっか！詳しく教えてくれてありがとう！次の章も楽しみにしてるね！
"""
                
                # 組み合わせたテキスト
                expanded_script = script_content + supplemental_text + additional_text
                
                # 必要な長さに調整
                if len(expanded_script) > target_chars + 100:
                    # 長すぎる場合は適切なサイズにカット
                    lines = expanded_script.split('\n')
                    adjusted_script = []
                    current_len = 0
                    
                    for line in lines:
                        if current_len + len(line) + 1 <= target_chars:  # +1は改行文字分
                            adjusted_script.append(line)
                            current_len += len(line) + 1
                        else:
                            break
                            
                    expanded_script = '\n'.join(adjusted_script)
                
                logger.info(f"台本を拡充しました: {len(script_content)}文字 → {len(expanded_script)}文字")
                return expanded_script
            else:
                # 基本の補足テキストで十分な場合
                expanded_script = script_content + supplemental_text
                logger.info(f"台本を拡充しました: {len(script_content)}文字 → {len(expanded_script)}文字")
                return expanded_script
            
        except Exception as main_error:
            logger.error(f"台本拡充の主要処理でエラー: {main_error}")
            # エラー発生時の最終手段として簡単な追加を行う
            fallback_text = "\n\nれいむ: 以上がこの章の解説になります。何か質問はありますか？\n\nまりさ: とても分かりやすかったです。ありがとうございます。"
            return script_content + fallback_text
    
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
    
    @with_aws_credential_refresh
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
                # 認証情報の有効性確認
                if hasattr(self.analyzer, 'credential_manager') and self.analyzer.credential_manager:
                    self.analyzer.credential_manager.check_credentials()
                
                # Bedrockモデル呼び出し
                try:
                    response = self.analyzer.bedrock_runtime.invoke_model(
                        modelId=self.analyzer.model,
                        body=json.dumps({
                            "anthropic_version": "bedrock-2023-05-31",
                            "max_tokens": 5000,  # 大幅に増加（最大10分の動画で約2000〜2500文字必要）
                            "messages": [
                                {"role": "user", "content": prompt}
                            ]
                        })
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
                    elif "UnrecognizedClientException" in error_msg or "security token" in error_msg.lower():
                        # セキュリティトークンエラーの場合、詳細なエラーメッセージを提供
                        logger.error("AWS認証エラー: セキュリティトークンが無効です")
                        logger.error("認証情報をリフレッシュして再試行します...")
                        
                        # 認証情報マネージャーがある場合は強制的にリフレッシュ
                        if hasattr(self.analyzer, 'credential_manager') and self.analyzer.credential_manager:
                            self.analyzer.credential_manager.refresh_credentials()
                            # クライアントを再作成
                            import botocore
                            client_config = botocore.config.Config(
                                connect_timeout=30,
                                read_timeout=120,
                                retries={'max_attempts': 5, 'mode': 'adaptive'},
                                max_pool_connections=20,
                                tcp_keepalive=True
                            )
                            self.analyzer.bedrock_runtime = self.analyzer.credential_manager.get_client(
                                'bedrock-runtime', config=client_config
                            )
                            # リフレッシュ後に再試行
                            response = self.analyzer.bedrock_runtime.invoke_model(
                                modelId=self.analyzer.model,
                                body=json.dumps({
                                    "anthropic_version": "bedrock-2023-05-31",
                                    "max_tokens": 5000,
                                    "messages": [
                                        {"role": "user", "content": prompt}
                                    ]
                                })
                            )
                        else:
                            raise ConnectionError("AWS認証エラー: セキュリティトークンが無効で、認証情報マネージャーがありません") from e
                    else:
                        # その他のエラーはそのまま伝播
                        raise
                
                # レスポンスの解析
                response_body = json.loads(response.get('body').read())
                script_content = response_body['content'][0]['text']
                
                # 目標文字数と実際の文字数をチェック
                target_chars = self.calculate_expected_length(duration_minutes)
                actual_chars = len(script_content)
                
                logger.info(f"章「{chapter['chapter_title']}」の台本生成が完了: 文字数={actual_chars}（目標: {target_chars}）")
            except Exception as e:
                # エラーメッセージから認証エラーを検出
                error_text = str(e).lower()
                if ('security token' in error_text and 'invalid' in error_text) or \
                   'unrecognized client' in error_text or 'expired token' in error_text:
                    # 認証情報を更新してユーザーフレンドリーなエラーメッセージを表示
                    logger.error(f"台本生成中のAWS認証エラー: {str(e)}")
                    raise ConnectionError("AWS認証情報の有効期限が切れているか、無効です。AWS認証情報を更新してください。") from e
                else:
                    logger.error(f"台本生成中にエラーが発生: {str(e)}")
                    raise
        else:
            # Anthropic APIの場合
            try:
                response = self.analyzer.client.messages.create(
                    model=self.analyzer.model,
                    max_tokens=5000,  # 大幅に増加（最大10分の動画で約2000〜2500文字必要）
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
            "feedback": [],
            "duration_minutes": duration_minutes  # 動画時間を追加
        }
        
        # スクリプトがサニタイズされていることを確認
        script_data["script_content"] = sanitize_script(script_content)
        
        return script_data
        
    @with_aws_credential_refresh
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
                # 認証情報の有効性確認
                if hasattr(self.analyzer, 'credential_manager') and self.analyzer.credential_manager:
                    self.analyzer.credential_manager.check_credentials()
                
                # Bedrockモデル呼び出し
                try:
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
                except Exception as e:
                    error_msg = str(e)
                    if "UnrecognizedClientException" in error_msg or "security token" in error_msg.lower():
                        # セキュリティトークンエラーの場合、詳細なエラーメッセージを提供
                        logger.error("AWS認証エラー: セキュリティトークンが無効です")
                        logger.error("認証情報をリフレッシュして再試行します...")
                        
                        # 認証情報マネージャーがある場合は強制的にリフレッシュ
                        if hasattr(self.analyzer, 'credential_manager') and self.analyzer.credential_manager:
                            self.analyzer.credential_manager.refresh_credentials()
                            # クライアントを再作成
                            import botocore
                            client_config = botocore.config.Config(
                                connect_timeout=30,
                                read_timeout=120,
                                retries={'max_attempts': 5, 'mode': 'adaptive'},
                                max_pool_connections=20,
                                tcp_keepalive=True
                            )
                            self.analyzer.bedrock_runtime = self.analyzer.credential_manager.get_client(
                                'bedrock-runtime', config=client_config
                            )
                            # リフレッシュ後に再試行
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
                        else:
                            raise ConnectionError("AWS認証エラー: セキュリティトークンが無効で、認証情報マネージャーがありません") from e
                    else:
                        # その他のエラーはそのまま伝播
                        raise
                
                # レスポンスの解析
                response_body = json.loads(response.get('body').read())
                analysis = response_body['content'][0]['text']
                
                # 「はい」または「いいえ」を抽出
                passed = "はい" in analysis[:50]
                
                logger.info(f"台本「{script_data['chapter_title']}」の品質分析が完了")
            except Exception as e:
                # エラーメッセージから認証エラーを検出
                error_text = str(e).lower()
                if ('security token' in error_text and 'invalid' in error_text) or \
                   'unrecognized client' in error_text or 'expired token' in error_text:
                    # 認証情報を更新してユーザーフレンドリーなエラーメッセージを表示
                    logger.error(f"台本品質分析中のAWS認証エラー: {str(e)}")
                    raise ConnectionError("AWS認証情報の有効期限が切れているか、無効です。AWS認証情報を更新してください。") from e
                else:
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
    
    @with_aws_credential_refresh
    def improve_script(self, script_data: Dict[str, Any], feedback: str) -> Union[Dict[str, str], str]:
        """フィードバックに基づいて台本を改善する
        
        Args:
            script_data: 改善する台本データ
            feedback: フィードバック内容
            
        Returns:
            改善された台本または台本データ
        """
        logger.info(f"台本「{script_data['chapter_title']}」の改善を開始")
        
        # 動画時間を取得（スクリプトデータに含まれていればそれを使用）
        duration_minutes = script_data.get('duration_minutes', 3)
        target_chars = self.calculate_expected_length(duration_minutes)
        logger.info(f"台本改善の動画時間: {duration_minutes}分（目標文字数：{target_chars}文字）")
        
        # 改善用のプロンプト - 動画時間と文字数情報を追加
        prompt = f"""
あなたは不動産の解説動画「ゆっくり不動産」の台本編集アシスタントです。
以下の台本とフィードバックに基づいて、台本を改善してください。

# 台本の長さ（最重要要件）
- 台本は{duration_minutes}分の動画用です
- 【絶対条件】目標文字数は{target_chars}文字以上必要です（最低でも1分あたり200文字）
- 現在の文字数: {len(script_data['script_content'])}文字
- 返答する台本は必ず{target_chars}文字以上にしてください
- 文字数が足りない場合は、内容を拡充してください（例：物件情報の詳細説明を追加、メリット・デメリットの具体的説明を増やす、専門用語の解説を詳しくするなど）

# 現在の台本
{script_data['script_content']}

# フィードバック
{feedback}

フィードバックを踏まえて改善した台本を作成してください。台本形式は元の形式を維持してください。
目標文字数（{target_chars}文字程度）を意識して、適切な長さになるよう調整してください。
        """
        
        # Bedrockモードの場合はBedrockを使用
        if self.analyzer.use_bedrock:
            try:
                # Bedrock AI Agentが使用可能であればそちらを優先
                if hasattr(self.analyzer, 'bedrock_agent_client') and self.analyzer.bedrock_agent_client is not None:
                    logger.info(f"Bedrock AI Agentを使用して台本改善を実行します: Agent ID={self.analyzer.bedrock_agent_id}")

                    # AI Agentプロンプトを強化（詳細な要件を指定）
                    agent_prompt = f"""
あなたはゆっくり不動産の台本編集AIアシスタントです。与えられた台本とフィードバックに基づいて台本を改善してください。

# フィードバック内容
{feedback}

# 台本の長さの要件（最重要）
- 台本は{duration_minutes}分の動画用です
- 【絶対条件】目標文字数は{target_chars}文字以上必要です
- 現在の台本は{len(script_data['script_content'])}文字です
- 返答する台本は必ず{target_chars}文字以上にする必要があります
- 文字数が足りない場合は内容を拡充してください

# キャラクター設定
- れいむ: 解説役の女性キャラクター（丁寧な口調）
- まりさ: 質問役の女性キャラクター（砕けた口調、{feedback}を反映した話し方）
- ナレーション: 状況説明

# 現在の台本
{script_data['script_content']}

上記のフィードバックに基づいて台本を改善し、返答は改善された台本のみを含めてください。
"""

                    try:
                        # セッションIDを生成（一意になるように）
                        import uuid
                        session_id = f"script_improvement_{uuid.uuid4().hex}"

                        # Bedrock Agentを呼び出し
                        logger.info(f"Bedrock Agent呼び出し: Session ID={session_id}")
                        response = self.analyzer.bedrock_agent_client.invoke_agent(
                            agentId=self.analyzer.bedrock_agent_id,
                            agentAliasId=self.analyzer.bedrock_agent_alias_id,
                            sessionId=session_id,
                            inputText=agent_prompt,
                            enableTrace=True
                        )

                        # レスポンスから台本テキストを抽出
                        if hasattr(response, 'completion'):
                            improved_script = response.completion
                            logger.info(f"AI Agentから改善台本を取得: {len(improved_script)}文字")
                        else:
                            # EventStreamの場合はイテレーションして取得
                            import botocore
                            if isinstance(response, botocore.eventstream.EventStream):
                                script_content = []
                                for event in response:
                                    if hasattr(event, 'chunk') and hasattr(event.chunk, 'bytes'):
                                        try:
                                            chunk_data = event.chunk.bytes.decode('utf-8')
                                            script_content.append(chunk_data)
                                        except Exception as chunk_err:
                                            logger.error(f"チャンク処理エラー: {chunk_err}")

                                if script_content:
                                    improved_script = ''.join(script_content)
                                    logger.info(f"EventStreamから改善台本を取得: {len(improved_script)}文字")
                                else:
                                    # 通常のモデル呼び出しにフォールバック
                                    logger.warning("AI Agentからの応答が空でした。通常のモデル呼び出しにフォールバックします。")
                                    raise ValueError("Empty response from AI Agent")
                            else:
                                # 辞書形式の場合
                                if isinstance(response, dict) and 'completion' in response:
                                    improved_script = response['completion']
                                    logger.info(f"辞書からcompletion取得: {len(improved_script)}文字")
                                else:
                                    # 通常のモデル呼び出しにフォールバック
                                    logger.warning(f"API応答から台本を抽出できません: {type(response)}")
                                    raise ValueError(f"Unable to extract script from response: {type(response)}")

                        logger.info(f"Bedrock AI Agentを使用して台本「{script_data['chapter_title']}」の改善が完了")
                    except Exception as agent_error:
                        # エラーの詳細を記録
                        logger.error(f"AI Agent呼び出しエラー: {agent_error}")

                        # 通常のモデル呼び出しにフォールバック
                        logger.info("AI Agentエラーのため通常のBedrock基盤モデルにフォールバックします")
                        # 通常のBedrock基盤モデル呼び出し（強化されたリトライ機能付き）
                        logger.info("通常のBedrock基盤モデルを使用します")
                else:
                    # 通常のBedrock基盤モデル呼び出し（強化されたリトライ機能付き）
                    logger.info("Bedrock AI Agentが使用できないため、通常のBedrock基盤モデルを使用します")
                
                @aws_api_retry(max_retries=3, base_delay=2, jitter=0.5)
                def call_bedrock_model():
                    # 最適化された設定でクライアント作成
                    import botocore
                    client_config = botocore.config.Config(
                        connect_timeout=30,    # 接続タイムアウト大幅増加
                        read_timeout=180,      # 読み取りタイムアウト大幅増加
                        retries={'max_attempts': 5, 'mode': 'adaptive'},  # アダプティブリトライ回数増加
                        max_pool_connections=20, # 接続プール拡大
                        tcp_keepalive=True      # TCP接続をキープアライブ
                    )
                    
                    # 最適化された設定でのクライアント
                    temp_client = boto3.client(
                        'bedrock-runtime', 
                        region_name=self.analyzer.bedrock_runtime._client_config.region_name,
                        config=client_config
                    )
                    
                    return temp_client.invoke_model(
                        modelId=self.analyzer.model,
                        body=json.dumps({
                            "anthropic_version": "bedrock-2023-05-31",
                            "max_tokens": 5000,  # 大幅に増加（最大10分の動画で約2000〜2500文字必要）
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
                    # エラー発生のため強化されたBedrock基盤モデルにフォールバック
                    logger.info("エラー発生のため強化されたBedrock基盤モデルに強化プロンプトでフォールバック")
                    
                    # フィードバックスタイルの解析と強化プロンプトの作成
                    style_hint = ""
                    if 'feedback' in script_data and isinstance(script_data['feedback'], list):
                        for fb in script_data['feedback']:
                            if 'ギャル' in fb:
                                style_hint = "ギャル風の口調（「～だよね～」「マジ」「ヤバイ」などの言葉を使う）で"
                                break
                            if 'お笑い' in fb:
                                style_hint = "お笑い風（ボケとツッコミの掛け合い、面白い例え話を含める）で"
                                break
                    
                    # 目標文字数を明確に指定
                    duration_minutes = script_data.get('duration_minutes', 3)
                    logger.info(f"最終フォールバックでの動画時間設定: {duration_minutes}分")
                    target_chars = self.calculate_expected_length(duration_minutes)
                    
                    # 強化されたプロンプト
                    enhanced_prompt = f"""
あなは不動産の解説動画「ゆっくり不動産」の台本編集スペシャリストです。以下の台本とフィードバックに基づいて台本を改善してください。

# 改善指示
{script_data.get('feedback', ['台本を改善'])[-1] if isinstance(script_data.get('feedback'), list) else '台本を改善'}

# スタイル指定
{style_hint}台本を作成してください。

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

# 文字数要件（最重要）
- この台本は{duration_minutes}分の動画用です（これは重要な情報です）
- 【絶対条件】：台本は必ず{target_chars}文字以上になるようにしてください
- 台本が短い場合は、具体例の追加、メリット・デメリットの詳細な説明、関連知識の補足で拡充してください
- 最低でも{target_chars}文字の台本を作成してください

# 現在の台本
{script_data['script_content']}

返答は台本のみを含めてください。解説や前置きは不要です。
"""
                    
                    # 最適化されたタイムアウト設定での改良版プロンプトを呼び出し
                    try:
                        import botocore
                        client_config = botocore.config.Config(
                            connect_timeout=15,    # 適切な接続タイムアウト
                            read_timeout=60,       # 長い読み取りタイムアウト
                            retries={'max_attempts': 3, 'mode': 'adaptive'},  # アダプティブリトライ
                            max_pool_connections=10 # 接続プール拡大
                        )
                        
                        temp_client = boto3.client(
                            'bedrock-runtime', 
                            region_name=self.analyzer.bedrock_runtime._client_config.region_name,
                            config=client_config
                        )
                        
                        response = temp_client.invoke_model(
                            modelId=self.analyzer.model,
                            body=json.dumps({
                                "anthropic_version": "bedrock-2023-05-31",
                                "max_tokens": 5000,  # 大幅に増加
                                "temperature": 0.7,  # より創造的な出力
                                "messages": [
                                    {"role": "user", "content": enhanced_prompt}
                                ]
                            })
                        )
                        
                        response_body = json.loads(response.get('body').read())
                        improved_script = response_body['content'][0]['text']
                        logger.info(f"強化プロンプトでフォールバック成功: 文字数={len(improved_script)}/{target_chars}文字")
                    except Exception as e2:
                        # 強化プロンプトも失敗した場合は元のプロンプトを使用
                        logger.error(f"強化プロンプト呼び出しにも失敗: {str(e2)}")
                        response = self.analyzer.bedrock_runtime.invoke_model(
                            modelId=self.analyzer.model,
                            body=json.dumps({
                                "anthropic_version": "bedrock-2023-05-31",
                                "max_tokens": 5000,  # 大幅に増加
                                "messages": [
                                    {"role": "user", "content": prompt}
                                ]
                            })
                        )
                        
                        # レスポンスの解析
                        response_body = json.loads(response.get('body').read())
                        improved_script = response_body['content'][0]['text']
                        
                    logger.info(f"フォールバック: Bedrock基盤モデルを使用して台本の改善が完了（文字数: {len(improved_script)}文字）")
                except Exception as fallback_error:
                    logger.error(f"フォールバックにも失敗: {str(fallback_error)}")
                    raise
        else:
            # Anthropic APIの場合
            try:
                response = self.analyzer.client.messages.create(
                    model=self.analyzer.model,
                    max_tokens=5000,  # 大幅に増加（最大10分の動画で約2000〜2500文字必要）
                    messages=[{"role": "user", "content": prompt}]
                )
                improved_script = response.content[0].text
                logger.info(f"台本「{script_data['chapter_title']}」の改善が完了")
            except Exception as e:
                logger.error(f"台本改善中にエラーが発生: {str(e)}")
                raise
        
        # ★★★ 根本対策: 全ての台本内容を最終サニタイズ処理 ★★★
        # EventStreamオブジェクト参照を完全に除去し、余計な前書きも削除
        sanitized_script = sanitize_script(improved_script)
        logger.info(f"最終サニタイズ処理を適用しました。処理前={len(improved_script)}文字、処理後={len(sanitized_script)}文字")
        
        # 文字数が目標に達しているか確認
        if len(sanitized_script) < target_chars:
            # 文字数不足の場合は拡充
            extended_script = self.ensure_minimum_length(sanitized_script, target_chars, script_data)
            return extended_script
        else:
            return sanitized_script