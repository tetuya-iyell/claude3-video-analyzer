import anthropic
import base64
import cv2
import os
import boto3
import json
import logging
from typing import List, Dict, Any, Optional, Tuple, Union
from dotenv import load_dotenv
from .aws_retry import aws_api_retry
from .aws_credentials import with_aws_credential_refresh

# ロガー設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# EventStream問題を解決するユーティリティ関数
def safe_stringify(obj: Any) -> str:
    """オブジェクトを安全に文字列化する関数。
    EventStreamやその他のPythonオブジェクト参照を含む場合は、それらを除去する。
    
    Args:
        obj: 文字列化する対象のオブジェクト
        
    Returns:
        安全な文字列表現
    """
    # すでに文字列ならそのまま返す
    if isinstance(obj, str):
        return obj
    
    # EventStreamなどの特殊オブジェクトの場合は空文字列を返す
    import botocore
    if hasattr(botocore, 'eventstream') and isinstance(obj, botocore.eventstream.EventStream):
        logger.warning("EventStreamオブジェクトを安全に変換: '[EventStream content]'")
        return "[EventStream content]"
    
    # その他のPythonオブジェクト参照を持つ可能性のある場合
    try:
        # 文字列化してPythonオブジェクト参照を検出
        obj_str = str(obj)
        if ('<' in obj_str and '>' in obj_str and 
            any(marker in obj_str for marker in ['object at 0x', 'EventStream', 'botocore'])):
            logger.warning(f"オブジェクト参照を検出: {obj_str[:30]}... - 安全な値に置換")
            return "[Object reference removed]"
        return obj_str
    except Exception:
        # 例外が発生した場合も安全な値を返す
        return "[Unstringifiable object]"


def sanitize_script(script_text: str) -> str:
    """台本テキストを徹底的にサニタイズし、EventStreamオブジェクト参照などを完全に除去する

    Args:
        script_text: サニタイズ対象の台本テキスト
        
    Returns:
        サニタイズされた台本テキスト
    """
    import re
    
    # テキストがない場合は空文字を返す
    if not script_text:
        return ""
        
    # 1. AIが追加した説明/前書きを削除（ユーザーの要望による）
    # 通常、台本は「ナレーション:」「れいむ:」「まりさ:」などで始まるので、その前の説明文を削除
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
        # EventStreamやオブジェクト参照を含む行は完全に除外（あらゆるパターンを検出）
        if any(marker in line for marker in 
              ['EventStream', 'botocore', 'object at 0x', 'at 0x']):
            removed_lines += 1
            logger.warning(f"サニタイズ: 問題のある行を完全に削除「{line[:30]}...」")
            continue
            
        # キャラクター発言行での特別チェック（最も重要）
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
        
        # キャラクター発言行内のオブジェクト参照（特に重要）
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
        
    def calculate_expected_length(self, duration_minutes: int) -> int:
        """動画の長さに基づいて必要な文字数を計算する
        
        Args:
            duration_minutes: 動画の長さ（分）
            
        Returns:
            目標文字数
        """
        # 基本計算: 1分あたり200〜250文字
        # 最小は200文字/分、最大は250文字/分
        min_chars = duration_minutes * 200
        max_chars = duration_minutes * 250
        
        # 目標文字数は範囲の中間値
        target_chars = int((min_chars + max_chars) / 2)
        
        # 文字数に関するログ出力
        logger.info(f"動画時間{duration_minutes}分に対する目標文字数: {min_chars}〜{max_chars}文字（目標: {target_chars}文字）")
        
        return target_chars
        
    def ensure_minimum_length(self, script_content: str, target_chars: int, script_data: dict) -> str:
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
        
        # 分割リクエスト方式で拡充する（大きなリクエストを複数の小さなリクエストに分ける）
        
        # フィードバックスタイルを解析
        style_hints = ""
        if 'feedback' in script_data and isinstance(script_data['feedback'], list):
            for fb in script_data['feedback']:
                if 'ギャル' in fb:
                    style_hints = "ギャル風の口調（「〜だよね〜」「マジ」「ヤバイ」などの言葉を使う）で"
                elif 'お笑い' in fb:
                    style_hints = "お笑い要素（ボケとツッコミ、ユーモアのある会話）を含めて"
                    
        # まず、章の内容を要約して把握するための小さなリクエスト
        chapter_title = script_data.get('chapter_title', '不動産解説')
        
        # 台本の拡充をいくつかのセクションに分けて処理
        # 1. 既存の内容を分析して要約する
        # 2. 拡充すべきポイントを特定する 
        # 3. 必要な数のセクションを追加する

        # 会話スタイルの継続性を確保するためのプロンプト
        try:
            # 短いタイムアウトで要約APIを呼び出す（高速処理）
            summary_prompt = f"""
以下の台本を簡潔に要約し、主要な論点・トピックを3点に絞って箇条書きでリストアップしてください。

# 台本のタイトル
{chapter_title}

# 台本
{script_content[:1000]}  # 最初の部分だけ要約用に送信

返答はJSON形式で、以下の構造にしてください:
{{
    "summary": "台本の簡潔な要約（100文字以内）",
    "main_topics": ["トピック1", "トピック2", "トピック3"],
    "style": "台本のスタイル（キャラクターの話し方や特徴）の特定"
}}
"""
            # API呼び出し
            response = None
            if self.analyzer.use_bedrock:
                try:
                    # AWS SDKの最適化されたクライアント設定
                    import botocore
                    client_config = botocore.config.Config(
                        connect_timeout=30,  # 接続タイムアウト大幅増加
                        read_timeout=120,    # 読み取りタイムアウト大幅増加
                        retries={'max_attempts': 5, 'mode': 'adaptive'},  # アダプティブリトライ回数増加
                        max_pool_connections=20, # 接続プール拡大
                        tcp_keepalive=True   # TCP接続をキープアライブ
                    )
                    
                    # タイムアウト設定付きのクライアントで呼び出し
                    temp_client = boto3.client(
                        'bedrock-runtime', 
                        region_name=self.analyzer.bedrock_runtime._client_config.region_name,
                        config=client_config
                    )
                    
                    response = temp_client.invoke_model(
                        modelId=self.analyzer.model,
                        body=json.dumps({
                            "anthropic_version": "bedrock-2023-05-31",
                            "max_tokens": 500,  # 要約なので少なめのトークン
                            "temperature": 0.2,  # より確実な出力のため低温度
                            "messages": [
                                {"role": "user", "content": summary_prompt}
                            ]
                        })
                    )
                    
                    response_body = json.loads(response.get('body').read())
                    summary_text = response_body['content'][0]['text']
                    logger.info(f"台本の要約取得に成功: {len(summary_text)}文字")
                    
                    # JSONデータの抽出を試みる
                    try:
                        import re
                        json_match = re.search(r'\{.*\}', summary_text, re.DOTALL)
                        if json_match:
                            summary_data = json.loads(json_match.group(0))
                            summary = summary_data.get('summary', '')
                            main_topics = summary_data.get('main_topics', [])
                            style_from_ai = summary_data.get('style', '')
                            
                            logger.info(f"抽出された要約: {summary}")
                            logger.info(f"抽出されたトピック: {', '.join(main_topics)}")
                        else:
                            summary = summary_text[:100]
                            main_topics = []
                            style_from_ai = ""
                    except Exception as e:
                        logger.error(f"JSON解析エラー: {e}")
                        summary = summary_text[:100]
                        main_topics = []
                        style_from_ai = ""
                        
                except Exception as e:
                    logger.warning(f"要約取得中にエラー: {e}")
                    summary = chapter_title
                    main_topics = []
                    style_from_ai = ""
            else:
                try:
                    response = self.analyzer.client.messages.create(
                        model=self.analyzer.model,
                        max_tokens=500,
                        temperature=0.2,
                        messages=[{"role": "user", "content": summary_prompt}]
                    )
                    summary_text = response.content[0].text
                    
                    # JSONデータの抽出を試みる
                    try:
                        import re
                        json_match = re.search(r'\{.*\}', summary_text, re.DOTALL)
                        if json_match:
                            summary_data = json.loads(json_match.group(0))
                            summary = summary_data.get('summary', '')
                            main_topics = summary_data.get('main_topics', [])
                            style_from_ai = summary_data.get('style', '')
                        else:
                            summary = summary_text[:100]
                            main_topics = []
                            style_from_ai = ""
                    except:
                        summary = summary_text[:100]
                        main_topics = []
                        style_from_ai = ""
                        
                except Exception as e:
                    logger.warning(f"要約取得中にエラー: {e}")
                    summary = chapter_title
                    main_topics = []
                    style_from_ai = ""
                    
            # 台本の拡充を複数の小さなリクエストに分割する
            # 足りないセクションの数を計算（1セクションあたり約400文字と仮定）
            sections_needed = (missing_chars + 200) // 400 + 1
            logger.info(f"追加するセクション数: {sections_needed}")
            
            # 台本の末尾を取得して、どのように終わっているかを把握
            last_lines = "\n".join(script_content.split('\n')[-5:])
            
            # 拡張された台本
            expanded_script = script_content
            
            # 各セクションを段階的に追加
            for i in range(sections_needed):
                # セクション追加用のプロンプトを作成
                section_type = ""
                if i == 0:
                    section_type = "より詳しい説明と具体例"
                elif i == sections_needed - 1:
                    section_type = "次の章への繋ぎと結論"
                else:
                    section_options = ["メリットとデメリットの詳細", "専門用語の詳しい解説", "具体的な事例", "関連する不動産知識"]
                    section_type = section_options[i % len(section_options)]
                    
                # 生成するセクションのサイズを計算（最後のセクションは残りの文字数に合わせる）
                if i == sections_needed - 1:
                    target_section_size = target_chars - len(expanded_script)
                else:
                    target_section_size = min(400, target_chars - len(expanded_script))
                
                if target_section_size <= 0:
                    continue  # 既に目標文字数に達している場合はスキップ
                
                section_prompt = f"""
あなたは不動産の解説動画「ゆっくり不動産」の台本編集スペシャリストです。
以下の台本に、自然につながる新しいセクションを追加してください。

# 台本の概要
{summary}

# 主要トピック
{', '.join(main_topics if main_topics else [chapter_title])}

# 現在の台本の最後の部分
{last_lines}

# 追加すべきコンテンツのタイプ
{section_type}について、{style_hints}約{target_section_size}文字程度の内容を台本形式で追加してください。

# 重要な条件
- キャラクター「れいむ」と「まりさ」の会話形式を維持する
- 既存の内容と自然につなげる
- 不動産またはコンテナハウスの情報として正確で有用な内容を含める
- {style_hints if style_hints else "既存の台本のスタイル・話し方を維持して"}書く
- 台本形式: 「れいむ:」「まりさ:」で始まる発言形式

返答は、追加するセクションの台本のみを返してください（既存の台本は含めない）。
"""

                # セクション追加のAPIリクエスト
                try:
                    section_content = ""
                    if self.analyzer.use_bedrock:
                        try:
                            # 最適化したタイムアウト設定でセクション追加リクエスト
                            import botocore
                            client_config = botocore.config.Config(
                                connect_timeout=30,     # 接続タイムアウトを大幅増加
                                read_timeout=120,       # 読み取りタイムアウトを大幅増加
                                retries={'max_attempts': 5, 'mode': 'adaptive'},  # アダプティブリトライ回数増加
                                max_pool_connections=20, # 接続プール拡大
                                tcp_keepalive=True      # TCP接続をキープアライブ
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
                                    "max_tokens": 800,  # セクション追加用のトークン数
                                    "temperature": 0.7,  # 多様な内容の生成のため
                                    "messages": [
                                        {"role": "user", "content": section_prompt}
                                    ]
                                })
                            )
                            
                            response_body = json.loads(response.get('body').read())
                            section_content = response_body['content'][0]['text']
                            logger.info(f"セクション{i+1}/{sections_needed}の追加に成功: {len(section_content)}文字")
                        except Exception as e:
                            logger.warning(f"セクション{i+1}追加中にエラー: {e}")
                            # エラー時は空のセクションか簡単なセクションを追加
                            section_content = f"\n\nれいむ: では、{section_type}についても少し触れておきましょう。\n\nまりさ: はい、お願いします！"
                    else:
                        try:
                            response = self.analyzer.client.messages.create(
                                model=self.analyzer.model,
                                max_tokens=800,
                                temperature=0.7,
                                messages=[{"role": "user", "content": section_prompt}]
                            )
                            section_content = response.content[0].text
                            logger.info(f"セクション{i+1}/{sections_needed}の追加に成功: {len(section_content)}文字")
                        except Exception as e:
                            logger.warning(f"セクション{i+1}追加中にエラー: {e}")
                            section_content = f"\n\nれいむ: では、{section_type}についても少し触れておきましょう。\n\nまりさ: はい、お願いします！"
                    
                    # セクションを追加
                    expanded_script += "\n\n" + section_content
                    
                    # 現在の文字数をチェック
                    logger.info(f"現在の台本の文字数: {len(expanded_script)}/{target_chars}")
                    
                    # 目標文字数に達したら終了
                    if len(expanded_script) >= target_chars:
                        logger.info(f"目標文字数{target_chars}文字に達したため、セクション追加を終了します")
                        break
                        
                except Exception as e:
                    logger.error(f"セクション追加全体でエラー: {e}")
                    # エラーが発生してもループを継続
            
            # 最終的な台本の文字数を確認
            logger.info(f"拡充処理完了: 最終文字数={len(expanded_script)}/{target_chars}")
            
            # 目標文字数に達していない場合は警告を表示
            if len(expanded_script) < target_chars:
                missing = target_chars - len(expanded_script)
                logger.warning(f"目標文字数に{missing}文字足りていません")
                
                # 最終的な足りない分は簡単な会話で補足
                try:
                    final_supplement = f"""
れいむ: 今回のポイントをもう一度整理しましょう。

まりさ: はい、今日はたくさんの情報があって勉強になりました！

れいむ: {chapter_title}について、重要なのは次の3点です。まず第一に、適切な計画と予算設定。第二に、専門家のアドバイスを受けること。そして第三に、長期的な視点で判断することです。

まりさ: なるほど！確かにその3点は大切ですね。特に長期的な視点は見落としがちかもしれません。

れいむ: その通りです。一時的な流行や感情に流されず、客観的に判断することが成功への鍵となります。

まりさ: とても参考になりました！次の章も楽しみにしています！
"""
                    
                    # 必要な分だけ追加（目標文字数を超えないように）
                    if len(expanded_script) + len(final_supplement) <= target_chars + 100:
                        expanded_script += "\n\n" + final_supplement
                        logger.info(f"最終補足を追加: 文字数={len(expanded_script)}")
                except Exception as e:
                    logger.error(f"最終補足の追加でエラー: {e}")
            
            return expanded_script
                
        except Exception as main_error:
            logger.error(f"台本拡充の主要処理でエラー: {main_error}")
            
            # エラー発生時の最終手段として、単純な追加コンテンツで埋める
            try:
                # 内容を変えずに、目標文字数まで標準的な内容を追加
                extra_content = f"""

れいむ: 以上が{chapter_title}についての基本的な説明です。いかがでしたか？

まりさ: とても分かりやすかったです！でも、もう少し詳しく知りたいことがあります。

れいむ: もちろん、どのような点に興味がありますか？

まりさ: 実際にこの知識を活かすための具体的なステップが知りたいです。

れいむ: それは素晴らしい質問ですね。具体的なステップとしては、まず情報収集から始めることをお勧めします。専門書やウェブサイト、セミナーなどを活用しましょう。次に、専門家に相談することも大切です。不動産の場合、信頼できる不動産会社や建築士、ファイナンシャルプランナーなどの意見を聞くことで、より実践的なアドバイスが得られます。

まりさ: なるほど！情報収集と専門家への相談が重要なんですね。

れいむ: はい、そしてもう一つ大切なのは、実際に現地を見ることです。写真やインターネットの情報だけでなく、自分の目で確かめることで、思わぬ発見があるかもしれません。

まりさ: 確かに、百聞は一見にしかずですね！

れいむ: その通りです。また、将来の計画をしっかり立てることも忘れないでください。不動産は長期的な視点が必要です。

まりさ: 具体的にどのような将来計画を考えるべきでしょうか？

れいむ: 例えば、5年後、10年後にどのような状況になっているかを想像してみましょう。家族構成の変化、キャリアの変化、そして地域の発展などを考慮に入れると良いでしょう。

まりさ: なるほど、長期的な視点が大切なんですね。今日はたくさん勉強になりました！

れいむ: お役に立てて嬉しいです。次の章でも、さらに詳しい情報をお伝えしていきますね。

まりさ: 楽しみにしています！
"""
                # 必要な文字数だけを追加
                current_length = len(script_content)
                chars_to_add = min(len(extra_content), target_chars - current_length)
                
                if chars_to_add > 0:
                    result = script_content + extra_content[:chars_to_add]
                    logger.info(f"エラー時のフォールバック: 追加文字数={chars_to_add}")
                    return result
                else:
                    return script_content
            except:
                return script_content
    
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
    def improve_script(self, script_data: Dict[str, str], feedback: str) -> Dict[str, str]:
        """フィードバックに基づいて台本を改善する
        
        Args:
            script_data: 改善する台本データ
            feedback: フィードバック内容
            
        Returns:
            改善された台本
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
                # AI Agentクライアントを使用するかどうか
                if self.analyzer.bedrock_agent_client:
                    
                    logger.info(f"Bedrock AI Agentを使用して台本を改善します: {self.analyzer.bedrock_agent_id}")
                    
                    try:
                        # AI Agentのプロンプトを強化 - 台本の長さと文字数要件を明確化
                        input_text = f"""
