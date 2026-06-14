import sys
sys.path.insert(0, r"c:\Users\shani\OneDrive\Desktop\yanflix (1)\yanflix")
from director import apply_emotion_tags

test = [
    {"speaker": "SPEAKER_00", "translated_text": "I will never forgive you for what you have done."},
    {"speaker": "SPEAKER_01", "translated_text": "Please, you have to believe me, I had no choice."},
    {"speaker": "SPEAKER_00", "translated_text": "There is always a choice."},
]

result = apply_emotion_tags(test)
for seg in result:
    print(seg.get("emotion_line"))
