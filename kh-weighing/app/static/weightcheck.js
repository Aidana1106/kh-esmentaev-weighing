// Проверка веса (ADR-003, задача #61). Считается на телефоне – работает без связи.
// Та же логика, что подход stats в evals/run.py (F1 0,71 на eval-наборе):
//   1) есть сегодняшние рейсы этой машины – предупреждаем, если нетто отличается от их медианы больше чем на 15 %;
//   2) сегодня рейсов нет – по медиане машины за сезон: больше 1,5 медианы или меньше 1/3;
//   3) нет ни того, ни другого – не предупреждаем.
// Возвращает null или объект с данными для текста предупреждения. Решение всегда за весовщиком.
(function (root) {
  "use strict";

  function median(xs) {
    const s = xs.slice().sort((a, b) => a - b);
    const m = Math.floor(s.length / 2);
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }

  function checkWeight({ net, todayNets = [], seasonMedian = null, thresholdPct = 15 }) {
    if (!(net > 0)) return null;
    if (todayNets.length) {
      const med = median(todayNets);
      const dev = (net - med) / med;
      if (Math.abs(dev) <= thresholdPct / 100) return null;
      return {
        basis: "today", net, deviationPct: Math.round(dev * 100),
        lo: Math.min(...todayNets), hi: Math.max(...todayNets), trips: todayNets.length,
      };
    }
    if (seasonMedian > 0 && (net > 1.5 * seasonMedian || net < seasonMedian / 3)) {
      return { basis: "season", net, deviationPct: Math.round((net / seasonMedian - 1) * 100), median: seasonMedian };
    }
    return null;
  }

  const api = { checkWeight, median };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.WeightCheck = api;
})(typeof window !== "undefined" ? window : globalThis);
