"""
Bedrock スクリプトジェネレーター用のAPI定義モジュール
"""

from flask import Blueprint
from .api_controller import APIController

def create_bedrock_scripts_blueprint(script_generator):
    """Bedrock スクリプトジェネレーター用のBlueprintを作成する
    
    Args:
        script_generator: ScriptGeneratorのインスタンス
        
    Returns:
        Flask Blueprint インスタンス
    """
    # Blueprintの作成
    bedrock_scripts_bp = Blueprint('bedrock_scripts', __name__, url_prefix='/api/bedrock-scripts')
    
    # APIコントローラーの作成
    api_controller = APIController(script_generator)
    
    # 各APIエンドポイントのルート定義
    @bedrock_scripts_bp.route('/analyze-chapters', methods=['POST'])
    def analyze_chapters():
        """章立て解析結果から各章を抽出するAPI"""
        response, status_code = api_controller.analyze_chapters()
        return response, status_code
    
    @bedrock_scripts_bp.route('/generate-script', methods=['POST'])
    def generate_script():
        """特定の章の台本を生成するAPI"""
        response, status_code = api_controller.generate_script()
        return response, status_code
    
    @bedrock_scripts_bp.route('/analyze-script', methods=['POST'])
    def analyze_script():
        """台本の品質を分析するAPI"""
        response, status_code = api_controller.analyze_script_quality()
        return response, status_code
    
    @bedrock_scripts_bp.route('/submit-feedback', methods=['POST'])
    def submit_feedback():
        """台本にフィードバックを送信するAPI"""
        response, status_code = api_controller.submit_feedback()
        return response, status_code
    
    @bedrock_scripts_bp.route('/apply-improvement', methods=['POST'])
    def apply_improvement():
        """改善された台本を適用するAPI"""
        response, status_code = api_controller.apply_improvement()
        return response, status_code
    
    @bedrock_scripts_bp.route('/get-all-scripts', methods=['GET'])
    def get_all_scripts():
        """すべての台本を取得するAPI"""
        response, status_code = api_controller.get_all_scripts()
        return response, status_code
    
    @bedrock_scripts_bp.route('/sync-with-dynamodb', methods=['POST'])
    def sync_with_dynamodb():
        """DynamoDBと同期するAPI"""
        response, status_code = api_controller.sync_with_dynamodb()
        return response, status_code
    
    return bedrock_scripts_bp