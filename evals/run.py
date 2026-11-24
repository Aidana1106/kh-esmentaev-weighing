"""Прогон eval-набора «Проверка веса» (ЛЗ 12): 4 подхода, метрики, сырые ответы.

    python evals/run.py                         # все подходы (нужен Ollama на localhost:11434)
    python evals/run.py --only rules stats      # без модели

Подходы:
  rules     – базовое решение без ИИ: нетто > 1,5 × медианы машины за сезон или < 1/3 медианы
  stats     – статистика без ИИ: отклонение от сегодняшних рейсов этой машины > 15 %
              (если сегодня рейсов не было – правило rules)
  zeroshot  – локальная модель (Ollama), промпт без примеров
  fewshot   – та же модель, промпт с 4 примерами (не из eval-набора)

Сырые ответы: evals/raw/<подход>.jsonl, итог: evals/results.json.
"""
import argparse
import json
import os
import statistics
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OPTIONS = {"temperature": 0, "seed": 12, "num_ctx": 4096}

data = [json.loads(x) for x in (HERE / "dataset.jsonl").read_text(encoding="utf-8").splitlines() if x]
hist = [json.loads(x) for x in (HERE / "history.jsonl").read_text(encoding="utf-8").splitlines() if x]
MEDIAN = {v: statistics.median(h["net"] for h in hist if h["vehicle"] == v) for v in {h["vehicle"] for h in hist}}


def rules(ex: dict) -> tuple[bool, str]:
    m = MEDIAN[ex["vehicle"]]
    if ex["net_kg"] > 1.5 * m:
        return True, f"нетто больше 1,5 медианы ({m:.0f})"
    if ex["net_kg"] < m / 3:
        return True, f"нетто меньше 1/3 медианы ({m:.0f})"
    return False, "в пределах правил"


def stats(ex: dict) -> tuple[bool, str]:
    prev = ex["prev_net_today_kg"]
    if not prev:
        return rules(ex)
    med = statistics.median(prev)
    dev = abs(ex["net_kg"] - med) / med
    return dev > 0.15, f"отклонение от сегодняшних рейсов {dev:.0%}"


SYSTEM = """Ты проверяешь рейс на зерновой весовой. Весовщик вручную вводит брутто с табло весов,
тара машины подставляется сама, нетто = брутто − тара. Частые ошибки ввода: переставил соседние цифры,
ошибся одной цифрой, ввёл нетто вместо брутто. Бывают и настоящие необычные рейсы: последний неполный
рейс с поля или перегруженный прицеп.
Определи, похоже ли введённое значение на ОШИБКУ ВВОДА. Ответь только JSON:
{"suspicious": true или false, "reason": "коротко по-русски"}"""

FEWSHOT = [
    ({"vehicle": "МТЗ 82", "tare_kg": 5800, "entered_gross_kg": 18900, "net_kg": 13100,
      "prev_net_today_kg": [12800, 13600, 13300], "season_median_net_kg": 14300},
     {"suspicious": False, "reason": "нетто как у сегодняшних рейсов"}),
    ({"vehicle": "МТЗ 952", "tare_kg": 6200, "entered_gross_kg": 81200, "net_kg": 75000,
      "prev_net_today_kg": [11800, 12400], "season_median_net_kg": 14400},
     {"suspicious": True, "reason": "нетто в 6 раз больше обычного – похоже на перестановку цифр 18200→81200"}),
    ({"vehicle": "МТЗ 80", "tare_kg": 5700, "entered_gross_kg": 15200, "net_kg": 9500,
      "prev_net_today_kg": [15100, 15800, 14900], "season_median_net_kg": 14500},
     {"suspicious": True, "reason": "нетто на 6 т меньше сегодняшних рейсов – возможно, введено нетто вместо брутто"}),
    ({"vehicle": "МТЗ 1025", "tare_kg": 6400, "entered_gross_kg": 11600, "net_kg": 5200,
      "prev_net_today_kg": [12900, 13200, 12600], "season_median_net_kg": 14600},
     {"suspicious": False, "reason": "сильно меньше обычного, но ровно на величину неполной загрузки – похоже на последний рейс с поля; брутто не похоже на нетто"}),
]


