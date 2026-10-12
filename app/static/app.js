/* plats.hosh.it — vanilla JS, no dependencies, no external calls. */
(function () {
  "use strict";

  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var root = document.documentElement;

  // ------------------------------------------------------------ small helpers
  function $(id) { return document.getElementById(id); }

  function setBars(scope) {
    (scope || document).querySelectorAll("[data-pct]").forEach(function (el) {
      var pct = Math.max(0, Math.min(100, parseFloat(el.getAttribute("data-pct")) || 0));
      el.style.width = pct + "%";
    });
    (scope || document).querySelectorAll("[data-hpct]").forEach(function (el) {
      el.style.height = Math.max(0, Math.min(100, parseFloat(el.getAttribute("data-hpct")) || 0)) + "%";
    });
  }

  function readState() {
    var el = $("initial-state");
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch (e) { return null; }
  }

  // ------------------------------------------------------------ colour theme drift
  var PALETTES = {
    classique: { sat: 70, light: 52, step: 23 },
    neon: { sat: 95, light: 55, step: 37 },
    or: { sat: 85, light: 50, step: 11 },
    holographique: { sat: 90, light: 60, step: 53 },
    cosmique: { sat: 100, light: 62, step: 71 }
  };

  function applyTheme(reward) {
    if (!reward) return;
    var p = PALETTES[reward.palette] || PALETTES.classique;
    var hue = (200 + reward.count * p.step) % 360;
    root.style.setProperty("--hue", String(hue));
    root.style.setProperty("--sat", p.sat + "%");
    root.style.setProperty("--light", p.light + "%");
    root.setAttribute("data-palette", reward.palette);
    try { localStorage.setItem("plats-theme", JSON.stringify(reward)); } catch (e) { /* private mode */ }
  }

  function storedTheme() {
    try { return JSON.parse(localStorage.getItem("plats-theme") || "null"); } catch (e) { return null; }
  }

  // ------------------------------------------------------------ particles (own code)
  var canvas = $("fx");
  var ctx = canvas ? canvas.getContext("2d") : null;
  var particles = [];
  var animating = false;

  function resize() {
    if (!canvas) return;
    var dpr = window.devicePixelRatio || 1;
    canvas.width = window.innerWidth * dpr;
    canvas.height = window.innerHeight * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function burst(x, y, amount, palette, big) {
    if (!ctx || reduceMotion) return;
    var count = Math.min(900, Math.round(amount));
    var baseHue = parseFloat(getComputedStyle(root).getPropertyValue("--hue")) || 200;
    for (var i = 0; i < count; i++) {
      var angle = Math.random() * Math.PI * 2;
      var speed = (big ? 6 : 3) + Math.random() * (big ? 12 : 6);
      var hue;
      if (palette === "or") hue = 40 + Math.random() * 20;
      else if (palette === "classique") hue = baseHue + Math.random() * 120 - 60;
      else hue = Math.random() * 360;
      particles.push({
        x: x, y: y,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed - (big ? 6 : 3),
        life: 0, max: 50 + Math.random() * (big ? 90 : 40),
        size: 3 + Math.random() * (big ? 7 : 4),
        hue: hue, light: palette === "holographique" || palette === "cosmique" ? 70 : 55,
        spin: Math.random() * 6, shape: Math.random() < 0.5 ? 0 : 1
      });
    }
    if (!animating) { animating = true; requestAnimationFrame(tick); }
  }

  function tick() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (var i = particles.length - 1; i >= 0; i--) {
      var p = particles[i];
      p.life++;
      p.vy += 0.25; p.vx *= 0.985; p.vy *= 0.985;
      p.x += p.vx; p.y += p.vy; p.spin += 0.2;
      var alpha = 1 - p.life / p.max;
      if (alpha <= 0) { particles.splice(i, 1); continue; }
      ctx.globalAlpha = alpha;
      ctx.fillStyle = "hsl(" + p.hue + ",95%," + p.light + "%)";
      if (p.shape === 0) {
        ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.spin);
        ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
        ctx.restore();
      } else {
        ctx.beginPath(); ctx.arc(p.x, p.y, p.size / 2, 0, Math.PI * 2); ctx.fill();
      }
    }
    ctx.globalAlpha = 1;
    if (particles.length) requestAnimationFrame(tick);
    else { animating = false; ctx.clearRect(0, 0, canvas.width, canvas.height); }
  }

  function flash(kind) {
    // Reduced-motion alternative (and extra accent for everyone): a colour pulse, no movement.
    document.body.classList.remove("pulse", "pulse-big");
    void document.body.offsetWidth;
    document.body.classList.add(kind === "big" ? "pulse-big" : "pulse");
  }

  // ------------------------------------------------------------ milestones
  var milestoneTimer = null;
  function showMilestone(m, reward) {
    var box = $("milestone");
    if (!box || !m) return;
    $("milestone-title").textContent = m.title;
    var sub = m.subtitle || (m.badge ? "Nouveau titre : " + m.badge : reward.count + " réponses !");
    $("milestone-sub").textContent = sub;
    box.className = "milestone milestone-" + m.tier + " palette-" + reward.palette;
    box.hidden = false;
    clearTimeout(milestoneTimer);
    milestoneTimer = setTimeout(function () { box.hidden = true; }, m.tier === "small" ? 1600 : 2600);
  }

  function celebrate(reward, origin) {
    if (!reward) return;
    var x = origin ? origin.x : window.innerWidth / 2;
    var y = origin ? origin.y : window.innerHeight / 2;
    var m = reward.milestone;
    burst(x, y, 25 * reward.intensity, reward.palette, false);
    if (m) {
      var level = m.level || 1;
      var amount = m.tier === "small" ? 80 + level * 20 : m.tier === "super" ? 450 : 300 + level * 40;
      var big = m.tier !== "small";
      var shots = m.tier === "small" ? 1 : Math.min(6, 2 + Math.floor(level / 3));
      for (var s = 0; s < shots; s++) {
        (function (k) {
          setTimeout(function () {
            burst(window.innerWidth * (0.2 + Math.random() * 0.6), window.innerHeight * (0.25 + Math.random() * 0.3),
              amount / shots, reward.palette, big);
          }, k * 180);
        })(s);
      }
      showMilestone(m, reward);
      flash(big ? "big" : "small");
    } else if (reduceMotion) {
      flash("small");
    }
  }

  function updateMeter(reward) {
    if (!reward) return;
    var pct = ((reward.progress.value - reward.progress.from) / (reward.progress.to - reward.progress.from)) * 100;
    var fill = $("meter-fill");
    if (fill) { fill.setAttribute("data-pct", String(pct)); fill.style.width = pct + "%"; }
    var meter = fill && fill.parentNode;
    if (meter) {
      meter.setAttribute("aria-valuemin", reward.progress.from);
      meter.setAttribute("aria-valuemax", reward.progress.to);
      meter.setAttribute("aria-valuenow", reward.progress.value);
    }
    if ($("count")) $("count").textContent = reward.count;
    if ($("next-stop")) $("next-stop").textContent = reward.progress.to;
    if ($("badge")) $("badge").textContent = reward.badge || "";
  }

  // ------------------------------------------------------------ the question loop
  function initPlay() {
    var form = $("answer-form");
    var state = readState() || {};
    applyTheme(state.reward);
    if (!form) return;
    resize();
    window.addEventListener("resize", resize);

    var MIN_THINK = 650;
    var shownAt = Date.now();
    var busy = false;
    var buttons = form.querySelectorAll("button[name=choice]");

    function enableAfterThink() {
      buttons.forEach(function (b) { b.disabled = true; });
      setTimeout(function () { buttons.forEach(function (b) { b.disabled = false; }); }, MIN_THINK);
    }

    function showQuestion(next) {
      if (!next) {
        $("question").hidden = true;
        $("end").hidden = false;
        $("end").setAttribute("tabindex", "-1");
        $("end").focus();
        return;
      }
      var d = next.dish;
      $("token").value = next.token;
      $("dish-title").textContent = d.title;
      $("dish-desc").textContent = d.description || "";
      var region = $("dish-region");
      region.textContent = d.region || "";
      region.hidden = !d.region;
      var fig = $("dish-figure");
      if (d.image) {
        $("dish-image").src = d.image;
        $("dish-credit").textContent = "Image : " + d.image_author + " — " + d.image_license;
        fig.hidden = false;
      } else {
        $("dish-image").removeAttribute("src");
        fig.hidden = true;
      }
      $("question").classList.remove("enter");
      void $("question").offsetWidth;
      $("question").classList.add("enter");
      shownAt = Date.now();
      enableAfterThink();
    }

    function renderResult(res) {
      var box = $("result");
      box.textContent = "";
      if (res.skipped) {
        var p0 = document.createElement("p");
        p0.className = "same muted";
        p0.textContent = "Pas de souci, plat suivant !";
        box.appendChild(p0);
        return;
      }
      var p = document.createElement("p");
      p.className = "same";
      var strong = document.createElement("strong");
      strong.textContent = (res.same_pct == null ? 0 : res.same_pct) + " %";
      p.appendChild(strong);
      p.appendChild(document.createTextNode(" ont répondu comme toi pour « " + res.dish.title + " »"
        + (res.distribution.total < 3 ? " (encore très peu de réponses)." : ".")));
      box.appendChild(p);
      var ul = document.createElement("ul");
      ul.className = "mini-dist";
      res.distribution.rows.forEach(function (r) {
        if (!r.count) return;
        var li = document.createElement("li");
        li.className = "cat-" + r.slug + (r.slug === res.choice ? " mine" : "");
        var bar = document.createElement("span");
        bar.className = "bar";
        bar.style.width = r.pct + "%";
        var label = document.createElement("span");
        label.textContent = r.emoji + " " + r.name + " " + r.pct + " %";
        li.appendChild(bar); li.appendChild(label);
        ul.appendChild(li);
      });
      box.appendChild(ul);
      var a = document.createElement("a");
      a.href = "/plat/" + res.dish.slug;
      a.className = "more";
      a.textContent = "Voir le détail";
      box.appendChild(a);
    }

    function send(choice, origin) {
      if (busy) return;
      var wait = MIN_THINK - (Date.now() - shownAt);
      if (wait > 0) return; // buttons are disabled anyway; ignore early key presses
      busy = true;
      if (choice !== "skip") {
        // Instant feedback: never wait for the network to say thanks.
        burst(origin ? origin.x : window.innerWidth / 2, origin ? origin.y : window.innerHeight / 2, 20,
          root.getAttribute("data-palette") || "classique", false);
      }
      fetch("/api/vote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ token: $("token").value, choice: choice })
      }).then(function (r) {
        return r.json().then(function (body) { return { status: r.status, body: body }; });
      }).then(function (res) {
        busy = false;
        var body = res.body;
        if (res.status === 200 && body.ok) {
          renderResult(body);
          if (body.reward) {
            applyTheme(body.reward);
            updateMeter(body.reward);
            celebrate(body.reward, origin);
          }
          showQuestion(body.next);
        } else if (res.status === 429) {
          $("result").textContent = "Doucement ! Petite pause de quelques secondes…";
          setTimeout(function () { shownAt = 0; }, 3000);
        } else if (body && "next" in body) {
          showQuestion(body.next);
        } else {
          window.location.reload();
        }
      }).catch(function () {
        busy = false;
        $("result").textContent = "Connexion perdue, réessayez.";
      });
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var btn = e.submitter;
      if (!btn || !btn.value) return;
      var rect = btn.getBoundingClientRect();
      send(btn.value, { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
    });

    document.addEventListener("keydown", function (e) {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      var t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")) return;
      if ($("question").hidden) return;
      var key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      var btn = form.querySelector('button[data-key="' + key + '"]');
      if (!btn || btn.disabled) return;
      e.preventDefault();
      btn.focus();
      btn.classList.add("pressed");
      setTimeout(function () { btn.classList.remove("pressed"); }, 150);
      var rect = btn.getBoundingClientRect();
      send(btn.value, { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
    });

    enableAfterThink();
  }

  // ------------------------------------------------------------ SHA-256 (proof-of-work)
  var K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
  ]);
  var W = new Uint32Array(64);

  function sha256Words(bytes) {
    var l = bytes.length;
    var padded = ((l + 9 + 63) >> 6) << 6;
    var m = new Uint8Array(padded);
    m.set(bytes); m[l] = 0x80;
    var dv = new DataView(m.buffer);
    dv.setUint32(padded - 4, (l * 8) >>> 0);
    dv.setUint32(padded - 8, Math.floor(l / 0x20000000));
    var h0 = 0x6a09e667, h1 = 0xbb67ae85, h2 = 0x3c6ef372, h3 = 0xa54ff53a,
      h4 = 0x510e527f, h5 = 0x9b05688c, h6 = 0x1f83d9ab, h7 = 0x5be0cd19;
    for (var off = 0; off < padded; off += 64) {
      var i, x, y;
      for (i = 0; i < 16; i++) W[i] = dv.getUint32(off + i * 4);
      for (i = 16; i < 64; i++) {
        x = W[i - 15]; y = W[i - 2];
        W[i] = (W[i - 16] + ((x >>> 7 | x << 25) ^ (x >>> 18 | x << 14) ^ (x >>> 3)) + W[i - 7]
          + ((y >>> 17 | y << 15) ^ (y >>> 19 | y << 13) ^ (y >>> 10))) | 0;
      }
      var a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, h = h7;
      for (i = 0; i < 64; i++) {
        var t1 = (h + ((e >>> 6 | e << 26) ^ (e >>> 11 | e << 21) ^ (e >>> 25 | e << 7))
          + ((e & f) ^ (~e & g)) + K[i] + W[i]) | 0;
        var t2 = (((a >>> 2 | a << 30) ^ (a >>> 13 | a << 19) ^ (a >>> 22 | a << 10))
          + ((a & b) ^ (a & c) ^ (b & c))) | 0;
        h = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      h0 = (h0 + a) | 0; h1 = (h1 + b) | 0; h2 = (h2 + c) | 0; h3 = (h3 + d) | 0;
      h4 = (h4 + e) | 0; h5 = (h5 + f) | 0; h6 = (h6 + g) | 0; h7 = (h7 + h) | 0;
    }
    return [h0 >>> 0, h1 >>> 0, h2 >>> 0, h3 >>> 0, h4 >>> 0, h5 >>> 0, h6 >>> 0, h7 >>> 0];
  }

  function leadingZeroBits(words) {
    var bits = 0;
    for (var i = 0; i < words.length; i++) {
      var z = Math.clz32(words[i]);
      bits += z;
      if (z < 32) break;
    }
    return bits;
  }

  function solvePow(challenge, difficulty, done) {
    var enc = new TextEncoder();
    var nonce = 0;
    (function chunk() {
      var end = nonce + 4000;
      for (; nonce < end; nonce++) {
        if (leadingZeroBits(sha256Words(enc.encode(challenge + ":" + nonce))) >= difficulty) {
          done(String(nonce));
          return;
        }
      }
      setTimeout(chunk, 0);
    })();
  }

  window.platsPow = { sha256Words: sha256Words, leadingZeroBits: leadingZeroBits, solve: solvePow };

  // ------------------------------------------------------------ propose page
  function initPropose() {
    var form = $("propose-form");
    if (!form) return;
    var loadedAt = Date.now();
    var MIN_FILL = 3200;
    var nom = $("nom"), desc = $("description"), live = $("similar-live"), counter = $("desc-counter");
    var timer = null, lastQuery = "";

    function updateCounter() {
      if (!counter) return;
      var n = desc.value.trim().length;
      counter.textContent = n + " / " + desc.getAttribute("maxlength") + " caractères"
        + (n < 20 ? " (encore " + (20 - n) + " au minimum)" : "");
    }
    desc.addEventListener("input", updateCounter);
    updateCounter();

    function checkSimilar() {
      var q = nom.value.trim();
      if (q.length < 2 || q === lastQuery) { if (q.length < 2) live.textContent = ""; return; }
      lastQuery = q;
      fetch("/api/similaires?nom=" + encodeURIComponent(q), { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (body) {
          live.textContent = "";
          if (!body.items || !body.items.length) return;
          var title = document.createElement("p");
          title.className = "similar-title";
          title.textContent = "C'est peut-être l'un de ceux-là ?";
          live.appendChild(title);
          var ul = document.createElement("ul");
          body.items.forEach(function (it) {
            var li = document.createElement("li");
            var s = document.createElement("strong");
            s.textContent = it.title + (it.pending ? " (en attente)" : "");
            li.appendChild(s);
            if (it.description) {
              li.appendChild(document.createTextNode(" — " + it.description.slice(0, 160)
                + (it.description.length > 160 ? "…" : "")));
            }
            ul.appendChild(li);
          });
          live.appendChild(ul);
          var hint = document.createElement("p");
          hint.className = "hint";
          hint.textContent = "Si c'est l'un d'eux, inutile de le proposer. Sinon, continuez : une description précise fera la différence.";
          live.appendChild(hint);
        })
        .catch(function () { /* silent */ });
    }
    nom.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(checkSimilar, 450);
    });

    var solving = false;
    form.addEventListener("submit", function (e) {
      if (form.getAttribute("data-solved") === "1") return;
      e.preventDefault();
      if (solving) return;
      solving = true;
      var submitter = e.submitter;
      var decision = submitter && submitter.name === "decision" ? submitter.value : "";
      var status = $("pow-status");
      status.textContent = "Vérification anti-robot en cours…";
      form.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
      var started = Date.now();
      solvePow(form.elements.form_token.value, parseInt(form.getAttribute("data-difficulty"), 10) || 0, function (nonce) {
        form.elements.pow_nonce.value = nonce;
        if (decision) {
          var hidden = document.createElement("input");
          hidden.type = "hidden"; hidden.name = "decision"; hidden.value = decision;
          form.appendChild(hidden);
        }
        var wait = Math.max(0, MIN_FILL - (Date.now() - loadedAt), 0);
        setTimeout(function () {
          status.textContent = "Envoi…";
          form.setAttribute("data-solved", "1");
          form.submit();
        }, wait);
        if (window.console && Date.now() - started > 4000) console.info("pow took", Date.now() - started, "ms");
      });
    });
  }

  // ------------------------------------------------------------ boot
  function boot() {
    setBars();
    var page = document.body.getAttribute("data-page");
    if (page === "play") initPlay();
    if (page === "propose") initPropose();
    if (page !== "play") applyTheme(storedTheme());
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
