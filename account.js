// The account page (account.html): sign in with Discord, GitHub or an emailed code, see the
// account, sign out, delete it. It talks to the accounts service at /api/ on this same site
// (forza-live, accounts/README.md): allauth's headless browser API under /api/auth/browser/v1/
// for the sign-ins, and /api/me, /api/logout-all and /api/account/delete. Every change sends the
// CSRF token from the __Host-fh_csrf cookie. Where the site has no /api/ (a static copy), the
// page says accounts are not available. Text from the API goes in with textContent only.
// The localStorage key fh-signed-in is the navbar's hint (nav.js): it says "Account" instead of
// "Sign in", without other pages ever calling the API.
(function () {
  "use strict";

  var AUTH = "/api/auth/browser/v1/";
  var HINT = "fh-signed-in";
  var stateEl = document.getElementById("account-state");
  var messageEl = document.getElementById("account-message");
  var signin = document.getElementById("signin");
  var profile = document.getElementById("profile");
  var emailForm = document.getElementById("email-form");
  var codeForm = document.getElementById("code-form");
  var me = null;

  var ERRORS = {
    busy: "Too many sign-ins from your network. Try again in an hour.",
    cancelled: "Sign-in was cancelled.",
    denied: "Sign-in was cancelled.",
    unavailable: "That sign-in method is not available right now."
  };
  var METHODS = { discord: "Discord", github: "GitHub", email: "Email" };

  function hint(on) {
    try {
      if (on) localStorage.setItem(HINT, "1");
      else localStorage.removeItem(HINT);
    } catch (e) { /* storage off: the navbar keeps saying Sign in */ }
    var link = document.querySelector(".nav-account");
    if (link) link.textContent = on ? "Account" : "Sign in";
  }

  function csrf() {
    var m = document.cookie.match(/(?:^|;\s*)__Host-fh_csrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function say(text, isError) {
    messageEl.textContent = text || "";
    messageEl.hidden = !text;
    messageEl.classList.toggle("error", !!isError);
  }

  function status(text) {
    stateEl.textContent = text || "";
    stateEl.hidden = !text;
  }

  function call(method, path, body) {
    var opts = { method: method, credentials: "same-origin", headers: { "Accept": "application/json" } };
    if (method !== "GET") {
      opts.headers["Content-Type"] = "application/json";
      opts.headers["X-CSRFToken"] = csrf();
      opts.body = JSON.stringify(body || {});
    }
    return fetch(path, opts).then(function (r) {
      var type = r.headers.get("Content-Type") || "";
      if (type.indexOf("application/json") !== 0) return { status: r.status, doc: null };
      return r.json().then(function (doc) { return { status: r.status, doc: doc }; },
                           function () { return { status: r.status, doc: null }; });
    });
  }

  function firstError(doc, fallback) {
    var e = doc && doc.errors && doc.errors[0];
    return (e && typeof e.message === "string" && e.message) || fallback;
  }

  function text(tag, value, cls) {
    var e = document.createElement(tag);
    e.textContent = value;
    if (cls) e.className = cls;
    return e;
  }

  function when(iso) {
    var d = new Date(iso);
    return isNaN(d) ? "" : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  }

  function showSignedOut(config) {
    me = null;
    hint(false);
    profile.hidden = true;
    signin.hidden = false;
    var providers = (config && config.socialaccount && config.socialaccount.providers) || [];
    var ids = providers.map(function (p) { return p && p.id; });
    var forms = signin.querySelectorAll("form[data-provider]");
    for (var i = 0; i < forms.length; i++) forms[i].hidden = ids.indexOf(forms[i].dataset.provider) < 0;
    var byCode = !!(config && config.account && config.account.login_by_code_enabled);
    emailForm.hidden = !byCode;
    if (!byCode) codeForm.hidden = true;
    if (!ids.length && !byCode) say("Sign-in is not available right now.", true);
  }

  function showAccount(doc) {
    me = doc;
    hint(true);
    signin.hidden = true;
    profile.hidden = false;
    document.getElementById("account-id").textContent = doc.id;
    document.getElementById("account-created").textContent = new Date(doc.created + "T00:00:00Z")
      .toLocaleDateString(undefined, { dateStyle: "long", timeZone: "UTC" });
    document.getElementById("account-methods").textContent =
      (doc.methods || []).map(function (m) { return METHODS[m] || m; }).join(", ");
    document.getElementById("account-sessions").textContent =
      doc.sessions === 1 ? "1 browser" : doc.sessions + " browsers";
    var list = document.getElementById("account-events");
    list.textContent = "";
    (doc.events || []).forEach(function (ev) {
      var li = document.createElement("li");
      li.appendChild(text("time", when(ev.at)));
      li.appendChild(document.createTextNode(" "));
      li.appendChild(text("span", ev.event));
      list.appendChild(li);
    });
    document.getElementById("delete-reauth").hidden = !!doc.recent;
  }

  function load(config) {
    return call("GET", "/api/me").then(function (r) {
      status("");
      if (r.status === 200 && r.doc && r.doc.id) showAccount(r.doc);
      else showSignedOut(config);
    });
  }

  function unavailable() {
    hint(false);
    status("Website accounts are not available on this copy of the site.");
  }

  // ?error=... after a Discord or GitHub sign-in, #code=... from the mail's link.
  function readAddress() {
    var params = new URLSearchParams(location.search);
    var error = params.get("error");
    var code = (location.hash.match(/^#code=([0-9]{8})$/) || [])[1];
    if (error || location.hash) history.replaceState(null, "", location.pathname);
    if (error) say(ERRORS[error] || "Signing in did not work. Try again.", true);
    return code;
  }

  var mailCode = readAddress();

  call("GET", AUTH + "config").then(function (r) {
    if (r.status !== 200 || !r.doc || !r.doc.data) return unavailable();
    var config = r.doc.data;
    return load(config).then(function () {
      if (mailCode && !me) {
        codeForm.hidden = false;
        document.getElementById("code").value = mailCode;
        confirmCode(mailCode, config);
      }
      wire(config);
    });
  }, unavailable);

  function confirmCode(code, config) {
    return call("POST", AUTH + "auth/code/confirm", { code: code.replace(/[\s-]/g, "") }).then(function (r) {
      if (r.status === 200) {
        say("");
        codeForm.hidden = true;
        return load(config);
      }
      if (r.status === 409) {
        codeForm.hidden = true;
        say("That code no longer works: it is used, too old, or from another browser. Ask for a new one.", true);
      } else if (r.status === 429) {
        say(firstError(r.doc, "Too many wrong codes. Try again later."), true);
      } else {
        say(firstError(r.doc, "That code is not right."), true);
      }
    });
  }

  function wire(config) {
    var forms = signin.querySelectorAll("form[data-provider]");
    for (var i = 0; i < forms.length; i++) {
      forms[i].addEventListener("submit", function (e) {
        e.currentTarget.elements.csrfmiddlewaretoken.value = csrf();
      });
    }

    emailForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var address = document.getElementById("email").value.trim();
      call("POST", AUTH + "auth/code/request", { email: address }).then(function (r) {
        // allauth answers 401 with a pending code login, for every address alike.
        if (r.status === 401 || r.status === 200) {
          say("If that address can sign in, a code is on its way. It can take a minute.");
          codeForm.hidden = false;
          document.getElementById("code").focus();
        } else {
          say(firstError(r.doc, "The code could not be sent. Try again later."), true);
        }
      });
    });

    codeForm.addEventListener("submit", function (e) {
      e.preventDefault();
      confirmCode(document.getElementById("code").value, config);
    });

    document.getElementById("logout").addEventListener("click", function () {
      call("DELETE", AUTH + "auth/session").then(function () {
        say("You are signed out.");
        load(config);
      });
    });

    document.getElementById("logout-all").addEventListener("click", function () {
      call("POST", "/api/logout-all", {}).then(function (r) {
        say(r.status === 200 ? "You are signed out on every browser." : "That did not work. Try again.", r.status !== 200);
        load(config);
      });
    });

    document.getElementById("delete").addEventListener("click", function () {
      if (!me) return;
      if (!window.confirm("Delete account " + me.id + "? This cannot be undone.")) return;
      call("POST", "/api/account/delete", { confirm: me.id }).then(function (r) {
        if (r.status === 200) {
          say("Your account is deleted.");
          load(config);
        } else if (r.status === 403) {
          document.getElementById("delete-reauth").hidden = false;
          say("Sign out and sign in again, then delete within 10 minutes.", true);
        } else {
          say("The account could not be deleted. Try again.", true);
        }
      });
    });
  }
})();
