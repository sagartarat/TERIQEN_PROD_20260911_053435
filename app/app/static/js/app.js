/* Production CSRF protection for same-origin forms and fetch requests. */
(function () {
  const meta = document.querySelector('meta[name="csrf-token"]');
  const token = meta ? meta.getAttribute("content") : "";
  if (!token) return;

  const unsafeMethods = new Set(["POST", "PUT", "PATCH", "DELETE"]);
  const originalFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    const options = Object.assign({}, init || {});
    const method = String(options.method || "GET").toUpperCase();
    let target = "";
    try { target = typeof input === "string" ? input : input.url; } catch (_) {}
    const sameOrigin = !target || target.startsWith("/") || target.startsWith(window.location.origin);
    if (sameOrigin && unsafeMethods.has(method)) {
      const headers = new Headers(options.headers || {});
      if (!headers.has("X-CSRFToken")) headers.set("X-CSRFToken", token);
      options.headers = headers;
    }
    return originalFetch(input, options);
  };

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll('form[method="post"], form[method="POST"]').forEach(function (form) {
      if (form.querySelector('input[name="csrf_token"]')) return;
      const hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = "csrf_token";
      hidden.value = token;
      form.appendChild(hidden);
    });
  });
})();

/* =========================
SIDEBAR expand/collapse
========================= */
document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll(".menu-item").forEach(item => {
    item.addEventListener("click", () => {
      const submenu = item.nextElementSibling;
      const arrow = item.querySelector(".arrow");

      document.querySelectorAll(".submenu").forEach(s => {
        if (s !== submenu) s.style.display = "none";
      });

      document.querySelectorAll(".arrow").forEach(a => {
        if (a !== arrow) a.classList.remove("open");
      });

      if (submenu && submenu.style.display === "flex") {
        submenu.style.display = "none";
        if (arrow) arrow.classList.remove("open");
      } else if (submenu) {
        submenu.style.display = "flex";
        if (arrow) arrow.classList.add("open");
      }
    });
  });

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".menu-item") && !e.target.closest(".submenu")) {
      document.querySelectorAll(".submenu").forEach(
        s => s.style.display = "none"
      );

      document.querySelectorAll(".arrow").forEach(
        a => a.classList.remove("open")
      );
    }
  });

  /* =========================
  PARTNER VIEW: filtering + pagination
  (runs ONLY if partnerTable exists)
  ========================= */
  const table = document.getElementById("partnerTable");
  if (!table) return;

  const tbody = table.querySelector("tbody");
  if (!tbody) return;

  const searchText = document.getElementById("searchText");
  const statusFilter = document.getElementById("statusFilter");
  const gstFilter = document.getElementById("gstFilter");
  const resetBtn = document.getElementById("btnReset");

  const pageSizeEl = document.getElementById("pageSize");
  const pagerInfo = document.getElementById("pagerInfo");
  const pagerButtons = document.getElementById("pagerButtons");
  const btnPrev = document.getElementById("btnPrev");
  const btnNext = document.getElementById("btnNext");
  const tableScroll = document.getElementById("tableScroll");

  function norm(v) {
    return (v || "").toString().toLowerCase().trim();
  }

  let currentPage = 1;

  const allRows = Array.from(
    tbody.querySelectorAll("tr.data-row")
  );

  const noDataRow = tbody.querySelector("tr.no-data-row");

  function getFilteredRows() {
    const q = norm(searchText && searchText.value);

    const st =
      (statusFilter && statusFilter.value)
        ? statusFilter.value.trim()
        : "";

    const gst =
      (gstFilter && gstFilter.value)
        ? gstFilter.value.trim()
        : "";

    return allRows.filter(row => {
      const rowText = norm([
        row.querySelector(".col-type")?.textContent,
        row.querySelector(".col-short")?.textContent,
        row.querySelector(".col-country")?.textContent,
        row.querySelector(".col-contact")?.textContent
      ].join(" "));

      const rowStatusRaw =
        (row.getAttribute("data-status") || "").trim();

      const rowGstRaw =
        (row.getAttribute("data-gst") || "").trim();

      const rowStatus =
        rowStatusRaw.toLowerCase().includes("active")
          ? "Active"
          : rowStatusRaw.toLowerCase().includes("inactive")
            ? "Inactive"
            : rowStatusRaw;

      const rowGst =
        rowGstRaw.toLowerCase().includes("yes")
          ? "Yes"
          : rowGstRaw.toLowerCase().includes("no")
            ? "No"
            : rowGstRaw;

      return (
        (q === "" || rowText.includes(q)) &&
        (st === "" || rowStatus === st) &&
        (gst === "" || rowGst === gst)
      );
    });
  }

  function clearPageNumberButtons() {
    if (!pagerButtons || !btnNext) return;

    const nodes = Array.from(
      pagerButtons.querySelectorAll("button.page-num")
    );

    nodes.forEach(n => n.remove());
  }

  function buildPageNumberButtons(totalPages) {
    if (!pagerButtons || !btnNext) return;

    clearPageNumberButtons();

    if (totalPages <= 1) return;

    const maxButtons = 7;

    let start = Math.max(
      1,
      currentPage - Math.floor(maxButtons / 2)
    );

    let end = start + maxButtons - 1;

    if (end > totalPages) {
      end = totalPages;
      start = Math.max(
        1,
        end - maxButtons + 1
      );
    }

    for (let p = start; p <= end; p++) {
      const b = document.createElement("button");

      b.className =
        "page-btn page-num" +
        (p === currentPage ? " active" : "");

      b.textContent = String(p);

      b.addEventListener("click", () => {
        currentPage = p;
        render();
      });

      pagerButtons.insertBefore(
        b,
        btnNext
      );
    }
  }

  function render() {
    const pageSize = parseInt(
      pageSizeEl?.value || "10",
      10
    );

    const filtered = getFilteredRows();
    const total = filtered.length;

    const totalPages = Math.max(
      1,
      Math.ceil(total / pageSize)
    );

    if (currentPage > totalPages) {
      currentPage = totalPages;
    }

    if (currentPage < 1) {
      currentPage = 1;
    }

    allRows.forEach(
      r => r.style.display = "none"
    );

    if (total === 0) {
      if (noDataRow) {
        noDataRow.style.display = "";
      }

      if (pagerInfo) {
        pagerInfo.textContent =
          "Showing 0–0 of 0";
      }

      if (btnPrev) {
        btnPrev.disabled = true;
      }

      if (btnNext) {
        btnNext.disabled = true;
      }

      buildPageNumberButtons(1);

      return;
    } else {
      if (noDataRow) {
        noDataRow.style.display = "none";
      }
    }

    const startIdx =
      (currentPage - 1) * pageSize;

    const endIdx =
      Math.min(
        startIdx + pageSize,
        total
      );

    filtered
      .slice(startIdx, endIdx)
      .forEach(
        r => r.style.display = ""
      );

    if (pagerInfo) {
      pagerInfo.textContent =
        `Showing ${startIdx + 1}–${endIdx} of ${total}`;
    }

    if (btnPrev) {
      btnPrev.disabled =
        currentPage <= 1;
    }

    if (btnNext) {
      btnNext.disabled =
        currentPage >= totalPages;
    }

    buildPageNumberButtons(
      totalPages
    );

    if (tableScroll) {
      tableScroll.scrollTop = 0;
    }
  }

  function resetFilters() {
    if (searchText) {
      searchText.value = "";
    }

    if (statusFilter) {
      statusFilter.value = "";
    }

    if (gstFilter) {
      gstFilter.value = "";
    }

    currentPage = 1;

    render();
  }

  if (searchText) {
    searchText.addEventListener(
      "input",
      () => {
        currentPage = 1;
        render();
      }
    );
  }

  if (statusFilter) {
    statusFilter.addEventListener(
      "change",
      () => {
        currentPage = 1;
        render();
      }
    );
  }

  if (gstFilter) {
    gstFilter.addEventListener(
      "change",
      () => {
        currentPage = 1;
        render();
      }
    );
  }

  if (resetBtn) {
    resetBtn.addEventListener(
      "click",
      resetFilters
    );
  }

  if (pageSizeEl) {
    pageSizeEl.addEventListener(
      "change",
      () => {
        currentPage = 1;
        render();
      }
    );
  }

  if (btnPrev) {
    btnPrev.addEventListener(
      "click",
      () => {
        if (currentPage > 1) {
          currentPage--;
          render();
        }
      }
    );
  }

  if (btnNext) {
    btnNext.addEventListener(
      "click",
      () => {
        currentPage++;
        render();
      }
    );
  }

  render();
});


