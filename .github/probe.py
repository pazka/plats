import collections, json, re, urllib.request
from html.parser import HTMLParser
UA = "plats.hosh.it-probe/0.1 (https://github.com/pazka/plats)"
def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "fr"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.geturl(), r.headers.get("content-type"), r.read().decode("utf-8", "replace")
    except Exception as e:
        return getattr(e, "code", 0), url, "", (e.read().decode("utf-8", "replace") if hasattr(e, "read") else str(e))
class A(HTMLParser):
    def __init__(s): super().__init__(); s.links=[]; s.cur=None
    def handle_starttag(s, t, a):
        if t == "a": s.cur=[dict(a).get("href",""), ""]
    def handle_data(s, d):
        if s.cur: s.cur[1]+=d
    def handle_endtag(s, t):
        if t == "a" and s.cur: s.links.append((s.cur[0], " ".join(s.cur[1].split()))); s.cur=None
def report(url):
    st, final, ct, body = get(url)
    print(f"\n======== {url}\nstatus={st} final={final} ct={ct} len={len(body)}")
    for m in re.findall(r'<script[^>]+ld\+json[^>]*>(.*?)</script>', body, re.S)[:3]:
        print("JSONLD:", m.strip()[:1500])
    for m in re.findall(r'<meta[^>]+(?:og:|name="description")[^>]*>', body)[:8]: print("META:", m[:300])
    p = A(); p.feed(body)
    shapes = collections.Counter(re.sub(r"\d+", "N", h.split("#")[0].split("?")[0]) for h, _ in p.links)
    print("links:", len(p.links)); print("top shapes:", shapes.most_common(15))
    for h, t in p.links[::max(1, len(p.links)//80)][:80]: print("  ", h[:140], "|", t[:70])
    return body, p.links
b, links = report("https://cuisine-facile.com/liste-tradition.php")
i = b.find("<body"); print("BODY SNIPPET:", b[i:i+3000])
j = len(b)//2; print("MID SNIPPET:", b[j:j+3000])
b2, links2 = report("https://www.elle.fr/Elle-a-Table/Idees-de-menus/Gastronomie-francaise")
k = b2.find("Flan parisien"); print("ELLE SNIPPET:", b2[max(0,k-2500):k+2500])
for h, t in links[:400]:
    if "recette" in h.lower() or "plat" in h.lower():
        u = h if h.startswith("http") else "https://cuisine-facile.com/" + h.lstrip("/")
        b3, _ = report(u); i = b3.find("<h1"); print("H1:", b3[i:i+2000]); break
for h, t in links2:
    if "Recettes-de-cuisine" in h or "2976349" in h:
        u = h if h.startswith("http") else "https://www.elle.fr" + h
        b4, _ = report(u); break
