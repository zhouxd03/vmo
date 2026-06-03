"""Shot duration estimation from dialogue volume + emotion.

正常语速 = 5 字/秒。按情绪调整语速：急/怒/紧张等说得更快（秒数更短），
悲/沉/庄重等说得更慢（秒数更长）。无台词的镜按动作文字粗估一个可视时长。
最终时长钳制在 [MIN_SEC, MAX_SEC]，其中 MAX_SEC=15 为视频模型单条上限。
"""

BASE_RATE = 5.0   # 字/秒（正常语速）
MIN_SEC = 3
MAX_SEC = 15      # seed 视频模型单条最长 15 秒

_FAST = ("急", "快", "紧张", "慌", "惊", "怒", "愤", "激动", "争吵", "打斗",
         "兴奋", "催", "喊", "吼")
_SLOW = ("悲", "伤", "哀", "沉", "庄", "肃", "平静", "低落", "沉思", "深情",
         "哽咽", "缓", "温柔", "舒缓", "凝重")


def _rate_for_emotion(emotion: str) -> float:
    e = emotion or ""
    if any(k in e for k in _FAST):
        return BASE_RATE * 1.25   # 语速更快 → 秒数更短
    if any(k in e for k in _SLOW):
        return BASE_RATE * 0.7    # 语速更慢 → 秒数更长
    return BASE_RATE


def _speech_len(text: str) -> int:
    """Count spoken characters: CJK ideographs + latin word chars, skip punctuation/space."""
    n = 0
    for ch in (text or ""):
        if "\u4e00" <= ch <= "\u9fff" or ch.isalnum():
            n += 1
    return n


def estimate_speech_seconds(dialogue: str, emotion: str = "") -> float:
    n = _speech_len(dialogue)
    if n <= 0:
        return 0.0
    return n / _rate_for_emotion(emotion)


def estimate_shot_seconds(dialogue: str, action: str = "", emotion: str = "") -> int:
    """Integer shot duration (s), clamped to [MIN_SEC, MAX_SEC]."""
    sec = estimate_speech_seconds(dialogue, emotion)
    if sec <= 0:
        # 无台词镜：按动作描述长度粗估可视时长，给个中性默认。
        sec = max(float(MIN_SEC), min(8.0, _speech_len(action) / 12.0 or MIN_SEC))
    return int(round(max(float(MIN_SEC), min(float(MAX_SEC), sec))))
