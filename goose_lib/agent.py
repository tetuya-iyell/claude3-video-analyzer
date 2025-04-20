"""
台本作成のためのAIエージェント実装
"""

import os
import json
from typing import List, Dict, Any, Optional, Tuple
import anthropic
from langchain.prompts import PromptTemplate
from langchain_anthropic import ChatAnthropic

from .models import ChapterScript, ScriptFeedback


class ScriptAgent:
    """ゆっくり不動産の台本作成AIエージェント"""
    
    def __init__(self, model_name: str = None, api_key: str = None):
        """初期化
        
        Args:
            model_name: 使用するモデル名
            api_key: APIキー（指定がなければ環境変数から取得）
        """
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.model_name = model_name or os.environ.get("ANTHROPIC_MODEL_ID", "claude-3-sonnet-20240229")
        
        # サンプル台本のパスを設定
        script_folder = os.path.join(os.getcwd(), "goose_lib", "sample_scripts")
        os.makedirs(script_folder, exist_ok=True)
        self.sample_script_path = os.path.join(script_folder, "sample_scripts.json")
        
        # LangChain統合
        self.llm = ChatAnthropic(
            model_name=self.model_name,
            temperature=0.7,
            anthropic_api_key=self.api_key
        )
        
        # 台本生成プロンプトテンプレート
        self.script_prompt = PromptTemplate(
            input_variables=["chapter_title", "chapter_summary", "sample_script"],
            template="""
あなたは不動産の解説動画「ゆっくり不動産」の台本作成専門のAIアシスタントです。
以下の章タイトルと概要に基づいて、ゆっくり不動産の台本を作成してください。

# 章タイトル
{chapter_title}

# 章の概要
{chapter_summary}

# 参考台本のスタイル
{sample_script}

以下の点に注意して台本を作成してください：
1. ゆっくり実況の口調で書く（「～です」「～ます」調）
2. 専門用語は噛み砕いて説明する
3. 重要なポイントは繰り返して強調する
4. 読者が実際に行動できる具体的なアドバイスを含める
5. 台本形式は「台詞:」で話者を示し、その後に台詞内容を記載する

台本を作成してください：
"""
        )
        
        # フィードバック分析プロンプトテンプレート
        self.feedback_analysis_prompt = PromptTemplate(
            input_variables=["script_content", "feedback"],
            template="""
あなたは不動産の解説動画「ゆっくり不動産」の台本編集アシスタントです。
以下の台本とフィードバックに基づいて、台本を改善してください。

# 現在の台本
{script_content}

# フィードバック
{feedback}

フィードバックを踏まえて改善した台本を作成してください。台本形式は元の形式を維持してください。
"""
        )
    
    def _load_sample_scripts(self) -> List[str]:
        """サンプル台本の読み込み"""
        if not os.path.exists(self.sample_script_path):
            # サンプルが存在しない場合はデフォルトを使用
            return ["台詞: 皆さんこんにちは、ゆっくり不動産です。今回は不動産投資における重要なポイントについて解説します。",
                    "台詞: まず最初に覚えておいていただきたいのが、「立地」「需要」「利回り」の3つの観点です。"]
        
        with open(self.sample_script_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get("sample_scripts", [])
    
    def _save_sample_script(self, script_content: str) -> None:
        """新しいサンプル台本を保存"""
        scripts = self._load_sample_scripts()
        
        # 同じ内容のサンプルがなければ追加（最大10件まで）
        if script_content not in scripts:
            scripts.append(script_content)
            if len(scripts) > 10:
                scripts.pop(0)  # 古いサンプルを削除
        
        # 保存
        os.makedirs(os.path.dirname(self.sample_script_path), exist_ok=True)
        with open(self.sample_script_path, 'w', encoding='utf-8') as f:
            json.dump({"sample_scripts": scripts}, f, ensure_ascii=False, indent=2)
    
    def extract_chapters(self, analysis_text: str) -> List[Dict[str, str]]:
        """章立て解析結果から各章の情報を抽出する
        
        Args:
            analysis_text: 章立て解析結果のテキスト
        
        Returns:
            章情報のリスト（タイトルと概要）
        """
        # Claudeに章構造を解析させる
        prompt = f"""
以下の動画解析テキストから章立て構造を抽出し、JSONフォーマットで返してください。
各章には章番号、タイトル、概要を含めてください。
概要があいまいなものは「詳細な説明はありません」としてください。

解析テキスト:
```
{analysis_text}
```

以下のJSON形式で出力してください:
```
[
  {{
    "chapter_num": 1,
    "chapter_title": "章のタイトル",
    "chapter_summary": "章の概要"
  }},
  ...
]
```

必ず有効なJSONフォーマットで出力してください。
        """
        
        # Claude APIで章構造を解析
        response = self.client.messages.create(
            model=self.model_name,
            max_tokens=1500,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        # JSON部分を抽出
        response_text = response.content[0].text
        json_start = response_text.find('[')
        json_end = response_text.rfind(']') + 1
        
        if json_start >= 0 and json_end > json_start:
            try:
                json_str = response_text[json_start:json_end]
                chapters = json.loads(json_str)
                return chapters
            except json.JSONDecodeError:
                # JSON解析に失敗した場合は空リストを返す
                print("JSON解析エラー:", response_text)
                return []
        else:
            print("JSON形式が見つかりません:", response_text)
            return []
    
    def generate_script_for_chapter(self, chapter: Dict[str, str]) -> ChapterScript:
        """各章の台本を生成
        
        Args:
            chapter: 章情報（タイトルと概要を含む辞書）
        
        Returns:
            生成された台本
        """
        # サンプル台本を取得
        sample_scripts = self._load_sample_scripts()
        sample_script_text = "\n".join(sample_scripts)
        
        # プロンプトを準備
        prompt_inputs = {
            "chapter_title": chapter["chapter_title"],
            "chapter_summary": chapter["chapter_summary"],
            "sample_script": sample_script_text
        }
        
        # 台本生成
        prompt = self.script_prompt.format(**prompt_inputs)
        response = self.client.messages.create(
            model=self.model_name,
            max_tokens=2000,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        script_content = response.content[0].text
        
        # 台本オブジェクトを作成
        return ChapterScript(
            chapter_title=chapter["chapter_title"],
            chapter_summary=chapter["chapter_summary"],
            script_content=script_content,
            status="review"
        )
    
    def improve_script(self, script: ChapterScript, feedback: str) -> ChapterScript:
        """フィードバックに基づいて台本を改善する
        
        Args:
            script: 改善する台本
            feedback: フィードバック内容
        
        Returns:
            改善された台本
        """
        # プロンプトを準備
        prompt_inputs = {
            "script_content": script.script_content,
            "feedback": feedback
        }
        
        # 台本改善
        prompt = self.feedback_analysis_prompt.format(**prompt_inputs)
        response = self.client.messages.create(
            model=self.model_name,
            max_tokens=2000,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        improved_script = response.content[0].text
        
        # 改善された台本オブジェクトを作成（元の情報を維持）
        new_script = ChapterScript(
            chapter_title=script.chapter_title,
            chapter_summary=script.chapter_summary,
            script_content=improved_script,
            status="review"
        )
        
        return new_script
    
    def analyze_script_quality(self, script: ChapterScript) -> Tuple[bool, str]:
        """台本の品質を分析する
        
        Args:
            script: 分析する台本
        
        Returns:
            (合格かどうか, 分析コメント)のタプル
        """
        prompt = f"""
以下のゆっくり不動産の台本を分析し、その品質を評価してください。

# 章タイトル
{script.chapter_title}

# 章の概要
{script.chapter_summary}

# 台本
{script.script_content}

以下の基準で評価してください：
1. ゆっくり実況の口調になっているか
2. 専門用語が適切に説明されているか
3. 重要なポイントが強調されているか
4. 具体的なアドバイスが含まれているか
5. 台本形式が適切か（「台詞:」で話者を示しているか）

この台本が基準を満たしていると思いますか？「はい」または「いいえ」で答え、その理由を具体的に説明してください。
改善点があれば具体的に指摘してください。
        """
        
        # 品質分析を実行
        response = self.client.messages.create(
            model=self.model_name,
            max_tokens=1000,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        
        analysis = response.content[0].text
        
        # 「はい」または「いいえ」を抽出
        if "はい" in analysis[:50]:
            passed = True
        else:
            passed = False
        
        return (passed, analysis)
    
    def generate_scripts_from_analysis(self, analysis_text: str) -> List[ChapterScript]:
        """章立て解析結果から台本を生成する
        
        Args:
            analysis_text: 章立て解析結果のテキスト
        
        Returns:
            生成された台本のリスト
        """
        # 章の抽出
        chapters = self.extract_chapters(analysis_text)
        
        # 各章の台本を生成
        scripts = []
        for chapter in chapters:
            script = self.generate_script_for_chapter(chapter)
            scripts.append(script)
        
        return scripts