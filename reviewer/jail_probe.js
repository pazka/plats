// Run inside a reviewer container by `scripts/review_dishes.py --self-test`: every way out must be
// closed except CONNECT api.anthropic.com:443 through the egress proxy. Prints one JSON object.
const net = require("net"), dns = require("dns"), fs = require("fs");
const PROXY = process.env.PROBE_PROXY || "plats-review-egress";
function direct(host, port) {
  return new Promise(r => { const s = net.connect({ host, port, timeout: 4000 });
    s.on("connect", () => { s.destroy(); r("open"); }); s.on("timeout", () => { s.destroy(); r("blocked"); });
    s.on("error", () => r("blocked")); });
}
function viaProxy(line) {
  return new Promise(r => { const s = net.connect(3128, PROXY); let done = false;
    s.setTimeout(20000, () => { s.destroy(); if (!done) r("timeout"); });
    s.on("connect", () => s.write(line + "\r\n\r\n"));
    s.on("data", d => { done = true; r(d.toString().split("\r\n")[0].split(" ")[1] || "?"); s.destroy(); });
    s.on("error", e => { if (!done) r("error " + e.code); }); });
}
function canWrite(path) { try { fs.writeFileSync(path, "x"); return "writable"; } catch (e) { return "read-only"; } }
(async () => {
  const out = {};
  out.dns = await new Promise(r => dns.lookup("example.com", e => r(e ? "blocked" : "resolved")));
  out.direct_ip = await direct("1.1.1.1", 443);
  out.direct_anthropic = await direct("160.79.104.10", 443);
  out.proxy_other_host = await viaProxy("CONNECT example.com:443 HTTP/1.1");
  out.proxy_other_port = await viaProxy("CONNECT api.anthropic.com:80 HTTP/1.1");
  out.proxy_ip_literal = await viaProxy("CONNECT 160.79.104.10:443 HTTP/1.1");
  out.proxy_plain_http = await viaProxy("GET http://example.com/ HTTP/1.1\r\nHost: example.com");
  out.proxy_lookalike = await viaProxy("CONNECT api.anthropic.com.evil.example:443 HTTP/1.1");
  out.proxy_anthropic = await viaProxy("CONNECT api.anthropic.com:443 HTTP/1.1");
  out.rootfs = canWrite("/usr/local/bin/x");
  out.uid = process.getuid();
  out.caps = fs.readFileSync("/proc/self/status", "utf8").match(/CapEff:\s*(\w+)/)[1];
  out.no_new_privs = fs.readFileSync("/proc/self/status", "utf8").match(/NoNewPrivs:\s*(\d)/)[1];
  console.log(JSON.stringify(out));
})();
