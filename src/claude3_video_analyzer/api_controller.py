"""
APIエンドポイントのコントローラークラス
"""

import os
import json
import logging
import uuid
from typing import Dict, Any, List, Optional, Tuple
from flask import jsonify, session, request
# Import the sanitize_script function directly from the module
from . import sanitize_script

# ロガー設定
logger = logging.getLogger(__name__)

# セッションデータ保存用のディレクトリ
SESSION_DATA_DIR = os.path.join(os.getcwd(), "flask_sessions")


class APIController:
    """APIエンドポイントを処理するコントローラークラス"""
    
    def __init__(self, script_generator):
        """初期化
        
        Args:
            script_generator: ScriptGeneratorインスタンス
        """
        self.script_generator = script_generator
        
    def analyze_chapters(self) -> Tuple[Dict[str, Any], int]:
        """章立て解析結果から各章を抽出するAPIエンドポイント処理
        
        Returns:
            APIレスポンスと状態コード
        """
        logger.info("======== 章立て解析APIが呼び出されました ========")
        
        # リクエストの詳細をログに記録
        logger.info(f"リクエスト内容: {request}")
        logger.info(f"ヘッダー: {request.headers}")
        
        data = request.json
        logger.info(f"JSONデータ: {data}")
        
        if not data or 'analysis_text' not in data:
            logger.error("解析テキストがリクエストに含まれていません")
            return {"error": "解析テキストが提供されていません"}, 400
            
        analysis_text = data['analysis_text']
        logger.info(f"解析テキスト長さ: {len(analysis_text)} 文字")
        
        try:
            # 章の抽出
            chapters = self.script_generator.extract_chapters(analysis_text)
            
            # セッションIDを生成（なければ）
            if 'session_id' not in session:
                session['session_id'] = str(uuid.uuid4())
            
            session_id = session['session_id']
            
            # ファイルにチャプターデータを保存
            chapters_file = os.path.join(SESSION_DATA_DIR, f"{session_id}_chapters.json")
            with open(chapters_file, 'w', encoding='utf-8') as f:
                json.dump(chapters, f, ensure_ascii=False)
            
            # セッションには参照のみ保存
            session['chapters_file'] = chapters_file
            
            return {
                "success": True,
                "chapters": chapters
            }, 200
        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            logger.error(f"章構造抽出エラー: {str(e)}")
            logger.error(f"トレースバック: {error_traceback}")
            return {"error": f"章構造の抽出に失敗しました: {str(e)}", "traceback": error_traceback}, 500

    def generate_script(self) -> Tuple[Dict[str, Any], int]:
        """特定の章の台本を生成するAPIエンドポイント処理
        
        Returns:
            APIレスポンスと状態コード
        """
        logger.info("======== 台本生成APIが呼び出されました ========")
        
        # リクエストの詳細をログに記録
        logger.info(f"リクエスト内容: {request}")
        logger.info(f"ヘッダー: {request.headers}")
        
        data = request.json
        logger.info(f"JSONデータ: {data}")
        
        if not data or 'chapter_index' not in data:
            logger.error("章のインデックスがリクエストに含まれていません")
            return {"error": "章のインデックスが指定されていません"}, 400
            
        chapter_index = int(data['chapter_index'])
        logger.info(f"対象章インデックス: {chapter_index}")
        
        # セッションIDの確認
        if 'session_id' not in session:
            session['session_id'] = str(uuid.uuid4())
        session_id = session['session_id']
        
        # 章情報の取得
        chapters = data.get('chapters')
        if not chapters:
            # セッションから章情報のファイルパスを取得
            chapters_file = session.get('chapters_file')
            if chapters_file and os.path.exists(chapters_file):
                with open(chapters_file, 'r', encoding='utf-8') as f:
                    chapters = json.load(f)
            else:
                return {"error": "章情報が見つかりません"}, 404
        else:
            # クライアントから送信された章情報をファイルに保存
            chapters_file = os.path.join(SESSION_DATA_DIR, f"{session_id}_chapters.json")
            with open(chapters_file, 'w', encoding='utf-8') as f:
                json.dump(chapters, f, ensure_ascii=False)
            session['chapters_file'] = chapters_file
            logger.info(f"クライアントから送信された章情報をファイルに保存しました: {len(chapters)}章")
        
        if not chapters or chapter_index >= len(chapters):
            return {"error": "指定された章が見つかりません"}, 404
            
        chapter = chapters[chapter_index]
        
        try:
            # 動画時間パラメータを取得（設定されていなければデフォルト3分）
            duration_minutes = int(data.get('duration_minutes', 3))
            
            # 台本生成（動画時間パラメータを渡す）
            logger.info(f"台本生成開始: 章={chapter['chapter_title']}, 動画時間={duration_minutes}分")
            try:
                script_data = self.script_generator.generate_script_for_chapter(chapter, duration_minutes)
                logger.info(f"台本生成成功: データキー={list(script_data.keys()) if isinstance(script_data, dict) else 'not_dict'}, 台本長さ={len(script_data['script_content']) if isinstance(script_data, dict) and 'script_content' in script_data else 0}文字")
            except Exception as gen_error:
                import traceback
                logger.error(f"台本生成中のエラー: {str(gen_error)}")
                logger.error(f"エラートレース: {traceback.format_exc()}")
                raise
            
            # スクリプト情報のファイル
            scripts_file = os.path.join(SESSION_DATA_DIR, f"{session_id}_scripts.json")
            
            # 既存のスクリプトを読み込む
            scripts = []
            if os.path.exists(scripts_file):
                with open(scripts_file, 'r', encoding='utf-8') as f:
                    scripts = json.load(f)
            
            logger.info(f"現在のスクリプト数: {len(scripts)}")
            
            # スクリプト配列を必要に応じて拡張
            while len(scripts) <= chapter_index:
                scripts.append(None)
                
            # 台本を保存
            scripts[chapter_index] = script_data
            
            # ファイルに保存
            with open(scripts_file, 'w', encoding='utf-8') as f:
                json.dump(scripts, f, ensure_ascii=False)
                
            # セッションに参照を保存
            session['scripts_file'] = scripts_file
            
            logger.info(f"台本をファイルに保存しました。chapter_index: {chapter_index}, スクリプト総数: {len(scripts)}")
            
            return {
                "success": True,
                "script": script_data,
                "chapter_index": chapter_index
            }, 200
        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            logger.error(f"台本生成エラー: {str(e)}")
            logger.error(f"トレースバック: {error_traceback}")
            # デバッグのためトレースバックも返す
            return {
                "error": f"台本生成に失敗しました: {str(e)}",
                "traceback": error_traceback
            }, 500
            
    def analyze_script_quality(self) -> Tuple[Dict[str, Any], int]:
        """台本の品質を分析するAPIエンドポイント処理
        
        Returns:
            APIレスポンスと状態コード
        """
        logger.info("======== 台本分析APIが呼び出されました ========")
        
        data = request.json
        if not data or 'chapter_index' not in data:
            return {"error": "章のインデックスが指定されていません"}, 400
            
        chapter_index = data['chapter_index']
        script_content = data.get('script_content')
        # 動画時間パラメータを取得（設定されていなければデフォルト3分）
        duration_minutes = int(data.get('duration_minutes', 3))
        
        # セッションIDの確認
        if 'session_id' not in session:
            session['session_id'] = str(uuid.uuid4())
        session_id = session['session_id']
        
        # スクリプトデータをファイルから取得
        scripts_file = session.get('scripts_file')
        if not scripts_file or not os.path.exists(scripts_file):
            return {"error": "スクリプトデータが見つかりません"}, 404
        
        # スクリプトデータを読み込む
        with open(scripts_file, 'r', encoding='utf-8') as f:
            scripts = json.load(f)
        
        if chapter_index >= len(scripts) or scripts[chapter_index] is None:
            return {"error": "指定された章の台本が見つかりません"}, 404
        
        script_data = scripts[chapter_index]
        
        # script_contentが指定された場合は、台本内容を更新
        if script_content:
            script_data['script_content'] = script_content
        
        try:
            # 品質分析
            analysis_result = self.script_generator.analyze_script_quality(script_data)
            
            # 分析結果を保存
            script_data['analysis'] = analysis_result['analysis']
            script_data['passed'] = analysis_result['passed']
            # 動画時間パラメータを保存
            script_data['duration_minutes'] = duration_minutes
            logger.info(f"台本に動画時間を保存: {duration_minutes}分")
            
            scripts[chapter_index] = script_data
            
            # ファイルに保存
            with open(scripts_file, 'w', encoding='utf-8') as f:
                json.dump(scripts, f, ensure_ascii=False)
            
            return {
                "success": True,
                "passed": analysis_result['passed'],
                "analysis": analysis_result['analysis'],
                "chapter_index": chapter_index
            }, 200
        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            logger.error(f"台本分析エラー: {str(e)}")
            logger.error(f"トレースバック: {error_traceback}")
            return {"error": f"台本分析に失敗しました: {str(e)}", "traceback": error_traceback}, 500
            
    def submit_feedback(self) -> Tuple[Dict[str, Any], int]:
        """台本にフィードバックを送信するAPIエンドポイント処理
        
        Returns:
            APIレスポンスと状態コード
        """
        logger.info("======== 台本フィードバックAPIが呼び出されました ========")
        
        data = request.json
        if not data or 'chapter_index' not in data or 'feedback' not in data or 'is_approved' not in data:
            return {"error": "必須パラメータが不足しています"}, 400
        
        chapter_index = int(data['chapter_index'])  # 明示的に整数型に変換
        feedback_text = data['feedback']
        is_approved = data['is_approved']
        
        # 動画時間パラメータを取得（設定されていなければデフォルト3分）
        duration_minutes = int(data.get('duration_minutes', 3))
        
        logger.info(f"フィードバック受信: chapter_index={chapter_index}, is_approved={is_approved}, feedback長さ={len(feedback_text)}, duration_minutes={duration_minutes}")
        
        # セッションIDの確認
        if 'session_id' not in session:
            session['session_id'] = str(uuid.uuid4())
        session_id = session['session_id']
        
        # スクリプトデータをファイルから取得
        scripts_file = session.get('scripts_file')
        if not scripts_file or not os.path.exists(scripts_file):
            logger.error("スクリプトファイルが見つかりません")
            return {"error": "スクリプトデータが見つかりません"}, 404
        
        # スクリプトデータを読み込む
        with open(scripts_file, 'r', encoding='utf-8') as f:
            scripts = json.load(f)
        
        logger.info(f"ファイルから読み込んだスクリプト数: {len(scripts)}")
        
        # スクリプト配列を必要に応じて拡張
        while len(scripts) <= chapter_index:
            scripts.append(None)
            logger.info(f"スクリプト配列を拡張: 新しいサイズ={len(scripts)}")
        
        # スクリプトデータが存在しない場合のエラーチェック
        if scripts[chapter_index] is None:
            return {"error": f"章 {chapter_index} の台本データが見つかりません"}, 404
        
        script_data = scripts[chapter_index]
        
        try:
            # フィードバックの処理
            if is_approved:
                # 承認の場合
                script_data['status'] = "approved"
                logger.info(f"台本を承認しました: chapter_index={chapter_index}")
            else:
                # フィードバックの場合
                script_data['status'] = "rejected"
                if 'feedback' not in script_data:
                    script_data['feedback'] = []
                script_data['feedback'].append(feedback_text)
                logger.info(f"フィードバックを追加: chapter_index={chapter_index}, フィードバック数={len(script_data['feedback'])}")
                
                # 詳細なログ:改善前の状態
                logger.info(f"台本改善前の状態:")
                logger.info(f"  chapter_index: {chapter_index}")
                logger.info(f"  status: {script_data['status']}")
                logger.info(f"  script_content文字数: {len(script_data['script_content'])}")
                logger.info(f"  'improved_script'キー: {'存在する' if 'improved_script' in script_data else '存在しない'}")
                
                if 'improved_script' in script_data:
                    logger.info(f"  既存のimproved_script文字数: {len(script_data['improved_script'])}")
                    # 次の改善リクエストで問題になるかもしれないので削除しておく
                    del script_data['improved_script']
                    logger.info(f"  既存のimproved_scriptを削除しました")
                
                # フィードバックに基づいて台本を改善
                logger.info(f"台本改善処理を開始: フィードバック長さ={len(feedback_text)}")
                
                # 台本改善時に動画時間パラメータを渡すための処理
                # スクリプトデータに動画時間を設定（改善関数内で使用可能にする）
                script_data['duration_minutes'] = duration_minutes
                
                improved_script_content = self.script_generator.improve_script(script_data, feedback_text)
                logger.info(f"台本改善処理が完了: 結果タイプ={type(improved_script_content)}")
                
                # 改善結果が文字列か辞書かで処理を分岐
                if isinstance(improved_script_content, dict) and 'script_content' in improved_script_content:
                    # 辞書型の場合はscript_contentキーを使用
                    script_content = improved_script_content['script_content']
                elif isinstance(improved_script_content, str):
                    # 文字列型の場合はそのまま使用
                    script_content = improved_script_content
                else:
                    # 想定外の型の場合はエラーメッセージを設定
                    logger.error(f"予期しない台本改善結果の型: {type(improved_script_content)}")
                    # 安全策として元の台本を使用
                    script_content = script_data['script_content'] + "\n\n（フィードバックによる改善に失敗しました。手動で編集してください）"
                
                # 処理済みの台本をセット（最終サニタイズ処理を適用）
                # sanitize_scriptはモジュールレベルの関数なので上部でインポート済み
                sanitized_content = sanitize_script(script_content)
                script_data['improved_script'] = sanitized_content
            
            # 変更を保存
            scripts[chapter_index] = script_data
            
            # 変更内容のより詳細なログ出力
            logger.info(f"台本の更新内容: chapter_index={chapter_index}, status={script_data['status']}")
            if 'improved_script' in script_data:
                logger.info(f"台本の改善データあり: 文字数={len(script_data['improved_script'])}")
            else:
                logger.info(f"台本の改善データなし")
                
            if 'feedback' in script_data:
                logger.info(f"台本のフィードバック: {len(script_data['feedback'])}件")
            
            # ファイルに保存
            with open(scripts_file, 'w', encoding='utf-8') as f:
                json.dump(scripts, f, ensure_ascii=False)
            
            logger.info(f"台本をファイルに保存: chapter_index={chapter_index}, スクリプト総数={len(scripts)}")
            
            return {
                "success": True,
                "chapter_index": chapter_index,
                "is_approved": is_approved,
                "improved_script": script_data.get('improved_script', None) if not is_approved else None
            }, 200
        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            logger.error(f"フィードバック処理エラー: {str(e)}")
            logger.error(f"トレースバック: {error_traceback}")
            return {"error": f"フィードバック処理に失敗しました: {str(e)}", "traceback": error_traceback}, 500
            
    def apply_improvement(self) -> Tuple[Dict[str, Any], int]:
        """改善された台本を適用するAPIエンドポイント処理
        
        Returns:
            APIレスポンスと状態コード
        """
        logger.info("======== 台本改善適用APIが呼び出されました ========")
        
        data = request.json
        if not data or 'chapter_index' not in data:
            return {"error": "章のインデックスが指定されていません"}, 400
            
        chapter_index = int(data['chapter_index'])  # 明示的に整数型に変換
        # 動画時間パラメータを取得（設定されていなければデフォルト3分）
        duration_minutes = int(data.get('duration_minutes', 3))
        
        # セッションIDの確認
        if 'session_id' not in session:
            session['session_id'] = str(uuid.uuid4())
        session_id = session['session_id']
        
        # スクリプトデータをファイルから取得
        scripts_file = session.get('scripts_file')
        if not scripts_file or not os.path.exists(scripts_file):
            logger.error("スクリプトファイルが見つかりません")
            return {"error": "スクリプトデータが見つかりません"}, 404
        
        # スクリプトデータを読み込む
        with open(scripts_file, 'r', encoding='utf-8') as f:
            scripts = json.load(f)
        
        logger.info(f"apply_improvement: ファイルから読み込んだスクリプト数: {len(scripts)}")
        
        if chapter_index >= len(scripts) or scripts[chapter_index] is None:
            logger.error(f"指定された章の台本が見つかりません。chapter_index: {chapter_index}, スクリプト数: {len(scripts)}")
            return {"error": "指定された章の台本が見つかりません"}, 404
        
        script_data = scripts[chapter_index]
        logger.info(f"台本データのキー: {list(script_data.keys())}")
        
        # improved_scriptキーが存在するか確認
        if 'improved_script' not in script_data or not script_data['improved_script']:
            logger.error(f"改善された台本が見つかりません。chapter_index: {chapter_index}, script_data keys: {list(script_data.keys())}")
            
            # 実験的に改善された台本が無い場合は元の台本をそのまま適用
            logger.info("改善された台本がないため、status を review に変更します")
            script_data['status'] = "review"
            scripts[chapter_index] = script_data
            
            # ファイルに保存
            with open(scripts_file, 'w', encoding='utf-8') as f:
                json.dump(scripts, f, ensure_ascii=False)
            
            return {
                "success": True,
                "chapter_index": chapter_index,
                "script": script_data,
                "warning": "改善された台本はありませんでしたが、ステータスを更新しました"
            }, 200
        
        try:
            # 改善された台本を適用
            logger.info(f"改善された台本を適用します。長さ={len(script_data['improved_script'])}")
            script_data['script_content'] = script_data['improved_script']
            script_data['status'] = "completed"  # 「編集完了」ステータスに変更
            
            # 動画時間パラメータを保存
            script_data['duration_minutes'] = duration_minutes
            logger.info(f"台本に動画時間を保存: {duration_minutes}分")
            
            # 更新後は改善台本キーを削除
            del script_data['improved_script']
            
            # 安全のため、_original_contentも削除（フロントエンドで保存されている可能性がある）
            if '_original_content' in script_data:
                del script_data['_original_content']
                logger.info(f"台本更新後、_original_content キーを削除しました")
                
            logger.info(f"台本更新後、improved_script キーを削除しました")
            
            # 変更を保存
            scripts[chapter_index] = script_data
            
            # 詳細なデバッグ情報を出力
            logger.info(f"台本を改善版で更新します - 詳細状態:")
            logger.info(f"  chapter_index: {chapter_index}")
            logger.info(f"  更新後status: {script_data['status']}")
            logger.info(f"  script_content文字数: {len(script_data['script_content'])}")
            logger.info(f"  'improved_script'キーの削除: 完了")
            
            # ファイルに保存
            with open(scripts_file, 'w', encoding='utf-8') as f:
                json.dump(scripts, f, ensure_ascii=False)
                
            logger.info(f"台本を改善版で更新しました。chapter_index: {chapter_index}")
            
            return {
                "success": True,
                "chapter_index": chapter_index,
                "script": script_data
            }, 200
        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            logger.error(f"台本改善適用エラー: {str(e)}")
            logger.error(f"トレースバック: {error_traceback}")
            return {"error": f"台本改善の適用に失敗しました: {str(e)}", "traceback": error_traceback}, 500
            
    def get_all_scripts(self) -> Tuple[Dict[str, Any], int]:
        """すべての台本を取得するAPIエンドポイント処理
        
        Returns:
            APIレスポンスと状態コード
        """
        logger.info("======== 全台本取得APIが呼び出されました ========")
        
        # セッションIDの確認
        if 'session_id' not in session:
            return {
                "success": True,
                "scripts": []
            }, 200
        
        session_id = session['session_id']
        
        # スクリプトデータをファイルから取得
        scripts_file = session.get('scripts_file')
        if not scripts_file or not os.path.exists(scripts_file):
            return {
                "success": True,
                "scripts": []
            }, 200
        
        # スクリプトデータを読み込む
        with open(scripts_file, 'r', encoding='utf-8') as f:
            scripts = json.load(f)
        
        logger.info(f"全スクリプト取得: {len(scripts)}件")
        
        return {
            "success": True,
            "scripts": scripts
        }, 200