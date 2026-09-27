/* 微言 · 前端逻辑（手机银行风格，零依赖，file:// 直接打开可用） */
(function () {
  "use strict";

  var API = "http://127.0.0.1:8000/api/v1";
  var ACC = "6222-0001";

  var chat = document.getElementById("chat");
  var input = document.getElementById("input");

  var TOOL_NAMES = {
    query_balance: "余额查询",
    list_transactions: "交易查询",
    transfer: "转账",
    list_subscriptions: "订阅查询",
    analyze_bills: "账单分析",
    buy_wealth: "理财申购",
    report_card_loss: "卡片挂失",
  };
  var RISK_BADGE = { green: "green", yellow: "yellow", red: "red", deny: "deny", chat: "chat", execute: "execute" };

  function fmtYuan(cents) { return (cents / 100).toFixed(2) + " 元"; }
  function fmtNum(cents) { return (cents / 100).toFixed(2); }
  function scrollChat() { chat.scrollTop = chat.scrollHeight; }

  /* ========== Tab 切换 ========== */
  var tabs = document.querySelectorAll(".tab-item");
  tabs.forEach(function (t) {
    t.onclick = function () {
      tabs.forEach(function (x) { x.classList.remove("active"); });
      document.querySelectorAll(".tab").forEach(function (x) { x.classList.remove("active"); });
      t.classList.add("active");
      document.getElementById("tab-" + t.dataset.tab).classList.add("active");
      if (t.dataset.tab === "bills") loadBills();
      if (t.dataset.tab === "cards") loadCards();
      if (t.dataset.tab === "contacts") loadContacts();
      if (t.dataset.tab === "audit") loadAudit();
      if (t.dataset.tab === "chat") refreshAsset();
    };
  });

  /* ========== 资产卡 ========== */
  function refreshAsset() {
    fetch(API + "/accounts/" + ACC + "/balance")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.data) d = d.data;
        document.getElementById("assetAmount").textContent = fmtNum(d.available_cents);
        document.getElementById("assetDetail").textContent =
          "主账户 " + d.account_id + " · 总 " + fmtYuan(d.balance_cents) + " · 锁定 " + fmtNum(d.locked_cents);
      })
      .catch(function () { /* 后端未启动时保持占位 */ });
  }
  document.getElementById("btnRefresh").onclick = refreshAsset;

  /* 快捷操作：把话术发给 Agent（演示：点击按钮 = 说一句话，仍走安全门） */
  function quickSend(msg) {
    input.value = msg;
    send();
  }
  document.querySelectorAll(".quick, .op").forEach(function (b) {
    b.onclick = function () {
      // 跳回对话 tab 再发送
      tabs.forEach(function (x) { x.classList.remove("active"); });
      document.querySelectorAll(".tab").forEach(function (x) { x.classList.remove("active"); });
      document.querySelector('.tab-item[data-tab="chat"]').classList.add("active");
      document.getElementById("tab-chat").classList.add("active");
      quickSend(b.dataset.msg);
    };
  });

  /* ========== 聊天 ========== */
  function addUser(text) {
    var d = document.createElement("div");
    d.className = "bubble user";
    d.innerHTML = '<div class="meta">小明</div><div class="text"></div>';
    d.querySelector(".text").textContent = text;
    chat.appendChild(d);
    scrollChat();
  }
  function addAssistant(text, opts) {
    opts = opts || {};
    var d = document.createElement("div");
    d.className = "bubble assistant" + (opts.deny ? " deny" : "");
    d.innerHTML = '<div class="meta">微言 · AI 银行助手</div><div class="text"></div>';
    d.querySelector(".text").textContent = text;
    if (opts.eid) {
      var e = document.createElement("div");
      e.className = "eid";
      e.textContent = "执行编号 " + opts.eid;
      d.appendChild(e);
    }
    chat.appendChild(d);
    scrollChat();
  }
  function addTyping() {
    var d = document.createElement("div");
    d.className = "typing";
    d.id = "typing";
    d.innerHTML = "<i></i><i></i><i></i>";
    chat.appendChild(d);
    scrollChat();
  }
  function removeTyping() { var t = document.getElementById("typing"); if (t) t.remove(); }

  function pendingCancel() { var card = document.querySelector(".confirm-card"); if (card) card.remove(); }

  var contactMap = {}; // account_id -> 联系人名（转账确认卡显示收款人，防转错人）

  function addConfirmCard(reply) {
    pendingCancel();
    var p = reply.params || {};
    var desc = [];
    if (reply.tool === "transfer") {
      desc.push("金额：<b>" + fmtYuan(p.amount_cents) + "</b>");
      var payee = contactMap[p.to_account_id];
      desc.push("收款" + (payee ? "人：<b>" + payee + "</b>（账户 " + p.to_account_id + "）" : "账户：" + p.to_account_id));
      if (p.note) desc.push("备注：" + p.note);
    } else {
      desc.push("操作：" + (TOOL_NAMES[reply.tool] || reply.tool));
      desc.push("参数：" + JSON.stringify(p));
    }
    var d = document.createElement("div");
    d.className = "confirm-card";
    d.innerHTML =
      '<div class="tag">🟡 黄级操作 · 需要你确认</div>' +
      '<div class="row">' + desc.join("</div><div class='row'>") + "</div>" +
      '<div class="btns"><button class="btn ok">确认执行</button><button class="btn no">取消</button></div>';
    chat.appendChild(d);
    d.querySelector(".ok").onclick = function () {
      d.querySelector(".ok").disabled = true;
      fetch(API + "/agent/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pending_id: reply.pending_id }),
      }).then(function (r) { return r.json(); }).then(function (res) {
        d.remove();
        renderReply(res);
      });
    };
    d.querySelector(".no").onclick = function () { d.remove(); addAssistant("操作已取消。"); };
    scrollChat();
  }

  /* MFA 弹层（红级） */
  function openMfa(reply) {
    var mask = document.getElementById("mfaMask");
    document.getElementById("mfaDesc").innerHTML =
      "操作：" + (TOOL_NAMES[reply.tool] || reply.tool) +
      (reply.params && reply.params.amount_cents ? " · " + fmtYuan(reply.params.amount_cents) : "") +
      "<br>" + reply.message;
    mask.classList.remove("hidden");
    document.getElementById("mfaCode").value = "";
    document.getElementById("mfaCode").focus();
    function submit() {
      var code = document.getElementById("mfaCode").value.trim();
      if (!code) return;
      document.getElementById("mfaOk").disabled = true;
      fetch(API + "/agent/authorize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pending_id: reply.pending_id, mfa_code: code }),
      }).then(function (r) { return r.json(); }).then(function (res) {
        mask.classList.add("hidden");
        document.getElementById("mfaOk").disabled = false;
        renderReply(res);
      });
    }
    document.getElementById("mfaOk").onclick = submit;
    document.getElementById("mfaCancel").onclick = function () {
      mask.classList.add("hidden");
      addAssistant("强验证已取消，操作未执行。");
    };
  }

  /* 安全状态徽章 */
  function refreshSecBadge() {
    fetch(API + "/agent/status")
      .then(function (r) { return r.json(); })
      .then(function (s) {
        var badge = document.getElementById("secBadge");
        if (s.locked) {
          badge.className = "sec-badge locked";
          badge.textContent = "已锁定（熔断）";
          showLockBanner();
        } else {
          badge.className = "sec-badge ok";
          badge.textContent = "安全在线";
          hideLockBanner();
        }
      })
      .catch(function () {});
  }
  function showLockBanner() { document.getElementById("lockBanner").classList.remove("hidden"); }
  function hideLockBanner() { document.getElementById("lockBanner").classList.add("hidden"); }

  function renderReply(res) {
    if (res.requires === "chat") { addAssistant(res.message); return; }
    if (res.requires === "confirm") { addConfirmCard(res); return; }
    if (res.requires === "mfa") { openMfa(res); return; }
    addAssistant(res.message, { deny: res.requires === "deny", eid: res.execution_id });
    if (res.requires === "deny" && res.message.indexOf("已安全锁定") >= 0) {
      refreshSecBadge();
    }
    if (res.requires === "auto" && res.tool === "analyze_bills") {
      addAssistant("已生成账单图表，可点击底部「账单」查看可视化分析。");
      loadBills();
    }
    if (res.tool === "apply_virtual_card" || res.tool === "freeze_card" || res.tool === "unfreeze_card" ||
        res.tool === "report_card_loss" || res.tool === "unlock_card" || res.tool === "adjust_card_limit") {
      loadCards(); // 卡片页保持同步
    }
    if (res.execution_id) refreshAsset();
  }

  /* ========== 账单 Tab ========== */
  var billMonth = null; // null = 当前月（后端默认）
  var billChart = null; // ECharts 实例复用（切换月份/刷新时先 dispose 再重建）
  function loadBills() {
    var q = billMonth ? "?month=" + billMonth : "";
    fetch(API + "/agent/bills" + q)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        document.getElementById("billPeriod").textContent = d.period;
        document.getElementById("billIncome").textContent = "+" + fmtNum(d.total_income_cents) + " 元";
        document.getElementById("billExpense").textContent = fmtNum(Math.abs(d.total_expense_cents)) + " 元";

        var cats = d.by_category.filter(function (c) { return c.amount_cents < 0; });
        document.getElementById("billCats").innerHTML = cats.map(function (c) {
          return '<div class="cat-row"><span class="c-name">' + c.category + "</span>" +
            '<span class="c-amt">' + fmtNum(Math.abs(c.amount_cents)) + " 元 · " + c.count + " 笔</span></div>";
        }).join("");

        var box = document.getElementById("billAnomalies");
        if (!d.anomaly_count) {
          box.innerHTML = '<div class="anomaly-empty">✅ 未识别到异常交易</div>';
        } else {
          box.innerHTML = d.anomalies.map(function (a) {
            return '<div class="anomaly-row"><b>' + a.counterparty + "</b> " + fmtYuan(a.amount_cents) +
              '<div class="reason">⚠ ' + a.reason + (a.note ? " · " + a.note : "") + "</div></div>";
          }).join("");
        }

        if (window.echarts) {
          // 实例复用：切换月份/刷新时先 dispose 再重建，避免重复 init 与隐藏容器尺寸问题
          if (billChart) { billChart.dispose(); billChart = null; }
          billChart = echarts.init(document.getElementById("billChart"), null, { renderer: "svg" });
          billChart.setOption({
            tooltip: { trigger: "item", formatter: "{b}: {c} 元 ({d}%)" },
            color: ["#1A4B8C", "#C9A227", "#0E9F6E", "#C2610C", "#64748B", "#0EA5E9"],
            // 环形图居中放大、中心显示总支出（银行 App 账单页惯例）；不设图例避免手机端图例换行挤压
            graphic: [
              { type: "text", left: "center", top: "33%",
                style: { text: "总支出", fill: "#6B7280", fontSize: 12, textAlign: "center" } },
              { type: "text", left: "center", top: "40%",
                style: { text: fmtNum(Math.abs(d.total_expense_cents)) + " 元",
                         fill: "#0B2D5C", fontWeight: 700, fontSize: 18, textAlign: "center" } },
            ],
            series: [{
              type: "pie", radius: ["40%", "64%"], center: ["50%", "45%"],
              itemStyle: { borderRadius: 6, borderColor: "#fff", borderWidth: 2 },
              label: { show: false },
              data: cats.map(function (c) { return { name: c.category, value: Math.abs(c.amount_cents) / 100 }; }),
            }],
          });
        }
      });
  }
  document.getElementById("billPrev").onclick = function () {
    var d = document.getElementById("billPeriod").textContent.split("-");
    if (d.length === 2) {
      var m = parseInt(d[1], 10) - 1;
      if (m >= 1) { billMonth = m; loadBills(); }
    }
  };
  document.getElementById("billNext").onclick = function () {
    var d = document.getElementById("billPeriod").textContent.split("-");
    if (d.length === 2) {
      var m = parseInt(d[1], 10) + 1;
      if (m <= 12) { billMonth = m; loadBills(); }
    }
  };

  /* ========== 卡片 Tab ========== */
  function loadCards() {
    fetch(API + "/accounts/" + ACC + "/cards")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.data) d = d.data;
        var list = document.getElementById("cardList");
        if (!d.cards.length) { list.innerHTML = '<div class="anomaly-empty">暂无卡片</div>'; return; }
        list.innerHTML = d.cards.map(function (c) {
          var statusText = c.status === "active" ? "正常" : c.status === "frozen" ? "已冻结" : c.status === "lost" ? "已挂失" : c.status;
          var cls = (c.status === "active") ? "active" : "lost";
          var cardNo = c.id + " · " + (c.card_type === "virtual" ? "虚拟卡" : "借记卡");
          return '<div class="bank-card">' +
            '<div class="card-top"><span>' + cardNo + '</span><span class="badge2 ' + cls + '">' + statusText + "</span></div>" +
            '<div class="card-no">•••• •••• •••• ' + c.id.slice(-4) + "</div>" +
            '<div class="card-bottom"><span>日限额 ' + fmtNum(c.daily_limit_cents) + " 元</span>" +
            (c.locked ? '<span style="color:#FECACA">已锁定</span>' : "<span>持卡人：小明</span>") + "</div></div>";
        }).join("");
      });
  }
  document.getElementById("btnAddCard").onclick = function () { quickSend("申请一张虚拟卡"); };

  /* ========== 联系人 Tab ========== */
  function loadContacts() {
    fetch(API + "/contacts")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.data) d = d.data;
        contactMap = {};
        var list = document.getElementById("contactList");
        if (!d.contacts.length) {
          list.innerHTML = '<div class="anomaly-empty">联系人簿为空——点右上角「＋ 添加」，或直接对微言说「添加联系人 爸爸，账户 6222-1004」</div>';
          return;
        }
        list.innerHTML = d.contacts.map(function (c) {
          contactMap[c.account_id] = c.name;
          var phone = c.phone ? " · " + c.phone : "";
          return '<div class="contact-row">' +
            '<div class="c-avatar">' + c.name.slice(0, 1) + "</div>" +
            '<div class="c-info"><div class="c-name">' + c.name +
            (c.relation ? '<span class="c-rel">' + c.relation + "</span>" : "") + "</div>" +
            '<div class="c-acct">' + c.account_id + phone + "</div></div>" +
            (c.aliases && c.aliases.length ? '<div class="c-alias">别名：' + c.aliases.join(" / ") + "</div>" : "") +
            "</div>";
        }).join("");
      });
  }
  document.getElementById("btnContactAdd").onclick = function () {
    document.getElementById("contactForm").classList.remove("hidden");
  };
  document.getElementById("cCancel").onclick = function () {
    document.getElementById("contactForm").classList.add("hidden");
  };
  document.getElementById("cSave").onclick = function () {
    var name = document.getElementById("cName").value.trim();
    var acc = document.getElementById("cAccount").value.trim();
    if (!name || !acc) { alert("姓名与收款账户必填"); return; }
    fetch(API + "/contacts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: name,
        account_id: acc,
        phone: document.getElementById("cPhone").value.trim(),
        relation: document.getElementById("cRelation").value.trim(),
      }),
    }).then(function (r) { return r.json(); }).then(function (res) {
      if (res && res.ok === false) { alert(res.message || "添加失败"); return; }
      document.getElementById("contactForm").classList.add("hidden");
      ["cName", "cAccount", "cPhone", "cRelation"].forEach(function (id) {
        document.getElementById(id).value = "";
      });
      loadContacts();
    });
  };

  /* ========== 审计 Tab ========== */
  function loadAudit() {
    fetch(API + "/agent/audit")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var body = document.getElementById("auditBody");
        if (!data.records.length) {
          body.innerHTML = '<div class="audit-empty">暂无审计记录——说一句「帮我看看余额」试试</div>';
        } else {
          body.innerHTML = data.records.map(function (rec) {
            var badge = RISK_BADGE[rec.risk] || "execute";
            return '<div class="audit-row">' +
              '<div class="a-time">' + rec.ts.replace("T", " ").slice(0, 19) + "</div>" +
              '<span class="a-tool">' + (rec.tool || "（对话）") + "</span>" +
              '<span class="badge ' + badge + '">' + (rec.risk || rec.action) + "</span>" +
              "<div>" + rec.message + "</div></div>";
          }).join("");
        }
      });
  }

  /* ========== 定时器演示（时间沙箱） ========== */
  function openTick() {
    document.getElementById("tickMask").classList.remove("hidden");
    document.getElementById("tickDate").focus();
  }
  function runTick() {
    var date = document.getElementById("tickDate").value.trim();
    if (!date) return;
    document.getElementById("tickOk").disabled = true;
    addUser("（系统定时器拨动到 " + date + "）");
    addTyping();
    fetch(API + "/agent/tick", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ date: date }),
    }).then(function (r) { return r.json(); }).then(function (res) {
      document.getElementById("tickMask").classList.add("hidden");
      document.getElementById("tickOk").disabled = false;
      removeTyping();
      renderReply(res);
      refreshAsset(); // 定时器可能触发扣款/订购：资产卡必须同步刷新（曾因漏刷让用户误以为"没扣钱"）
      loadAudit();
    });
  }
  document.getElementById("btnTick").onclick = openTick;
  document.getElementById("tickOk").onclick = runTick;
  document.getElementById("tickCancel").onclick = function () {
    document.getElementById("tickMask").classList.add("hidden");
  };

  /* ========== 发送 ========== */
  function send() {
    var text = input.value.trim();
    if (!text) return;
    input.value = "";
    addUser(text);
    addTyping();
    fetch(API + "/agent/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    }).then(function (r) { return r.json(); }).then(function (res) {
      removeTyping();
      renderReply(res);
    }).catch(function () {
      removeTyping();
      addAssistant("连接失败，请确认后端已启动：uvicorn backend.api.main:app --reload", { deny: true });
    });
  }
  document.getElementById("send").onclick = send;
  input.onkeydown = function (e) { if (e.key === "Enter") send(); };

  /* ========== 重置演示 ========== */
  document.getElementById("btnReset").onclick = function () {
    fetch(API + "/agent/reset", { method: "POST" })
      .then(function () {
        chat.innerHTML = "";
        var d = document.createElement("div");
        d.className = "bubble assistant first";
        d.innerHTML = '<div class="meta">微言 · AI 银行助手</div><div class="text">会话已重置（银行数据已复原，锁定已解除）。试试「帮我看看余额」。</div>';
        chat.appendChild(d);
        refreshAsset();
        refreshSecBadge();
        billMonth = null;
        loadBills();
        loadCards();
        loadAudit();
      });
  };

  /* 弹层关闭 */
  document.getElementById("mfaCancel").onclick = function () {};

  /* 初始化 */
  refreshAsset();
  refreshSecBadge();
  loadContacts(); // 预载联系人：转账确认卡显示收款人姓名（防转错人）
  input.focus();
  setInterval(refreshSecBadge, 5000);
})();
