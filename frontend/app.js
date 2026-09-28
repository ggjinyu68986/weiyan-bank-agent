/* 微言 · 前端逻辑（手机银行风格，零依赖，file:// 直接打开可用） */
(function () {
  "use strict";

  var API = "http://127.0.0.1:8000/api/v1";
  var CURRENT_USER = "小明";   // 当前视角用户（AA 多方协作：谁登录就是谁的账户）
  var ACC = "6222-0001";
  var VIEWS = { "小明": "6222-0001" };  // 视角用户 → 账户号（/agent/users 加载）

  var chat = document.getElementById("chat");
  var input = document.getElementById("input");

  var TOOL_NAMES = {
    query_balance: "余额查询",
    list_transactions: "交易查询",
    transfer: "转账",
    schedule_transfer: "定时转账",
    split_bill: "AA拆分收款",
    split_bill_status: "AA收款进度",
    pay_split_bill: "AA收款入账",
    list_subscriptions: "订阅查询",
    analyze_bills: "账单分析",
    buy_wealth: "理财申购",
    report_card_loss: "卡片挂失",
  };
  var RISK_BADGE = { green: "green", yellow: "yellow", red: "red", deny: "deny", chat: "chat", execute: "execute" };
  /* 支出分类色板（与环形图扇区颜色一一对应，列表色点=饼图扇区） */
  var CAT_COLORS = ["#1A4B8C", "#C9A227", "#0E9F6E", "#C2610C", "#64748B", "#0EA5E9"];
  /* 异常类型 → 图标（深夜/大额🌙、异地📍、高频🔁） */
  function anomalyIcon(reason) {
    if (reason.indexOf("深夜") >= 0) return "🌙";
    if (reason.indexOf("异地") >= 0) return "📍";
    if (reason.indexOf("高频") >= 0) return "🔁";
    return "⚠";
  }

  /* 双引擎判定证据行（规则 ⊕ JEV 置信度）——确认卡 / MFA 弹层 / 审计面板共用 */
  function decisionLine(dec) {
    if (!dec || !dec.grade) return "";
    var g = dec.grade;
    if (g.engine === "rule") return "";
    var cn = { green: "绿", yellow: "黄", red: "红" }[g.value] || g.value;
    var label = "规则" + cn + " ⊕ JEV " + cn + " · 置信 " + Math.round(g.confidence * 100) + "%";
    return '<div class="dec-line">双引擎判定：' + label + "</div>";
  }
  function routeLine(dec) {
    if (!dec || !dec.route || dec.route.value !== "mismatch") return "";
    return '<div class="dec-line warn">⚠ JEV 路由校验：意图与所选工具类别不一致（置信 ' +
      Math.round(dec.route.confidence * 100) + "%）</div>";
  }

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
          CURRENT_USER + " · 主账户 " + d.account_id + " · 总 " + fmtYuan(d.balance_cents) +
          " · 锁定 " + fmtNum(d.locked_cents);
      })
      .catch(function () { /* 后端未启动时保持占位 */ });
  }
  document.getElementById("btnRefresh").onclick = refreshAsset;

  /* ========== 视角切换（多用户：AA 多方协作） ========== */
  function loadViews() {
    fetch(API + "/agent/users")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        VIEWS = {};
        d.users.forEach(function (u) { VIEWS[u.name] = u.account_id; });
        renderViewSwitch();
      })
      .catch(function () {});
  }
  function renderViewSwitch() {
    var box = document.getElementById("viewSwitch");
    if (!box) return;
    box.innerHTML = Object.keys(VIEWS).map(function (name) {
      return '<button class="chip' + (name === CURRENT_USER ? " on" : "") +
        '" data-user="' + name + '">' + name + "</button>";
    }).join("");
    box.querySelectorAll(".chip").forEach(function (c) {
      c.onclick = function () { switchUser(c.dataset.user); };
    });
  }
  function switchUser(name) {
    if (name === CURRENT_USER) return;
    CURRENT_USER = name;
    ACC = VIEWS[name] || ACC;
    // 每个用户独立会话：切换视角即切换会话，聊天区重置
    chat.innerHTML = "";
    var d = document.createElement("div");
    d.className = "bubble assistant first";
    d.innerHTML = '<div class="meta">微言 · AI 银行助手</div><div class="text">已切换视角：' +
      '<b>' + CURRENT_USER + "</b>（账户 " + ACC + "）。这是独立会话——" +
      "试试「我有哪些待付的AA」或「帮我看看余额」。</div>";
    chat.appendChild(d);
    renderViewSwitch();
    refreshAsset();
    refreshSecBadge();
    loadAudit();
    loadPendingSplits(); // 对端视角：主动展示待付 AA 卡
  }

  /* ========== 待付 AA 卡（对端视角：看别人有没有 A 钱） ========== */
  function loadPendingSplits() {
    fetch(API + "/agent/pending-splits?account_id=" + ACC)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.items || !d.items.length) return;
        var holder = document.createElement("div");
        holder.innerHTML = renderPendingSplits(d.items);
        if (holder.firstChild) { chat.appendChild(holder.firstChild); scrollChat(); bindPayButtons(); }
      })
      .catch(function () {});
  }
  function renderPendingSplits(items) {
    return items.map(function (it) {
      return '<div class="pending-card">' +
        '<div class="aa-head">💸 待付 AA · ' + it.title +
        ' <span class="aa-count">' + fmtNum(it.amount_cents) + " 元</span></div>" +
        '<div class="aa-sub">发起人 ' + it.initiator_account_id + " · 已收 " +
        it.paid_count + "/" + it.payer_count + "（人均 " + fmtNum(it.per_person_cents) + " 元）</div>" +
        '<div class="btns"><button class="btn primary pay-aa">确认支付</button></div>' +
        "</div>";
    }).join("");
  }
  function bindPayButtons() {
    chat.querySelectorAll(".pay-aa").forEach(function (b) {
      b.onclick = function () {
        var card = b.closest(".pending-card");
        var title = card.querySelector(".aa-head").textContent.split("· ")[1] || "AA";
        quickSend("支付" + title);
      };
    });
  }

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
    d.innerHTML = '<div class="meta">' + CURRENT_USER + '</div><div class="text"></div>';
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
    } else if (reply.tool === "split_bill") {
      desc.push("总额：<b>" + fmtYuan(p.total_cents) + "</b>");
      desc.push("人数：" + p.people_count + " 人（人均 " + fmtNum(p.total_cents / p.people_count) + " 元）");
      desc.push("事由：" + (p.title || "AA收款") + " · 向其余参与者收款");
    } else if (reply.tool === "pay_split_bill") {
      var payerNm = contactMap[p.payer_account_id] || p.payer_account_id;
      desc.push("收款人：<b>" + payerNm + "</b>（账户 " + p.payer_account_id + "）");
      desc.push("动作：从收款人账户扣 AA 分摊款 → 入账发起人（金额见收款单）");
    } else {
      desc.push("操作：" + (TOOL_NAMES[reply.tool] || reply.tool));
      desc.push("参数：" + JSON.stringify(p));
    }
    var d = document.createElement("div");
    d.className = "confirm-card";
    d.innerHTML =
      '<div class="tag">🟡 黄级操作 · 需要你确认</div>' +
      decisionLine(reply.decision) +
      '<div class="row">' + desc.join("</div><div class='row'>") + "</div>" +
      '<div class="btns"><button class="btn ok">确认执行</button><button class="btn no">取消</button></div>';
    chat.appendChild(d);
    d.querySelector(".ok").onclick = function () {
      d.querySelector(".ok").disabled = true;
      fetch(API + "/agent/confirm?user=" + encodeURIComponent(CURRENT_USER), {
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
      "<br>" + reply.message +
      (decisionLine(reply.decision) ? "<br>" + decisionLine(reply.decision) : "");
    mask.classList.remove("hidden");
    document.getElementById("mfaCode").value = "";
    document.getElementById("mfaCode").focus();
    function submit() {
      var code = document.getElementById("mfaCode").value.trim();
      if (!code) return;
      document.getElementById("mfaOk").disabled = true;
      fetch(API + "/agent/authorize?user=" + encodeURIComponent(CURRENT_USER), {
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
    fetch(API + "/agent/status?user=" + encodeURIComponent(CURRENT_USER))
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

  /* AA 收款进度卡片（发起/入账/查询后自动渲染，进度条 + 收款人名单） */
  function renderAABill(d) {
    if (!d || !Array.isArray(d.payers)) return "";
    var total = d.payer_count != null ? d.payer_count : d.payers.length;
    var paid = d.paid_count != null ? d.paid_count : d.payers.filter(function (p) { return p.paid; }).length;
    var pct = total ? Math.round((paid / total) * 100) : 0;
    var rows = d.payers.map(function (p) {
      var nm = contactMap[p.account_id] || p.account_id;
      return '<div class="aa-row' + (p.paid ? " done" : "") + '">' +
        '<span class="aa-name">' + nm + "</span>" +
        '<span class="aa-amt">' + fmtNum(p.amount_cents) + " 元</span>" +
        '<span class="aa-badge ' + (p.paid ? "ok" : "due") + '">' + (p.paid ? "✓ 已收" : "待收") + "</span></div>";
    }).join("");
    var settled = total && paid === total;
    return '<div class="aa-card">' +
      '<div class="aa-head">' + d.title + (settled ? " · <span class='aa-settled'>已收齐 ✓</span>" : " · AA 收款进度") +
      ' <span class="aa-count">' + paid + "/" + total + "</span></div>" +
      '<div class="aa-bar"><div class="aa-bar-fill" style="width:' + pct + '%"></div></div>' +
      '<div class="aa-sub">每人 ' + fmtNum(d.per_person_cents) + " 元 · 已收 " + fmtNum(paid * d.per_person_cents) + " 元</div>" +
      rows + "</div>";
  }

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
    if (res.requires === "auto" &&
        (res.tool === "split_bill" || res.tool === "pay_split_bill" || res.tool === "split_bill_status") &&
        res.data && res.data.payers) {
      // AA 收款进度卡片（进度条 + 收款人名单，实时反映已收/待收）
      var holder = document.createElement("div");
      holder.innerHTML = renderAABill(res.data);
      if (holder.firstChild) { chat.appendChild(holder.firstChild); scrollChat(); }
    }
    if (res.requires === "auto" && res.tool === "list_pending_splits" && res.data) {
      // 对端视角：待付 AA 卡（带「确认支付」按钮）
      var ph = document.createElement("div");
      ph.innerHTML = renderPendingSplits(res.data.items || []);
      if (ph.firstChild) { chat.appendChild(ph.firstChild); scrollChat(); bindPayButtons(); }
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
        document.getElementById("billCats").innerHTML = cats.map(function (c, i) {
          return '<div class="cat-row"><span class="c-name">' +
            '<i class="dot" style="background:' + CAT_COLORS[i % CAT_COLORS.length] + '"></i>' + c.category + "</span>" +
            '<span class="c-amt">' + fmtNum(Math.abs(c.amount_cents)) + " 元 · " + c.count + " 笔</span></div>";
        }).join("");

        var box = document.getElementById("billAnomalies");
        if (!d.anomaly_count) {
          box.innerHTML = '<div class="anomaly-empty">✅ 未识别到异常交易</div>';
        } else {
          box.innerHTML = '<div class="anomaly-head">共 ' + d.anomaly_count + " 笔异常 · AI 风控识别</div>" +
            d.anomalies.map(function (a) {
              return '<div class="anomaly-row"><span class="a-ico">' + anomalyIcon(a.reason) + "</span><b>" +
                a.counterparty + "</b> " + fmtYuan(a.amount_cents) +
                '<div class="reason">' + a.reason + (a.note ? " · " + a.note : "") + "</div></div>";
            }).join("");
        }

        if (window.echarts) {
          // 实例复用：切换月份/刷新时先 dispose 再重建，避免重复 init 与隐藏容器尺寸问题
          if (billChart) { billChart.dispose(); billChart = null; }
          // 最大占比扇区高亮：外描边加深 + 直接标注「类别+占比」，一眼看到支出大头
          var maxIdx = 0;
          cats.forEach(function (c, i) {
            if (Math.abs(c.amount_cents) > Math.abs(cats[maxIdx].amount_cents)) maxIdx = i;
          });
          var pieData = cats.map(function (c, i) {
            return {
              name: c.category,
              value: Math.abs(c.amount_cents) / 100,
              itemStyle: i === maxIdx
                ? { borderRadius: 6, borderColor: "#0B2D5C", borderWidth: 3 }
                : { borderRadius: 6, borderColor: "#fff", borderWidth: 2 },
              label: { show: false },
            };
          });
          billChart = echarts.init(document.getElementById("billChart"), null, { renderer: "svg" });
          billChart.setOption({
            tooltip: { trigger: "item", formatter: "{b}: {c} 元 ({d}%)" },
            color: CAT_COLORS,
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
              label: { show: false },
              labelLine: { show: false },
              data: pieData,
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
    fetch(API + "/agent/audit?user=" + encodeURIComponent(CURRENT_USER))
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
              "<div>" + rec.message + "</div>" +
              decisionLine(rec.decision) + routeLine(rec.decision) +
              "</div>";
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
      body: JSON.stringify({ message: text, user: CURRENT_USER }),
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
  loadViews();       // 视角切换器（多用户）
  refreshAsset();
  refreshSecBadge();
  loadContacts(); // 预载联系人：转账确认卡显示收款人姓名（防转错人）
  input.focus();
  setInterval(refreshSecBadge, 5000);
})();
