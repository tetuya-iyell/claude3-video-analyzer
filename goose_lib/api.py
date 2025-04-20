"""
Gooseエージェント API
"""

from flask import Blueprint, request, jsonify, session
import json
from typing import Dict, List, Any
import os

from .agent import ScriptAgent
from .models import ChapterScript, ScriptFeedback


# Blueprintの作成
goose_bp = Blueprint('goose', __name__, url_prefix='/api/goose')

# グローバル変数（セッション外で保持する必要がある変数）
_scripts_store = {}  # sessionIDをキーとして台本を保存

def _get_session_id() -> str:
    """一意のセッションIDを取得する"""
    if 'session_id' not in session:
        import uuid
        session['session_id'] = str(uuid.uuid4())
    return session['session_id']

def _get_scripts(session_id: str = None) -> List[Dict]:
    """セッションに保存された台本を取得する"""
    if session_id is None:
        session_id = _get_session_id()
        
    if session_id not in _scripts_store:
        _scripts_store[session_id] = []
        
    return _scripts_store[session_id]

def _save_scripts(scripts_data: List[Dict], session_id: str = None) -> None:
    """セッションに台本を保存する"""
    if session_id is None:
        session_id = _get_session_id()
        
    _scripts_store[session_id] = scripts_data


@goose_bp.route('/analyze-chapters', methods=['POST'])
def analyze_chapters():
    """
    章立て解析結果から各章を抽出するAPI
    ---
    リクエストボディ:
    {
        "analysis_text": "章立て解析結果のテキスト"
    }
    """
    data = request.json
    if not data or 'analysis_text' not in data:
        return jsonify({"error": "解析テキストが提供されていません"}), 400
        
    analysis_text = data['analysis_text']
    
    try:
        # ScriptAgentのインスタンス作成
        agent = ScriptAgent()
        
        # 章の抽出
        chapters = agent.extract_chapters(analysis_text)
        
        # セッションに保存
        session['chapters'] = chapters
        
        return jsonify({
            "success": True,
            "chapters": chapters
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@goose_bp.route('/generate-script', methods=['POST'])
def generate_script():
    """
    特定の章の台本を生成するAPI
    ---
    リクエストボディ:
    {
        "chapter_index": 0,  // 章のインデックス
        "chapters": []  // 省略可能。指定がなければセッションから取得
    }
    """
    data = request.json
    if not data or 'chapter_index' not in data:
        return jsonify({"error": "章のインデックスが指定されていません"}), 400
        
    chapter_index = data['chapter_index']
    
    # 章情報の取得
    chapters = data.get('chapters')
    if not chapters:
        chapters = session.get('chapters')
    
    if not chapters or chapter_index >= len(chapters):
        return jsonify({"error": "指定された章が見つかりません"}), 404
        
    chapter = chapters[chapter_index]
    
    try:
        # ScriptAgentのインスタンス作成
        agent = ScriptAgent()
        
        # 台本生成
        script = agent.generate_script_for_chapter(chapter)
        
        # 台本を保存
        scripts = _get_scripts()
        if chapter_index >= len(scripts):
            # 新しい章の台本を追加
            scripts.append(script.to_dict())
        else:
            # 既存の章の台本を更新
            scripts[chapter_index] = script.to_dict()
        _save_scripts(scripts)
        
        return jsonify({
            "success": True,
            "script": script.to_dict(),
            "chapter_index": chapter_index
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@goose_bp.route('/analyze-script', methods=['POST'])
def analyze_script():
    """
    台本の品質を分析するAPI
    ---
    リクエストボディ:
    {
        "chapter_index": 0,  // 章のインデックス
        "script_content": "台本内容"  // 省略可能。指定がなければ保存された台本を使用
    }
    """
    data = request.json
    if not data or 'chapter_index' not in data:
        return jsonify({"error": "章のインデックスが指定されていません"}), 400
        
    chapter_index = data['chapter_index']
    script_content = data.get('script_content')
    
    # 台本の取得
    scripts = _get_scripts()
    if chapter_index >= len(scripts):
        return jsonify({"error": "指定された章の台本が見つかりません"}), 404
    
    script_data = scripts[chapter_index]
    
    # script_contentが指定された場合は、台本内容を更新
    if script_content:
        script_data['script_content'] = script_content
        scripts[chapter_index] = script_data
        _save_scripts(scripts)
    
    # ChapterScriptオブジェクトを作成
    script = ChapterScript(
        chapter_title=script_data['chapter_title'],
        chapter_summary=script_data['chapter_summary'],
        script_content=script_data['script_content'],
        status=script_data['status']
    )
    
    try:
        # ScriptAgentのインスタンス作成
        agent = ScriptAgent()
        
        # 品質分析
        passed, analysis = agent.analyze_script_quality(script)
        
        # 分析結果を保存
        script_data['analysis'] = analysis
        script_data['passed'] = passed
        scripts[chapter_index] = script_data
        _save_scripts(scripts)
        
        return jsonify({
            "success": True,
            "passed": passed,
            "analysis": analysis,
            "chapter_index": chapter_index
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@goose_bp.route('/submit-feedback', methods=['POST'])
def submit_feedback():
    """
    台本にフィードバックを送信するAPI
    ---
    リクエストボディ:
    {
        "chapter_index": 0,  // 章のインデックス
        "feedback": "フィードバック内容",
        "is_approved": false  // 承認するかどうか
    }
    """
    data = request.json
    if not data or 'chapter_index' not in data or 'feedback' not in data or 'is_approved' not in data:
        return jsonify({"error": "必須パラメータが不足しています"}), 400
        
    feedback = ScriptFeedback(
        chapter_index=data['chapter_index'],
        feedback_text=data['feedback'],
        is_approved=data['is_approved']
    )
    
    # 台本の取得
    scripts = _get_scripts()
    if feedback.chapter_index >= len(scripts):
        return jsonify({"error": "指定された章の台本が見つかりません"}), 404
    
    script_data = scripts[feedback.chapter_index]
    
    # ChapterScriptオブジェクトを作成
    script = ChapterScript(
        chapter_title=script_data['chapter_title'],
        chapter_summary=script_data['chapter_summary'],
        script_content=script_data['script_content'],
        status=script_data['status'],
        feedback=script_data.get('feedback', [])
    )
    
    try:
        # フィードバックの処理
        if feedback.is_approved:
            # 承認の場合
            script.approve()
            script_data['status'] = "approved"
        else:
            # フィードバックの場合
            script.add_feedback(feedback.feedback_text)
            script.reject()
            script_data['status'] = "rejected"
            if 'feedback' not in script_data:
                script_data['feedback'] = []
            script_data['feedback'].append(feedback.feedback_text)
            
            # フィードバックに基づいて台本を改善
            agent = ScriptAgent()
            improved_script = agent.improve_script(script, feedback.feedback_text)
            script_data['improved_script'] = improved_script.script_content
        
        # 変更を保存
        scripts[feedback.chapter_index] = script_data
        _save_scripts(scripts)
        
        return jsonify({
            "success": True,
            "chapter_index": feedback.chapter_index,
            "is_approved": feedback.is_approved,
            "improved_script": script_data.get('improved_script', None) if not feedback.is_approved else None
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@goose_bp.route('/apply-improvement', methods=['POST'])
def apply_improvement():
    """
    改善された台本を適用するAPI
    ---
    リクエストボディ:
    {
        "chapter_index": 0  // 章のインデックス
    }
    """
    data = request.json
    if not data or 'chapter_index' not in data:
        return jsonify({"error": "章のインデックスが指定されていません"}), 400
        
    chapter_index = data['chapter_index']
    
    # 台本の取得
    scripts = _get_scripts()
    if chapter_index >= len(scripts):
        return jsonify({"error": "指定された章の台本が見つかりません"}), 404
    
    script_data = scripts[chapter_index]
    if 'improved_script' not in script_data:
        return jsonify({"error": "改善された台本がありません"}), 400
    
    try:
        # 改善された台本を適用
        script_data['script_content'] = script_data['improved_script']
        script_data['status'] = "review"
        del script_data['improved_script']
        
        # フィードバック履歴は残す
        
        # 変更を保存
        scripts[chapter_index] = script_data
        _save_scripts(scripts)
        
        return jsonify({
            "success": True,
            "chapter_index": chapter_index,
            "script": script_data
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@goose_bp.route('/get-all-scripts', methods=['GET'])
def get_all_scripts():
    """
    すべての台本を取得するAPI
    """
    scripts = _get_scripts()
    return jsonify({
        "success": True,
        "scripts": scripts
    })