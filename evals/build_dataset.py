"""Составление eval-набора для ИИ-функции «Проверка веса» (ЛЗ 12).

Набор составлен нами, а не моделью: каждый пример – это рейс, где мы ЗНАЕМ настоящий вес,
и в часть примеров намеренно вносим типичную ошибку ввода. Эталон (ошибка / нет) задан
при составлении примера. Система ещё не работает у заказчика, поэтому рейсы синтетические:
вес и тара – по тестовым данным проекта (тара МТЗ из 002_seed.sql, нетто 8–22 т как в
scripts/make_test_data.py). Через 2 недели эксплуатации набор пополняется реальными рейсами
из журнала исправлений trip_revisions.

В набор входят только значения, которые ПРОХОДЯТ текущую проверку формы
(0 < брутто ≤ 100 000, тара < брутто): ошибки вроде «потерян ноль» (2 150 вместо 21 500)
форма уже отсекает, функция нужна для правдоподобных ошибок.

    python evals/build_dataset.py   →  evals/dataset.jsonl, evals/history.jsonl
"""
import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent
VEHICLES = {"МТЗ 952": 6200, "МТЗ 82": 5800, "МТЗ 80": 5700, "МТЗ 1025": 6400}
FIELDS = ["100 гектар", "200 гектар", "Березка", "ВзрывПром", "Катлаван", "Навозка", "409",
          "Давыденовка", "Ракета", "Балгабай"]
rnd = random.Random(1225)


def r100(x: float) -> int:
    return int(round(x / 100) * 100)


def passes_form(gross: int, tare: int) -> bool:
    return 0 < gross <= 100_000 and tare < gross


def history() -> list[dict]:
    """Нормальные рейсы сезона (обучение статистической модели и медиана машины)."""
    rows = []
    for _ in range(400):
        v = rnd.choice(list(VEHICLES))
        base = rnd.uniform(9000, 20000)
        for _k in range(rnd.randint(2, 5)):
            rows.append({"vehicle": v, "net": r100(base * rnd.uniform(0.92, 1.08))})
    return rows


def day(base: float, n_prev: int) -> list[int]:
    return [r100(base * rnd.uniform(0.92, 1.08)) for _ in range(n_prev)]


def transpose(g: int) -> int:
    s = list(str(g))
    for _ in range(50):
        i = rnd.randrange(len(s) - 1)
        if s[i] != s[i + 1] and not (i == 0 and s[i + 1] == "0"):
            t = s[:]
            t[i], t[i + 1] = t[i + 1], t[i]
            return int("".join(t))
    return g


def wrong_digit(g: int) -> int:
    s = list(str(g))
    i = rnd.choice([0, 1]) if len(s) == 5 else 0
    d = int(s[i]) + rnd.choice([-1, 1, 2, -2])
    s[i] = str(min(9, max(1 if i == 0 else 0, d)))
    return int("".join(s))


def example(i: int, kind: str) -> dict:
    for _ in range(200):
        ex = _example(i, kind)
        if ex["label"] == "верно" or ex["entered_gross_kg"] != ex["true_gross_kg"]:
            return ex
    raise RuntimeError(kind)


def _example(i: int, kind: str) -> dict:
    v = rnd.choice(list(VEHICLES))
    tare = VEHICLES[v]
    base = rnd.uniform(9000, 20000)
    n_prev = 0 if kind == "correct_first_trip" else rnd.randint(2, 4)
    prev = day(base, n_prev)
    true_net = base * rnd.uniform(0.92, 1.08)
    if kind == "correct_partial":      # последний неполный рейс с поля – настоящий, но необычный
        true_net = base * rnd.uniform(0.35, 0.5)
    if kind == "correct_overload":     # перегрузили прицеп – настоящий, но тяжелее обычного
        true_net = base * rnd.uniform(1.2, 1.3)
    true_gross = tare + r100(true_net)
    entered = true_gross
    for _ in range(100):
        if kind == "err_transposition":
            entered = transpose(true_gross)
        elif kind == "err_wrong_digit":
            entered = wrong_digit(true_gross)
        elif kind == "err_net_as_gross":   # ввёл нетто с бумажки вместо показания весов
            entered = r100(true_net)
        if (kind.startswith("correct") or entered != true_gross) and passes_form(entered, tare):
            break
    is_error = kind.startswith("err")
    return {
        "id": f"w{i:02d}", "vehicle": v, "field": rnd.choice(FIELDS), "tare_kg": tare,
        "entered_gross_kg": entered, "net_kg": entered - tare,
        "prev_net_today_kg": prev,
        "label": "ошибка" if is_error else "верно", "kind": kind,
        "true_gross_kg": true_gross,
    }


def main() -> None:
    plan = (["correct"] * 18 + ["correct_first_trip"] * 2 + ["correct_partial"] * 2 + ["correct_overload"] * 2
            + ["err_transposition"] * 6 + ["err_wrong_digit"] * 5 + ["err_net_as_gross"] * 5)
    hist = history()
    data = [example(i + 1, k) for i, k in enumerate(plan)]
    rnd.shuffle(data)
    (OUT / "history.jsonl").write_text("\n".join(json.dumps(h, ensure_ascii=False) for h in hist) + "\n",
                                       encoding="utf-8")
    (OUT / "dataset.jsonl").write_text("\n".join(json.dumps(d, ensure_ascii=False) for d in data) + "\n",
                                       encoding="utf-8")
    print(f"dataset: {len(data)} примеров, ошибок {sum(d['label'] == 'ошибка' for d in data)}; history: {len(hist)}")


if __name__ == "__main__":
    main()
