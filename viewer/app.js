const $ = (id) => document.getElementById(id);
let latest = null;
let selectedKey = null;
let lastCapture = null;
const translations = new Map();
const translationErrors = new Map();
const pendingTranslations = new Map();

function statusName(status) {
  return {matched: "已匹配", tentative: "待确认", unknown: "待确认", empty: "空位", occluded: "被遮挡"}[status] || status;
}

function displayText(value, language = "zh") {
  const energy = {grass: "草", fire: "火", water: "水", lightning: "雷", psychic: "超",
    fighting: "斗", darkness: "恶", metal: "钢", colorless: "无色", dragon: "龙"};
  return String(value || "")
    .replace(/<sprite name="([^"]+)"[^>]*>/g, (_, name) => `[${language === "en" ? name : (energy[name] || name)}]`)
    .replace(/<br\s*\/?\s*>/gi, "\n")
    .replace(/<[^>]+>/g, "");
}

function appendText(parent, className, value) {
  const element = document.createElement("div");
  element.className = className;
  element.textContent = value;
  parent.append(element);
  return element;
}

function translationKey(cardId, field, index) {
  return `${cardId}:${field}:${index ?? ""}`;
}

function updateTranslationWidget(widget, key) {
  const result = translations.get(key);
  widget.classList.toggle("error", translationErrors.has(key));
  widget.textContent = result
    ? `${result.source === "local" ? "" : "机器翻译 · "}${displayText(result.translation)}`
    : (translationErrors.get(key) || "翻译中……");
}

function syncTranslationWidgets(key) {
  document.querySelectorAll("[data-translation-key]").forEach((widget) => {
    if (widget.dataset.translationKey === key) updateTranslationWidget(widget, key);
  });
}

function appendBilingual(parent, chinese, english, cardId, field, index = null,
                         chineseClass = "move-text", englishClass = "english-rule") {
  if (chinese) {
    appendText(parent, chineseClass, displayText(chinese));
  } else if (english) {
    const key = `${translationKey(cardId, field, index)}:${english}`;
    const widget = appendText(parent, `${chineseClass} translation-output`, "翻译中……");
    widget.dataset.translationKey = key;
    updateTranslationWidget(widget, key);
    if (!translations.has(key) && !pendingTranslations.has(key) && !translationErrors.has(key)) {
      const request = fetch("/api/translate", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({card_id: cardId, field, index, expected_source: english}),
      }).then(async (response) => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        return data;
      });
      pendingTranslations.set(key, request);
      request.then((data) => translations.set(key, data))
        .catch((error) => translationErrors.set(key, `中文翻译暂不可用：${error.message}`))
        .finally(() => { pendingTranslations.delete(key); syncTranslationWidgets(key); });
    }
  }
  if (english) appendText(parent, englishClass, displayText(english, "en"));
}

function setCardView(view) {
  const chinese = view === "zh";
  $("localized-card").classList.toggle("is-hidden", !chinese);
  $("large-card-image").classList.toggle("is-hidden", chinese);
  $("view-zh").classList.toggle("active", chinese);
  $("view-original").classList.toggle("active", !chinese);
  $("large-caption").textContent = chinese
    ? "依据本地卡图与资料生成的中英双语阅读版"
    : "游戏本地缓存原图，卡面文字为英文";
}

