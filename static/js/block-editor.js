/*
 * ブロックエディター（依存パッケージなし）。
 *
 * 方針:
 *   - HTTP は fetch のみ。外部ライブラリを増やさない。
 *   - DOM は createElement と textContent で組み立てる。
 *     innerHTML に利用者の入力を渡すと、その時点で XSS の口になる。
 *   - 送るのは「意味」だけ（見出し・段落・画像）。HTML は送らない。
 *
 * 使い方: 記事フォームに次の要素があるときだけ動く。
 *   <div id="block-editor" data-autosave-url="..." data-version="...">
 *   <input name="blocks" type="hidden">  ← ここへ JSON を書き戻す
 *
 * data-version は同時編集の検出に使う版番号。
 * 保存に成功するたびにサーバーが新しい番号を返すので、必ず控え直す。
 */
(function () {
  "use strict";

  var root = document.getElementById("block-editor");
  if (!root) {
    return;
  }

  var field = document.getElementById("id_blocks");
  if (!field) {
    return;
  }

  var listEl = root.querySelector(".editor__blocks");
  var statusEl = root.querySelector(".editor__status");
  var autosaveUrl = root.dataset.autosaveUrl || "";
  var version = parseInt(root.dataset.version || "0", 10);

  // --- ブロックの定義 -----------------------------------------------------
  // サーバー側 blog/blocks.py の BLOCK_TYPES と対応させる。
  var typeData = document.getElementById("block-editor-types");
  var TYPES = {};
  try {
    TYPES = typeData ? JSON.parse(typeData.textContent) : {};
  } catch (e) {
    setStatus("ブロック定義を読み取れませんでした。", "error");
  }

  var blocks = [];

  function parseInitial() {
    var raw = (field.value || "").trim();
    if (!raw) {
      return [];
    }
    try {
      var parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
      setStatus("保存済みのブロックを読み取れませんでした。", "error");
      return [];
    }
  }

  function setStatus(message, kind) {
    statusEl.textContent = message;
    statusEl.className = "editor__status" + (kind ? " editor__status--" + kind : "");
  }

  // --- 描画 ---------------------------------------------------------------
  function makeField(spec, data, onChange) {
    var wrap = document.createElement("label");
    wrap.style.display = "block";
    wrap.style.marginBottom = "0.4rem";

    var caption = document.createElement("span");
    caption.textContent = spec.label;
    caption.style.fontSize = "0.8rem";
    caption.style.color = "var(--muted)";
    wrap.appendChild(caption);

    var input;
    if (spec.type === "textarea") {
      input = document.createElement("textarea");
    } else if (spec.type === "select") {
      input = document.createElement("select");
      (spec.options || []).forEach(function (option) {
        var el = document.createElement("option");
        var value = (option && typeof option === "object") ? option.value : option;
        var label = (option && typeof option === "object") ? option.label : option;
        el.value = String(value);
        el.textContent = String(label);
        input.appendChild(el);
      });
    } else {
      input = document.createElement("input");
      input.type = spec.type === "number" ? "number" : "text";
    }

    var current = data[spec.key];
    if (current === undefined || current === null) {
      current = spec.value !== undefined ? spec.value : "";
    }
    input.value = String(current);

    input.addEventListener("input", function () {
      onChange(spec, input.value);
    });
    input.addEventListener("change", function () {
      onChange(spec, input.value);
    });

    wrap.appendChild(input);
    return wrap;
  }

  function makeButton(label, title, handler) {
    var button = document.createElement("button");
    button.type = "button";          // form の中なので type を明示しないと送信される
    button.className = "btn";
    button.textContent = label;
    button.title = title;
    button.addEventListener("click", handler);
    return button;
  }

  function render() {
    listEl.textContent = "";

    if (blocks.length === 0) {
      var empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "上のボタンからブロックを追加してください。";
      listEl.appendChild(empty);
    }

    blocks.forEach(function (block, index) {
      var spec = TYPES[block.type];
      if (!spec) {
        return;
      }

      var card = document.createElement("div");
      card.className = "editor-block";

      var head = document.createElement("div");
      head.className = "editor-block__head";

      var label = document.createElement("span");
      label.className = "editor-block__type";
      label.textContent = (index + 1) + ". " + spec.label;
      head.appendChild(label);

      var actions = document.createElement("div");
      actions.className = "editor-block__actions";
      actions.appendChild(makeButton("↑", "上へ移動", function () { move(index, -1); }));
      actions.appendChild(makeButton("↓", "下へ移動", function () { move(index, 1); }));
      actions.appendChild(makeButton("×", "削除", function () { remove(index); }));
      head.appendChild(actions);

      card.appendChild(head);

      spec.fields.forEach(function (fieldSpec) {
        card.appendChild(makeField(fieldSpec, block.data, function (s, value) {
          if (s.type === "number") {
            var parsed = parseInt(value, 10);
            block.data[s.key] = isNaN(parsed) ? 0 : parsed;
          } else if (s.key === "level") {
            block.data[s.key] = parseInt(value, 10);
          } else {
            block.data[s.key] = value;
          }
          sync();
        }));
      });

      listEl.appendChild(card);
    });
  }

  function sync() {
    field.value = JSON.stringify(blocks);
    scheduleAutosave();
  }

  function add(type) {
    var spec = TYPES[type];
    if (!spec) {
      return;
    }
    var data = {};
    spec.fields.forEach(function (f) {
      data[f.key] = f.value !== undefined ? f.value : "";
    });
    blocks.push({ type: type, data: data });
    render();
    sync();
  }

  function move(index, delta) {
    var target = index + delta;
    if (target < 0 || target >= blocks.length) {
      return;
    }
    var moved = blocks.splice(index, 1)[0];
    blocks.splice(target, 0, moved);
    render();
    sync();
  }

  function remove(index) {
    blocks.splice(index, 1);
    render();
    sync();
  }

  // --- 自動保存 -----------------------------------------------------------
  var autosaveTimer = null;
  var AUTOSAVE_DELAY_MS = 6000;   // サーバー側の制限（5秒に1回）より緩くする

  function getCookie(name) {
    var match = document.cookie.match(new RegExp("(^| )" + name + "=([^;]+)"));
    return match ? decodeURIComponent(match[2]) : "";
  }

  function scheduleAutosave() {
    if (!autosaveUrl) {
      return;
    }
    if (autosaveTimer) {
      clearTimeout(autosaveTimer);
    }
    setStatus("未保存の変更があります");
    autosaveTimer = setTimeout(autosave, AUTOSAVE_DELAY_MS);
  }

  function autosave() {
    var titleField = document.getElementById("id_title");
    var payload = {
      title: titleField ? titleField.value : "",
      blocks: blocks,
      version: version
    };

    fetch(autosaveUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCookie("csrftoken"),
        "X-Requested-With": "XMLHttpRequest"
      },
      body: JSON.stringify(payload)
    })
      .then(function (response) {
        return response.json().then(function (body) {
          return { status: response.status, body: body };
        });
      })
      .then(function (result) {
        if (result.body && result.body.ok) {
          // サーバーが返した版番号を控える。
          // ここを更新し忘れると、2回目の自動保存が必ず 409 になる。
          version = result.body.version;
          setStatus("自動保存しました（" + new Date().toLocaleTimeString() + "）", "saved");
        } else {
          var message = (result.body && result.body.error) || "自動保存に失敗しました。";
          setStatus(message, "error");
        }
      })
      .catch(function () {
        // 通信断。書いた内容は画面に残っているので、保存を促すだけにする。
        setStatus("自動保存できませんでした（通信エラー）。保存ボタンを押してください。", "error");
      });
  }

  // --- 初期化 -------------------------------------------------------------
  blocks = parseInitial();
  render();

  var toolbar = root.querySelector(".editor__toolbar");
  Object.keys(TYPES).forEach(function (type) {
    toolbar.appendChild(makeButton(TYPES[type].label, TYPES[type].label + "を追加", function () {
      add(type);
    }));
  });

  root.querySelectorAll("[data-add-block]").forEach(function (button) {
    button.addEventListener("click", function () {
      add(button.dataset.addBlock);
    });
  });

  // 送信直前にもう一度書き出す。
  // 入力欄の change を拾い損ねていても、最後の状態が確実に入る。
  var form = field.closest("form");
  if (form) {
    form.addEventListener("submit", function () {
      field.value = JSON.stringify(blocks);
    });
  }
})();
