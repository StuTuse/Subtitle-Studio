"""在 Buzz 自带的 Python 环境里跑的 worker（openai-whisper .pt 权重）。

用法: python _buzz_worker.py <audio> <ckpt.pt|模型名> <language|auto> <word_ts 0/1>
输出: 一行 __JSON__{...}
"""

import json
import sys


def main() -> int:
    audio, ckpt = sys.argv[1], sys.argv[2]
    lang = sys.argv[3] if len(sys.argv) > 3 else "auto"
    wts = len(sys.argv) > 4 and sys.argv[4] == "1"

    import whisper  # 由 Buzz 自带环境提供

    try:
        import torch
        use_fp16 = torch.cuda.is_available()
    except Exception:
        use_fp16 = False

    def _log(seg):
        # 让上层能从 stdout 里读出进度（最后一段文本即可）
        try:
            print(seg.get("text", "").strip()[:60], flush=True)
        except Exception:
            pass

    model = whisper.load_model(ckpt)
    result = model.transcribe(
        audio,
        language=None if lang in ("", "auto") else lang,
        word_timestamps=wts,
        fp16=use_fp16,
        verbose=False,
        progress_callback=_log if hasattr(whisper.Whisper, "transcribe") else None,
    )
    segs = []
    for s in result.get("segments", []):
        if not (s.get("text") or "").strip():
            continue
        segs.append({"start": float(s["start"]), "end": float(s["end"]),
                     "text": s["text"].strip()})
    print("__JSON__" + json.dumps({"segments": segs,
                                   "language": result.get("language", "")},
                                  ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
