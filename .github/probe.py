import json, re, urllib.request, urllib.parse
UA = "plats.hosh.it-probe/0.1 (https://github.com/pazka/plats)"
def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "fr"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return getattr(e, "code", 0), str(e)
for u in ["https://cuisine-facile.com/robots.txt", "https://www.elle.fr/robots.txt"]:
    st, b = get(u); print("=====", u, st); print(b[:1500])
st, b = get("https://cuisine-facile.com/liste-tradition.php")
for m in re.findall(r'<script[^>]+ld\+json[^>]*>(.*?)</script>', b, re.S):
    try: d = json.loads(m)
    except Exception as e: print("JSON ERR", e, m[:200]); continue
    if d.get("@type") == "ItemList":
        items = d["itemListElement"]; print("ITEMS", len(items))
        for it in items: print("  ", it["item"]["url"], "|", it["item"]["name"], "|", it["item"].get("description","")[:60])
print("SECTIONS:", re.findall(r'<h4[^>]*id="([^"]+)"', b))
st, r = get("https://cuisine-facile.com/viandes/recette-boeuf-bourguignon.php")
print("RECIPE status", st)
for m in re.findall(r'<script[^>]+ld\+json[^>]*>(.*?)</script>', r, re.S): print("RJSONLD:", m.strip()[:2500])
API = "https://fr.wikipedia.org/w/api.php?"
for q in ["Liste de spécialités régionales françaises", "Liste de plats français", "Cuisine française liste spécialités", "Liste des fromages français"]:
    st, s = get(API + urllib.parse.urlencode({"action":"query","list":"search","srsearch":q,"format":"json","srlimit":8}))
    print("SEARCH", q, [x["title"] for x in json.loads(s)["query"]["search"]] if st == 200 else st)
for cat in ["Catégorie:Cuisine française", "Catégorie:Spécialité régionale française", "Catégorie:Plat français"]:
    st, s = get(API + urllib.parse.urlencode({"action":"query","list":"categorymembers","cmtitle":cat,"cmlimit":500,"format":"json"}))
    if st == 200:
        mem = json.loads(s)["query"]["categorymembers"]; print("CAT", cat, len(mem), [m["title"] for m in mem][:60])
    else: print("CAT", cat, st)
