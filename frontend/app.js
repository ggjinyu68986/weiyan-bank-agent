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
      if (t.dataset.tab === "bills") { if (billMode === "year") loadAnnual(); else loadBills(); }
      if (t.dataset.tab === "cards") loadCards();
      if (t.dataset.tab === "contacts") loadContacts();
      if (t.dataset.tab === "wealth") { loadWealth(); loadRiskStatus(); }
      if (t.dataset.tab === "subs") loadSubs();
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
  document.getElementById("btnWealthRecommend").onclick = function () { quickSend("根据我的情况推荐几款理财"); };
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
    if (opts.quiz) {
      var box = document.createElement("div");
      box.className = "quiz-options";
      opts.quiz.options.forEach(function (label, i) {
        var b = document.createElement("button");
        b.className = "quiz-opt-btn";
        b.textContent = (i + 1) + ". " + label;
        b.onclick = function () { input.value = label; send(); };
        box.appendChild(b);
      });
      var c = document.createElement("button");
      c.className = "quiz-cancel-btn";
      c.textContent = "✕ 取消问卷";
      c.onclick = function () { input.value = "取消"; send(); };
      box.appendChild(c);
      d.appendChild(box);
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
    if (res.requires === "chat") { addAssistant(res.message, { quiz: res.data && res.data.quiz }); return; }
    if (res.requires === "confirm") { addConfirmCard(res); return; }
    if (res.requires === "mfa") { openMfa(res); return; }
    addAssistant(res.message, { deny: res.requires === "deny", eid: res.execution_id });
    if (res.requires === "deny" && res.message.indexOf("已安全锁定") >= 0) {
      refreshSecBadge();
    }
    // 人工接管流程卡：锁定 → 转人工 → 工单 → 客服核实解锁
    if (res.requires === "deny" && res.data && res.data.card === "lockout") {
      var hc = document.createElement("div");
      hc.className = "handoff-card";
      hc.innerHTML = '<div class="hc-title">🔒 账户已安全锁定</div>' +
        '<div class="hc-sub">' + res.message + "</div>" +
        '<button class="hc-btn" id="btnHandoff">📞 转人工客服</button>';
      chat.appendChild(hc);
      scrollChat();
      document.getElementById("btnHandoff").onclick = function () {
        hc.innerHTML = '<div class="hc-title">📞 正在转接人工客服…</div><div class="hc-sub">请稍候</div>';
        fetch(API + "/agent/handoff", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
          .then(function (r) { return r.json(); }).then(function (r2) {
            var t = (r2.data && r2.data.ticket) || "";
            hc.innerHTML = '<div class="hc-title">📞 人工客服已接管</div>' +
              '<div class="hc-sub">服务工单号：<b>' + t + "</b></div>" +
              '<div class="hc-sub">客服将核实您的身份后为您解锁账户。</div>' +
              '<button class="hc-btn" id="btnResolve">✅ 客服核实完成，解锁账户</button>';
            document.getElementById("btnResolve").onclick = function () {
              hc.innerHTML = '<div class="hc-title">🕒 正在核实并解锁…</div>';
              fetch(API + "/agent/handoff/resolve", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
                .then(function (r) { return r.json(); }).then(function (r3) {
                  hc.innerHTML = '<div class="hc-title">✅ 账户已解锁</div><div class="hc-sub">' + r3.message + "</div>";
                  refreshSecBadge();
                  loadAudit();
                }).catch(function () {
                  hc.innerHTML = '<div class="hc-title">❌ 解锁失败</div><div class="hc-sub">请确认后端已启动</div>';
                });
            };
          }).catch(function () {
            hc.innerHTML = '<div class="hc-title">❌ 转接失败</div><div class="hc-sub">请确认后端已启动</div>';
          });
      };
    }
    if (res.requires === "auto" && res.tool === "analyze_bills") {
      addAssistant("已生成账单图表，可点击底部「账单」查看可视化分析。");
      loadBills();
    }
    if (res.requires === "auto" && res.tool === "wealth_products") {
      addAssistant("已加载理财列表，可点击底部「理财」查看在售产品与持仓。");
      loadWealth();
    }
    if (res.requires === "auto" && res.tool === "wealth_recommend") {
      addAssistant("已生成智能推荐，可点击底部「理财」查看推荐配置。");
      fetch(API + "/agent/wealth/recommend?user_id=1").then(function (r) { return r.json(); }).then(renderWealthRecommend).catch(function () {});
    }
    if (res.requires === "auto" && res.tool === "wealth_compare") {
      addAssistant("对比结果已生成，可点击底部「理财」查看。");
      fetch(API + "/agent/wealth/compare?product_ids=WP-001,WP-002").then(function (r) { return r.json(); }).then(renderWealthCompare).catch(function () {});
    }
    if (res.requires === "auto" && res.tool === "risk_assessment") {
      addAssistant("风险评估完成：可在底部「理财」查看适配与推荐配置。");
    }
    if (res.requires === "auto" && res.tool === "annual_report") {
      addAssistant("已生成年度账单报告，可点击底部「账单」查看年度收支趋势。");
      // 预切年度视图状态：用户点「账单」Tab 即加载年度报告
      billMode = "year";
      document.querySelectorAll("#tab-bills .bill-switch .chip").forEach(function (c) {
        c.classList.toggle("on", c.dataset.billmode === "year");
      });
      document.querySelectorAll("#tab-bills .month-switch").forEach(function (x) {
        x.style.display = "none";
      });
    }
    if (res.tool === "apply_virtual_card" || res.tool === "freeze_card" || res.tool === "unfreeze_card" ||
        res.tool === "report_card_loss" || res.tool === "unlock_card" || res.tool === "adjust_card_limit") {
      loadCards(); // 卡片页保持同步
      if (document.querySelector("#tab-subs.active")) loadSubs();
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

  /* ========== 风险评估（KYC 问卷） ========== */
  var riskAnswers = {};
  function loadRiskStatus() {
    fetch(API + "/agent/wealth/recommend?user_id=1")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var el = document.getElementById("riskStatus");
        el.innerHTML = '<div class="risk-now">当前等级：<b>' + d.level_cn + "</b>" +
          '<button class="ghost-btn" id="btnRiskReeval">重新评估</button></div>';
        document.getElementById("btnRiskReeval").onclick = openRiskModal;
      })
      .catch(function () {});
  }
  function openRiskModal() {
    fetch(API + "/agent/risk/questions")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        riskAnswers = {};
        var el = document.getElementById("riskQuestions");
        el.innerHTML = d.questions.map(function (q) {
          return '<div class="risk-q"><div class="risk-q-text">' + q.text + "</div>" +
            '<div class="risk-opts">' + q.options.map(function (o, i) {
              return '<label class="risk-opt"><input type="radio" name="' + q.id + '" value="' + o.label + '">' +
                '<span>' + o.label + "</span></label>";
            }).join("") + "</div></div>";
        }).join("");
        el.querySelectorAll("input[type=radio]").forEach(function (r) {
          r.onchange = function () { riskAnswers[r.name] = r.value; };
        });
        document.getElementById("riskMask").classList.remove("hidden");
      });
  }
  function submitRisk() {
    if (Object.keys(riskAnswers).length < 6) {
      addAssistant("请完成全部 6 题后再提交评估。", { deny: true });
      return;
    }
    fetch(API + "/agent/risk/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: 1, answers: riskAnswers }),
    })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        document.getElementById("riskMask").classList.add("hidden");
        if (res.requires === "auto") {
          addAssistant(res.message, { eid: res.execution_id });
          loadRiskStatus();  // 刷新等级卡
          loadWealth();      // 刷新推荐（画像变了）
        } else {
          addAssistant(res.message, { deny: true });
        }
      })
      .catch(function () { addAssistant("提交失败，请确认后端已启动。", { deny: true }); });
  }
  document.getElementById("riskSubmit").onclick = submitRisk;
  document.getElementById("riskCancel").onclick = function () {
    document.getElementById("riskMask").classList.add("hidden");
  };

  /* ========== 理财 Tab（场景3：产品推荐与对比） ========== */
  function riskCn(l) { return l === "low" ? "低风险" : l === "mid" ? "中风险" : "高风险"; }
  function loadWealth() {
    fetch(API + "/agent/wealth?user_id=1")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var holdEl = document.getElementById("wealthHoldings");
        if (!d.holdings || !d.holdings.length) {
          holdEl.innerHTML = '<div class="empty">暂无持仓，可申购在售产品</div>';
        } else {
          holdEl.innerHTML = d.holdings.map(function (h) {
            var p = d.products.find(function (x) { return x.id === h.product_id; });
            var nm = p ? p.name : h.product_id;
            return '<div class="wealth-held"><b>' + nm + "</b>" +
              '<span>持有 ' + fmtNum(h.amount_cents) + " 元</span>" +
              '<button class="op wc-btn red" data-msg="赎回 ' + fmtYuan(h.amount_cents) + " 元的" + nm + '">赎回</button></div>';
          }).join("");
          bindWealthOps();
        }
        var other = d.products[1] || d.products[0];
        document.getElementById("wealthProducts").innerHTML = d.products.map(function (p) {
          return '<div class="wealth-card"><div class="wc-top"><b>' + p.name + "</b>" +
            '<span class="risk risk-' + p.risk_level + '">' + riskCn(p.risk_level) + "</span></div>" +
            '<div class="wc-meta">年化 <b>' + (p.expected_return * 100).toFixed(1) + "%</b> · 起购 " +
            fmtNum(p.min_amount_cents) + " 元</div>" +
            '<div class="wc-actions"><button class="op wc-btn" data-msg="对比' + p.name + "和" + other.name + '">对比</button>' +
            '<button class="op wc-btn" data-msg="买 ' + fmtYuan(p.min_amount_cents * 10) + " 元的" + p.name + '">申购</button></div></div>';
        }).join("");
        bindWealthOps();
      });
  }
  function bindWealthOps() {
    document.querySelectorAll("#tab-wealth .wc-btn").forEach(function (b) {
      b.onclick = function () { quickSend(b.dataset.msg); };
    });
  }
  function renderWealthCompare(d) {
    var el = document.getElementById("wealthCompare");
    el.innerHTML = '<table class="cmp-table"><tr><th></th>' +
      d.compare.map(function (c) { return "<th>" + c.name + "</th>"; }).join("") + "</tr>" +
      '<tr><td>年化收益</td>' + d.compare.map(function (c) { return "<td>" + (c.expected_return * 100).toFixed(1) + "%</td>"; }).join("") + "</tr>" +
      '<tr><td>风险等级</td>' + d.compare.map(function (c) { return "<td>" + riskCn(c.risk_level) + "</td>"; }).join("") + "</tr>" +
      '<tr><td>起购金额</td>' + d.compare.map(function (c) { return "<td>" + fmtNum(c.min_amount_cents) + " 元</td>"; }).join("") + "</tr>" +
      "</table><div class='cmp-sug'>💡 " + d.suggestion + "</div>";
    document.getElementById("wealthCompareBox").classList.remove("hidden");
  }
  function renderWealthRecommend(d) {
    var el = document.getElementById("wealthRecommend");
    el.innerHTML = '<div class="rec-summary">📊 ' + d.summary + "</div>" +
      d.recommendations.map(function (r, i) {
        return '<div class="rec-row"><b class="idx">' + (i + 1) + ".</b><b>" + r.name + "</b>" +
          '<span class="risk risk-' + r.risk_level + '">' + riskCn(r.risk_level) + "</span>" +
          '<div class="rec-meta">年化 ' + (r.expected_return * 100).toFixed(1) + "% · 参考投入 " +
          fmtNum(r.suggest_amount_cents) + " 元 · 示例月收益约 " + r.est_monthly_income + " 元</div>" +
          '<div class="rec-reason">' + r.reason + "</div></div>";
      }).join("");
    document.getElementById("wealthRecommendBox").classList.remove("hidden");
  }

  /* ========== 账单 Tab ========== */
  var billMonth = null; // null = 当前月（后端默认）
  var billChart = null; // ECharts 实例复用（切换月份/刷新时先 dispose 再重建）
  var billMode = "month"; // month 月度视图 / year 年度视图
  function switchBillMode(mode) {
    billMode = mode;
    document.querySelectorAll("#tab-bills .bill-switch .chip").forEach(function (c) {
      c.classList.toggle("on", c.dataset.billmode === mode);
    });
    document.querySelectorAll("#tab-bills .month-switch").forEach(function (x) {
      x.style.display = mode === "month" ? "flex" : "none";
    });
    if (mode === "year") loadAnnual(); else loadBills();
  }
  document.querySelectorAll("#tab-bills .bill-switch .chip").forEach(function (c) {
    c.onclick = function () { switchBillMode(c.dataset.billmode); };
  });
  /* 年度账单报告视图：月度收支趋势柱状图 + 年度汇总 + 分类 TOP（赛题场景2：年度账单报告） */
  function loadAnnual() {
    fetch(API + "/agent/annual?account_id=" + ACC)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        document.getElementById("billPeriod").textContent = d.year + "年度";
        document.getElementById("billIncome").textContent = "+" + fmtNum(d.total_income_cents) + " 元";
        document.getElementById("billExpense").textContent = fmtNum(Math.abs(d.total_expense_cents)) + " 元";

        var insight = document.getElementById("billInsight");
        if (insight) {
          var top = d.top_categories[0];
          insight.innerHTML = "📊 <b>" + d.year + " 年度账单</b>：共 " + d.month_count + " 个月有交易，全年支出 <b>" +
            fmtNum(Math.abs(d.total_expense_cents)) + " 元</b>" +
            (top ? "，占比最高分类：<b>" + top.category + "</b>" : "") + " · 点击月份可看当月明细";
        }
        document.getElementById("billCats").innerHTML = d.top_categories.map(function (c, i) {
          return '<div class="cat-row"><span class="c-name"><b class="idx">' + (i + 1) + ".</b>" +
            '<i class="dot" style="background:' + CAT_COLORS[i % CAT_COLORS.length] + '"></i>' + c.category + "</span>" +
            '<span class="c-pct">' + (d.total_expense_cents ? Math.round(Math.abs(c.amount_cents) / Math.abs(d.total_expense_cents) * 100) + "%" : "") + "</span>" +
            '<span class="c-amt">' + fmtNum(Math.abs(c.amount_cents)) + " 元</span></div>";
        }).join("");
        document.getElementById("billAnomalies").innerHTML =
          '<div class="anomaly-empty">年度报告 · 切回「月度」可展开每月异常交易识别</div>';

        if (window.echarts) {
          if (billChart) { billChart.dispose(); billChart = null; }
          billChart = echarts.init(document.getElementById("billChart"), null, { renderer: "svg" });
          billChart.setOption({
            tooltip: { trigger: "axis", axisPointer: { type: "shadow" },
              formatter: function (ps) {
                return ps.map(function (p) { return p.seriesName + "：" + p.value.toFixed(0) + " 元"; }).join("<br>");
              } },
            legend: { data: ["收入", "支出"], top: 4, right: 6, itemWidth: 10, itemHeight: 10, textStyle: { fontSize: 10.5, color: "#6B7280" } },
            grid: { left: 36, right: 10, top: 30, bottom: 24 },
            xAxis: { type: "category", data: d.months.map(function (m) { return m.month + "月"; }),
              axisLine: { lineStyle: { color: "#E4E9F0" } }, axisLabel: { fontSize: 10, color: "#6B7280" } },
            yAxis: { type: "value", splitLine: { lineStyle: { color: "#EEF2F7" } },
              axisLabel: { fontSize: 10, color: "#6B7280", formatter: function (v) { return (v / 1000).toFixed(0) + "k"; } } },
            series: [
              { name: "收入", type: "bar", data: d.months.map(function (m) { return m.income_cents / 100; }),
                itemStyle: { color: "#0E9F6E", borderRadius: [3, 3, 0, 0] } },
              { name: "支出", type: "bar", data: d.months.map(function (m) { return Math.abs(m.expense_cents) / 100; }),
                itemStyle: { color: "#C2610C", borderRadius: [3, 3, 0, 0] } },
            ],
          });
        }
      });
  }
  function loadBills() {
    var q = billMonth ? "?month=" + billMonth : "";
    fetch(API + "/agent/bills" + q)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        document.getElementById("billPeriod").textContent = d.period;
        document.getElementById("billIncome").textContent = "+" + fmtNum(d.total_income_cents) + " 元";
        document.getElementById("billExpense").textContent = fmtNum(Math.abs(d.total_expense_cents)) + " 元";

        var cats = d.by_category.filter(function (c) { return c.amount_cents < 0; });
        var totalExp = Math.abs(d.total_expense_cents);
        function pct(v) { return totalExp ? Math.round(Math.abs(v) / totalExp * 100) + "%" : ""; }
        // 支付宝式明细：序号 + 色点 + 类别 + 占比 + 金额(笔数)
        document.getElementById("billCats").innerHTML = cats.map(function (c, i) {
          return '<div class="cat-row"><span class="c-name"><b class="idx">' + (i + 1) + ".</b>" +
            '<i class="dot" style="background:' + CAT_COLORS[i % CAT_COLORS.length] + '"></i>' + c.category + "</span>" +
            '<span class="c-pct">' + pct(c.amount_cents) + "</span>" +
            '<span class="c-amt">' + fmtNum(Math.abs(c.amount_cents)) + " 元(" + c.count + "笔)</span></div>";
        }).join("");
        // 顶部提示条：占比最高类别（支付宝式智能小结）
        var insight = document.getElementById("billInsight");
        if (insight && cats.length) {
          insight.innerHTML = "🏆 <b>" + cats[0].category + "</b> 分类消费占比最高（" + pct(cats[0].amount_cents) + "）";
        }

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
              // 支付宝式：前 3 大类别扇区外侧 leader-line 标注「类别 占比%」，其余看列表
              label: i < 3
                ? { show: true, formatter: "{b} {d}%", color: "#0B2D5C", fontSize: 10.5, fontWeight: 600, lineHeight: 14 }
                : { show: false },
              labelLine: i < 3
                ? { show: true, length: 9, length2: 7, lineStyle: { color: "#94A3B8", width: 1 } }
                : { show: false },
            };
          });
          billChart = echarts.init(document.getElementById("billChart"), null, { renderer: "svg" });
          billChart.setOption({
            tooltip: { trigger: "item", formatter: "{b}: {c} 元 ({d}%)" },
            color: CAT_COLORS,
            // 支付宝式中心：显示占比最高类别 + 百分比（顶部已有支出总额，中心不重复）
            graphic: [
              { type: "text", left: "center", top: "30%",
                style: { text: cats[maxIdx].category, fill: "#6B7280", fontSize: 12, textAlign: "center" } },
              { type: "text", left: "center", top: "39%",
                style: { text: pct(cats[maxIdx].amount_cents),
                         fill: "#0B2D5C", fontWeight: 800, fontSize: 20, textAlign: "center" } },
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
          var actions = "";
          if (c.status === "active") {
            actions = '<button class="ca-btn" data-op="freeze" data-card="' + c.id + '">冻结</button>' +
              '<button class="ca-btn" data-limit="' + c.id + '" data-cur="' + c.daily_limit_cents + '">额度调整</button>' +
              '<button class="ca-btn red" data-op="lock" data-card="' + c.id + '">挂失</button>';
          } else if (c.status === "frozen") {
            actions = '<button class="ca-btn" data-op="unfreeze" data-card="' + c.id + '">解冻</button>';
          } else if (c.status === "lost") {
            actions = '<button class="ca-btn red" data-op="unlock" data-card="' + c.id + '">解挂</button>';
          }
          return '<div class="bank-card">' +
            '<div class="card-top"><span>' + cardNo + '</span><span class="badge2 ' + cls + '">' + statusText + "</span></div>" +
            '<div class="card-no">•••• •••• •••• ' + c.id.slice(-4) + "</div>" +
            '<div class="card-bottom"><span>日限额 ' + fmtNum(c.daily_limit_cents) + " 元</span>" +
            (c.locked ? '<span style="color:#FECACA">已锁定</span>' : "<span>持卡人：小明</span>") + "</div>" +
            '<div class="card-actions">' + actions + "</div></div>";
        }).join("");
        bindCardOps();
      });
  }
  function cardOp(action, cardId, amountCents) {
    addTyping();
    fetch(API + "/agent/card-op", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: action, card_id: cardId, amount_cents: amountCents }),
    }).then(function (r) { return r.json(); }).then(function (res) {
      removeTyping();
      renderReply(res);
      loadCards();  // 状态变化后刷新卡片列表
    }).catch(function () {
      removeTyping();
      addAssistant("操作失败，请确认后端已启动。", { deny: true });
    });
  }
  function bindCardOps() {
    document.querySelectorAll("#tab-card .ca-btn").forEach(function (b) {
      if (b.dataset.op) {
        b.onclick = function () { cardOp(b.dataset.op, b.dataset.card); };
      }
      if (b.dataset.limit) {
        b.onclick = function () {
          document.getElementById("limitCardInfo").textContent = b.dataset.limit + " · 当前日限额 " + fmtNum(Number(b.dataset.cur)) + " 元";
          document.getElementById("limitInput").value = "";
          document.getElementById("cardLimitMask").classList.remove("hidden");
          document.getElementById("limitInput").focus();
        };
      }
    });
  }
  document.getElementById("limitOk").onclick = function () {
    var v = Number(document.getElementById("limitInput").value);
    var card = document.getElementById("limitCardInfo").textContent.split(" ")[0];
    if (!v || v <= 0) { document.getElementById("limitInput").focus(); return; }
    document.getElementById("cardLimitMask").classList.add("hidden");
    cardOp("limit", card, Math.round(v * 100));
  };
  document.getElementById("limitCancel").onclick = function () {
    document.getElementById("cardLimitMask").classList.add("hidden");
  };
  document.getElementById("btnAddCard").onclick = function () { cardOp("apply"); };

  /* ========== 订阅 Tab ========== */
  function fmtDays(days) {
    if (days < 0) return "已逾期 " + (-days) + " 天";
    if (days === 0) return "今天扣费";
    return days + " 天后扣费";
  }
  function loadSubs() {
    fetch(API + "/agent/subscriptions")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        // 续费提醒条
        var rem = document.getElementById("subReminders");
        if (!d.reminders.length) {
          rem.innerHTML = '<div class="anomaly-empty">暂无待续费订阅</div>';
        } else {
          rem.innerHTML = d.reminders.map(function (s) {
            var urgent = s.urgent ? "urgent" : "";
            return '<div class="reminder-row ' + urgent + '"><span class="r-ico">' +
              (s.urgent ? "🔔" : "⏰") + "</span><b>" + s.merchant + "</b> " +
              fmtNum(s.amount_cents) + " 元/期 · " + fmtDays(s.days_left) +
              '<span class="r-date">' + s.next_bill_date + "</span></div>";
          }).join("");
        }
        // 订阅列表
        var list = document.getElementById("subList");
        if (!d.subscriptions.length) {
          list.innerHTML = '<div class="anomaly-empty">暂无订阅</div>';
          return;
        }
        list.innerHTML = d.subscriptions.map(function (s) {
          return '<div class="sub-card"><div class="sub-top"><b>' + s.merchant + "</b>" +
            '<button class="ca-btn red" data-sub="' + s.id + '" data-name="' + s.merchant + '">取消订阅</button></div>' +
            '<div class="sub-meta">' + s.item + " · " + fmtNum(s.amount_cents) + " 元/期 · 下次扣费 " +
            s.next_bill_date + "</div></div>";
        }).join("");
        bindSubOps();
      });
  }
  function subOp(action, subscriptionId) {
    addTyping();
    fetch(API + "/agent/subscription-op", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: action, subscription_id: subscriptionId }),
    }).then(function (r) { return r.json(); }).then(function (res) {
      removeTyping();
      renderReply(res);
      loadSubs();  // 取消后刷新列表
    }).catch(function () {
      removeTyping();
      addAssistant("操作失败，请确认后端已启动。", { deny: true });
    });
  }
  function bindSubOps() {
    document.querySelectorAll("#tab-subs .ca-btn").forEach(function (b) {
      if (b.dataset.sub) {
        b.onclick = function () { subOp("cancel", b.dataset.sub); };
      }
    });
  }
  document.getElementById("btnSubDetect").onclick = function () { subOp("detect"); };

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
        billMode = "month";
        document.getElementById("wealthRecommendBox").classList.add("hidden");
        document.getElementById("wealthCompareBox").classList.add("hidden");
        document.querySelectorAll("#tab-bills .bill-switch .chip").forEach(function (c) {
          c.classList.toggle("on", c.dataset.billmode === "month");
        });
        document.querySelectorAll("#tab-bills .month-switch").forEach(function (x) {
          x.style.display = "flex";
        });
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
