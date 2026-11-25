"""Подсчёт токенов токенизатором облачной модели (tiktoken, o200k_base) для расчёта стоимости.

Считает те же сообщения, что run.py отправляет в Ollama, и ответы модели из evals/raw/.
Ollama считает токены своим токенизатором (Qwen), у облачной модели счёт другой – поэтому считаем отдельно.

    pip install tiktoken && python evals/count_tokens.py
"""
import json
import statistics
import sys
from pathlib import Path

import tiktoken

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run

enc = tiktoken.get_encoding("o200k_base")
PER_MSG = 4  # служебные токены на каждое сообщение чата (оценка по документации OpenAI)


def tokens(msgs: list[dict]) -> int:
    return sum(len(enc.encode(m["content"])) + PER_MSG for m in msgs) + 3


out = {}
for name, few in (("zeroshot", False), ("fewshot", True), ("features", None)):
    ins = []
    for ex in run.data:
        if few is None:
            ins.append(tokens([{"role": "system", "content": run.SYSTEM_FEATURES},
                               {"role": "user", "content": json.dumps(run.features(ex), ensure_ascii=False)}]))
            continue
        msgs = [{"role": "system", "content": run.SYSTEM}]
        if few:
            for i, o in run.FEWSHOT:
                msgs += [{"role": "user", "content": json.dumps(i, ensure_ascii=False)},
                         {"role": "assistant", "content": json.dumps(o, ensure_ascii=False)}]
        msgs.append({"role": "user", "content": json.dumps(run.llm_input(ex), ensure_ascii=False)})
        ins.append(tokens(msgs))
    raw = run.HERE / "raw" / f"{name}.jsonl"
    outs = [len(enc.encode(json.loads(x)["raw"])) for x in raw.read_text(encoding="utf-8").splitlines() if x] \
        if raw.exists() else []
    out[name] = {"input_avg": round(statistics.mean(ins)), "input_max": max(ins),
                 "output_avg": round(statistics.mean(outs)) if outs else None, "output_max": max(outs) if outs else None}
print(json.dumps({"tokenizer": "tiktoken o200k_base", **out}, ensure_ascii=False, indent=1))
