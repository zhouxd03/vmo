"""Shot duration estimation from dialogue volume + emotion.

正常语速 = 5 字/秒。按情绪调整语速：急/怒/紧张等说得更快（秒数更短），
悲/沉/庄重等说得更慢（秒数更长）。无台词的镜按动作文字粗估一个可视时长。
最终时长钳制在 [MIN_SEC, MAX_SEC]，其中 MAX_SEC=15 为视频模型单条上限。
"""

BASE_RATE = 5.0       # 字/秒（对白正常语速）
NARRATION_RATE = 8.0  # 字/秒（纯画面/旁白：120字≈15秒）
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


def estimate_shot_seconds(dialogue: str, action: str = "", emotion: str = "",
                          target_seconds: float = 0.0) -> int:
    """Integer shot duration (s), clamped to [MIN_SEC, MAX_SEC].

    *target_seconds* (设置「单镜目标时长」) centers no-dialogue shots so the
    pacing follows the user's configured shot length instead of a fixed default.
    """
    sec = estimate_speech_seconds(dialogue, emotion)
    if sec <= 0:
        # 无台词镜：以「单镜目标时长」为中心；未配置时按动作描述长度粗估。
        if target_seconds and target_seconds > 0:
            sec = float(target_seconds)
        else:
            sec = max(float(MIN_SEC), min(8.0, _speech_len(action) / 12.0 or MIN_SEC))
    return int(round(max(float(MIN_SEC), min(float(MAX_SEC), sec))))


def dialogue_ratio(dialogue: str, action: str = "") -> float:
    """Share of a shot that is spoken dialogue (0..1). Drives字数折算：对白越多，
    单镜承载文字越少（对白 5字/秒 vs 画面 8字/秒）。"""
    d = _speech_len(dialogue)
    a = _speech_len(action)
    tot = d + a
    return (d / tot) if tot > 0 else 0.0


def target_chars(target_seconds: float, ratio: float = 0.0) -> int:
    """How many source chars a single shot of *target_seconds* should carry.
    Pure narration/visual packs ≈8字/秒; dialogue is spoken ≈5字/秒, so a more
    dialogue-heavy shot (*ratio*→1) holds fewer chars (避免对话过载)。"""
    ratio = max(0.0, min(1.0, ratio))
    rate = NARRATION_RATE - (NARRATION_RATE - BASE_RATE) * ratio
    return int(round(max(float(MIN_SEC), target_seconds) * rate))