function renderLocalizedCard(card, slot) {
  const container = $("localized-card");
  container.replaceChildren();
  const top = document.createElement("div");
  top.className = "zh-card-top";
  appendText(top, "zh-card-edition", "PTCGLens · 中英双语阅读版");
  const row = document.createElement("div");
  row.className = "zh-card-name-row";
  const names = document.createElement("div");
  appendBilingual(names, card.name_zh, card.name_en || slot.name_en,
    slot.card_id, "card_name", null, "zh-card-name", "zh-english");
  row.append(names);
  if (card.hp) appendText(row, "zh-card-hp", `${card.hp} HP`);
  top.append(row);
  container.append(top);
  const art = document.createElement("img");
  art.className = "zh-card-art";
  art.alt = `${card.name_zh || card.name_en || slot.card_id} 插画`;
  art.onerror = () => { art.onerror = null; art.src = `/api/card-image/${encodeURIComponent(slot.card_id)}`; };
  art.src = `/api/card-art/${encodeURIComponent(slot.card_id)}`;
  container.append(art);
  const body = document.createElement("div");
  body.className = "zh-card-body";
  appendText(body, "zh-card-label", `${card.set_code || ""} ${card.number || ""}`);
  if (card.card_text_en || card.card_text_zh) {
    const section = document.createElement("div");
    section.className = "zh-move";
    appendText(section, "zh-move-kind", "卡牌效果");
    appendBilingual(section, card.card_text_zh, card.card_text_en, slot.card_id,
      "card_text", null, "zh-move-text", "zh-english");
    body.append(section);
  }
  for (const [index, attack] of (card.attacks || []).entries()) {
    const section = document.createElement("div");
    section.className = "zh-move";
    appendText(section, "zh-move-kind", `${attack.kind === "ability" ? "特性" : "招式"}${attack.damage ? ` · ${attack.damage}` : ""}`);
    appendBilingual(section, attack.name_zh, attack.name_en, slot.card_id,
      "attack_name", index, "zh-move-head", "zh-english");
    appendBilingual(section, attack.text_zh, attack.text_en, slot.card_id,
      "attack_text", index, "zh-move-text", "zh-english");
    body.append(section);
  }
  appendText(body, "zh-card-foot", "此为便于阅读的重排版本，不是官方中文印刷卡面。");
  container.append(body);
}

$("view-zh").addEventListener("click", () => setCardView("zh"));
$("view-original").addEventListener("click", () => setCardView("original"));

function openLargeCard(key) {
  selectSlot(key);
  const slot = latest?.slots.find((item) => item.key === key);
  if (!slot?.card_id) return;
  const card = latest.cards[slot.card_id] || {};
  const dialog = $("card-dialog");
  $("large-title").textContent = card.name_zh || slot.name_zh || card.name_en || slot.name_en || slot.card_id;
  const image = $("large-card-image");
  image.alt = `${card.name_zh || card.name_en || slot.name_en || slot.card_id} 原卡大图`;
  image.onerror = () => {
    image.onerror = null;
    image.src = `/api/thumb/${encodeURIComponent(slot.card_id)}`;
    $("large-caption").textContent = "本机仅缓存了缩略图";
  };
  image.src = `/api/card-image/${encodeURIComponent(slot.card_id)}`;
  renderLocalizedCard(card, slot);
  setCardView("zh");
  const info = $("large-card-info");
  info.replaceChildren();
  appendBilingual(info, card.name_zh, card.name_en || slot.name_en, slot.card_id,
    "card_name", null, "card-name", "english-name");
  const meta = document.createElement("div");
  meta.className = "meta";
  for (const item of [card.hp ? `${card.hp} HP` : null,
    card.set_code && card.number ? `${card.set_code} · ${card.number}` : null]) {
    if (item) appendText(meta, "", item);
  }
  info.append(meta);
  appendText(info, "translation-note", "中英逐项对照；缺失中文时自动翻译，机器译文仅供参考。");
  if (card.card_text_zh || card.card_text_en) {
    const effect = document.createElement("section");
    effect.className = "move";
    appendText(effect, "move-kind", "卡牌效果");
    appendBilingual(effect, card.card_text_zh, card.card_text_en, slot.card_id, "card_text");
    info.append(effect);
  }
  for (const [index, attack] of (card.attacks || []).entries()) {
    const section = document.createElement("section");
    section.className = "move";
    appendText(section, "move-kind", `${attack.kind === "ability" ? "特性" : "招式"}${attack.damage ? ` · ${attack.damage}` : ""}`);
    appendBilingual(section, attack.name_zh, attack.name_en, slot.card_id, "attack_name", index, "move-title", "move-en");
    appendBilingual(section, attack.text_zh, attack.text_en, slot.card_id, "attack_text", index);
    info.append(section);
  }
  if (!dialog.open) dialog.showModal();
}