/* =========================
PARTNER VIEW: actions
(global functions because HTML uses onclick="")
========================= */
window.viewPartner = function (btn) {
  const key =
    btn.getAttribute("data-key") || "";

  window.location.href =
    `/partner-creation?mode=view&key=${encodeURIComponent(key)}`;
};


window.editPartner = function (btn) {
  const key =
    btn.getAttribute("data-key") || "";

  window.location.href =
    `/partner-creation?mode=modify&key=${encodeURIComponent(key)}`;
};


window.deletePartner = function (btn) {
  const key =
    btn.getAttribute("data-key") || "";

  if (!key) return;

  if (
    confirm(
      "Are you sure you want to delete " +
      key +
      " ?"
    )
  ) {
    const form =
      document.createElement("form");

    form.method = "POST";

    form.action =
      `/partner-delete/${encodeURIComponent(key)}`;

// Add CSRF token to dynamically created POST form
const csrfToken =
  document.querySelector('meta[name="csrf-token"]')?.getAttribute("content");

if (csrfToken) {
  const csrfInput = document.createElement("input");
  csrfInput.type = "hidden";
  csrfInput.name = "csrf_token";
  csrfInput.value = csrfToken;
  form.appendChild(csrfInput);
}

    document.body.appendChild(form);

    form.submit();
  }
};


