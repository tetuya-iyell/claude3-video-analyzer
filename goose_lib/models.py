"""
データモデル定義
"""

from typing import List, Optional, Dict
from pydantic import BaseModel


class ChapterScript:
    """チャプターごとの台本モデル"""
    
    def __init__(
        self,
        chapter_title: str,
        chapter_summary: str,
        script_content: str = "",
        status: str = "draft",
        feedback: Optional[List[str]] = None
    ):
        self.chapter_title = chapter_title
        self.chapter_summary = chapter_summary
        self.script_content = script_content
        self.status = status  # "draft", "review", "approved", "rejected"
        self.feedback = feedback or []
    
    @property
    def is_approved(self) -> bool:
        return self.status == "approved"
    
    @property
    def needs_revision(self) -> bool:
        return self.status == "rejected" or (self.status == "review" and len(self.feedback) > 0)
    
    def add_feedback(self, feedback_text: str) -> None:
        """フィードバックを追加"""
        if not self.feedback:
            self.feedback = []
        self.feedback.append(feedback_text)
        
    def approve(self) -> None:
        """台本を承認状態に変更"""
        self.status = "approved"
        
    def reject(self) -> None:
        """台本を却下状態に変更"""
        self.status = "rejected"
        
    def to_dict(self) -> Dict:
        """辞書形式に変換"""
        return {
            "chapter_title": self.chapter_title,
            "chapter_summary": self.chapter_summary,
            "script_content": self.script_content,
            "status": self.status,
            "feedback": self.feedback,
        }


class ScriptFeedback(BaseModel):
    """フィードバックモデル"""
    chapter_index: int
    feedback_text: str
    is_approved: bool