$("close-card").addEventListener("click", () => $("card-dialog").close());
$("card-dialog").addEventListener("click", (event) => {
  if (event.target === $("card-dialog")) $("card-dialog").close();
});

function selectAndOpen(key) {
  openLargeCard(key);
}

function selectSlot(key) {
  selectedKey = key;
  if (!latest) return;
  const slot = latest.slots.find((item) => item.key === key);
  if (!slot) return;
  const panel = $("selection");
  panel.replaceChildren();
  const heading = document.createElement("strong");
  heading.textContent = `${slot.label} · ${statusName(slot.status)}`;
  panel.append(heading);
  if (slot.card_id) {
    const card = latest.cards[slot.card_id] || {};
    const names = document.createElement("div");
    names.className = "detail";
    appendBilingual(names, card.name_zh || slot.name_zh, card.name_en || slot.name_en,
      slot.card_id, "card_name", null, "detail", "detail secondary");
    if (card.hp) appendText(names, "detail", `HP ${card.hp}`);
    panel.append(names);
    if (card.set_code) {
      const code = document.createElement("div");
      code.className = "detail secondary";
      code.textContent = `${card.set_code} ${card.number || ""} · ${slot.card_id}`;
      panel.append(code);
    }
    appendBilingual(panel, card.card_text_zh, card.card_text_en, slot.card_id,
      "card_text", null, "detail", "detail secondary");
    for (const [index, attack] of (card.attacks || []).entries()) {
      appendText(panel, "detail", `${attack.kind === "ability" ? "特性" : "招式"}${attack.damage ? ` · ${attack.damage}` : ""}`);
      appendBilingual(panel, attack.name_zh, attack.name_en, slot.card_id,
        "attack_name", index, "detail", "detail secondary");
      appendBilingual(panel, attack.text_zh, attack.text_en, slot.card_id,
        "attack_text", index, "detail", "detail secondary");
    }
  } else {
    const note = document.createElement("div");
    note.className = "detail secondary";
    note.textContent = slot.status === "occluded" ? "卡图被放大预览挡住，当前帧无法可靠确认。"
      : slot.status === "tentative" ? "候选结果太接近，暂不打开卡牌大图；可在游戏中打开该卡特写再识别。"
      : "当前帧没有可靠的卡牌身份。";
    panel.append(note);
    if (slot.status === "tentative" && slot.candidates?.length) {
      appendText(panel, "detail secondary", `候选：${slot.candidates.map((item) => item.name_en).join("、")}`);
    }
  }
  document.querySelectorAll(".box,.card-row").forEach((element) =>
    element.classList.toggle("selected", element.dataset.key === key));
}