/* =========================================================
   TERIQEN APPLICATION LOGOUT SYNC
   ---------------------------------------------------------
   Purpose:
   When Logout is clicked in the main TERIQEN application,
   notify other TERIQEN browser windows/popups.

   Example:
   - Email Communication
   - Future popup screens
========================================================= */

const TERIQEN_LOGOUT_STORAGE_KEY =
  "teriqen_logout_event";

const TERIQEN_SESSION_CHANNEL =
  "teriqen_session";

let teriqenSessionChannel = null;


/* ---------------------------------------------------------
   Create BroadcastChannel when supported by browser
--------------------------------------------------------- */
try {
  if ("BroadcastChannel" in window) {
    teriqenSessionChannel =
      new BroadcastChannel(
        TERIQEN_SESSION_CHANNEL
      );
  }
} catch (error) {
  console.warn(
    "TERIQEN BroadcastChannel could not be initialized.",
    error
  );
}


/* ---------------------------------------------------------
   Broadcast logout to other TERIQEN windows
--------------------------------------------------------- */
function broadcastTeriqenLogout() {
  const logoutEvent = {
    type: "LOGOUT",
    timestamp: Date.now()
  };


  /* BroadcastChannel */
  if (teriqenSessionChannel) {
    try {
      teriqenSessionChannel.postMessage(
        logoutEvent
      );
    } catch (error) {
      console.warn(
        "Unable to broadcast TERIQEN logout.",
        error
      );
    }
  }


  /* localStorage fallback */
  try {
    localStorage.setItem(
      TERIQEN_LOGOUT_STORAGE_KEY,
      String(Date.now())
    );
  } catch (error) {
    console.warn(
      "Unable to publish TERIQEN logout through localStorage.",
      error
    );
  }
}


/* ---------------------------------------------------------
   Existing base.html calls:

   handleAppLogout(event, logoutUrl)

   IMPORTANT:
   This function does NOT change the existing Flask logout
   endpoint. It only broadcasts the event and then allows
   the existing anchor navigation to continue.
--------------------------------------------------------- */
window.handleAppLogout =
  function (event, logoutUrl) {

    try {
      broadcastTeriqenLogout();
    } catch (error) {
      console.warn(
        "TERIQEN logout notification failed.",
        error
      );
    }


    /*
     * Do not stop normal logout.
     *
     * base.html already contains:
     *
     * href="{{ url_for('logout') }}"
     *
     * Returning true allows the browser to continue
     * normally to the Flask logout route.
     */
    return true;
  };

