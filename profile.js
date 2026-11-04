// A player's page (profile.html?p=<owner>): their community photos and shared designs, from the
// site's own data/gallery.json and data/designs.json (photos.js and designs.js draw the lists,
// filtered by the owner), and here the heading: the gamertag of their newest item and how many
// photos, designs and likes they have. All of it is already public on the site; the owner id is
// the player id the game server publishes (a salted hash, never a XUID), and nothing here comes
// from the accounts service. The gamertag is the player's own text: textContent only.
(function () {
  "use strict";

  var nameEl = document.getElementById("profile-name");
  var summary = document.getElementById("profile-summary");
  if (!nameEl || !summary) return;
  var wanted = new URLSearchParams(location.search).get("p") || "";
  if (!/^[0-9a-f]{12}$/.test(wanted)) {
    summary.textContent = "There is no such player. Open a player's page from a photo or a design.";
    return;
  }

  function list(path, field) {
    return fetch(path, { cache: "no-cache" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (doc) {
      return doc && Array.isArray(doc[field]) ? doc[field].filter(function (i) {
        return i && i.owner === wanted && typeof i.gamertag === "string" && i.gamertag;
      }) : [];
    }, function () { return null; });
  }
  function count(n, one, many) { return n.toLocaleString("en") + " " + (n === 1 ? one : many); }
  function likes(items) {
    return items.reduce(function (sum, i) {
      return sum + (typeof i.likes === "number" && isFinite(i.likes) && i.likes > 0 ? Math.round(i.likes) : 0);
    }, 0);
  }

  Promise.all([list("data/gallery.json", "photos"), list("data/designs.json", "designs")]).then(function (r) {
    var photos = r[0] || [], designs = r[1] || [];
    if (r[0] === null && r[1] === null) {
      summary.textContent = "This page could not be loaded. Try again later.";
      return;
    }
    if (!photos.length && !designs.length) {
      summary.textContent = "This player has no photos or designs on the site right now.";
      return;
    }
    // The gamertag on their newest photo (photos are listed newest first), else their newest design.
    var newestDesign = designs.slice().sort(function (a, b) { return (b.createdAt || 0) - (a.createdAt || 0); })[0];
    var tag = photos.length ? photos[0].gamertag : newestDesign.gamertag;
    nameEl.textContent = tag;
    document.title = tag + " - Forza Horizon 1: ReImagined";
    var parts = [count(photos.length, "photo", "photos"), count(designs.length, "design", "designs")];
    var total = likes(photos) + likes(designs);
    if (photos.concat(designs).some(function (i) { return typeof i.likes === "number"; })) {
      parts.push(count(total, "like", "likes"));
    }
    summary.textContent = parts.join(" · ");
  });
})();
