// Static game metadata shared by every module. Pure data — no DOM access.

export const RESOURCES = ["wood", "clay", "grain", "wool", "ore"];

export const RES = {
  wood: { ru: "Дерево", gen: "дерева", var: "--r-wood" },
  clay: { ru: "Кирпич", gen: "кирпича", var: "--r-clay" },
  grain: { ru: "Зерно", gen: "зерна", var: "--r-grain" },
  wool: { ru: "Шерсть", gen: "шерсти", var: "--r-wool" },
  ore: { ru: "Руда", gen: "руды", var: "--r-ore" },
};

export const TERRAIN = {
  forest: { ru: "Лес", res: "wood", var: "--t-forest" },
  hills: { ru: "Холмы", res: "clay", var: "--t-hills" },
  fields: { ru: "Поля", res: "grain", var: "--t-fields" },
  pasture: { ru: "Пастбища", res: "wool", var: "--t-pasture" },
  mountains: { ru: "Горы", res: "ore", var: "--t-mountains" },
  wasteland: { ru: "Пустошь", res: null, var: "--t-wasteland" },
  ruins: { ru: "Руины", res: null, var: "--t-ruins" },
  unknown: { ru: "Неизведанно", res: null, var: "--t-unknown" },
};

export const COSTS = {
  build_route: { wood: 1, clay: 1 },
  build_outpost: { wood: 1, clay: 1, grain: 1, wool: 1 },
  build_metropolis: { grain: 2, ore: 3 },
  buy_charter: { grain: 1, wool: 1, ore: 1 },
};

// dice pips by number token (probability dots)
export const PIPS = { 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1 };

export const CHARTERS = {
  vanguard: { ru: "Страж", icon: "sword", text: "Переместите рейдера: яркий контур — там есть чьи-то постройки и украдите карту. Три стража дают «Великую стражу» (+2 ПО)." },
  triumph: { ru: "Триумф", icon: "star", text: "Скрытое победное очко. Раскрывается само, когда оно приносит победу." },
  expansion_act: { ru: "Закон экспансии", icon: "route", text: "Постройте два тракта бесплатно." },
  abundance: { ru: "Рог изобилия", icon: "horn", text: "Возьмите из банка любые два ресурса." },
  monopoly: { ru: "Монополия", icon: "coins", text: "Назовите ресурс — все отдадут вам все карты этого вида." },
  pioneer: { ru: "Первопроходец", icon: "compass", text: "Раскройте любой неизведанный сектор." },
  raider_hunter: { ru: "Охотник на рейдеров", icon: "hunt", text: "Отправьте рейдера обратно в пустошь." },
};

export const BOTS = {
  c3po: { name: "C3PO", role: "Стратег", blurb: "Взвешивает каждый ход и редко рискует.", hue: 205 },
  r2d2: { name: "R2D2", role: "Торговец", blurb: "Всегда с предложением, любит порты и банк.", hue: 150 },
  t800: { name: "T-800", role: "Агрессор", blurb: "Давит рейдером и картами на лидера.", hue: 8 },
  walle: { name: "Валл-И", role: "Строитель", blurb: "Дороги и поселения повсюду, чистит скверну.", hue: 38 },
};

export const DIFFICULTY = [
  ["easy", "Лёгкий"], ["normal", "Обычный"], ["hard", "Сложный"], ["tournament", "Турнирный"], ["skynet", "Скайнет"],
];

export const PLAYER_SHAPES = ["circle", "square", "diamond", "triangle"];

export const PHASE_HINT = {
  lobby: "Ожидание игроков",
  setup: "Расстановка: поставьте поселение",
  setup_route: "Расстановка: проложите тракт",
  roll: "Бросьте кубики",
  main: "Ваш ход",
  raider_move: "Переместите рейдера",
  raider_steal: "Выберите, у кого украсть",
  discard: "Сбросьте лишние карты",
  finished: "Партия окончена",
};

export const STORAGE_KEYS = { session: "ti.session", settings: "ti.settings", name: "ti.name" };

export const fmtCost = (cost) => RESOURCES.filter((r) => cost[r]).map((r) => `${cost[r]} ${RES[r].gen}`).join(", ");
export const plural = (n, one, few, many) => {
  const a = Math.abs(n) % 100, b = a % 10;
  return a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many;
};
