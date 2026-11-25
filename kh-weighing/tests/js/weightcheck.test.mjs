// Проверка веса (ADR-003, #61): node --test tests/js/
// Кроме простых случаев, гоняем ту же функцию по eval-набору из ЛЗ 12 и сверяем с подходом stats из evals/run.py.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { checkWeight, median } = require("../../app/static/weightcheck.js");
const EVALS = new URL("../../../evals/", import.meta.url);

test("обычный рейс – без предупреждения", () => {
  assert.equal(checkWeight({ net: 13600, todayNets: [13300, 14400, 13900] }), null);
});

test("нетто вместо брутто – предупреждение по сегодняшним рейсам", () => {
  const w = checkWeight({ net: 7600, todayNets: [13300, 14400] });
  assert.equal(w.basis, "today");
  assert.equal(w.lo, 13300);
  assert.equal(w.hi, 14400);
  assert.ok(w.deviationPct < -15);
});

test("ровно 15 % – ещё не предупреждаем", () => {
  assert.equal(checkWeight({ net: 11500, todayNets: [10000] }), null);
  assert.ok(checkWeight({ net: 11600, todayNets: [10000] }));
});

test("первый рейс дня – по медиане за сезон", () => {
  assert.equal(checkWeight({ net: 14000, todayNets: [], seasonMedian: 14300 }), null);
  assert.equal(checkWeight({ net: 75000, seasonMedian: 14300 }).basis, "season");
  assert.equal(checkWeight({ net: 4000, seasonMedian: 14300 }).basis, "season");
});

test("нет ни сегодняшних рейсов, ни медианы – молчим", () => {
  assert.equal(checkWeight({ net: 90000, todayNets: [], seasonMedian: null }), null);
});

test("медиана", () => {
  assert.equal(median([3, 1, 2]), 2);
  assert.equal(median([4, 1, 2, 3]), 2.5);
});

test("на eval-наборе ЛЗ 12 решения совпадают с подходом stats (F1 0,71)", { skip: !existsSync(EVALS) }, () => {
  const lines = (f) => readFileSync(new URL(f, EVALS), "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
  const hist = lines("history.jsonl");
  const seasonMedian = (v) => median(hist.filter((h) => h.vehicle === v).map((h) => h.net));
  const expected = Object.fromEntries(lines("raw/stats.jsonl").map((r) => [r.id, r.pred]));
  let tp = 0, fp = 0, fn = 0;
  for (const ex of lines("dataset.jsonl")) {
    const pred = checkWeight({ net: ex.net_kg, todayNets: ex.prev_net_today_kg, seasonMedian: seasonMedian(ex.vehicle) }) !== null;
    assert.equal(pred, expected[ex.id], ex.id);
    const err = ex.label === "ошибка";
    tp += pred && err; fp += pred && !err; fn += !pred && err;
  }
  const f1 = (2 * tp) / (2 * tp + fp + fn);
  assert.equal(Math.round(f1 * 100) / 100, 0.71);
});
