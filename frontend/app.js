/* 微言 · 前端聊天页逻辑（零依赖，file:// 直接打开可用） */
(function () {
  "use strict";

  var API = "http://127.0.0.1:8000/api/v1";

  var chat = document.getElementById("chat");
  var input = document.getElementById("input");
  var sendBtn = document.getElementById("send");

  var TOOL_NAMES = {
    query_balance: "余额查询",
    list_transactions: "交易查询",
    transfer: "转账",
    list_subscriptions: "订阅查询",
  };

  var RISK_BADGE = { green: "green", yellow: "yellow", red: "red", deny: "deny", chat: "chat", execute: "execute" };

  function fmtYuan(cents) {
    return (cents / 100).toFixed(2) + " 元";
  }

  function scrollBottom() {
    chat.scrollTop = chat.scrollHeight;
  }

  function addUser(text) {
    var d = document.createElement("div");
    d.className = "bubble user";
    d.innerHTML = '<div class="meta">小明</div><div class="text"></div>';
    d.querySelector(".text").textContent = text;
    chat.appendChild(d);
    scrollBottom();
  }

  function addAssistant(text, opts) {
    opts = opts || {};
    var d = document.createElement("div");
    d.className = "bubble assistant" + (opts.deny ? " deny" : "");
    d.innerHTML = '<div class="meta">微言</div><div class="text"></div>';
    d.querySelector(".text").textContent = text;
    if (opts.eid) {
      var e = document.createElement("div");
      e.className = "eid";
      e.textContent = "执行编号 " + opts.eid;
      d.appendChild(e);
    }
    chat.appendChild(d);
    scrollBottom();
  }

  function addTyping() {
    var d = document.createElement("div");
    d.className = "typing";
    d.id = "typing";
    d.innerHTML = "<i></i><i></i><i></i>";
    chat.appendChild(d);
    scrollBottom();
    return d;
  }

  function removeTyping() {
    var t = document.getElementById("typing");
    if (t) t.remove();
  }

  function pendingCancel() {
    var card = document.querySelector(".confirm-card");
    if (card) card.remove();
  }

  function addConfirmCard(reply) {
    pendingCancel();
    var p = reply.params || {};
    var desc = [];
    if (reply.tool === "transfer") {
      desc.push("金额：<b>" + fmtYuan(p.amount_cents) + "</b>");
      desc.push("收款账户：" + p.to_account_id);
      if (p.note) desc.push("备注：" + p.note);
    } else {
      desc.push("操作：" + (TOOL_NAMES[reply.tool] || reply.tool));
      desc.push("参数：" + JSON.stringify(p));
    }
    var d = document.createElement("div");
    d.className = "confirm-card";
    d.innerHTML =
      '<div class="tag">黄级操作 · 需要你确认</div>' +
      '<div class="row">' + desc.join("</div><div class='row'>") + "</div>" +
      '<div class="btns"><button class="btn ok">确认执行</button><button class="btn no">取消</button></div>';
    chat.appendChild(d);
    d.querySelector(".ok").onclick = function () {
      d.querySelector(".ok").disabled = true;
      fetch(API + "/agent/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pending_id: reply.pending_id }),
      })
        .then(function (r) { return r.json(); })
        .then(function (res) {
          d.remove();
          renderReply(res);
        });
    };
    d.querySelector(".no").onclick = function () { d.remove(); addAssistant("操作已取消。"); };
    scrollBottom();
  }

  /* MFA 弹层（红级） */
  function openMfa(reply) {
    var mask = document.getElementById("mfaMask");
    var desc = document.getElementById("mfaDesc");
    desc.innerHTML =
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
      })
        .then(function (r) { return r.json(); })
        .then(function (res) {
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

  function renderReply(res) {
    if (res.requires === "chat") { addAssistant(res.message); return; }
    if (res.requires === "confirm") { addConfirmCard(res); return; }
    if (res.requires === "mfa") { openMfa(res); return; }
    addAssistant(res.message, { deny: res.requires === "deny", eid: res.execution_id });
    if (res.requires === "auto" && res.tool === "analyze_bills") { renderBillChart(); }
  }

  /* 账单分析可视化：分类环形图 + 异常列表 */
  function renderBillChart() {
    fetch(API + "/agent/bills")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var box = document.createElement("div");
        box.className = "bubble assistant chart";
        box.innerHTML = '<div class="meta">微言 · 账单可视化</div>';
        var title = document.createElement("div");
        title.className = "chart-title";
        title.textContent = d.period + " 支出分类（总支出 " + fmtYuan(Math.abs(d.total_expense_cents)) + "）";
        box.appendChild(title);
        var chartDiv = document.createElement("div");
        chartDiv.style.cssText = "width:100%;height:220px;";
        box.appendChild(chartDiv);
        var listDiv = document.createElement("div");
        listDiv.className = "anomaly-list";
        if (d.anomaly_count > 0) {
          listDiv.innerHTML = '<div class="anomaly-head">⚠ 识别到 ' + d.anomaly_count + ' 笔异常交易</div>' +
            d.anomalies.map(function (a) {
              return '<div class="anomaly-row"><b>' + a.counterparty + '</b> ' + fmtYuan(a.amount_cents) +
                "（" + a.reason + "）</div>";
            }).join("");
        }
        box.appendChild(listDiv);
        chat.appendChild(box);
        scrollBottom();
        if (window.echarts) {
          var cats = d.by_category.filter(function (c) { return c.amount_cents < 0; });
          var chart = echarts.init(chartDiv);
          chart.setOption({
            tooltip: { trigger: "item", formatter: "{b}: {c} 元 ({d}%)" },
            legend: { bottom: 0, textStyle: { fontSize: 11 } },
            series: [{
              type: "pie", radius: ["42%", "68%"], center: ["50%", "44%"],
              itemStyle: { borderRadius: 4, borderColor: "#fff", borderWidth: 1 },
              label: { show: false },
              data: cats.map(function (c) {
                return { name: c.category, value: Math.abs(c.amount_cents) / 100 };
              }),
            }],
          });
        } else {
          chartDiv.style.display = "none";
          box.appendChild(document.createTextNode("（离线环境未加载图表库，仅显示文字）"));
        }
      });
  }

  /* 定时器演示（时间沙箱） */
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
    })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        document.getElementById("tickMask").classList.add("hidden");
        document.getElementById("tickOk").disabled = false;
        removeTyping();
        renderReply(res);
      });
  }

  /* 审计日志弹层 */
  function openAudit() {
    fetch(API + "/agent/audit")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var body = document.getElementById("auditBody");
        body.innerHTML = "";
        if (!data.records.length) {
          body.innerHTML = '<div style="color:#6b7280;padding:12px;">暂无审计记录</div>';
        } else {
          data.records.forEach(function (rec) {
            var row = document.createElement("div");
            row.className = "audit-row";
            var badge = RISK_BADGE[rec.risk] || "execute";
            row.innerHTML =
              '<div class="a-time">' + rec.ts.replace("T", " ").slice(0, 19) + "</div>" +
              '<span class="a-tool">' + (rec.tool || "（对话）") + "</span>" +
              '<span class="badge ' + badge + '">' + (rec.risk || rec.action) + "</span>" +
              "<div>" + rec.message + "</div>";
            body.appendChild(row);
          });
        }
        document.getElementById("auditMask").classList.remove("hidden");
      });
  }

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
    })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        removeTyping();
        renderReply(res);
      })
      .catch(function (e) {
        removeTyping();
        addAssistant("连接失败，请确认后端已启动：uvicorn backend.api.main:app --reload", { deny: true });
      });
  }

  sendBtn.onclick = send;
  input.onkeydown = function (e) { if (e.key === "Enter") send(); };
  document.getElementById("btnAudit").onclick = openAudit;
  document.getElementById("btnTick").onclick = openTick;
  document.getElementById("tickOk").onclick = runTick;
  document.getElementById("tickCancel").onclick = function () {
    document.getElementById("tickMask").classList.add("hidden");
  };
  document.getElementById("btnReset").onclick = function () {
    fetch(API + "/agent/reset", { method: "POST" })
      .then(function () {
        chat.innerHTML = "";
        var d = document.createElement("div");
        d.className = "bubble assistant first";
        d.innerHTML = '<div class="meta">微言</div><div class="text">会话已重置（银行数据已复原）。试试「帮我看看余额」。</div>';
        chat.appendChild(d);
      });
  };
  document.getElementById("auditClose").onclick = function () {
    document.getElementById("auditMask").classList.add("hidden");
  };

  input.focus();
})();
