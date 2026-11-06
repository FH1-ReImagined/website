// The account page (account.html): sign in with Discord, GitHub or an emailed code, see the
// account, link it to the game, sign out, delete it. It talks to the accounts service at /api/ on this same site
// (forza-live, accounts/README.md): allauth's headless browser API under /api/auth/browser/v1/
// for the sign-ins, and /api/me, /api/logout-all and /api/account/delete. Every change sends the
// CSRF token from the __Host-fh_csrf cookie. Where the site has no /api/ (a static copy), the
// page says accounts are not available. Text from the API goes in with textContent only.
// The localStorage key fh-signed-in is the navbar's hint (nav.js): it says "Account" instead of
// "Sign in" and hides Download, without other pages ever calling the API.
// Linking the game: the launcher opens account.html#link=<token>. The page moves the token into
// sessionStorage (fh-link) and out of the address at once, so it survives a Discord or GitHub
// sign-in in this tab but is never in the history or a log. Signed in, it asks the service whose
// game it is (/api/link/peek), and Link claims it for this account (/api/link/claim); the player
// then confirms the account id in the launcher, and the page follows the request until it is
// linked or declined. A code typed in the form (from the launcher, on another device) works the
// same way.
// A linked account sees its game's photos and designs (/api/me/items) in every state, with their
// likes, and can delete them (/api/me/items/delete, after a sign-in in the past 10 minutes). The
// link to the player's public page (profile.html?p=<owner>) comes from the site's own data
// files: the accounts service never knows that id.
(function () {
  "use strict";

  var AUTH = "/api/auth/browser/v1/";
  var HINT = "fh-signed-in";
  var LINK = "fh-link";
  var stateEl = document.getElementById("account-state");
  var messageEl = document.getElementById("account-message");
  var signin = document.getElementById("signin");
  var profile = document.getElementById("profile");
  var emailForm = document.getElementById("email-form");
  var codeForm = document.getElementById("code-form");
  var linkPanel = document.getElementById("link-panel");
  var me = null;
  var linkSecret = null;  // {token} or {code} of the link being looked at
  var linkTimer = null;

  var ERRORS = {
    busy: "Too many sign-ins from your network. Try again in an hour.",
    cancelled: "Sign-in was cancelled.",
    denied: "Sign-in was cancelled.",
    unavailable: "That sign-in method is not available right now."
  };
  var METHODS = { discord: "Discord", github: "GitHub", email: "Email" };
  var LINK_ERRORS = {
    "not found": "That link or code does not work: it was used, it is over (a link lasts 10 minutes), or it is mistyped. Start again in the launcher.",
    "not a code": "A link code has 8 letters and digits, such as K7M2-QXPA.",
    "another account claimed this link": "Another website account already asked for this link. If that was not you, click Not me in the launcher.",
    "this link is over": "This link is over. Start again in the launcher.",
    "changed in the past day": "This account's game link changed in the past day. Try again tomorrow.",
    "too many tries": "Too many tries. Wait 10 minutes.",
    "codes are paused": "Codes are paused for a few minutes. Use the link the launcher opened in your browser, or try again later."
  };
  var ITEM_STATES = {
    live: "On the site",
    pending: "Waiting for the automatic check",
    held: "Held for the maintainer to check",
    hidden: "Hidden"
  };
  var ITEM_KINDS = { photo: "Photo", design: "Design" };
  var GAME_STATES = {
    frozen: "This link is on hold: your game's key changed. The maintainer checks such links; ask on Discord.",
    waiting: "The public server has not seen your game for a long time. Play online once and the link works again.",
    banned: "This game is banned from the public server's shared features, so the link does nothing for now."
  };

  function stored(value) {
    try {
      if (value === undefined) return sessionStorage.getItem(LINK);
      if (value) sessionStorage.setItem(LINK, value);
      else sessionStorage.removeItem(LINK);
    } catch (e) { /* storage off: the link works only until the page is left */ }
    return null;
  }

  function hint(on) {
    try {
      if (on) localStorage.setItem(HINT, "1");
      else localStorage.removeItem(HINT);
    } catch (e) { /* storage off: the navbar keeps saying Sign in */ }
    if (window.FHNav) window.FHNav.signedIn(on);  // the navbar: Account and no Download, or Sign in
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
    var game = document.getElementById("account-game");
    var note = document.getElementById("game-note");
    var link = doc.link;
    if (link && link.gamertag) {
      game.textContent = link.gamertag + " (linked " + new Date(link.since + "T00:00:00Z")
        .toLocaleDateString(undefined, { dateStyle: "medium", timeZone: "UTC" }) + ")";
      note.textContent = GAME_STATES[link.state] || "";
    } else {
      game.textContent = "Not linked. Link it from the launcher: Settings > Online > Website account. Until then this account can only browse.";
      note.textContent = "";
    }
    note.hidden = !note.textContent;
    document.getElementById("game-actions").hidden = !link;
    document.getElementById("unlink-reauth").hidden = true;
    if (link && link.state === "active") {
      loadItems(link);
    } else {
      document.getElementById("items").hidden = true;
    }
  }

  // ---- the game's photos and designs ----

  function thumbOf(item) {
    if (!item.public) return "";
    return item.kind === "photo" ? "photos/" + item.id + "-thumb.jpg" : "designs/" + item.id + ".png";
  }

  function profileLink(items) {
    var mine = {};
    items.forEach(function (i) { if (i.public) mine[i.kind + ":" + i.id] = true; });
    var p = document.getElementById("items-profile");
    p.hidden = true;
    if (!Object.keys(mine).length) return;
    function find(path, field, kind) {
      return fetch(path, { cache: "no-cache" }).then(function (r) { return r.ok ? r.json() : null; })
        .then(function (doc) {
          var list = doc && Array.isArray(doc[field]) ? doc[field] : [];
          var hit = list.filter(function (i) { return i && mine[kind + ":" + i.id] && /^[0-9a-f]{12}$/.test(i.owner || ""); })[0];
          return hit ? hit.owner : null;
        }, function () { return null; });
    }
    Promise.all([find("data/gallery.json", "photos", "photo"), find("data/designs.json", "designs", "design")])
      .then(function (r) {
        var owner = r[0] || r[1];
        if (!owner) return;
        document.getElementById("items-profile-link").href = "profile.html?p=" + owner;
        p.hidden = false;
      });
  }

  function showItems(doc) {
    var box = document.getElementById("items");
    var list = document.getElementById("items-list");
    var items = Array.isArray(doc.items) ? doc.items : [];
    list.textContent = "";
    items.forEach(function (item) {
      var li = document.createElement("li");
      var src = thumbOf(item);
      if (src) {
        var img = document.createElement("img");
        img.src = src;
        img.alt = "";
        img.loading = "lazy";
        img.addEventListener("error", function () { img.remove(); });  // not on the site yet
        li.appendChild(img);
      }
      var body = document.createElement("div");
      body.className = "item-body";
      body.appendChild(text("b", item.title || "Untitled"));
      var facts = [ITEM_KINDS[item.kind] || item.kind, ITEM_STATES[item.state] || item.state];
      if (item.public) facts.push(item.likes === 1 ? "1 like" : (item.likes || 0).toLocaleString("en") + " likes");
      body.appendChild(text("span", facts.join(" · ")));
      li.appendChild(body);
      var del = text("button", "Delete", "btn ghost danger");
      del.type = "button";
      del.setAttribute("aria-label", "Delete the " + (item.kind === "photo" ? "photo " : "design ") + (item.title || item.id));
      del.addEventListener("click", function () { deleteItem(item); });
      li.appendChild(del);
      list.appendChild(li);
    });
    document.getElementById("items-empty").hidden = items.length > 0;
    var note = document.getElementById("items-note");
    note.textContent = doc.counted ? "" : "Your likes are saved, and count in the public totals once your game has played online on 2 days, 72 hours after its first online sign-in.";
    note.hidden = !note.textContent;
    box.hidden = false;
    profileLink(items);
  }

  function loadItems() {
    return call("GET", "/api/me/items").then(function (r) {
      if (r.status === 200 && r.doc) showItems(r.doc);
      else document.getElementById("items").hidden = true;
    });
  }

  function deleteItem(item) {
    var what = (item.kind === "photo" ? "photo" : "design") + (item.title ? " “" + item.title + "”" : "");
    if (!window.confirm("Delete your " + what + "? It is removed from the website" +
                        (item.kind === "design" ? " and the game's storefront" : "") + ", with its likes. This cannot be undone.")) return;
    document.getElementById("items-reauth").hidden = true;
    call("POST", "/api/me/items/delete", { kind: item.kind, id: item.id }).then(function (r) {
      if (r.status === 200) {
        say("Your " + what + " is deleted. It leaves the site's pages at their next update.");
        loadItems();
      } else if (r.status === 403 && r.doc && r.doc.error === "sign in again") {
        document.getElementById("items-reauth").hidden = false;
      } else if (r.status === 404) {
        say("That one is already gone.");
        loadItems();
      } else if (r.status === 429) {
        say("Too many deletions today. Try again tomorrow.", true);
      } else {
        say("It could not be deleted. Try again later.", true);
      }
    });
  }

  // ---- linking the game ----

  function linkSay(question, extra) {
    linkPanel.hidden = false;
    document.getElementById("link-question").textContent = question;
    var replaces = document.getElementById("link-replaces");
    replaces.textContent = extra || "";
    replaces.hidden = !extra;
  }

  function linkDone(text) {
    clearTimeout(linkTimer);
    linkSecret = null;
    stored(null);
    document.getElementById("link-buttons").hidden = true;
    document.getElementById("link-signin").hidden = true;
    linkSay(text);
  }

  function linkError(r) {
    var code = r.doc && typeof r.doc.error === "string" ? r.doc.error : "";
    linkDone(LINK_ERRORS[code] || "The link did not work. Try again.");
  }

  // What the service says about the request: ask to claim it, follow it, or say how it ended.
  function showLink(doc) {
    var buttons = document.getElementById("link-buttons");
    document.getElementById("link-signin").hidden = true;
    if (doc.state === "pending") {
      linkSay("Link the game of " + doc.gamertag + " to this account?",
              doc.replaces ? "This account is linked to " + doc.replaces + ". Linking moves it to " + doc.gamertag + "." : "");
      buttons.hidden = false;
    } else if (doc.state === "claimed") {
      buttons.hidden = true;
      linkSay("Now confirm in the launcher. It shows account " + (me ? me.id : "") + ".",
              "This page updates when you do.");
      clearTimeout(linkTimer);
      linkTimer = setTimeout(peekLink, 3000);
    } else if (doc.state === "linked") {
      linkDone("Your game " + doc.gamertag + " is linked to this account.");
      return call("GET", "/api/me").then(function (r) { if (r.status === 200 && r.doc) showAccount(r.doc); });
    } else if (doc.state === "declined") {
      linkDone("The game declined the link.");
    } else {
      linkDone("This link is over (it lasts 10 minutes). Start again in the launcher.");
    }
  }

  function peekLink() {
    if (!linkSecret) return;
    return call("POST", "/api/link/peek", linkSecret).then(function (r) {
      if (r.status === 200 && r.doc) showLink(r.doc);
      else linkError(r);
    });
  }

  function claimLink() {
    if (!linkSecret) return;
    document.getElementById("link-buttons").hidden = true;
    return call("POST", "/api/link/claim", linkSecret).then(function (r) {
      if (r.status === 200) return peekLink();
      linkError(r);
    });
  }

  function startLink(secret) {
    linkSecret = secret;
    if (!me) {
      linkSay("The launcher asked to link your game to a website account.");
      document.getElementById("link-signin").hidden = false;
      return;
    }
    return peekLink();
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

  // ?error=... after a Discord or GitHub sign-in, #code=... from the mail's link, #link=... from
  // the launcher.
  function readAddress() {
    var params = new URLSearchParams(location.search);
    var error = params.get("error");
    var code = (location.hash.match(/^#code=([0-9]{8})$/) || [])[1];
    var token = (location.hash.match(/^#link=([A-Za-z0-9_-]{43})$/) || [])[1];
    if (token) stored(token);
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
      var token = stored();
      if (token) startLink({ token: token });
      wire(config);
    });
  }, unavailable);

  function confirmCode(code, config) {
    return call("POST", AUTH + "auth/code/confirm", { code: code.replace(/[\s-]/g, "") }).then(function (r) {
      if (r.status === 200) {
        say("");
        codeForm.hidden = true;
        return load(config).then(function () { if (linkSecret) startLink(linkSecret); });
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

    document.getElementById("link-claim").addEventListener("click", claimLink);
    document.getElementById("link-dismiss").addEventListener("click", function () {
      linkDone("Not linked. You can start again from the launcher.");
    });

    document.getElementById("link-code-form").addEventListener("submit", function (e) {
      e.preventDefault();
      var typed = document.getElementById("link-code").value.trim();
      startLink({ code: typed });
    });

    document.getElementById("unlink").addEventListener("click", function () {
      if (!me || !me.link) return;
      if (!window.confirm("Unlink the game of " + me.link.gamertag + " from this account? The account can then only browse.")) return;
      call("POST", "/api/link/remove", {}).then(function (r) {
        if (r.status === 200) {
          say("Your game is unlinked.");
          load(config);
        } else if (r.status === 403) {
          document.getElementById("unlink-reauth").hidden = false;
        } else {
          say("The game could not be unlinked. Try again.", true);
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
