"""Deterministic word-only NeMo normalization. Raw JSON is never edited."""
import math

SEGMENTER_VERSION = 1


def normalize(raw, transcript_id):
    words = raw.get("words")
    if not isinstance(words, list) or not words:
        raise ValueError("ASR returned no timestamped words")
    segments = []
    previous = -1
    for index, word in enumerate(words):
        start, end = word.get("start"), word.get("end")
        if any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) for t in (start, end)):
            raise ValueError("Invalid word timestamps")
        if start < previous or start < 0 or end < start:
            raise ValueError("Unordered or invalid word timestamps")
        previous = start
        label = word.get("speaker")
        if label is None or isinstance(label, (dict, list, bool)) or not str(label):
            raise ValueError("Diarization missing: require speaker on every word")
        if not isinstance(word.get("word"), str):
            raise ValueError("Invalid word text")
        label = str(label)
        if not segments or segments[-1]["label"] != label or start - segments[-1]["end"] > 1.5 or end - segments[-1]["start"] > 20:
            segments.append(dict(id=f"{transcript_id}:s{len(segments):05d}", label=label, start=start, end=end, text="", word_count=0))
        segment = segments[-1]
        segment["end"] = max(end, segment["end"])
        segment["text"] = (segment["text"] + " " + word["word"]).strip()
        segment["word_count"] += 1
    return segments


def effective(segments, corrections):
    undone = {c["value"] for c in corrections if c["kind"] == "undo"}
    labels = {s["label"]: s["label"] for s in segments}
    names, overrides, texts = {}, {}, {}
    base_names = {s['label']: s.get('base_name') for s in segments}
    for c in corrections:
        if c["id"] in undone:
            continue
        kind, target, value = c["kind"], c["target"], c["value"]
        if kind == "merge":
            old, new = labels[target], labels[value]
            labels = {k: new if v == old else v for k, v in labels.items()}
            # Turns moved to the merged speaker earlier move with it, so later
            # renames and note owners apply to them too.
            overrides = {k: new if v == old else v for k, v in overrides.items()}
        elif kind == "rename":
            names[target] = value
        elif kind == "speaker":
            overrides[target] = value
        elif kind == "text":
            texts[target] = value
    result = []
    for s in segments:
        participant = overrides.get(s["id"], labels[s["label"]])
        result.append({**s, "participant": participant, "name": names.get(participant, base_names.get(participant)), "text": texts.get(s["id"], s["text"])})
    return result