function render(data) {
  latest = data;
  const otherScene = data.scene === "other";
  const hasPreview = data.slots.some((slot) => slot.key === "preview" && slot.card_id);
  const valid = data.slots.filter((slot) => slot.status !== "empty");
  const confirmed = data.slots.filter((slot) => slot.status === "matched").length;
  $("stats").textContent = otherScene
    ? (hasPreview ? `已识别卡牌特写 · ${data.reference_cards} 张缓存卡图` : "当前不是对局界面") :
    `${confirmed} 已匹配 · ${data.slots.length} 卡位 · ${data.reference_cards} 张缓存卡图`;
  const image = $("screenshot");
  image.src = `/api/screenshot?t=${encodeURIComponent(data.captured_at)}`;
  $("screen-message").textContent = otherScene
    ? (hasPreview ? "非对局界面 · 已识别卡牌特写" : "非对局界面 · 等待卡牌特写") : "";
  $("screen-message").classList.toggle("hidden", !otherScene);
  $("screen-message").classList.toggle("other", otherScene);
  const boxes = $("boxes");
  boxes.replaceChildren();
  const [, , width, height] = data.window_client_bbox || [0, 0, 1920, 1080];
  const screenWidth = data.window_client_bbox ? data.window_client_bbox[2] - data.window_client_bbox[0] : width;
  const screenHeight = data.window_client_bbox ? data.window_client_bbox[3] - data.window_client_bbox[1] : height;
  data.slots.forEach((slot, index) => {
    if (slot.status === "empty") return;
    const [x0, y0, x1, y1] = slot.box;
    const box = document.createElement("button");
    box.className = `box ${slot.status}`;
    box.dataset.key = slot.key;
    box.title = `${slot.label}: ${slot.name_zh || slot.name_en || statusName(slot.status)}`;
    box.style.left = `${x0 / screenWidth * 100}%`;
    box.style.top = `${y0 / screenHeight * 100}%`;
    box.style.width = `${(x1 - x0) / screenWidth * 100}%`;
    box.style.height = `${(y1 - y0) / screenHeight * 100}%`;
    const badge = document.createElement("span");
    badge.className = "number";
    badge.textContent = String(index + 1).padStart(2, "0");
    box.append(badge);
    box.addEventListener("click", () => selectAndOpen(slot.key));
    boxes.append(box);
  });
  const groups = [
    ["对手场上", (slot) => slot.key.startsWith("opponent_")],
    ["我方场上", (slot) => slot.key.startsWith("own_") || slot.key === "stadium"],
    ["手牌", (slot) => slot.key.startsWith("hand_")],
    ["卡牌特写", (slot) => slot.key === "preview"],
  ];
  const list = $("card-list");
  list.replaceChildren();
  for (const [title, filter] of groups) {
    const members = valid.filter(filter);
    if (!members.length) continue;
    const heading = document.createElement("div");
    heading.className = "section-title";
    heading.textContent = `${title} · ${members.length}`;
    list.append(heading);
    for (const slot of members) {
      const row = document.createElement("button");
      row.className = `card-row ${slot.status}`;
      row.dataset.key = slot.key;
      if (slot.card_id) {
        const thumb = document.createElement("img");
        thumb.className = "thumb";
        thumb.src = `/api/thumb/${encodeURIComponent(slot.card_id)}`;
        thumb.alt = "";
        row.append(thumb);
      }
      const copy = document.createElement("span");
      copy.className = "card-copy";
      const label = document.createElement("span");
      label.className = "label";
      label.textContent = slot.label;
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = slot.name_zh || slot.name_en || statusName(slot.status);
      const sub = document.createElement("span");
      sub.className = "sub";
      sub.textContent = slot.name_zh ? slot.name_en : statusName(slot.status);
      copy.append(label, name, sub);
      row.append(copy);
      row.addEventListener("click", () => selectAndOpen(slot.key));
      list.append(row);
    }
  }
  if (selectedKey && data.slots.some((slot) => slot.key === selectedKey)) selectSlot(selectedKey);
  else if (valid.length) selectSlot(valid[0].key);
  else {
    selectedKey = null;
    $("selection").textContent = otherScene ? "打开卡牌特写后会自动显示卡牌资料。" : "当前没有可确认的卡牌。";
  }
}

function refreshClock() {
  if (!lastCapture) return;
  const age = Math.max(0, Math.floor(Date.now() / 1000 - lastCapture));
  $("pulse").className = `pulse ${age <= 4 ? "live" : "stale"}`;
  $("connection").textContent = age <= 4 ? "收到游戏画面" : "等待游戏新画面";
  $("updated").textContent = `更新于 ${age} 秒前`;
}

async function poll() {
  try {
    const response = await fetch("/api/state", {cache: "no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (data.captured_at !== lastCapture) render(data);
    lastCapture = data.captured_at;
  } catch (error) {
    $("pulse").className = "pulse stale";
    $("connection").textContent = "等待识别结果";
    $("updated").textContent = "";
  }
  refreshClock();
}

poll();
setInterval(poll, 1000);
setInterval(refreshClock, 1000);