あなたは不動産の解説動画「ゆっくり不動産」の台本編集スペシャリストです。以下のフィードバックに基づいて台本を改善してください。

# 台本の長さ【絶対に守るべき最重要要件】
- 台本は{duration_minutes}分の動画用です
- 【絶対条件】目標文字数は{target_chars}文字以上、最大でも{target_chars + 500}文字程度にしてください
- 現在の文字数: {len(script_data['script_content'])}文字
- 返答する台本は【必ず{target_chars}文字以上】にしてください
- これは最も重要な要件です - 台本は必ず指定された文字数以上になるようにしてください
- 文字数が足りない場合は、以下のいずれかの方法で長さを確保してください:
  * コンテナハウスの具体的な事例や統計データを追加（例: 価格、サイズ、耐久年数など）
  * コンテナハウスのメリットとデメリットの詳細な説明（例: 短期間で建設可能、移動できる、断熱性の問題など）
  * 専門用語の詳しい解説（例: 断熱材、防水処理、建築基準法など）
  * 関連する法律や制度の説明（例: 建築確認申請、建ぺい率、固定資産税など）
  * 読者がすぐに実践できる具体的なアドバイス（例: 施工業者の選び方、予算計画の立て方など）

# 台本形式のガイドライン
- 改善台本では必ず「れいむ:」「まりさ:」「ナレーション:」のようにキャラクター名を明記してください
- 話者の役割は以下のとおりです:
  * れいむ: 女性キャラ、丁寧な口調で専門的な説明をする役割（例: 「～ですね」「～と言えるでしょう」）
  * まりさ: 女性キャラ、砕けた口調で質問や共感を示す役割（例: 「～だよね」「～なんだ！」）
  * ナレーション: 状況説明や概要を述べる役割（例: 「まずは～について見ていきましょう」）
- ゆっくり実況形式（「〜です」「〜ます」調の丁寧語）を維持してください
- 専門用語は必ず噛み砕いて説明してください（例: 「断熱材とは、熱の移動を防ぐ素材のことです」）
- 重要なポイントには「！」マークを付けて強調してください
- 台詞は自然な会話のように書いてください（質問→回答、問いかけ→返答の形式を意識する）
- フィードバックの内容を必ず反映してください
- 台本の終わりは必ず次の章へつながるような文で締めてください（例: 「次回は～について詳しく説明します」）

# フィードバックスタイルの分析と適用
- フィードバックに「ギャル風」の要望があれば、まりさのセリフを「〜だよね〜」「マジ」「ヤバイ」などのギャル言葉を使って書いてください
- フィードバックに「お笑い」の要望があれば、ボケとツッコミの掛け合いや面白い例え話を追加してください
- フィードバックの要望に合わせて、台本の全体的なトーンを調整してください

# フィードバック内容の反映ポイント
- フィードバック内容を精査し、具体的な修正事項を洗い出してください
- 不足している情報・説明があれば追加してください
- わかりにくい部分があれば、より分かりやすく書き換えてください
- 台本全体のテンポと流れを自然にしてください

# 現在の台本
{script_data['script_content']}

# ユーザーからのフィードバック
{feedback}

