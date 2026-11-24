"""Еженедельный статус-отчёт PM из данных GitHub + черновик текста от локальной модели (ЛЗ 12).

Как работает:
  1. Факты (закрытые задачи, PR, CI, риски) собираются из GitHub API и считаются кодом – без модели.
  2. Модель (Ollama локально или бесплатный API) пишет только текст «итог / сделано / риски / вопросы»
     по этим фактам. Таблицы фактов в отчёте всегда от кода, поэтому их можно сверять.

Запуск:
  export GH_TOKEN=...                       # токен только на чтение, из переменной окружения
  python scripts/pm_report/status_report.py --sprint "Спринт 5" --start 2026-11-17 --end 2026-11-23
  python scripts/pm_report/status_report.py --last-week          # для запуска по расписанию
  --llm ollama (по умолчанию) | github | none                     # none – только факты, без модели

Переменные: GH_TOKEN, REPO, OLLAMA_URL (http://localhost:11434), OLLAMA_MODEL (qwen2.5:7b),
GITHUB_MODEL (openai/gpt-4o-mini), OUT_DIR (reports).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

REPO = os.environ.get("REPO", "Aidana1106/kh-esmentaev-weighing")
API = "https://api.github.com"
TZ = timezone(timedelta(hours=5))  # Костанай, UTC+5
HERE = Path(__file__).resolve().parent
AGE_RISK_DAYS = 14        # открытая задача старше 2 недель
PR_WAIT_RISK_HOURS = 48   # PR без ревью дольше 2 суток

TOKEN = os.environ.get("GH_TOKEN")
if not TOKEN:
    sys.exit("Нет GH_TOKEN. Токен берётся только из переменной окружения или секрета Actions.")
H = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json"}


def gh(path: str, **params) -> list | dict:
    """GET к GitHub API с постраничной загрузкой списков."""
    url, out, page = f"{API}/repos/{REPO}{path}", [], 1
    while True:
        r = requests.get(url, headers=H, params={"per_page": 100, "page": page, **params}, timeout=30)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and "workflow_runs" in data:
            data = data["workflow_runs"]
        if not isinstance(data, list):
            return data
        out += data
        if len(data) < 100:
            return out
        page += 1


def ts(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def d(x: datetime | None) -> str:
    return x.astimezone(TZ).strftime("%d.%m") if x else "—"


def labels(item: dict) -> list[str]:
    return [lb["name"] for lb in item.get("labels", [])]


def deadline_of(body: str | None) -> date | None:
    """Срок из описания задачи: «Срок: 24.11.2026» (так пишем действия postmortem)."""
    m = re.search(r"Срок:\**\s*(\d{2})\.(\d{2})\.(\d{4})", body or "")
    return date(int(m[3]), int(m[2]), int(m[1])) if m else None


def collect(sprint: str, start: date, end: date) -> dict:
    t0 = datetime.combine(start, datetime.min.time(), TZ)
    t1 = datetime.combine(end + timedelta(days=1), datetime.min.time(), TZ)  # конец дня end
    inside = lambda x: x is not None and t0 <= x < t1  # noqa: E731

    items = gh("/issues", state="all")
    issues = [i for i in items if "pull_request" not in i]
    prs = gh("/pulls", state="all")

    closed = [i for i in issues if inside(ts(i["closed_at"])) and i.get("state_reason") != "not_planned"]
    opened = [i for i in issues if inside(ts(i["created_at"]))]
    open_at_end = [i for i in issues if ts(i["created_at"]) < t1 and (not i["closed_at"] or ts(i["closed_at"]) >= t1)]
    merged = [p for p in prs if inside(ts(p["merged_at"]))]
    closed_unmerged = [p for p in prs if inside(ts(p["closed_at"])) and not p["merged_at"]]
    open_prs_end = [p for p in prs if ts(p["created_at"]) < t1 and (not p["closed_at"] or ts(p["closed_at"]) >= t1)]

    runs = [r for r in gh("/actions/runs", branch="main", event="push")
            if r["name"] == "CI" and inside(ts(r["run_started_at"]))]
    commits = gh("/commits", sha="main", since=t0.isoformat(), until=t1.isoformat())

    def pr_info(p: dict) -> dict:
        reviews = gh(f"/pulls/{p['number']}/reviews")
        approved = sum(1 for r in reviews if r["state"] == "APPROVED")
        hours = (ts(p["merged_at"] or p["closed_at"]) - ts(p["created_at"])).total_seconds() / 3600
        return {"number": p["number"], "title": p["title"], "approvals": approved,
                "hours_open": round(hours, 1), "merged": d(ts(p["merged_at"]))}

    risks = []
    for i in open_at_end:
        age = (t1 - ts(i["created_at"])).days
        lb = labels(i)
        dl = deadline_of(i.get("body"))
        reasons = []
        if any(x.startswith(("P1", "P2")) for x in lb):
            reasons.append("высокий приоритет (" + ", ".join(x for x in lb if x.startswith(("P1", "P2"))) + ")")
        if dl and dl <= end:
            reasons.append(f"срок {dl:%d.%m} прошёл")
        elif dl and dl <= end + timedelta(days=7):
            reasons.append(f"срок {dl:%d.%m} – на следующей неделе")
        if age > AGE_RISK_DAYS:
            reasons.append(f"открыта {age} дн.")
        if {"bug", "дефект"} & set(lb):
            reasons.append("открытый дефект")
        if not i.get("assignees"):
            reasons.append("нет исполнителя")
        if reasons:
            risks.append({"number": i["number"], "title": i["title"], "age_days": age, "labels": lb,
                          "deadline": dl.strftime("%d.%m.%Y") if dl else None, "reasons": reasons})
    for p in open_prs_end:
        wait = (t1 - ts(p["created_at"])).total_seconds() / 3600
        if wait > PR_WAIT_RISK_HOURS:
            risks.append({"number": p["number"], "title": p["title"], "age_days": round(wait / 24),
                          "labels": [], "deadline": None, "reasons": [f"PR ждёт ревью {round(wait / 24)} дн."]})
    risks.sort(key=lambda r: (-len(r["reasons"]), -r["age_days"]))

    lead = [(ts(i["closed_at"]) - ts(i["created_at"])).days for i in closed]
    return {
        "sprint": sprint,
        "period": f"{start:%d.%m.%Y} – {end:%d.%m.%Y}",
        "counts": {
            "issues_closed": len(closed),
            "issues_opened": len(opened),
            "issues_open_at_end": len(open_at_end),
            "prs_merged": len(merged),
            "prs_closed_without_merge": len(closed_unmerged),
            "commits_to_main": len(commits),
            "lead_time_days_avg": round(sum(lead) / len(lead), 1) if lead else None,
        },
        "ci": {
            "runs_on_main": len(runs),
            "success": sum(r["conclusion"] == "success" for r in runs),
            "failure": sum(r["conclusion"] == "failure" for r in runs),
        },
        "closed_issues": [{"number": i["number"], "title": i["title"], "labels": labels(i),
                           "closed": d(ts(i["closed_at"])),
                           "lead_time_days": (ts(i["closed_at"]) - ts(i["created_at"])).days} for i in closed],
        "opened_issues": [{"number": i["number"], "title": i["title"], "labels": labels(i)} for i in opened],
        "merged_prs": [pr_info(p) for p in merged],
        "prs_closed_without_merge": [{"number": p["number"], "title": p["title"]} for p in closed_unmerged],
        "risks": risks,
    }


def ask_llm(facts: dict, provider: str) -> tuple[str, float]:
    prompt = (HERE / "prompt.md").read_text(encoding="utf-8") + "\n" + json.dumps(facts, ensure_ascii=False, indent=1)
    started = time.time()
    if provider == "ollama":
        url = os.environ.get("OLLAMA_URL", "http://localhost:11434") + "/api/chat"
        body = {"model": os.environ.get("OLLAMA_MODEL", "qwen2.5:7b"), "stream": False,
                "options": {"temperature": 0.2, "seed": 12},
                "messages": [{"role": "user", "content": prompt}]}
        r = requests.post(url, json=body, timeout=900)
        r.raise_for_status()
        text = r.json()["message"]["content"]
    elif provider == "github":
        r = requests.post("https://models.github.ai/inference/chat/completions", timeout=300,
                          headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
                          json={"model": os.environ.get("GITHUB_MODEL", "openai/gpt-4o-mini"), "temperature": 0.2,
                                "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
    else:
        text = "_Текст модели не запрашивался (--llm none)._"
    return text.strip(), time.time() - started


def render(facts: dict, narrative: str, meta: dict) -> str:
    c, ci = facts["counts"], facts["ci"]
    lead = c["lead_time_days_avg"] if c["lead_time_days_avg"] is not None else "—"
    lines = [
        f"# Статус-отчёт: {facts['sprint']} ({facts['period']})",
        "",
        f"_Сгенерировано автоматически {meta['generated']}. Факты и таблицы – из GitHub API (код), "
        f"текст раздела «Черновик от модели» – {meta['model']}. Перед отправкой текст проверяет PM._",
        "",
        "## Черновик от модели",
        "",
        narrative,
        "",
        "## Факты (собраны кодом)",
        "",
        "| Показатель | Значение |",
        "|---|---|",
        f"| Закрыто задач | {c['issues_closed']} |",
        f"| Открыто новых задач | {c['issues_opened']} |",
        f"| Открытых задач на конец спринта | {c['issues_open_at_end']} |",
        f"| Влито PR | {c['prs_merged']} |",
        f"| PR закрыто без слияния | {c['prs_closed_without_merge']} |",
        f"| Коммитов в main | {c['commits_to_main']} |",
        f"| Среднее время задачи (создание → закрытие), дн. | {lead} |",
        f"| Прогонов CI на main (успешно / красных) | {ci['runs_on_main']} ({ci['success']} / {ci['failure']}) |",
        "",
        "### Закрытые задачи",
        "",
    ]
    lines += [f"- #{i['number']} {i['title']} – закрыта {i['closed']}, {i['lead_time_days']} дн."
              for i in facts["closed_issues"]] or ["- нет"]
    lines += ["", "### Влитые PR", ""]
    lines += [f"- #{p['number']} {p['title']} – влит {p['merged']}, "
              f"был открыт {p['hours_open']} ч, одобрений: {p['approvals']}"
              for p in facts["merged_prs"]] or ["- нет"]
    lines += ["", "### Новые задачи", ""]
    lines += [f"- #{i['number']} {i['title']}" for i in facts["opened_issues"]] or ["- нет"]
    lines += ["", "### Риски", "",
              "_Правила: приоритет P1/P2, срок, возраст > 14 дн., дефект, нет исполнителя, PR без ревью > 2 дн._", "",
              "| # | Задача | Возраст, дн. | Почему риск |", "|---|---|---|---|"]
    lines += [f"| #{r['number']} | {r['title']} | {r['age_days']} | {'; '.join(r['reasons'])} |"
              for r in facts["risks"]] or ["| – | рисков нет | | |"]
    lines += ["", f"_Время работы: сбор данных {meta['collect_s']} с, модель {meta['llm_s']} с._", ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sprint")
    ap.add_argument("--start", type=date.fromisoformat)
    ap.add_argument("--end", type=date.fromisoformat)
    ap.add_argument("--last-week", action="store_true", help="прошлая неделя пн–вс (для расписания)")
    ap.add_argument("--llm", default=os.environ.get("LLM", "ollama"), choices=["ollama", "github", "none"])
    a = ap.parse_args()
    if a.last_week:
        today = datetime.now(TZ).date()
        a.end = today - timedelta(days=today.weekday() + 1)
        a.start = a.end - timedelta(days=6)
        a.sprint = a.sprint or f"Неделя {a.start:%d.%m}–{a.end:%d.%m}"
    if not (a.sprint and a.start and a.end):
        ap.error("нужны --sprint, --start, --end или --last-week")

    t = time.time()
    facts = collect(a.sprint, a.start, a.end)
    collect_s = round(time.time() - t, 1)
    narrative, llm_s = ask_llm(facts, a.llm)
    model = {"ollama": f"Ollama {os.environ.get('OLLAMA_MODEL', 'qwen2.5:7b')} (локально)",
             "github": f"GitHub Models {os.environ.get('GITHUB_MODEL', 'openai/gpt-4o-mini')}",
             "none": "не использовалась"}[a.llm]
    meta = {"generated": datetime.now(TZ).strftime("%d.%m.%Y %H:%M"), "model": model,
            "collect_s": collect_s, "llm_s": round(llm_s, 1)}

    out = Path(os.environ.get("OUT_DIR", "reports"))
    out.mkdir(parents=True, exist_ok=True)
    slug = f"{a.start:%Y-%m-%d}_{a.end:%Y-%m-%d}"
    (out / f"facts_{slug}.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / f"report_{slug}.md").write_text(render(facts, narrative, meta), encoding="utf-8")
    print(f"{a.sprint}: готово за {collect_s + meta['llm_s']:.1f} с → {out}/report_{slug}.md")


if __name__ == "__main__":
    main()
