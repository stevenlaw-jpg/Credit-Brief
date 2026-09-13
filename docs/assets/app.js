/* Credit Brief — frontend. BUILD_SPEC §8.
 *
 * Plain JS, no framework, no build step, no storage of any kind. Both languages
 * ship inside the same JSON, so the toggle swaps the page without a refetch and
 * without a reload. The URL carries the date and the language so a view can be
 * copied and shared.
 */
(function () {
  "use strict";

  var DATA = "data/";
  var TZ = "America/New_York";
  var AGGREGATORS = { "news.google.com": 1, "bing.com": 1, "www.bing.com": 1,
                      "news.yahoo.com": 1, "finance.yahoo.com": 1 };

  /* ------------------------------------------------------------- strings */

  var STRINGS = {
    en: {
      title: "Credit Brief",
      standfirst: "Credit-markets news for a portfolio of 13 managers across 8 sub-sectors. US-focused, with roughly a fifth on Europe.",
      updated: "Updated",
      cadence: function (h) { return "refreshes every " + h + " hours"; },
      itemsOne: "1 item",
      itemsMany: function (n) { return n + " items"; },
      readOriginal: "Read the original",
      alsoReported: function (list) { return "also reported by " + list; },
      key: "Key",
      exposure: "Portfolio exposure",
      exposureNote: function (n) { return "exposed via this sub-sector; not named in the story"; },
      empty: "No relevant news for this date.",
      emptyNote: "The brief refreshes every three hours; a quiet day is reported as a quiet day rather than padded.",
      loading: "Loading…",
      staleTitle: "Automatic updates appear to have stopped.",
      staleNote: function (h) {
        return "The brief last refreshed " + h + " hours ago, against a three-hour schedule. " +
               "The items below are still what was published then, but newer news is missing.";
      },
      errorTitle: "The brief could not be loaded.",
      errorNote: "Please reload the page. If it keeps failing, the last successful build is still served at this address.",
      missingDay: "No brief was published for this date.",
      missingDayNote: "Pick another date above. History begins on the date this system was first built.",
      notYetBuilt: "The first brief has not been published yet.",
      notYetBuiltNote: "The pipeline runs every three hours and writes its first day of news on its next run. History begins on the build date; there is no backfill.",
      footer: function (days, tz) {
        return "Items are ordered newest first by publication time, and dated by the US Eastern calendar day. " +
               "A " + days + "-day rolling archive is kept. Times shown in " + tz + ". " +
               "Filled manager tags are firms named in the story; outlined ones are holdings " +
               "exposed to the sub-sector, shown when the story names no manager.";
      },
      tz: "ET",
      langLabel: "Language",
      today: "Today",
      yesterday: "Yesterday"
    },
    zh: {
      title: "信贷简报",
      standfirst: "覆盖 13 家管理人、8 个信贷子板块的每日信贷市场新闻。以美国为主，约五分之一关注欧洲。",
      updated: "更新于",
      cadence: function (h) { return "每 " + h + " 小时刷新一次"; },
      itemsOne: "1 条",
      itemsMany: function (n) { return n + " 条"; },
      readOriginal: "阅读原文",
      alsoReported: function (list) { return "另见 " + list; },
      key: "重点",
      exposure: "持仓涉及",
      exposureNote: function (n) { return "通过该子板块涉及，文章中未点名"; },
      empty: "该日期没有相关新闻。",
      emptyNote: "简报每三小时刷新一次；清淡的一天会如实呈现，不做填充。",
      loading: "加载中…",
      staleTitle: "自动更新似乎已停止。",
      staleNote: function (h) {
        return "简报上一次刷新在 " + h + " 小时前，而设定的周期为三小时。" +
               "以下条目仍是当时发布的内容，但更新的新闻尚未收录。";
      },
      errorTitle: "简报加载失败。",
      errorNote: "请重新载入页面。若持续失败，此地址仍会提供最近一次成功构建的内容。",
      missingDay: "该日期没有发布简报。",
      missingDayNote: "请在上方选择其他日期。历史记录自本系统建成之日开始。",
      notYetBuilt: "首份简报尚未发布。",
      notYetBuiltNote: "管道每三小时运行一次，将在下次运行时写入第一天的新闻。历史记录自建成之日开始，不做回补。",
      footer: function (days, tz) {
        return "条目按发布时间从新到旧排列，日期按美国东部时区的自然日归档。保留 " + days +
               " 天滚动存档。时间以" + tz + "显示。实心管理人标签表示文章中点名的机构；" +
               "描边标签表示该子板块涉及的持仓，仅在文章未点名任何管理人时显示。";
      },
      tz: "美东时间",
      langLabel: "语言",
      today: "今天",
      yesterday: "昨天"
    }
  };

  /* ---------------------------------------------------------------- state */

  var state = { lang: "en", date: null, index: null, day: null, names: { gps: {}, sectors: {} } };

  var el = {
    body: document.body,
    title: document.getElementById("site-title"),
    standfirst: document.getElementById("site-standfirst"),
    meta: document.getElementById("meta-line"),
    dayHeading: document.getElementById("day-heading"),
    status: document.getElementById("status"),
    items: document.getElementById("items"),
    track: document.getElementById("date-track"),
    newer: document.getElementById("date-newer"),
    older: document.getElementById("date-older"),
    footer: document.getElementById("footer-note"),
    template: document.getElementById("item-template"),
    langButtons: Array.prototype.slice.call(document.querySelectorAll(".lang-toggle button"))
  };

  function t() { return STRINGS[state.lang] || STRINGS.en; }

  /* ------------------------------------------------------------ utilities */

  function qs() {
    var out = {};
    (window.location.search || "").replace(/^\?/, "").split("&").forEach(function (pair) {
      if (!pair) { return; }
      var kv = pair.split("=");
      out[decodeURIComponent(kv[0])] = decodeURIComponent((kv[1] || "").replace(/\+/g, " "));
    });
    return out;
  }

  function setUrl() {
    var url = window.location.pathname + "?date=" + state.date + "&lang=" + state.lang;
    try { history.replaceState(null, "", url); } catch (e) { /* file:// */ }
  }

  function isDate(s) { return typeof s === "string" && /^\d{4}-\d{2}-\d{2}$/.test(s); }

  /* Today's date in the market timezone, so "today" means the same thing to a
     reader in Hong Kong as it does to the pipeline that wrote the files. */
  function todayInMarketTz() {
    try {
      var parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit"
      }).format(new Date());
      return isDate(parts) ? parts : new Date().toISOString().slice(0, 10);
    } catch (e) {
      return new Date().toISOString().slice(0, 10);
    }
  }

  function formatDayHeading(dateStr) {
    var d = new Date(dateStr + "T12:00:00Z");
    var today = todayInMarketTz();
    var opts = { timeZone: "UTC", weekday: "long", day: "numeric", month: "long", year: "numeric" };
    var locale = state.lang === "zh" ? "zh-CN" : "en-GB";
    var label;
    try { label = new Intl.DateTimeFormat(locale, opts).format(d); }
    catch (e) { label = dateStr; }
    if (dateStr === today) { label = t().today + " · " + label; }
    return label;
  }

  function formatChip(dateStr) {
    var d = new Date(dateStr + "T12:00:00Z");
    var locale = state.lang === "zh" ? "zh-CN" : "en-GB";
    try {
      return new Intl.DateTimeFormat(locale, {
        timeZone: "UTC", day: "numeric", month: "short"
      }).format(d);
    } catch (e) { return dateStr.slice(5); }
  }

  /* Timestamps display in ET with the zone labelled, matching the date buckets. */
  function formatStamp(iso) {
    if (!iso) { return ""; }
    var d = new Date(iso);
    if (isNaN(d.getTime())) { return ""; }
    var locale = state.lang === "zh" ? "zh-CN" : "en-GB";
    try {
      var s = new Intl.DateTimeFormat(locale, {
        timeZone: TZ, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
        hour12: false
      }).format(d);
      return s + " " + t().tz;
    } catch (e) {
      return iso.slice(0, 16).replace("T", " ") + " UTC";
    }
  }

  function hostOf(url) {
    try { return new URL(url).hostname.replace(/^www\./, ""); }
    catch (e) { return url; }
  }

  function tagName(kind, id) {
    var table = state.names[kind] || {};
    var entry = table[id];
    if (!entry) { return id; }
    return entry[state.lang] || entry.en || id;
  }

  function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }

  /* ------------------------------------------------------------ rendering */

  function showStatus(headline, note) {
    clear(el.status);
    var p = document.createElement("p");
    p.textContent = headline;
    el.status.appendChild(p);
    if (note) {
      var n = document.createElement("p");
      n.textContent = note;
      el.status.appendChild(n);
    }
    el.status.hidden = false;
  }

  function hideStatus() { el.status.hidden = true; clear(el.status); }

  /* Hours since the last successful pipeline run, or null if unknown. */
  function hoursSinceUpdate() {
    var index = state.index;
    if (!index || !index.generated_at) { return null; }
    var then = new Date(index.generated_at).getTime();
    if (isNaN(then)) { return null; }
    return (Date.now() - then) / 3600000;
  }

  /* A page that quietly serves last week's news as if it were current is worse
     than one that says so. Three missed runs is the threshold. */
  function renderStaleness() {
    var hours = hoursSinceUpdate();
    var interval = (state.index && state.index.update_interval_hours) || 3;
    var banner = document.getElementById("stale");
    if (hours === null || hours < interval * 3) {
      banner.hidden = true;
      clear(banner);
      return;
    }
    clear(banner);
    var head = document.createElement("p");
    head.textContent = t().staleTitle;
    var note = document.createElement("p");
    note.textContent = t().staleNote(Math.round(hours));
    banner.appendChild(head);
    banner.appendChild(note);
    banner.hidden = false;
  }

  function renderChrome() {
    var s = t();
    el.body.setAttribute("lang", state.lang === "zh" ? "zh" : "en");
    document.documentElement.setAttribute("lang", state.lang === "zh" ? "zh-Hans" : "en");
    document.title = s.title;
    el.title.textContent = s.title;
    el.standfirst.textContent = s.standfirst;

    el.langButtons.forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-lang") === state.lang));
    });

    var index = state.index;
    if (index) {
      var bits = [];
      if (index.generated_at) { bits.push(s.updated + " " + formatStamp(index.generated_at)); }
      bits.push(s.cadence(index.update_interval_hours || 3));
      el.meta.textContent = bits.join(" · ");
      el.footer.textContent = s.footer(index.retention_days || 90, s.tz);
    } else {
      el.meta.textContent = "";
      el.footer.textContent = "";
    }
  }

  function renderDates() {
    clear(el.track);
    var index = state.index;
    var nav = document.getElementById("dates");
    if (!index || !index.dates || !index.dates.length) {
      el.newer.disabled = true; el.older.disabled = true;
      nav.hidden = true;             /* no dates: hide the whole strip, not just the chips */
      return;
    }
    nav.hidden = false;
    index.dates.forEach(function (entry) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "date-chip";
      b.setAttribute("role", "listitem");
      b.setAttribute("data-date", entry.date);
      b.appendChild(document.createTextNode(formatChip(entry.date)));
      var count = document.createElement("span");
      count.className = "count";
      count.textContent = entry.count;
      b.appendChild(count);
      b.setAttribute("aria-label", formatDayHeading(entry.date) + ", " +
        (entry.count === 1 ? t().itemsOne : t().itemsMany(entry.count)));
      if (entry.date === state.date) { b.setAttribute("aria-current", "date"); }
      b.addEventListener("click", function () { go(entry.date); });
      el.track.appendChild(b);
    });

    var i = indexOfDate(state.date);
    el.newer.disabled = !(i > 0);
    el.older.disabled = !(i > -1 && i < index.dates.length - 1);

    var current = el.track.querySelector('[aria-current="date"]');
    if (current && current.scrollIntoView) {
      current.scrollIntoView({ block: "nearest", inline: "center" });
    }
  }

  function indexOfDate(date) {
    var dates = (state.index && state.index.dates) || [];
    for (var i = 0; i < dates.length; i++) { if (dates[i].date === date) { return i; } }
    return -1;
  }

  function renderItems() {
    clear(el.items);
    var day = state.day;
    if (!day) { return; }

    var items = day.items || [];
    if (!items.length) {
      showStatus(t().empty, t().emptyNote);
      return;
    }
    hideStatus();

    /* Render in file order. The pipeline already sorted newest-first and the
       frontend never re-sorts: importance is a tag, never a position. */
    items.forEach(function (item) {
      var node = el.template.content.cloneNode(true);
      var li = node.querySelector(".item");
      var zh = state.lang === "zh";

      li.querySelector(".item-title").textContent =
        (zh ? item.title_zh : item.title_en) || item.title_en || "";
      li.querySelector(".item-summary").textContent =
        (zh ? item.summary_zh : item.summary_en) || item.summary_en || "";

      var link = li.querySelector(".item-link");
      link.href = item.url || "#";
      link.textContent = t().readOriginal + (item.source ? " (" + item.source + ")" : "");

      var time = li.querySelector(".item-time");
      time.textContent = formatStamp(item.published_at);
      if (item.published_at) { time.setAttribute("datetime", item.published_at); }

      /* "Also reported by" names publishers. An aggregator host is not a
         publisher, and the same host twice is not corroboration. */
      var also = li.querySelector(".item-also");
      var seenHosts = {};
      var others = [];
      (item.also_urls || []).forEach(function (u) {
        var h = hostOf(u);
        if (!h || AGGREGATORS[h] || seenHosts[h] || h === hostOf(item.url)) { return; }
        seenHosts[h] = true;
        others.push(h);
      });
      others = others.slice(0, 2);
      if (others.length) { also.textContent = t().alsoReported(others.join(", ")); }
      else { also.remove(); }

      /* R9 + §8.2: importance first and only on Tier 1, then GP, then sub-sector. */
      var tags = li.querySelector(".tags");
      if (Number(item.importance) === 1) {
        tags.appendChild(makeTag("tag-key", t().key));
      }
      (item.gp_ids || []).forEach(function (id) {
        tags.appendChild(makeTag("tag-gp", tagName("gps", id)));
      });
      (item.sector_ids || []).forEach(function (id) {
        tags.appendChild(makeTag("tag-sector", tagName("sectors", id)));
      });
      /* Managers exposed to this sub-sector but not named in the story. Outlined,
         never filled, so an inferred exposure can never read as a confirmed
         mention. Only ever present when no manager was confirmed. */
      var exposure = item.exposure_gp_ids || [];
      if (exposure.length) {
        var label = makeTag("tag-exposure-label", t().exposure);
        label.setAttribute("title", t().exposureNote(exposure.length));
        tags.appendChild(label);
        exposure.forEach(function (id) {
          var tag = makeTag("tag-exposure", tagName("gps", id));
          tag.setAttribute("title", t().exposureNote(1));
          tags.appendChild(tag);
        });
      }
      if (!tags.childNodes.length) { tags.remove(); }

      el.items.appendChild(node);
    });
  }

  function makeTag(cls, text) {
    var span = document.createElement("span");
    span.className = "tag " + cls;
    span.textContent = text;
    return span;
  }

  function renderDayHeading() {
    if (!state.date) { el.dayHeading.textContent = ""; return; }
    var count = state.day ? (state.day.items || []).length : null;
    var label = formatDayHeading(state.date);
    if (count !== null) {
      label += " · " + (count === 1 ? t().itemsOne : t().itemsMany(count));
    }
    el.dayHeading.textContent = label;
  }

  function renderAll() {
    renderChrome();
    renderStaleness();
    renderDates();
    renderDayHeading();
    renderItems();
  }

  /* --------------------------------------------------------------- data */

  /* GitHub Pages serves these with a ten-minute max-age, so a reader refreshing
     just after a run would otherwise be shown the previous build. The index is
     always fetched fresh; a day file is versioned by its own `updated_at`, so a
     past day still caches properly and the current day busts when it changes. */
  function fetchJson(path, version) {
    var url = path + "?v=" + encodeURIComponent(version || Date.now());
    return fetch(url, { cache: "no-store" }).then(function (r) {
      if (!r.ok) { throw new Error(path + ": HTTP " + r.status); }
      return r.json();
    });
  }

  function dayVersion(date) {
    var dates = (state.index && state.index.dates) || [];
    for (var i = 0; i < dates.length; i++) {
      if (dates[i].date === date) { return dates[i].updated_at || dates[i].count; }
    }
    return Date.now();
  }

  function go(date) {
    if (!isDate(date) || date === state.date) { return; }
    state.date = date;
    setUrl();
    loadDay(date);
  }

  function loadDay(date) {
    showStatus(t().loading);
    renderDates();
    renderDayHeading();
    fetchJson(DATA + date + ".json", dayVersion(date)).then(function (day) {
      state.day = day;
      hideStatus();
      renderDayHeading();
      renderItems();
    }).catch(function () {
      state.day = { date: date, items: [] };
      renderDayHeading();
      clear(el.items);
      showStatus(t().missingDay, t().missingDayNote);
    });
  }

  /* Default date: the most recent date in the index that is <= today (ET). */
  function defaultDate(index) {
    var today = todayInMarketTz();
    var dates = (index.dates || []).map(function (d) { return d.date; });
    for (var i = 0; i < dates.length; i++) {      /* already newest-first */
      if (dates[i] <= today) { return dates[i]; }
    }
    return dates[0] || today;
  }

  function setLang(lang) {
    if (lang !== "en" && lang !== "zh") { return; }
    if (lang === state.lang) { return; }
    state.lang = lang;
    setUrl();
    renderAll();                 /* no reload, no refetch: both languages are loaded */
  }

  function bind() {
    el.langButtons.forEach(function (b) {
      b.addEventListener("click", function () { setLang(b.getAttribute("data-lang")); });
    });
    el.newer.addEventListener("click", function () {
      var i = indexOfDate(state.date);
      if (i > 0) { go(state.index.dates[i - 1].date); }
    });
    el.older.addEventListener("click", function () {
      var i = indexOfDate(state.date);
      if (i > -1 && i < state.index.dates.length - 1) { go(state.index.dates[i + 1].date); }
    });
    document.addEventListener("keydown", function (e) {
      if (e.metaKey || e.ctrlKey || e.altKey) { return; }
      var tag = (e.target && e.target.tagName) || "";
      if (tag === "INPUT" || tag === "TEXTAREA") { return; }
      if (e.key === "ArrowLeft") { el.older.click(); }
      if (e.key === "ArrowRight") { el.newer.click(); }
    });
  }

  function boot() {
    var params = qs();
    state.lang = (params.lang === "zh" || params.lang === "en") ? params.lang : "en";
    renderChrome();
    showStatus(t().loading);
    bind();

    fetchJson(DATA + "index.json").then(function (index) {
      state.index = index;
      state.names = index.names || { gps: {}, sectors: {} };
      if (!state.lang && index.default_language) { state.lang = index.default_language; }

      var wanted = isDate(params.date) ? params.date : null;
      var known = (index.dates || []).some(function (d) { return d.date === wanted; });
      state.date = (wanted && known) ? wanted : defaultDate(index);

      setUrl();
      renderAll();

      /* Before the first successful run the index is valid but empty. Say so,
         rather than letting it read as a missing day. */
      if (!index.dates || !index.dates.length) {
        clear(el.items);
        el.dayHeading.textContent = "";
        showStatus(t().notYetBuilt, t().notYetBuiltNote);
        return;
      }
      if (state.date) { loadDay(state.date); }
    }).catch(function (err) {
      /* Never a silent blank page. */
      if (window.console) { console.error(err); }
      clear(el.items);
      showStatus(t().errorTitle, t().errorNote);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