【重要】フィードバックを反映した改善版台本を作成し、必ず{target_chars}文字以上の長さを確保してください。新しい台本だけを返し、説明なしで改善された台本全体を出力してください。
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
                        @aws_api_retry(max_retries=2, base_delay=2, jitter=0.5)
                        def call_agent_with_retry():
                            # 最適化されたタイムアウト設定でAgentを呼び出し
                            import botocore
                            client_config = botocore.config.Config(
                                connect_timeout=30,     # 接続タイムアウトを大幅増加（30秒）
                                read_timeout=180,       # 読み取りタイムアウトを大幅増加（180秒）
                                retries={'max_attempts': 5, 'mode': 'adaptive'},  # アダプティブリトライ回数増加
                                max_pool_connections=20, # 接続プール拡大
                                tcp_keepalive=True      # TCP接続をキープアライブ
                            )
                            
                            # 最適化されたクライアント設定で新しいクライアントを作成
                            temp_agent_client = boto3.client(
                                'bedrock-agent-runtime',
                                region_name="us-east-1",
                                config=client_config
                            )
                            
                            # セッションIDに現在時刻とランダムな文字列を追加して一意性を保証
                            import uuid
                            unique_session_id = f"script_improvement_{int(self.analyzer.time_module.time())}_{uuid.uuid4().hex[:8]}"
                            
                            logger.info(f"Agent API呼び出し: セッションID={unique_session_id}, タイムアウト設定=接続{client_config.connect_timeout}秒, 読取{client_config.read_timeout}秒")
                            
                            # keepAliveオプションを有効化してロングランニング接続をサポート
                            return temp_agent_client.invoke_agent(
                                agentId=agent_id,
                                agentAliasId=alias_id,
                                sessionId=unique_session_id,
                                inputText=input_text,
                                enableTrace=True  # トレースを有効化して問題診断を容易に
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
                                    
                                    # completion値やテキストコンテンツを見つける
                                    completion_found = False
                                    content_found = False
                                    extracted_content = None
                                    extracted_completion = None
                                    
                                    # まず、レスポンスのトップレベルで'completion'キーがないか確認
                                    if isinstance(response, dict) and 'completion' in response:
                                        extracted_completion = response['completion']
                                        completion_found = True
                                        logger.info("レスポンスから直接completionを取得")
                                    
                                    for event in events_list:
                                        # イベントの型をログ出力（安全な文字列化で）
                                        logger.info(f"イベント詳細検証: 型={type(event)}, 文字列表現={safe_stringify(event)[:50]}")
                                        
                                        # 辞書として直接アクセス
                                        if isinstance(event, dict):
                                            if 'completion' in event:
                                                extracted_completion = event['completion']
                                                completion_found = True
                                                # EventStream参照問題の根本対策: 安全なstringify関数を使用
                                                logger.info(f"dictイベントからcompletionを取得: {safe_stringify(extracted_completion)[:30]}...")
                                                break
                                                
                                            # chunkデータを探す
                                            elif 'chunk' in event:
                                                try:
                                                    logger.info(f"チャンク情報を検出: {safe_stringify(event['chunk'])[:50]}")
                                                    
                                                    # バイナリデータの可能性
                                                    if hasattr(event['chunk'], 'bytes'):
                                                        chunk_bytes = event['chunk'].bytes
                                                        chunk_text = chunk_bytes.decode('utf-8', errors='replace')
                                                        extracted_content = chunk_text
                                                        content_found = True
                                                        logger.info(f"chunkバイナリデータからコンテンツを取得: {chunk_text[:30]}...")
                                                        break
                                                except Exception as e:
                                                    logger.warning(f"chunkデータ処理エラー: {e}")
                                        
                                        # 属性として確認
                                        if hasattr(event, 'completion'):
                                            extracted_completion = event.completion
                                            completion_found = True
                                            # EventStream参照問題根本対策: 安全な文字列化
                                            logger.info(f"イベント属性からcompletionを取得: {safe_stringify(extracted_completion)[:30]}...")
                                            break
                                        
                                        # __dict__を使って確認
                                        if hasattr(event, '__dict__'):
                                            event_dict = event.__dict__
                                            logger.info(f"イベント__dict__のキー: {list(event_dict.keys())}")
                                            if 'completion' in event_dict:
                                                extracted_completion = event_dict['completion']
                                                completion_found = True
                                                # EventStream参照問題根本対策: 安全な文字列化
                                                logger.info(f"イベント__dict__からcompletionを取得: {safe_stringify(extracted_completion)[:30]}...")
                                                break
                                    
                                    # 最初にcompletionを使用
                                    if completion_found:
                                        response = {'completion': extracted_completion}
                                        logger.info(f"完了テキストの抽出に成功: {len(extracted_completion) if isinstance(extracted_completion, str) else 'N/A'}文字")
                                    # 次にコンテンツを使用
                                    elif content_found:
                                        response = {'completion': extracted_content}
                                        logger.info(f"コンテンツの抽出に成功: {len(extracted_content) if isinstance(extracted_content, str) else 'N/A'}文字")
                                    else:
                                        logger.warning("EventStreamからテキストコンテンツを抽出できませんでした")
                                    
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
                                # ★★★ 根本対策: EventStreamを含む場合、安全な方法で処理 ★★★
                                completion_value = response['completion']
                                
                                # 安全な文字列化でレスポンスを記録（直接str()呼び出しを避ける）
                                # EventStreamオブジェクトの直接stringifyによる混入を防止
                                try:
                                    safe_response = {}
                                    for k, v in response.items():
                                        if k == 'completion' and not isinstance(v, str):
                                            safe_response[k] = safe_stringify(v)
                                        else:
                                            safe_response[k] = v
                                    response_repr = str(safe_response)[:100]  # さらに短く制限
                                    logger.info(f"レスポンス文字列表現(安全版): {response_repr}")
                                except Exception as format_err:
                                    logger.warning(f"レスポンス安全文字列化エラー: {format_err}")
                                    # 最低限の情報だけ記録
                                    logger.info("レスポンス文字列表現: [安全に表示できない内容]")
                                
                                # EventStreamの最適化処理
                                import botocore
                                if isinstance(completion_value, botocore.eventstream.EventStream):
                                    logger.info("EventStreamを検出: 最適化処理を開始")
                                    
                                    # EventStreamの内容をテキストとして処理
                                    # 必要なモジュールを先にインポート
                                    import json
                                    import re
                                    import time
                                    
                                    event_texts = []
                                    content_events = []  # 実際のコンテンツを含むイベントのみ保存
                                    try:
                                        # タイムアウトを避けるためにイベントを効率的に処理
                                        from concurrent.futures import ThreadPoolExecutor
                                        import queue
                                        
                                        # イベント処理のためのキュー
                                        event_queue = queue.Queue()
                                        result_complete = False
                                        found_content = False  # コンテンツを見つけたかどうかのフラグ
                                        completion_text = None  # 完成したテキストを保持する変数
                                        # EventStream処理を効率化するフラグを追加
                                        seen_trace_events = 0   # トレースイベントの数を追跡
                                        content_bytes_count = 0 # 有効なコンテンツバイト数をカウント
                                        
                                        def event_collector(stream):
                                            """バックグラウンドでイベントを収集してキューに追加"""
                                            try:
                                                nonlocal result_complete, seen_trace_events, content_bytes_count
                                                
                                                # イベントの重複チェック用セット
                                                seen_events = set()
                                                
                                                for event in stream:
                                                    if result_complete:
                                                        break
                                                        
                                                    # トレースイベントの最適化（大量のトレースイベントがある場合は制限）
                                                    if hasattr(event, 'trace'):
                                                        seen_trace_events += 1
                                                        # トレースイベントが多すぎる場合はスキップ（10件まで）
                                                        if seen_trace_events > 10:
                                                            continue
                                                    
                                                    # チャンクデータの場合はサイズをチェック
                                                    if hasattr(event, 'chunk') and hasattr(event.chunk, 'bytes'):
                                                        chunk_size = len(event.chunk.bytes)
                                                        content_bytes_count += chunk_size
                                                        
                                                        # チャンクデータのハッシュを生成して重複チェック
                                                        import hashlib
                                                        event_hash = hashlib.md5(event.chunk.bytes).hexdigest()
                                                        if event_hash in seen_events:
                                                            logger.info(f"重複イベント検出: ハッシュ {event_hash[:8]}...")
                                                            continue
                                                        seen_events.add(event_hash)
                                                        
                                                    event_queue.put(event)
                                                
                                                # 終了マーカー
                                                event_queue.put(None)
                                                logger.info(f"イベントストリーム読み込み完了: トレース={seen_trace_events}件, コンテンツ={content_bytes_count}バイト")
                                            except Exception as e:
                                                logger.error(f"イベント収集エラー: {e}")
                                                event_queue.put(None)
                                        
                                        # 別スレッドでイベント収集を開始
                                        with ThreadPoolExecutor(max_workers=1) as executor:
                                            collector_future = executor.submit(event_collector, completion_value)
                                            
                                            # メインスレッドでイベントを処理
                                            timeout_sec = 60  # 最大待機時間を60秒に増加
                                            start_time = time.time()
                                            
                                            # 進捗ログ用のカウンタ
                                            processed_events = 0
                                            valid_content_events = 0
                                            total_content_length = 0
                                            
                                            # イベント処理のメインループ
                                            while time.time() - start_time < timeout_sec:
                                                try:
                                                    # キューからイベントを取得（1秒のタイムアウト）
                                                    try:
                                                        event = event_queue.get(timeout=1)
                                                    except queue.Empty:
                                                        # ただの待機中なので何もしない
                                                        continue
                                                    
                                                    # 終了マーカーを検出
                                                    if event is None:
                                                        logger.info(f"イベントストリーム処理完了: 処理済み={processed_events}件, 有効={valid_content_events}件")
                                                        break
                                                    
                                                    # 処理イベントをカウント
                                                    processed_events += 1
                                                    
                                                    # 処理が冗長にならないよう、10件ごとにログ出力
                                                    if processed_events == 1 or processed_events % 10 == 0:
                                                        logger.info(f"イベント処理中: {processed_events}件目, 有効コンテンツ={valid_content_events}件, 合計{total_content_length}文字")
                                                    
                                                    # イベントからテキストを抽出する様々な方法を試行
                                                    text_extracted = False
                                                    
                                                    # 方法1: completionプロパティ
                                                    if hasattr(event, 'completion'):
                                                        completion_content = event.completion
                                                        # 有効なテキストデータかを検証
                                                        if isinstance(completion_content, str) and len(completion_content.strip()) > 0:
                                                            event_texts.append(completion_content)
                                                            valid_content_events += 1
                                                            total_content_length += len(completion_content)
                                                            logger.info(f"completionプロパティから抽出: {completion_content[:30] if len(completion_content) > 30 else completion_content}...")
                                                            text_extracted = True
                                                    
                                                    # 方法2: textプロパティ
                                                    elif hasattr(event, 'text'):
                                                        text_content = event.text
                                                        if isinstance(text_content, str) and len(text_content.strip()) > 0:
                                                            event_texts.append(text_content)
                                                            valid_content_events += 1
                                                            total_content_length += len(text_content)
                                                            logger.info(f"textプロパティから抽出: {text_content[:30] if len(text_content) > 30 else text_content}...")
                                                            text_extracted = True
                                                        
                                                    # 方法3: chunkプロパティ（バイナリデータ） - 最重要な方法
                                                    elif hasattr(event, 'chunk') and hasattr(event.chunk, 'bytes'):
                                                        try:
                                                            chunk_bytes = event.chunk.bytes
                                                            chunk_text = chunk_bytes.decode('utf-8', errors='replace')
                                                            
                                                            # ★★★ 根本的な原因修正: EventStreamの直接参照を事前チェック ★★★
                                                            # 文字列をバッファに格納する前に徹底的な浄化を実施
                                                            if chunk_text.strip():
                                                                # EventStreamオブジェクトや他のPythonオブジェクト参照をチェック
                                                                contains_object_ref = any(marker in chunk_text for marker in 
                                                                    ['<botocore', 'EventStream', '<boto', 'object at 0x', 'at 0x'])
                                                                
                                                                if contains_object_ref:
                                                                    # 参照が含まれる場合はPython処理前にサニタイズ
                                                                    logger.warning("EventStreamチャンクにPythonオブジェクト参照を検出。事前サニタイズを実施")
                                                                    
                                                                    # 行単位で処理（最も確実な方法）
                                                                    cleaned_lines = []
                                                                    for line in chunk_text.split('\n'):
                                                                        # 問題がある行は完全に除去
                                                                        if any(marker in line for marker in 
                                                                              ['<botocore', 'EventStream', '<boto', 'object at 0x', 'at 0x']):
                                                                            logger.warning(f"事前チェック: 問題行を除去「{line[:30]}...」")
                                                                            continue
                                                                            
                                                                        # キャラクター発言行の特別チェック
                                                                        if any(char in line for char in ['れいむ:', 'まりさ:', 'ナレーション:']):
                                                                            if any(ref in line for ref in ['<', '>', 'object', 'EventStream']):
                                                                                logger.warning(f"事前チェック: 問題のあるキャラクター行を除去「{line[:30]}...」")
                                                                                continue
                                                                        
                                                                        # 安全な行のみを保持
                                                                        cleaned_lines.append(line)
                                                                    
                                                                    # 浄化済みのテキストを使用
                                                                    chunk_text = '\n'.join(cleaned_lines)
                                                                    logger.info(f"事前サニタイズ完了: イベントチャンクを安全に処理")
                                                                
                                                                # 安全になったテキストのみをバッファに追加
                                                                if chunk_text.strip():
                                                                    event_texts.append(chunk_text)
                                                                    content_events.append(chunk_text)  # 実際のコンテンツとして保存
                                                                    valid_content_events += 1
                                                                    total_content_length += len(chunk_text)
                                                                    logger.info(f"バイナリchunkから抽出: {chunk_text[:30] if len(chunk_text) > 30 else chunk_text}...")
                                                                    text_extracted = True
                                                                    found_content = True  # コンテンツフラグを設定
                                                        except Exception as decode_err:
                                                            logger.warning(f"バイナリデータのデコードに失敗: {decode_err}")
                                                    
                                                    # 方法4: 辞書型のイベント
                                                    elif isinstance(event, dict):
                                                        keys = list(event.keys())
                                                        logger.info(f"辞書イベントのキー: {keys}")
                                                        
                                                        if 'completion' in event:
                                                            event_texts.append(event['completion'])
                                                            logger.info(f"辞書からcompletion抽出: {event['completion'][:30] if len(event['completion']) > 30 else event['completion']}...")
                                                            text_extracted = True
                                                        elif 'text' in event:
                                                            event_texts.append(event['text'])
                                                            logger.info(f"辞書からtext抽出: {event['text'][:30] if len(event['text']) > 30 else event['text']}...")
                                                            text_extracted = True
                                                    
                                                    # 最後の手段: 文字列表現
                                                    # chunk.bytesが直接利用できるかチェック - 最も信頼性の高い方法
                                                    if not text_extracted and hasattr(event, 'chunk') and hasattr(event.chunk, 'bytes'):
                                                        try:
                                                            chunk_bytes = event.chunk.bytes
                                                            chunk_text = chunk_bytes.decode('utf-8', errors='replace')
                                                            event_texts.append(chunk_text)
                                                            logger.info(f"chunk.bytesから直接抽出: {chunk_text[:30] if len(chunk_text) > 30 else chunk_text}...")
                                                            # 実際のコンテンツを別途保存
                                                            content_events.append(chunk_text)
                                                            text_extracted = True
                                                            found_content = True  # 実際のコンテンツを見つけた
                                                            # 完全なテキストを取得できた場合
                                                            if len(chunk_text) > 100:  # 一定以上の長さなら有効な応答と見なす
                                                                completion_text = chunk_text
                                                        except Exception as decode_err:
                                                            logger.warning(f"バイト列のデコードエラー: {decode_err}")
                                                    
                                                    # 文字列表現 - 最後の手段
                                                    if not text_extracted:
                                                        event_str = str(event)
                                                        if event_str and event_str != "None" and len(event_str) > 5:
                                                            # chunk.bytesを含む場合は特別に処理
                                                            if "'chunk': {'bytes': b'" in event_str:
                                                                try:
                                                                    import re
                                                                    bytes_match = re.search(r"b'(.*?)'", event_str)
                                                                    if bytes_match:
                                                                        byte_str = bytes_match.group(1).encode('latin-1').decode('unicode_escape').encode('latin-1')
                                                                        decoded_text = byte_str.decode('utf-8', errors='replace')
                                                                        event_texts.append(decoded_text)
                                                                        logger.info(f"バイナリチャンクから抽出: {decoded_text[:30] if len(decoded_text) > 30 else decoded_text}...")
                                                                        # 実際のコンテンツを別途保存
                                                                        content_events.append(decoded_text)
                                                                        found_content = True  # 実際のコンテンツを見つけた
                                                                        # 完全なテキストを取得できた場合
                                                                        if len(decoded_text) > 100:  # 一定以上の長さなら有効な応答と見なす
                                                                            completion_text = decoded_text
                                                                    else:
                                                                        event_texts.append(event_str)
                                                                        logger.info(f"文字列表現を使用: {event_str[:30]}...")
                                                                except Exception as e:
                                                                    logger.error(f"バイナリデータ処理エラー: {e}")
                                                                    event_texts.append(event_str)
                                                                    logger.info(f"文字列表現を使用: {event_str[:30]}...")
                                                            else:
                                                                # トレース情報は無視
                                                                if "'trace':" not in event_str:
                                                                    event_texts.append(event_str)
                                                                    logger.info(f"文字列表現を使用: {event_str[:30]}...")
                                                except Exception as e:
                                                    logger.error(f"イベント処理エラー: {e}")
                                            
                                            # タイムアウトで強制終了
                                            if time.time() - start_time >= timeout_sec:
                                                result_complete = True
                                                logger.warning(f"EventStream処理がタイムアウト({timeout_sec}秒)のため強制終了")
                                                # タイムアウト時点でもレスポンスに'completion'キーがあれば抽出
                                                if isinstance(response, dict) and 'completion' in response and isinstance(response['completion'], str):
                                                    completion_text = response['completion']
                                                    found_content = True
                                                    logger.info(f"タイムアウト時点でレスポンスから直接completionを取得: {len(completion_text)}文字")
                                        
                                        # 結合してスクリプトを作成
                                        if event_texts:
                                            # completion_textが直接取得できている場合、それを優先的に使用
                                            if completion_text:
                                                improved_script = completion_text
                                                logger.info(f"直接取得したcompletion_textを使用します: {len(completion_text)}文字")
                                            # content_eventsから有効なコンテンツを抽出
                                            elif content_events:
                                                # コンテンツが複数ある場合は結合
                                                if len(content_events) > 1:
                                                    improved_script = "".join(content_events)
                                                    logger.info(f"{len(content_events)}個のコンテンツイベントを結合: {len(improved_script)}文字")
                                                else:
                                                    improved_script = content_events[0]
                                                    logger.info(f"単一のコンテンツイベントを使用: {len(improved_script)}文字")
                                            # found_contentフラグで実際のコンテンツが見つかったかを確認
                                            elif found_content:
                                                # コンテンツフラグが立っていれば、有効なコンテンツのみを抽出
                                                content_texts = []
                                                for text in event_texts:
                                                    if isinstance(text, str) and text.strip():
                                                        # トレース情報と辞書文字列表現を除外
                                                        if not text.startswith("{'trace':") and not "chunk" in text and not "dict_keys" in text:
                                                            content_texts.append(text)
                                                
                                                if content_texts:
                                                    improved_script = "\n".join(content_texts)
                                                else:
                                                    # 直接抽出したchunk.bytesデータがある場合
                                                    for text in event_texts:
                                                        if isinstance(text, str) and not text.startswith("{'"):
                                                            improved_script = text
                                                            break
                                                    else:
                                                        improved_script = "コンテンツデータの抽出に失敗しました。"
                                            else:
                                                # コンテンツが見つからなければフォールバック処理
                                                # JSON形式の応答があれば、そこからcompletionキーを探す
                                                import json  # 明示的に再インポート
                                                import re    # 正規表現用に再インポート
                                                
                                                for text in event_texts:
                                                    if isinstance(text, str) and "completion" in text:
                                                        try:
                                                            import ast  # 文字列から辞書への変換用
                                                            # 文字列をディクショナリに変換して抽出を試みる
                                                            try:
                                                                data = ast.literal_eval(text)
                                                                if isinstance(data, dict) and 'completion' in data:
                                                                    improved_script = data['completion']
                                                                    break
                                                            except:
                                                                # JSON形式の場合
                                                                match = re.search(r'"completion"\s*:\s*"([^"]+)"', text)
                                                                if match:
                                                                    improved_script = match.group(1)
                                                                    break
                                                        except Exception as e:
                                                            logger.error(f"JSON解析エラー: {e}")
                                                else:
                                                    # テキストの中からコード・スクリプトらしき部分だけを抽出
                                                    non_debug_texts = []
                                                    for text in event_texts:
                                                        if isinstance(text, str) and text.strip():
                                                            # トレース情報と辞書の文字列表現を除外
                                                            if not text.startswith("{'trace':") and \
                                                               not text.startswith("{'chunk':") and \
                                                               not text.startswith("{'"):
                                                                non_debug_texts.append(text)
                                                    
                                                    if non_debug_texts:
                                                        improved_script = "\n".join(non_debug_texts)
                                                    else:
                                                        # デフォルトスクリプトを作成し、フォールバックをトリガー
                                                        logger.warning("EventStreamから有効なデータを抽出できません。フォールバックします。")
                                                        raise ValueError("EventStreamからコンテンツを抽出できませんでした")
                                            
                                            logger.info(f"EventStreamから改善台本を取得: {len(improved_script)}文字, サンプル: {improved_script[:100]}...")
                                            
                                            # EventStreamオブジェクト文字列を検出して除去（強化版）
                                            if '<botocore' in improved_script or 'EventStream' in improved_script or 'object at 0x' in improved_script or ('<' in improved_script and '>' in improved_script and '0x' in improved_script):
                                                logger.warning("スクリプト中にPythonオブジェクト参照が検出されました。徹底的なクリーニングを実行します")
                                                import re
                                                
                                                # 1. 行単位での厳格なフィルタリング
                                                lines = improved_script.split('\n')
                                                clean_lines = []
                                                removed_lines = 0
                                                
                                                for line in lines:
                                                    # 1-1. 明確に問題のある行を完全に除外
                                                    if ('EventStream' in line or 
                                                        'botocore' in line or 
                                                        'object at 0x' in line or
                                                        ('<' in line and '>' in line and '0x' in line)):
                                                        removed_lines += 1
                                                        logger.warning(f"問題のある行を削除: {line[:50]}...")
                                                        continue
                                                        
                                                    # 1-2. キャラクター発言行の特別処理
                                                    if any(char_prefix in line for char_prefix in ['れいむ:', 'まりさ:', 'ナレーション:']):
                                                        # キャラクター発言内に問題があればその行を除外
                                                        if any(obj_ref in line for obj_ref in ['<', '>', 'object', 'EventStream', 'botocore']):
                                                            removed_lines += 1
                                                            logger.warning(f"問題のあるキャラクター発言行を削除: {line[:50]}...")
                                                            continue
                                                    
                                                    # クリーンな行だけを保持
                                                    clean_lines.append(line)
                                                
                                                # 2. 正規表現による徹底的なクリーニング
                                                cleaned_script = '\n'.join(clean_lines)
                                                patterns = [
                                                    # Pythonオブジェクト参照の一般的なパターン
                                                    r'<[^>]*?at 0x[0-9a-f]+[^>]*?>',
                                                    r'<[^>]*?object[^>]*?>',
                                                    r'<[^>]*?botocore[^>]*?>',
                                                    r'<[^>]*?EventStream[^>]*?>',
                                                    
                                                    # キャラクター発言内の参照（行全体のパターン）
                                                    r'れいむ:.*?<.*?object.*?>.*(\n|$)',
                                                    r'まりさ:.*?<.*?object.*?>.*(\n|$)',
                                                    r'ナレーション:.*?<.*?object.*?>.*(\n|$)',
                                                    r'れいむ:.*?<.*?EventStream.*?>.*(\n|$)',
                                                    r'まりさ:.*?<.*?EventStream.*?>.*(\n|$)',
                                                    r'ナレーション:.*?<.*?EventStream.*?>.*(\n|$)',
                                                    r'れいむ:.*?<.*?at 0x[0-9a-f]+.*?>.*(\n|$)',
                                                    r'まりさ:.*?<.*?at 0x[0-9a-f]+.*?>.*(\n|$)',
                                                    r'ナレーション:.*?<.*?at 0x[0-9a-f]+.*?>.*(\n|$)',
                                                    
                                                    # 残存している可能性のある参照
                                                    r'<.*?EventStream.*?>',
                                                    r'<.*?object at 0x[0-9a-f]+.*?>',
                                                    r'<.*?at 0x[0-9a-f]+.*?>'
                                                ]
                                                
                                                # パターン適用して徹底的に浄化
                                                for pattern in patterns:
                                                    prev_len = len(cleaned_script)
                                                    cleaned_script = re.sub(pattern, '', cleaned_script)
                                                    if len(cleaned_script) != prev_len:
                                                        logger.info(f"パターン '{pattern}' で {prev_len - len(cleaned_script)} 文字を削除")
                                                
                                                # 3. 追加の検証と最終クリーニング
                                                if 'EventStream' in cleaned_script or 'botocore' in cleaned_script or 'object at 0x' in cleaned_script:
                                                    logger.warning("最初のクリーニング後も問題が残っています。最終フィルタリングを適用")
                                                    
                                                    # 確実に問題を解決するための再フィルタリング
                                                    lines = cleaned_script.split('\n')
                                                    final_lines = []
                                                    extra_removed = 0
                                                    
                                                    for line in lines:
                                                        # 問題のあるキーワードを含む行は完全に除外
                                                        if not any(kw in line for kw in ['EventStream', 'botocore', 'object at 0x', '<', '>']):
                                                            final_lines.append(line)
                                                        else:
                                                            extra_removed += 1
                                                    
                                                    cleaned_script = '\n'.join(final_lines)
                                                    logger.warning(f"最終フィルタリングで追加 {extra_removed} 行を除去")
                                                
                                                # 結果を返す
                                                improved_script = cleaned_script
                                                logger.info(f"Pythonオブジェクト参照の徹底クリーニング完了: 合計 {removed_lines} 行を除去、最終テキスト長 {len(improved_script)} 文字")
                                        else:
                                            logger.warning("EventStreamから有効なテキストを取得できませんでした")
                                            raise ValueError("EventStream processing failed to extract text")
                                    except Exception as es_err:
                                        logger.error(f"EventStream処理エラー: {es_err}")
                                        logger.exception("詳細:")
                                        raise ValueError(f"EventStream processing error: {es_err}")
                                        
                                    # 処理に成功したらそのまま続行
                                        
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
                            
                            # 強化された通常のBedrock基盤モデルにフォールバック
                            logger.info("強化された通常のBedrock基盤モデルにフォールバックします")
                            
                            # 必要なモジュールを明示的に再インポート
                            import json
                            import botocore
                            import time
                            
                            # フィードバックスタイルの解析（ギャル風かお笑い風か）
                            style_hint = ""
                            if 'feedback' in script_data and isinstance(script_data['feedback'], list):
                                for fb in script_data['feedback']:
                                    if 'ギャル' in fb:
                                        style_hint = "ギャル風の口調（「～だよね～」「マジ」「ヤバイ」などの言葉を使う）で"
                                        break
                                    if 'お笑い' in fb:
                                        style_hint = "お笑い風（ボケとツッコミの掛け合い、面白い例え話を含める）で"
                                        break
                            
                            # 動画時間を正確に取得し、目標文字数を明確に指定
                            duration_minutes = script_data.get('duration_minutes', 3)
                            logger.info(f"台本改善の正しい動画時間設定: {duration_minutes}分")
                            target_chars = self.calculate_expected_length(duration_minutes)
                            
                            # 強化されたプロンプト（タイムアウトを避けるため1回で十分な長さを生成）
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
                            
                            # タイムアウト設定を追加
                            import botocore
                            client_config = botocore.config.Config(
                                connect_timeout=30,    # 接続タイムアウト大幅増加
                                read_timeout=180,      # 読み取りタイムアウト大幅増加
                                retries={'max_attempts': 5, 'mode': 'adaptive'},  # アダプティブリトライ回数増加
                                max_pool_connections=20, # 接続プール拡大
                                tcp_keepalive=True     # TCP接続をキープアライブ
                            )
                            
                            # 最適化された設定で一時クライアントを作成
                            temp_client = boto3.client(
                                'bedrock-runtime', 
                                region_name=self.analyzer.bedrock_runtime._client_config.region_name,
                                config=client_config
                            )
                            
                            # 強化されたプロンプトで呼び出し
                            try:
                                response = temp_client.invoke_model(
                                    modelId=self.analyzer.model,
                                    body=json.dumps({
                                        "anthropic_version": "bedrock-2023-05-31",
                                        "max_tokens": 5000,  # 大幅に増加
                                        "temperature": 0.7,  # より創造的な出力を促す
                                        "messages": [
                                            {"role": "user", "content": enhanced_prompt}
                                        ]
                                    })
                                )
                                
                                # レスポンスの解析
                                response_body = json.loads(response.get('body').read())
                                improved_script = response_body['content'][0]['text']
                                logger.info(f"フォールバック: Bedrock基盤モデルを使用して台本改善が完了（文字数: {len(improved_script)}）")
                                
                                # 文字数が目標に達していない場合は警告
                                if len(improved_script) < target_chars:
                                    logger.warning(f"改善台本が目標文字数に達していません: {len(improved_script)}/{target_chars}文字")
                            except Exception as e:
                                logger.error(f"基盤モデル呼び出し時にエラー: {str(e)}")
                                # 元のクライアントでシンプルな呼び出しを試す
                                # 必要なモジュールを再インポート
                                import json
                                
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
                                
                                # レスポンスの解析
                                response_body = json.loads(response.get('body').read())
                                improved_script = response_body['content'][0]['text']
                                logger.info(f"フォールバック（シンプル）: 基盤モデルによる台本改善が完了（文字数: {len(improved_script)}）")
                        
                        if not improved_script:
                            logger.warning("Bedrock AI Agentからの応答が空です。通常のモデル呼び出しに切り替えます。")
                            # レスポンスからcompletionキーを直接取得する最終手段
                            if isinstance(response, dict) and 'completion' in response and isinstance(response['completion'], str):
                                improved_script = response['completion']
                                if len(improved_script) > 100:  # ある程度の長さがあるか確認
                                    logger.info(f"レスポンスから直接completionを検出: {len(improved_script)}文字")
                            # それでも空であればフォールバック
                            if not improved_script:
                                # 強化されたBedrock基盤モデル呼び出しにフォールバック
                                raise ValueError("Empty response from AI Agent")
                        
                        logger.info(f"Bedrock AI Agentを使用して台本「{script_data['chapter_title']}」の改善が完了")
                    except Exception as agent_error:
                        logger.error(f"Bedrock AI Agent呼び出しエラー: {str(agent_error)}")
                        # 強化されたBedrock基盤モデル呼び出しにフォールバック
                        raise ValueError(f"AI Agent error: {str(agent_error)}")
                        
                    # AIエージェントから受け取ったテキスト結果が文字列の場合の処理
                    if isinstance(improved_script, str) and improved_script:
                        # スクリプトを精査して余分な情報を除去
                        cleaned_script = improved_script
                        
                        # EventStreamオブジェクト文字列を検出して除去（根本的な解決策）
                        if '<botocore' in improved_script or 'EventStream' in improved_script or 'object at 0x' in improved_script or (('<' in improved_script and '>' in improved_script)):
                            logger.warning("最終出力段階でPythonオブジェクト参照が検出されました。徹底的なサニタイズを実行します")
                            import re
                            
                            # 1. 行単位での厳格なフィルタリング（最も効果的なアプローチ）
                            lines = improved_script.split('\n')
                            clean_lines = []
                            removed_lines = 0
                            
                            for line in lines:
                                # 1-1. 明確に問題のある行を完全に除外（厳格な基準）
                                if ('EventStream' in line or 
                                    'botocore' in line or 
                                    'object at 0x' in line or
                                    'at 0x' in line or
                                    ('<' in line and '>' in line and ('0x' in line or 'object' in line or 'EventStream' in line))):
                                    removed_lines += 1
                                    logger.warning(f"問題のある行を完全に削除: {line[:50]}...")
                                    continue
                                    
                                # 1-2. キャラクター発言行の特別処理（最も重要）
                                if any(character in line for character in ['れいむ:', 'まりさ:', 'ナレーション:']):
                                    # 疑わしいキャラクタが含まれる発言行は削除
                                    if any(obj_marker in line for obj_marker in ['<', '>', 'object', 'EventStream', 'botocore', 'at 0x']):
                                        removed_lines += 1
                                        logger.warning(f"問題のあるキャラクター発言行を削除: {line[:50]}...")
                                        continue
                                
                                # 安全な行のみを保持
                                clean_lines.append(line)
                            
                            cleaned_script = '\n'.join(clean_lines)
                            
                            # 2. 正規表現による二次フィルタリング（念のため）
                            patterns = [
                                # Pythonオブジェクト参照の一般的なパターン
                                r'<[^>]*?at 0x[0-9a-f]+[^>]*?>',
                                r'<[^>]*?object[^>]*?>',
                                r'<[^>]*?botocore[^>]*?>',
                                r'<[^>]*?EventStream[^>]*?>',
                                r'<[^>]*?0x[0-9a-f]+[^>]*?>',
                                
                                # キャラクター発言内の参照（特に重要）
                                r'れいむ:.*?<.*?object.*?>.*(\n|$)',
                                r'まりさ:.*?<.*?object.*?>.*(\n|$)',
                                r'ナレーション:.*?<.*?object.*?>.*(\n|$)',
                                r'れいむ:.*?<.*?EventStream.*?>.*(\n|$)',
                                r'まりさ:.*?<.*?EventStream.*?>.*(\n|$)',
                                r'ナレーション:.*?<.*?EventStream.*?>.*(\n|$)',
                                r'れいむ:.*?<.*?at 0x[0-9a-f]+.*?>.*(\n|$)',
                                r'まりさ:.*?<.*?at 0x[0-9a-f]+.*?>.*(\n|$)',
                                r'ナレーション:.*?<.*?at 0x[0-9a-f]+.*?>.*(\n|$)',
                                
                                # その他の問題となるパターン
                                r'<.*?EventStream.*?>',
                                r'<.*?object at 0x[0-9a-f]+.*?>',
                                r'<.*?at 0x[0-9a-f]+.*?>'
                            ]
                            
                            # パターン適用して追加クリーニング
                            for pattern in patterns:
                                prev_len = len(cleaned_script)
                                cleaned_script = re.sub(pattern, '', cleaned_script)
                                if len(cleaned_script) != prev_len:
                                    logger.info(f"パターン '{pattern}' で追加 {prev_len - len(cleaned_script)} 文字を削除")
                            
                            # 3. 最終確認 - 残っている問題がないか三次チェック
                            if any(marker in cleaned_script for marker in ['EventStream', 'botocore', 'object at 0x', 'at 0x']):
                                logger.warning("サニタイズ後もオブジェクト参照が残っています。最終クリーニングを実行")
                                
                                # 最後の手段として厳格なルールで行フィルタリング
                                lines = cleaned_script.split('\n')
                                final_lines = []
                                
                                for line in lines:
                                    # 問題の可能性のあるキーワードが含まれる行を完全に除去
                                    if not any(kw in line for kw in ['EventStream', 'botocore', 'object at', 'at 0x', '<', '>']):
                                        final_lines.append(line)
                                
                                cleaned_script = '\n'.join(final_lines)
                                logger.warning(f"最終クリーニング後の長さ: {len(cleaned_script)}文字")
                            
                            logger.info(f"徹底的なサニタイズ処理完了: {removed_lines}行を削除、最終長さ: {len(cleaned_script)}文字")
                            # 処理済みスクリプトを設定
                            improved_script = cleaned_script
                        
                        # JSONやトレース情報が含まれているかチェック
                        if '{' in improved_script and '}' in improved_script and ('trace' in improved_script or 'completion' in improved_script):
                            try:
                                import re
                                # JSONから直接スクリプトを取り出す試み
                                completion_match = re.search(r'"completion"\s*:\s*"(.*?)"', improved_script, re.DOTALL)
                                if completion_match:
                                    extracted_text = completion_match.group(1)
                                    if len(extracted_text) > 100:  # 有効なコンテンツか確認
                                        cleaned_script = extracted_text
                                        logger.info(f"JSONから直接completionを抽出: {len(cleaned_script)}文字")
                            except Exception as e:
                                logger.warning(f"JSONからの抽出に失敗: {e}")
                        
                        actual_chars = len(cleaned_script)
                        # スクリプトデータから直接動画時間を取得する（より正確）
                        duration_minutes = script_data.get('duration_minutes', 3)
                        target_chars = self.calculate_expected_length(duration_minutes)
                        logger.info(f"AIエージェントから文字列として受け取った改善台本を処理します（長さ: {actual_chars}文字、動画時間: {duration_minutes}分、目標: {target_chars}文字）")
                        
                        # 文字数チェック - 目標文字数に達していない場合は2回目のAI Agent呼び出し
                        if actual_chars < target_chars:
                            logger.info(f"文字数不足のため2回目のAI Agent処理を開始: 現在={actual_chars}, 目標={target_chars}, 不足={target_chars - actual_chars}文字")
                            try:
                                # セッションIDを新しく生成
                                import uuid
                                unique_session_id = f"script_improvement_second_{int(self.analyzer.time_module.time())}_{uuid.uuid4().hex[:8]}"
                                
                                # 文字数不足に特化した強化プロンプト
                                second_input_text = f"""
あなたは不動産の解説動画「ゆっくり不動産」の台本編集スペシャリストです。
以下の台本の文字数が不足しているため、内容を拡充して文字数を増やしてください。

# 【最優先要件】：文字数を必ず増やす
- 現在の台本は{actual_chars}文字ですが、{duration_minutes}分の動画には最低でも{target_chars}文字必要です
- 【絶対条件】現在の台本から{target_chars - actual_chars}文字以上追加してください
- 台本全体で合計{target_chars}文字以上にすることが最優先課題です
- 現在の台本をベースにして、必要な文字数になるまで自然に拡充してください
- 不足している文字数を確実に補うため、{target_chars - actual_chars + 100}文字程度を追加してください（多めに追加）
- 必ず台本全体を返してください（追加部分だけでなく、既存部分も含めた完全な台本）
- この台本は{duration_minutes}分の動画用です（これは重要な情報です）

# 拡充すべき内容（以下から2-3項目を選んで詳しく追加してください）
1. コンテナハウスの具体的な事例と施工例（実際の価格、サイズ、完成までの期間、施工事例の写真など）
2. コンテナハウスの技術的詳細（断熱性能の具体的数値、耐震性の具体的数値、断熱材の種類と特徴など）
3. コンテナハウスの価格帯と予算計画（基礎工事、内装、設備などの具体的な費用内訳）
4. 法規制や建築基準の解説（建築確認申請の要否、固定/不動産としての扱いの違い、税金面の特徴など）
5. DIYコンテナハウス実践アドバイス（初心者でも始められるDIYのステップ、必要な工具リスト、業者選びのポイントなど）
6. コンテナハウスのメリットとデメリット（特に長期的な視点での比較、一般住宅との具体的な比較データなど）

# キャラクター設定と台本形式
- 「れいむ」と「まりさ」の対話形式を維持してください
- れいむ：丁寧で教師的な口調の女性キャラクター（例：「～ですね」「～と言えるでしょう」など）
- まりさ：ギャル口調で好奇心旺盛な女性キャラクター（例：「～だよね～」「マジやばくない？」「そうなんだ～！」など）
- 両キャラクターの掛け合いが自然に見える会話展開にしてください
- 「れいむ:」「まりさ:」のようにキャラクター名の後にコロンを入れてください

# 拡充方法の具体例
- 既存の台本の最後に自然につながるように追加するのがベスト
- 例：「れいむ: では次に、コンテナハウスの断熱性能について詳しく見ていきましょう。」など
- まりさから質問を投げかけて、れいむが詳しく回答するパターンが効果的
- 例：「まりさ: コンテナハウスって夏は暑くならないの？」「れいむ: 断熱材選びが重要になります。具体的には...」
- 具体的な数字や図解的な説明を含めると分かりやすい
- 例：「れいむ: 一般的なコンテナハウスの断熱性能は、熱貫流率で表すとU値=○○W/m2K程度です」

# 既存の台本
{cleaned_script}

# フィードバック内容
{feedback if isinstance(feedback, str) else ''}

【重要】以上の条件を踏まえて、必ず{target_chars}文字以上（目標は{target_chars + 100}文字程度）の拡充した完全な台本を作成してください。台本全体を返し、解説や前置き/後書きなどは一切含めないでください。
"""
                                
                                # タイムアウト設定を最適化したクライアント
                                import botocore
                                client_config = botocore.config.Config(
                                    connect_timeout=15,     # 接続タイムアウト
                                    read_timeout=60,        # 読み取りタイムアウト
                                    retries={'max_attempts': 3, 'mode': 'adaptive'},
                                    max_pool_connections=10
                                )
                                
                                # 既存のAgentIDとエイリアスIDを使用
                                if not agent_id:
                                    agent_id = "QKIWJP7RL9"  # デフォルトのAgent ID
                                if not alias_id:
                                    alias_id = "HMJDNE7YDR"  # デフォルトのAlias ID
                                
                                # 新しいクライアントを作成
                                temp_agent_client = boto3.client(
                                    'bedrock-agent-runtime',
                                    region_name="us-east-1",
                                    config=client_config
                                )
                                
                                logger.info(f"2回目のAgent呼び出し: セッションID={unique_session_id}, 目標文字数={target_chars}")
                                
                                # モデルを検証し、最適なモデルIDを選択
                                model_id = self.analyzer.model
                                
                                # Bedrock APIの準備とAI Agent呼び出し
                                try:
                                    # ハイライト：拡充の重要性を説明
                                    logger.info(f"2回目のAI Agent処理：目標文字数{target_chars}文字に合わせて{target_chars - actual_chars}文字を追加")
                                    
                                    # セーフティメカニズム - 例外ハンドリングを強化
                                    def safe_invoke_agent():
                                        try:
                                            logger.info(f"2回目: Agent実行 - モデル={model_id}、最大待機時間=60秒")
                                            # タイムアウト値を大きめに設定（60秒）+ 重要なパラメータ明示
                                            import botocore
                                            custom_config = botocore.config.Config(
                                                connect_timeout=30,
                                                read_timeout=180,
                                                retries={'max_attempts': 5, 'mode': 'adaptive'},
                                                max_pool_connections=20,
                                                tcp_keepalive=True
                                            )
                                            
                                            # 最適化されたクライアントで呼び出し
                                            optimized_client = boto3.client(
                                                'bedrock-agent-runtime',
                                                region_name="us-east-1",  # 明示的に指定
                                                config=custom_config
                                            )
                                            
                                            # セッションメタデータを付与して呼び出し
                                            return optimized_client.invoke_agent(
                                                agentId=agent_id,
                                                agentAliasId=alias_id,
                                                sessionId=unique_session_id,
                                                inputText=second_input_text,
                                                enableTrace=True,  # トレース情報を有効化
                                                endSession=False   # セッションを閉じない（レスポンス取得のため）
                                            )
                                        except Exception as invoke_error:
                                            logger.error(f"2回目: Agent直接呼び出しエラー: {invoke_error}")
                                            # エラーの詳細情報を出力
                                            if hasattr(invoke_error, '__dict__'):
                                                error_attrs = {k: str(v)[:100] for k, v in invoke_error.__dict__.items()}
                                                logger.error(f"2回目: エラー詳細: {error_attrs}")
                                            # しっかり例外を伝播して適切な回復処理ができるようにする
                                            raise
                                    
                                    # リトライ機能を強化して2回目のAI Agent呼び出し
                                    @aws_api_retry(max_retries=2, base_delay=2, jitter=0.5)
                                    def call_second_agent():
                                        return safe_invoke_agent()
                                    
                                    # リトライ機能付きで呼び出し - タイムスタンプを記録
                                    start_time = self.analyzer.time_module.time()
                                    second_response = call_second_agent()
                                    elapsed = self.analyzer.time_module.time() - start_time
                                    logger.info(f"2回目のAI Agent呼び出しに成功（処理時間: {elapsed:.2f}秒）")
                                    
                                    # EventStream処理に成功した場合
                                    import botocore
                                    if isinstance(second_response, botocore.eventstream.EventStream):
                                        logger.info("2回目: EventStreamレスポンスを検出")
                                        
                                        # 必要なモジュールを先にインポート
                                        import json
                                        import re
                                        import time
                                        from collections import deque
                                        
                                        # 効率的なイベント処理のためのバッファ
                                        event_buffer = []
                                        content_events = []
                                        enhanced_script = None
                                        
                                        # メインスレッドでイベントを処理
                                        timeout_sec = 45  # タイムアウト時間
                                        start_time = time.time()
                                        completion_found = False
                                        
                                        try:
                                            # EventStreamからテキストを安全に抽出
                                            for event in second_response:
                                                # タイムアウトチェック
                                                if time.time() - start_time > timeout_sec:
                                                    logger.warning(f"2回目: イベント処理がタイムアウト({timeout_sec}秒)のため中断")
                                                    break
                                                
                                                # イベントをバッファに追加
                                                event_buffer.append(event)
                                                
                                                # イベントの型をログ出力（但しログが多すぎないように）
                                                if len(event_buffer) < 5 or len(event_buffer) % 10 == 0:
                                                    logger.info(f"2回目: イベント{len(event_buffer)}の型={type(event)}")
                                                
                                                # completionプロパティを持つイベントを優先的に処理
                                                if hasattr(event, 'completion'):
                                                    enhanced_script = event.completion
                                                    completion_found = True
                                                    logger.info("2回目: completionプロパティからテキストを直接抽出")
                                                    break
                                                
                                                # 辞書型イベントからcompletionを探す
                                                if isinstance(event, dict) and 'completion' in event:
                                                    enhanced_script = event['completion']
                                                    completion_found = True
                                                    logger.info("2回目: 辞書イベントからcompletionを取得")
                                                    break
                                                
                                                # チャンクデータからテキストを抽出
                                                if hasattr(event, 'chunk') and hasattr(event.chunk, 'bytes'):
                                                    try:
                                                        chunk_bytes = event.chunk.bytes
                                                        chunk_text = chunk_bytes.decode('utf-8', errors='replace')
                                                        
                                                        # ★★★ 根本的な原因修正: EventStreamの直接参照を事前に除去 ★★★
                                                        if chunk_text.strip():
                                                            # オブジェクト参照が含まれるかチェック（よりアグレッシブに）
                                                            contains_object_ref = any(marker in chunk_text for marker in 
                                                                ['<botocore', 'EventStream', '<boto', 'object at 0x', 'at 0x', '<', '>'])
                                                            
                                                            if contains_object_ref:
                                                                # オブジェクト参照がある場合、行単位でフィルタリング
                                                                logger.warning("2回目: チャンクにPythonオブジェクト参照を検出。サニタイズ実施")
                                                                
                                                                # 行単位でフィルタリング（最も効果的）
                                                                cleaned_lines = []
                                                                for line in chunk_text.split('\n'):
                                                                    # 問題のある行は除外
                                                                    if any(marker in line for marker in 
                                                                          ['<botocore', 'EventStream', '<boto', 'object at 0x', 'at 0x']):
                                                                        logger.warning(f"2回目: 問題行を除去「{line[:30]}...」") 
                                                                        continue
                                                                    
                                                                    # キャラクター発言行の特別チェック
                                                                    if any(char in line for char in ['れいむ:', 'まりさ:', 'ナレーション:']):
                                                                        if '<' in line and '>' in line:
                                                                            logger.warning(f"2回目: 問題のあるキャラクター行を除去「{line[:30]}...」")
                                                                            continue
                                                                    
                                                                    # 問題ない行だけを保持
                                                                    cleaned_lines.append(line)
                                                                
                                                                # サニタイズされたテキストを使用
                                                                chunk_text = '\n'.join(cleaned_lines)
                                                                logger.info("2回目: チャンクデータの事前サニタイズ完了")
                                                            
                                                            # 安全になったテキストのみを格納
                                                            if chunk_text.strip():
                                                                content_events.append(chunk_text)
                                                                if len(content_events) == 1 or len(content_events) % 5 == 0:
                                                                    logger.info(f"2回目: チャンクデータを追加（{len(chunk_text)}文字, 合計{sum(len(t) for t in content_events)}文字）")
                                                    except Exception as e:
                                                        logger.warning(f"2回目: チャンクデコードエラー: {e}")
                                            
                                            # イベント処理完了後のログ
                                            logger.info(f"2回目: イベントストリーム処理完了 - {len(event_buffer)}個のイベントを処理")
                                        except Exception as event_err:
                                            logger.error(f"2回目: イベント処理エラー: {event_err}")
                                        
                                        # 完全なコンテンツを構築
                                        if enhanced_script:
                                            # テキスト長をsafelyに取得
                                            try:
                                                enhanced_len = len(enhanced_script)
                                                logger.info(f"2回目: completion直接取得に成功: {enhanced_len}文字")
                                                if enhanced_len > actual_chars:
                                                    cleaned_script = enhanced_script
                                                    logger.info(f"2回目: 台本を更新しました（{len(cleaned_script)}文字）")
                                                else:
                                                    logger.warning(f"2回目: 取得したテキストが元より短いため無視（{enhanced_len}文字 vs {actual_chars}文字）")
                                            except Exception as len_error:
                                                # 長さ測定でエラーが起きた場合
                                                logger.error(f"2回目: テキスト長測定エラー: {len_error}")
                                                # 安全に文字列化して長さを取得
                                                try:
                                                    enhanced_str = str(enhanced_script)
                                                    if len(enhanced_str) > actual_chars:
                                                        cleaned_script = enhanced_str
                                                        logger.info(f"2回目: 文字列化したテキストで更新（{len(enhanced_str)}文字）")
                                                except Exception:
                                                    logger.error("2回目: 文字列化にも失敗")
                                        
                                        # チャンクからのコンテンツ構築
                                        elif content_events:
                                            try:
                                                enhanced_script = "".join(content_events)
                                                enhanced_len = len(enhanced_script)
                                                logger.info(f"2回目: コンテンツイベント結合に成功: {enhanced_len}文字")
                                                # チャンクから構築したテキストが有用な長さなら使用
                                                if enhanced_len > actual_chars:
                                                    cleaned_script = enhanced_script
                                                    logger.info(f"2回目: 台本をチャンク結合テキストで更新（{enhanced_len}文字）")
                                                else:
                                                    logger.warning(f"2回目: チャンク結合テキストが元より短いため無視（{enhanced_len}文字 vs {actual_chars}文字）")
                                            except Exception as combine_error:
                                                logger.error(f"2回目: チャンク結合エラー: {combine_error}")
                                    
                                    # EventStreamまたは辞書形式の処理
                                    else:
                                        try:
                                            # 安全な方法でレスポンスタイプを確認
                                            response_type = str(type(second_response))
                                            logger.info(f"2回目: レスポンスの実際の型: {response_type}")
                                            
                                            # 辞書型の場合の処理
                                            if isinstance(second_response, dict):
                                                # completionキーがある場合
                                                if 'completion' in second_response:
                                                    # 安全に文字列変換
                                                    try:
                                                        enhanced_script = str(second_response['completion'])
                                                        enhanced_len = len(enhanced_script)
                                                        logger.info(f"2回目: 辞書からcompletionを直接取得: {enhanced_len}文字")
                                                        
                                                        # 実際に使える十分な長さの台本かどうかを確認
                                                        if enhanced_len > actual_chars:
                                                            # 元の台本より長い場合は使用
                                                            cleaned_script = enhanced_script
                                                            logger.info(f"2回目: 台本を更新しました（{enhanced_len}文字）")
                                                        elif enhanced_len > 50:
                                                            # 短いが内容がある場合は既存の台本に追加して文字数を確保
                                                            logger.info(f"2回目: 取得したテキストをマージします（元:{actual_chars}文字 + 新:{enhanced_len}文字）")
                                                            try:
                                                                # 会話の自然なつながりを確保する接続テキスト
                                                                connector = "\n\nれいむ: もう少し詳しく説明しましょう。\n\nまりさ: お願いします！\n\n"
                                                                
                                                                # 会話形式でなければ形式を調整
                                                                if "れいむ:" not in enhanced_script and "まりさ:" not in enhanced_script:
                                                                    # 台詞の先頭にキャラクター名を追加
                                                                    formatted_content = f"れいむ: {enhanced_script.strip()}"
                                                                    enhanced_script = formatted_content
                                                                
                                                                # 台本のマージ
                                                                cleaned_script = f"{cleaned_script}{connector}{enhanced_script}"
                                                                logger.info(f"2回目: マージ後の文字数: {len(cleaned_script)}文字")
                                                            except Exception as merge_error:
                                                                logger.error(f"2回目: 台本マージエラー: {merge_error}")
                                                        else:
                                                            logger.warning(f"2回目: 取得したテキストが短すぎるため無視（{enhanced_len}文字）")
                                                    except Exception as str_error:
                                                        logger.error(f"2回目: completion文字列化エラー: {str_error}")
                                                
                                                # レスポンスボディを探す
                                                elif 'body' in second_response:
                                                    try:
                                                        body_content = second_response['body']
                                                        if hasattr(body_content, 'read'):
                                                            body_text = body_content.read().decode('utf-8')
                                                            body_data = json.loads(body_text)
                                                            if isinstance(body_data, dict) and 'content' in body_data:
                                                                content_text = body_data['content'][0]['text']
                                                                if len(content_text) > actual_chars:
                                                                    cleaned_script = content_text
                                                                    logger.info(f"2回目: レスポンスボディから台本を更新（{len(content_text)}文字）")
                                                    except Exception as body_error:
                                                        logger.warning(f"2回目: レスポンスボディ解析エラー: {body_error}")
                                            
                                            # EventStreamオブジェクトの可能性
                                            else:
                                                logger.info(f"2回目: 非辞書型レスポンス、文字列表現を試行")
                                                # EventStreamを文字列として安全に扱う
                                                try:
                                                    # EventStreamを直接文字列化しないように注意する
                                                    if hasattr(second_response, '__class__'):
                                                        class_name = second_response.__class__.__name__
                                                        logger.info(f"2回目: レスポンスクラス名: {class_name}")
                                                        if 'EventStream' in class_name:
                                                            logger.warning(f"EventStreamオブジェクトを検出しました。直接の文字列化は避けて内容を抽出します。")
                                                            # EventStreamの内容を安全に抽出するコード
                                                            extracted_text = None
                                                            try:
                                                                # EventStreamをイテレートして中身を抽出
                                                                for event in second_response:
                                                                    if hasattr(event, 'chunk') and hasattr(event.chunk, 'bytes'):
                                                                        chunk_bytes = event.chunk.bytes
                                                                        chunk_text = chunk_bytes.decode('utf-8', errors='replace')
                                                                        if chunk_text.strip():  # 空でなければ
                                                                            # 抽出したテキストから不要なEventStream参照などを完全に除去
                                                                            # 根本的な問題解決のための徹底的なクリーニング処理
                                                                            import re
                                                                            
                                                                            # 最初に文字列チェック - オブジェクト参照が含まれているか確認
                                                                            has_python_obj = ('EventStream' in chunk_text or 
                                                                                             'botocore' in chunk_text or 
                                                                                             'object at 0x' in chunk_text or 
                                                                                             ('<' in chunk_text and '>' in chunk_text and '0x' in chunk_text))
                                                                            
                                                                            if has_python_obj:
                                                                                logger.warning("テキストにPythonオブジェクト参照が検出されました - 厳格なフィルタリングを適用します")
                                                                                
                                                                                # 1. まず行単位でフィルタリング - オブジェクト参照を含む行を完全に削除
                                                                                lines = chunk_text.split('\n')
                                                                                clean_lines = []
                                                                                removed_lines = 0
                                                                                
                                                                                for line in lines:
                                                                                    # EventStreamやオブジェクト参照を含む行は完全に除外
                                                                                    if ('EventStream' in line or 
                                                                                        'botocore' in line or 
                                                                                        'object at 0x' in line or 
                                                                                        ('<' in line and '>' in line and '0x' in line) or  # オブジェクト参照のパターン
                                                                                        ('at 0x' in line)): # Pythonオブジェクトのアドレス参照パターン
                                                                                        removed_lines += 1
                                                                                        logger.warning(f"問題のある行を検出して除外: {line[:30]}...")
                                                                                        continue
                                                                                        
                                                                                    # キャラクター発言内の問題をチェック
                                                                                    if any(character in line for character in ['れいむ:', 'まりさ:', 'ナレーション:']):
                                                                                        # オブジェクト参照のある発言行をチェック
                                                                                        if ('<' in line and '>' in line) or 'object' in line or 'EventStream' in line:
                                                                                            removed_lines += 1
                                                                                            logger.warning(f"問題があるキャラクター発言行を除外: {line[:30]}...")
                                                                                            continue
                                                                                    
                                                                                    # クリーンな行のみ追加
                                                                                    clean_lines.append(line)
                                                                                
                                                                                # 2. 正規表現を使った二次フィルタリング
                                                                                chunk_text = '\n'.join(clean_lines)
                                                                                
                                                                                # 徹底的なパターンマッチング
                                                                                patterns = [
                                                                                    # あらゆるPythonオブジェクト表現
                                                                                    r'<[^>]*?at 0x[0-9a-f]+[^>]*?>',
                                                                                    r'<[^>]*?object[^>]*?>',
                                                                                    r'<[^>]*?botocore[^>]*?>',
                                                                                    r'<[^>]*?EventStream[^>]*?>',
                                                                                    
                                                                                    # キャラクターセリフ中の参照
                                                                                    r'れいむ:.*?<.*?object.*?>.*(\n|$)',
                                                                                    r'まりさ:.*?<.*?object.*?>.*(\n|$)',
                                                                                    r'ナレーション:.*?<.*?object.*?>.*(\n|$)',
                                                                                    r'れいむ:.*?<.*?EventStream.*?>.*(\n|$)',
                                                                                    r'まりさ:.*?<.*?EventStream.*?>.*(\n|$)',
                                                                                    r'ナレーション:.*?<.*?EventStream.*?>.*(\n|$)',
                                                                                    
                                                                                    # 残りの行の修正
                                                                                    r'<.*?EventStream.*?>',
                                                                                    r'<.*?object at 0x[0-9a-f]+.*?>',
                                                                                ]
                                                                                
                                                                                # パターンを適用
                                                                                for pattern in patterns:
                                                                                    prev_len = len(chunk_text)
                                                                                    chunk_text = re.sub(pattern, '', chunk_text)
                                                                                    if len(chunk_text) != prev_len:
                                                                                        logger.info(f"パターン '{pattern}' でテキストを浄化しました")
                                                                                
                                                                                # 3. 最終チェック - 三次フィルタリング
                                                                                if ('EventStream' in chunk_text or 'botocore' in chunk_text or 'object at 0x' in chunk_text):
                                                                                    logger.warning("浄化後も問題が残っているため、最終フィルタリングを適用")
                                                                                    # 行単位で再度厳格にフィルタリング
                                                                                    lines = chunk_text.split('\n')
                                                                                    clean_lines = []
                                                                                    for line in lines:
                                                                                        # 問題キーワードを含む行を完全に削除
                                                                                        if not any(kw in line for kw in ['EventStream', 'botocore', 'object at 0x', '<', '>']):
                                                                                            clean_lines.append(line)
                                                                                    chunk_text = '\n'.join(clean_lines)
                                                                                    
                                                                                logger.info(f"厳格なフィルタリング完了: {removed_lines}行を除去、最終テキスト長={len(chunk_text)}文字")
                                                                            else:
                                                                                logger.info("テキストにオブジェクト参照がないため標準クリーニングのみ適用")
                                                                            
                                                                            # クリーンなテキストを設定（フィルター済みのchunk_textを使用）
                                                                            if chunk_text.strip():  # 空でなければ
                                                                                extracted_text = chunk_text
                                                                                logger.info(f"EventStreamから直接テキスト抽出（クリーニング済み）: {len(extracted_text)}文字")
                                                                                break
                                                                
                                                                if extracted_text:
                                                                    # 抽出したテキストを使用
                                                                    cleaned_script = extracted_text
                                                                    logger.info(f"EventStreamから抽出したテキストで更新: {len(extracted_text)}文字")
                                                                    # 文字列表現は設定しない
                                                                    str_representation = None
                                                                else:
                                                                    str_representation = "EventStreamからテキスト抽出に失敗"
                                                            except Exception as extr_err:
                                                                logger.error(f"EventStream抽出エラー: {extr_err}")
                                                                str_representation = "EventStream処理エラー"
                                                        else:
                                                            # 文字列表現を取得（先頭1000文字まで）
                                                            str_representation = str(second_response)[:1000]
                                                            logger.info(f"2回目: レスポンス文字列表現: {str_representation[:100]}...")
                                                    else:
                                                        # 文字列表現を取得（先頭1000文字まで）
                                                        str_representation = str(second_response)[:1000]
                                                        logger.info(f"2回目: レスポンス文字列表現: {str_representation[:100]}...")
                                                    
                                                    # 文字列表現からcompletionキーを探す
                                                    if "completion" in str_representation:
                                                        try:
                                                            import re
                                                            # 正規表現でcompletionの内容を抽出
                                                            completion_match = re.search(r"completion['\"]?\s*[:=]\s*['\"]?(.*?)['\"]?[,}]", str_representation)
                                                            if completion_match:
                                                                extracted_text = completion_match.group(1)
                                                                logger.info(f"2回目: 正規表現でcompletionを抽出: {extracted_text[:50]}...")
                                                                if len(extracted_text) > actual_chars:
                                                                    cleaned_script = extracted_text
                                                                    logger.info(f"2回目: 正規表現抽出テキストで更新（{len(extracted_text)}文字）")
                                                        except Exception as regex_error:
                                                            logger.warning(f"2回目: 正規表現抽出エラー: {regex_error}")
                                                except Exception as str_error:
                                                    logger.warning(f"2回目: レスポンス文字列化エラー: {str_error}")
                                        except Exception as response_error:
                                            logger.error(f"2回目: レスポンス処理全体エラー: {response_error}")
                                    
                                    # 文字数が目標に達しているか最終確認 - 安全に長さを取得
                                    try:
                                        current_length = len(cleaned_script)
                                        if current_length >= target_chars:
                                            logger.info(f"2回目のAI Agent処理で目標文字数を達成: {current_length}/{target_chars}文字")
                                        else:
                                            logger.warning(f"2回目のAI Agent処理後も目標文字数に達していません: {current_length}/{target_chars}文字")
                                    except Exception as len_check_error:
                                        logger.error(f"2回目: 最終文字数チェックエラー: {len_check_error}")
                                        # cleaned_scriptが何らかの理由で文字列でない場合に安全に文字列化
                                        try:
                                            if cleaned_script is not None:
                                                cleaned_script = str(cleaned_script)
                                                logger.info(f"2回目: 台本を安全に文字列化: {len(cleaned_script)}文字")
                                        except:
                                            logger.critical("2回目: 台本の文字列化に完全に失敗。元の台本を使用します。")
                                            # このポイントに到達したら、台本を元に戻す
                                            cleaned_script = script_content
                                        
                                except Exception as agent_call_error:
                                    logger.error(f"2回目のAI Agent呼び出し実行エラー: {agent_call_error}")
                                                            
                            except Exception as second_call_error:
                                logger.error(f"2回目のAI Agent処理全体エラー: {second_call_error}")
                        else:
                            logger.info(f"文字数は十分です: {actual_chars}文字（目標: {target_chars}文字）")
                        
                        # 最終的な文字数チェック - それでも目標文字数に達していない場合は標準の補完処理を使用
                        if len(cleaned_script) < target_chars:
                            logger.info(f"AI Agent処理後も文字数が不足しているため標準補完処理を開始: {len(cleaned_script)}/{target_chars}文字")
                            cleaned_script = self.ensure_minimum_length(cleaned_script, target_chars, script_data)
                            logger.info(f"標準補完処理後の文字数: {len(cleaned_script)}/{target_chars}文字")
                        
                        # クリーニングされたスクリプトを使用
                        improved_script = cleaned_script
                else:
                    # 通常のBedrock基盤モデル呼び出し（強化されたリトライ機能付き）
                    logger.info("通常のBedrock基盤モデルを使用します")
                    
                    @aws_api_retry(max_retries=3, base_delay=2, jitter=0.5, event_stream_handling=True)
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
                    
                    # 必要なモジュールを明示的に再インポート
                    import json
                    import botocore
                    import time
                    
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
        
        # 元の台本データをコピー
        improved_script_data = script_data.copy()
        
        # 改善された台本が文字列型である場合の処理
        if isinstance(improved_script, str) and improved_script:
            logger.info(f"文字列型の改善台本（長さ: {len(improved_script)}）を処理して辞書型に変換します")
            
            # ★★★ 根本対策: 全ての台本内容を最終サニタイズ処理 ★★★
            # EventStreamオブジェクト参照を完全に除去し、余計な前書きも削除
            sanitized_script = sanitize_script(improved_script)
            logger.info(f"最終サニタイズ処理を適用しました。処理前={len(improved_script)}文字、処理後={len(sanitized_script)}文字")
            
            improved_script_data["script_content"] = sanitized_script
            improved_script_data["status"] = "review"
        else:
            # 正常な処理（辞書または何らかのオブジェクトを返す場合）
            logger.info(f"既存の改善台本のフォーマットを使用: 型={type(improved_script)}")
            
            # 安全のために文字列化とサニタイズを適用
            if improved_script is not None:
                script_str = str(improved_script) if not isinstance(improved_script, str) else improved_script
                sanitized_script = sanitize_script(script_str)
                improved_script_data["script_content"] = sanitized_script
            else:
                improved_script_data["script_content"] = ""
                
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

        # 認証情報マネージャー
        self.credential_manager = None

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
            # AWS認証情報マネージャーの初期化
            from .aws_credentials import CredentialManager
            aws_region = os.getenv("AWS_REGION", "us-east-1")
            self.credential_manager = CredentialManager(region_name=aws_region)
            self.region_name = aws_region  # リージョン名を保存
            
            # Bedrockクライアントの初期化
            try:
                # 認証情報マネージャーから有効なセッションを取得
                if not self.credential_manager.session:
                    raise ValueError("有効なAWS認証情報が取得できませんでした")
                    
                # 利用可能なリージョンのログ出力
                available_regions = ["us-east-1", "us-west-2", "eu-central-1", "ap-northeast-1"]
                logger.info(f"設定されたリージョン: {aws_region}")
                logger.info(f"Bedrock利用可能リージョン: {', '.join(available_regions)}")

                # Bedrockランタイムクライアントの作成 - 認証情報マネージャーを使用
                import botocore
                client_config = botocore.config.Config(
                    connect_timeout=30,    # 接続タイムアウト30秒
                    read_timeout=120,      # 読み取りタイムアウト120秒
                    retries={'max_attempts': 5, 'mode': 'adaptive'},  # 最大リトライ回数増加 + アダプティブモード
                    max_pool_connections=20, # 接続プールを拡大
                    tcp_keepalive=True      # TCP接続をキープアライブ
                )
                
                self.bedrock_runtime = self.credential_manager.get_client(
                    'bedrock-runtime', config=client_config
                )
                
                # Bedrock Agentクライアントの作成 - 認証情報マネージャーを使用
                agent_config = botocore.config.Config(
                    connect_timeout=30,    # 接続タイムアウト30秒
                    read_timeout=120,      # 読み取りタイムアウト120秒
                    retries={'max_attempts': 5, 'mode': 'adaptive'},  # 最大リトライ回数増加 + アダプティブモード
                    max_pool_connections=20, # 接続プールを拡大
                    tcp_keepalive=True      # TCP接続をキープアライブ
                )
                
                self.bedrock_agent_client = self.credential_manager.get_client(
                    'bedrock-agent-runtime', config=agent_config
                )
                
                logger.info("Bedrock Agentクライアントの初期化に成功しました")
                self.use_bedrock = True
                
                # 認証情報が有効かどうか確認するためのテスト呼び出し
                try:
                    sts = self.credential_manager.session.client('sts')
                    identity = sts.get_caller_identity()
                    logger.info(f"AWS認証情報が有効です: {identity.get('Arn')}")
                except Exception as e:
                    logger.warning(f"AWS認証情報の検証中に問題が発生しました: {str(e)}")
                    logger.warning("AWS APIコール実行時に認証情報が自動的にリフレッシュされます")
                
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

    @with_aws_credential_refresh
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
                # 認証情報の有効性確認
                if hasattr(self, 'credential_manager') and self.credential_manager:
                    self.credential_manager.check_credentials()
                
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
                    elif "UnrecognizedClientException" in error_msg or "security token" in error_msg.lower():
                        # セキュリティトークンエラーの場合、詳細なエラーメッセージを提供
                        logger.error("AWS認証エラー: セキュリティトークンが無効です")
                        logger.error("認証情報をリフレッシュして再試行します...")
                        
                        # 認証情報マネージャーがある場合は強制的にリフレッシュ
                        if hasattr(self, 'credential_manager') and self.credential_manager:
                            self.credential_manager.refresh_credentials()
                            # クライアントを再作成
                            import botocore
                            client_config = botocore.config.Config(
                                connect_timeout=30,
                                read_timeout=120,
                                retries={'max_attempts': 5, 'mode': 'adaptive'},
                                max_pool_connections=20,
                                tcp_keepalive=True
                            )
                            self.bedrock_runtime = self.credential_manager.get_client(
                                'bedrock-runtime', config=client_config
                            )
                            # リフレッシュ後に再試行
                            response = self.bedrock_runtime.invoke_model(
                                modelId=model, body=body
                            )
                        else:
                            raise ConnectionError("AWS認証エラー: セキュリティトークンが無効で、認証情報マネージャーがありません") from e
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
                # エラーメッセージから認証エラーを検出
                error_text = str(e).lower()
                if ('security token' in error_text and 'invalid' in error_text) or \
                   'unrecognized client' in error_text or 'expired token' in error_text:
                    # 認証情報を更新してユーザーフレンドリーなエラーメッセージを表示
                    logger.error(f"AWS認証エラー: {str(e)}")
                    raise ConnectionError("AWS認証情報の有効期限が切れているか、無効です。AWS認証情報を更新してください。") from e
                else:
                    raise RuntimeError(f"Bedrock API error: {str(e)}")

        return result_text

    @with_aws_credential_refresh
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
                # 認証情報の有効性確認
                if hasattr(self, 'credential_manager') and self.credential_manager:
                    self.credential_manager.check_credentials()
                
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
                    elif "UnrecognizedClientException" in error_msg or "security token" in error_msg.lower():
                        # セキュリティトークンエラーの場合、詳細なエラーメッセージを提供
                        logger.error("AWS認証エラー: セキュリティトークンが無効です")
                        logger.error("認証情報をリフレッシュして再試行します...")
                        
                        # 認証情報マネージャーがある場合は強制的にリフレッシュ
                        if hasattr(self, 'credential_manager') and self.credential_manager:
                            self.credential_manager.refresh_credentials()
                            # クライアントを再作成
                            import botocore
                            client_config = botocore.config.Config(
                                connect_timeout=30,
                                read_timeout=120,
                                retries={'max_attempts': 5, 'mode': 'adaptive'},
                                max_pool_connections=20,
                                tcp_keepalive=True
                            )
                            self.bedrock_runtime = self.credential_manager.get_client(
                                'bedrock-runtime', config=client_config
                            )
                            # リフレッシュ後に再試行
                            response = self.bedrock_runtime.invoke_model(
                                modelId=model, body=body
                            )
                        else:
                            raise ConnectionError("AWS認証エラー: セキュリティトークンが無効で、認証情報マネージャーがありません") from e
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
                # エラーメッセージから認証エラーを検出
                error_text = str(e).lower()
                if ('security token' in error_text and 'invalid' in error_text) or \
                   'unrecognized client' in error_text or 'expired token' in error_text:
                    # 認証情報を更新してユーザーフレンドリーなエラーメッセージを表示
                    logger.error(f"AWS認証エラー: {str(e)}")
                    raise ConnectionError("AWS認証情報の有効期限が切れているか、無効です。AWS認証情報を更新してください。") from e
                else:
                    raise RuntimeError(f"Bedrock API error: {str(e)}")

        return result_text
