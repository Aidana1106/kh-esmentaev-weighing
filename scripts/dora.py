"""Сбор метрик DORA по истории нашего репозитория (ЛЗ 10).

Запуск (токен только из переменной окружения, в коде его нет):
    pip install -r scripts/requirements.txt
    export GH_TOKEN=...            # Windows PowerShell: $env:GH_TOKEN="..."
    python scripts/dora.py

Результат:
    docs/dora/metrics.csv   – все изменения main с датами
    docs/dora/summary.csv   – четыре метрики
    docs/dora/dashboard.png – дашборд

Определения метрик – в docs/dora.md.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402
import pandas as pd  # noqa: E402
import requests  # noqa: E402

REPO = os.environ.get("REPO", "Aidana1106/kh-esmentaev-weighing")
WORKFLOW = "ci.yml"
API = "https://api.github.com"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "dora"
INCIDENTS = ROOT / "docs" / "incidents.md"
LOCAL_TZ = "Asia/Qyzylorda"  # время Костаная, UTC+5

token = os.environ.get("GH_TOKEN")
if not token:
    sys.exit("Нет переменной окружения GH_TOKEN (fine-grained токен с доступом на чтение к репозиторию).")
H = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def get_all(url: str, key: str | None = None, **params) -> list[dict]:
    """GET с постраничной загрузкой."""
    items, page = [], 1
    while True:
        r = requests.get(url, headers=H, params={"per_page": 100, "page": page, **params}, timeout=30)
        r.raise_for_status()
        data = r.json()
        chunk = data[key] if key else data
        items += chunk
        if len(chunk) < 100:
            return items
        page += 1


def ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s).tz_convert("UTC")


# 1. Прогоны CI на main = изменения, попавшие в main
runs = get_all(f"{API}/repos/{REPO}/actions/workflows/{WORKFLOW}/runs", "workflow_runs", branch="main", event="push")
runs = [r for r in runs if r["status"] == "completed" and r["conclusion"] in ("success", "failure")]
runs.sort(key=lambda r: r["run_started_at"])
if not runs:
    sys.exit("На main ещё нет прогонов CI.")

pr_first_commit: dict[int, pd.Timestamp] = {}


def change_of(run: dict) -> tuple[str, pd.Timestamp]:
    """Ключ изменения и время его первого коммита."""
    msg = run["head_commit"]["message"]
    m = re.match(r"Merge pull request #(\d+)", msg)
    if m:
        n = int(m.group(1))
        if n not in pr_first_commit:
            commits = get_all(f"{API}/repos/{REPO}/pulls/{n}/commits")
            pr_first_commit[n] = min(ts(c["commit"]["author"]["date"]) for c in commits)
        return f"PR #{n}", pr_first_commit[n]
    return f"коммит {run['head_sha'][:7]}", ts(run["head_commit"]["timestamp"])


rows = []
for r in runs:
    key, first_commit = change_of(r)
    rows.append({
        "change": key,
        "title": r["head_commit"]["message"].split("\n")[0],
        "first_commit": first_commit,
        "ci_started": ts(r["run_started_at"]),
        "ci_finished": ts(r["updated_at"]),
        "result": r["conclusion"],
        "run_url": r["html_url"],
    })
df = pd.DataFrame(rows)

# Один PR мог прогоняться на main дважды (перезапуск) – считаем изменение один раз, по первому прогону
changes = df.drop_duplicates("change", keep="first").reset_index(drop=True)

# Развёртывание = успешный прогон CI на main. Для каждого изменения – первое развёртывание после него.
deploys = df[df.result == "success"].sort_values("ci_finished")


def deployed_at(row) -> pd.Timestamp | None:
    later = deploys[deploys.ci_started >= row.ci_started]
    return later.ci_finished.iloc[0] if len(later) else None


changes["deployed"] = changes.apply(deployed_at, axis=1)
changes["lead_time_min"] = (changes.deployed - changes.first_commit).dt.total_seconds() / 60
changes["failed"] = changes.result == "failure"

# 2. Время восстановления – из журнала инцидентов (из API его не взять)
incidents = []
if INCIDENTS.exists():
    for line in INCIDENTS.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and re.fullmatch(r"INC-\d+", cells[0]):
            start = pd.Timestamp(cells[1], tz=LOCAL_TZ)
            end = pd.Timestamp(cells[2], tz=LOCAL_TZ)
            minutes = (end - start).total_seconds() / 60
            incidents.append({"id": cells[0], "start": start, "end": end, "minutes": minutes})
inc = pd.DataFrame(incidents)

# 3. Четыре метрики
first_day = df.ci_started.min().tz_convert(LOCAL_TZ).normalize()
now = pd.Timestamp(datetime.now(UTC)).tz_convert(LOCAL_TZ)
weeks = max((now - first_day) / timedelta(weeks=1), 1)
per_week = (
    deploys.assign(week=deploys.ci_finished.dt.tz_convert(LOCAL_TZ).dt.tz_localize(None).dt.to_period("W-SUN"))
    .groupby("week").size()
    .reindex(pd.period_range(first_day.tz_localize(None), now.tz_localize(None), freq="W-SUN"), fill_value=0)
)

summary = pd.DataFrame([
    ["Частота развёртываний, в неделю (среднее)", round(len(deploys) / weeks, 2)],
    ["Частота развёртываний, в неделю (медиана)", float(per_week.median())],
    ["Время от коммита до стенда, медиана, мин", round(changes.lead_time_min.median(), 1)],
    ["Время от коммита до стенда, p85, мин", round(changes.lead_time_min.quantile(0.85), 1)],
    ["Доля неудачных изменений, %", round(100 * changes.failed.mean(), 1)],
    ["Время восстановления, медиана, мин", round(inc.minutes.median(), 1) if len(inc) else None],
    ["Изменений в main", len(changes)],
    ["Развёртываний (зелёных прогонов main)", len(deploys)],
    ["Инцидентов в журнале", len(inc)],
], columns=["Метрика", "Значение"])

OUT.mkdir(parents=True, exist_ok=True)
loc = changes.copy()
for c in ("first_commit", "ci_started", "ci_finished", "deployed"):
    loc[c] = loc[c].dt.tz_convert(LOCAL_TZ).dt.strftime("%Y-%m-%d %H:%M")
loc.to_csv(OUT / "metrics.csv", index=False, encoding="utf-8-sig")
summary.to_csv(OUT / "summary.csv", index=False, encoding="utf-8-sig")
print(summary.to_string(index=False))

# 4. Дашборд
plt.rcParams["font.family"] = "DejaVu Sans"
fig, ax = plt.subplots(2, 2, figsize=(12, 7.5), dpi=150)
fig.suptitle(f"DORA-метрики {REPO} (данные GitHub API, {now:%d.%m.%Y})", fontsize=13)

a = ax[0, 0]
labels = [f"{p.start_time:%d.%m}" for p in per_week.index]
a.bar(labels, per_week.values, color="#1E9BD7")
a.set_title(f"Частота развёртываний: {summary.Значение[0]} в неделю")
a.set_ylabel("Зелёных прогонов main")
a.set_xlabel("Неделя, начиная с")

a = ax[0, 1]
lt = changes.dropna(subset=["lead_time_min"])
a.barh(lt.change, lt.lead_time_min, color="#2E7D32")
a.set_title(f"Время от коммита до стенда: медиана {summary.Значение[2]} мин")
a.set_xlabel("минут")
a.invert_yaxis()

a = ax[1, 0]
ok, bad = int((~changes.failed).sum()), int(changes.failed.sum())
a.bar(["Успешные", "Неудачные"], [ok, bad], color=["#2E7D32", "#C62828"])
a.set_title(f"Доля неудачных изменений: {summary.Значение[4]} %")
a.set_ylabel("Изменений в main")

a = ax[1, 1]
if len(inc):
    a.bar(inc.id, inc.minutes, color="#F9A825")
    a.set_title(f"Время восстановления: медиана {summary.Значение[5]} мин")
    a.set_ylabel("минут")
else:
    a.text(0.5, 0.5, "Инцидентов нет", ha="center", va="center")
    a.set_title("Время восстановления")

for a in (ax[0, 0], ax[1, 0]):
    a.yaxis.set_major_locator(MaxNLocator(integer=True))
for a in ax.flat:
    a.grid(axis="y", alpha=0.3)
fig.tight_layout()
fig.savefig(OUT / "dashboard.png")
print(f"Сохранено: {OUT / 'dashboard.png'}")