def llm_input(ex: dict) -> dict:
    return {k: ex[k] for k in ("vehicle", "tare_kg", "entered_gross_kg", "net_kg", "prev_net_today_kg")} | {
        "season_median_net_kg": round(MEDIAN[ex["vehicle"]])}


def ask(ex: dict, fewshot: bool) -> tuple[bool, str, dict]:
    msgs = [{"role": "system", "content": SYSTEM}]
    if fewshot:
        for inp, out in FEWSHOT:
            msgs += [{"role": "user", "content": json.dumps(inp, ensure_ascii=False)},
                     {"role": "assistant", "content": json.dumps(out, ensure_ascii=False)}]
    msgs.append({"role": "user", "content": json.dumps(llm_input(ex), ensure_ascii=False)})
    r = requests.post(f"{OLLAMA}/api/chat", timeout=600, json={
        "model": MODEL, "messages": msgs, "stream": False, "format": "json", "options": OPTIONS})
    r.raise_for_status()
    j = r.json()
    raw = j["message"]["content"]
    try:
        ans = json.loads(raw)
        flag = bool(ans.get("suspicious"))
        reason = str(ans.get("reason", ""))
    except (json.JSONDecodeError, AttributeError):
        flag, reason = False, "НЕ JSON: " + raw[:200]
    meta = {"raw": raw, "prompt_tokens": j.get("prompt_eval_count"), "output_tokens": j.get("eval_count")}
    return flag, reason, meta


APPROACHES = {
    "rules": lambda ex: (*rules(ex), {}),
    "stats": lambda ex: (*stats(ex), {}),
    "zeroshot": lambda ex: ask(ex, False),
    "fewshot": lambda ex: ask(ex, True),
}


def metrics(rows: list[dict]) -> dict:
    tp = sum(r["pred"] and r["label"] == "ошибка" for r in rows)
    fp = sum(r["pred"] and r["label"] == "верно" for r in rows)
    fn = sum(not r["pred"] and r["label"] == "ошибка" for r in rows)
    tn = sum(not r["pred"] and r["label"] == "верно" for r in rows)
    p = tp / (tp + fp) if tp + fp else 0.0
    rc = tp / (tp + fn) if tp + fn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": round(p, 2), "recall": round(rc, 2),
            "f1": round(2 * p * rc / (p + rc), 2) if p + rc else 0.0,
            "false_alarm_rate": round(fp / (fp + tn), 2) if fp + tn else 0.0,
            "accuracy": round((tp + tn) / len(rows), 2)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=list(APPROACHES))
    a = ap.parse_args()
    (HERE / "raw").mkdir(exist_ok=True)
    results = {"model": MODEL, "options": OPTIONS, "date": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
               "dataset_size": len(data), "errors_in_dataset": sum(d["label"] == "ошибка" for d in data),
               "approaches": {}}
    for name in a.only:
        rows = []
        for ex in data:
            t = time.perf_counter()
            pred, reason, meta = APPROACHES[name](ex)
            ms = (time.perf_counter() - t) * 1000
            rows.append({"id": ex["id"], "vehicle": ex["vehicle"], "kind": ex["kind"], "label": ex["label"],
                         "pred": pred, "reason": reason, "ms": round(ms, 1), **meta})
        (HERE / "raw" / f"{name}.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        m = metrics(rows)
        m["ms_per_example"] = round(statistics.mean(r["ms"] for r in rows), 1)
        m["by_vehicle"] = {v: metrics([r for r in rows if r["vehicle"] == v]) for v in sorted(MEDIAN)}
        m["by_kind"] = {k: f"{sum(r['pred'] for r in rows if r['kind'] == k)}/{sum(r['kind'] == k for r in rows)}"
                        for k in sorted({r["kind"] for r in rows})}
        tok = [r for r in rows if r.get("prompt_tokens")]
        if tok:
            m["avg_prompt_tokens"] = round(statistics.mean(r["prompt_tokens"] for r in tok))
            m["avg_output_tokens"] = round(statistics.mean(r["output_tokens"] for r in tok))
        results["approaches"][name] = m
        print(f"{name:9s} P={m['precision']:.2f} R={m['recall']:.2f} F1={m['f1']:.2f} "
              f"ложные={m['false_alarm_rate']:.2f} {m['ms_per_example']} мс")
    (HERE / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