/* =========================================================
   TERIQEN SESSION INACTIVITY + BROWSER PRIVACY
   ---------------------------------------------------------
   - Server-side permanent session lifetime remains authoritative.
   - User activity is shared across TERIQEN tabs.
   - A small heartbeat is sent only while the user is active, so a
     user completing a long form is not logged out while typing.
   - Five minutes without activity triggers immediate logout.
   - Pages restored from the browser back/forward cache are reloaded
     so Flask can re-validate the session instead of showing stale data.
========================================================= */
(function () {
  "use strict";

  const idleMeta = document.querySelector('meta[name="teriqen-session-idle-seconds"]');
  const heartbeatMeta = document.querySelector('meta[name="teriqen-session-heartbeat-url"]');
  const logoutMeta = document.querySelector('meta[name="teriqen-session-logout-url"]');

  if (!idleMeta || !heartbeatMeta || !logoutMeta) return;

  const idleSeconds = Math.max(60, Number(idleMeta.getAttribute("content") || 300));
  const idleMs = idleSeconds * 1000;
  const heartbeatMs = Math.max(30000, Math.min(60000, Math.floor(idleMs / 3)));
  const activityPublishMs = 10000;
  const tickMs = 5000;

  const ACTIVITY_STORAGE_KEY = "teriqen_last_activity";
  let lastActivity = Date.now();
  let lastActivityPublish = 0;
  let lastHeartbeat = Date.now();
  let heartbeatInFlight = false;
  let idleLogoutStarted = false;

  function readSharedActivity() {
    try {
      const stored = Number(localStorage.getItem(ACTIVITY_STORAGE_KEY) || 0);
      return Number.isFinite(stored) ? stored : 0;
    } catch (_) {
      return 0;
    }
  }

  function publishActivity(now) {
    if (now - lastActivityPublish < activityPublishMs) return;
    lastActivityPublish = now;

    try {
      localStorage.setItem(ACTIVITY_STORAGE_KEY, String(now));
    } catch (_) {}

    if (teriqenSessionChannel) {
      try {
        teriqenSessionChannel.postMessage({ type: "ACTIVITY", timestamp: now });
      } catch (_) {}
    }
  }

  function markActivity() {
    const now = Date.now();
    lastActivity = now;
    publishActivity(now);
  }

  ["pointerdown", "keydown", "input", "touchstart", "scroll", "focus"].forEach(function (eventName) {
    window.addEventListener(eventName, markActivity, { passive: true });
  });

  window.addEventListener("storage", function (event) {
    if (event.key !== ACTIVITY_STORAGE_KEY || !event.newValue) return;
    const ts = Number(event.newValue || 0);
    if (Number.isFinite(ts) && ts > lastActivity) lastActivity = ts;
  });

  if (teriqenSessionChannel) {
    try {
      teriqenSessionChannel.addEventListener("message", function (event) {
        if (!event.data || event.data.type !== "ACTIVITY") return;
        const ts = Number(event.data.timestamp || 0);
        if (Number.isFinite(ts) && ts > lastActivity) lastActivity = ts;
      });
    } catch (_) {}
  }

  async function sendHeartbeat() {
    if (heartbeatInFlight || idleLogoutStarted) return;
    heartbeatInFlight = true;
    try {
      const response = await fetch(heartbeatMeta.getAttribute("content"), {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        redirect: "follow"
      });

      // An expired Flask-Login session normally redirects to the login page.
      // fetch() follows redirects, so check both status and final URL.
      const finalUrl = new URL(response.url, window.location.origin);
      if (response.status !== 204 || response.redirected || finalUrl.pathname === "/") {
        idleLogoutStarted = true;
        window.location.replace("/");
        return;
      }
      lastHeartbeat = Date.now();
    } catch (_) {
      // Network interruption is not treated as a valid session refresh.  The
      // authoritative server-side expiry will still protect the session.
    } finally {
      heartbeatInFlight = false;
    }
  }

  function beginIdleLogout() {
    if (idleLogoutStarted) return;
    idleLogoutStarted = true;
    try { broadcastTeriqenLogout(); } catch (_) {}
    window.location.replace(logoutMeta.getAttribute("content") + "?reason=idle");
  }

  function sessionTick() {
    const now = Date.now();
    const shared = readSharedActivity();
    if (shared > lastActivity) lastActivity = shared;

    const idleFor = now - lastActivity;
    if (idleFor >= idleMs) {
      beginIdleLogout();
      return;
    }

    // Refresh the server session only when there has been recent activity.
    // This keeps active users working while preserving a true idle timeout.
    if (now - lastHeartbeat >= heartbeatMs && idleFor < heartbeatMs + tickMs) {
      sendHeartbeat();
    }
  }

  // Page load itself is activity and the request has already refreshed the
  // server session. Share that activity with sibling TERIQEN tabs.
  markActivity();
  window.setInterval(sessionTick, tickMs);

  // Modern browsers may restore no-store pages from BFCache. Reload a restored
  // page so the server re-checks authentication before anything remains usable.
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) window.location.reload();
  });
})();

