"""Initial data inserted by the first migration. Everything here is editable in /admin afterwards."""

# Paul Tol's "muted" qualitative palette: 9 colours designed to stay distinguishable
# with the common colour-vision deficiencies. Labels + emoji are always shown too.
CATEGORIES = [
    # slug, name, colour, emoji
    ("ingredient", "Ingrédient", "#DDCC77", "🧂"),
    ("pain", "Pain", "#CC6677", "🥖"),
    ("puree", "Purée", "#88CCEE", "🥔"),
    ("soupe", "Soupe", "#332288", "🥣"),
    ("sandwich", "Sandwich", "#117733", "🥪"),
    ("tarte", "Tarte", "#AA4499", "🥧"),
    ("gratin", "Gratin", "#999933", "🧀"),
    ("ragout", "Ragoût", "#882255", "🥘"),
    ("salade", "Salade", "#44AA99", "🥗"),
]

# Migration 3 (v0.3.0): owner asked for neutral pastels with some art direction. "Grocery labels":
# same OKLCH lightness + chroma for every category, hues 36° apart (app/colors.py palette(10)),
# so no answer looks more inviting than another; ink and border are derived in the same hue.
PASTEL_SLUGS = ["ingredient", "pain", "puree", "soupe", "sandwich", "tarte", "gratin", "ragout", "salade",
                "saucisse"]

# Added by migration 2 (v0.2.0). Okabe-Ito vermillion: distinct from the 9 muted colours above.
CATEGORY_SAUCISSE = ("saucisse", "Saucisse", "#D55E00", "🌭")

DEFAULT_SETTINGS = {
    "require_approval": "0",
    # "count/window" pairs, comma separated; window suffix s, m, h or d.
    # Per visitor (cookie). The per-IP variants (*_ip) are looser: many people can share an IP
    # (office, school, mobile carrier NAT).
    "rl_vote": "3/3s,300/1h",
    "rl_vote_ip": "30/3s,3000/1h",
    "rl_submit": "5/1h,20/1d",
    "rl_submit_ip": "20/1h,60/1d",
    "rl_similar": "30/1m",
    "rl_similar_ip": "300/1m",
    "rl_question": "120/1m",
    "rl_question_ip": "1200/1m",
    "rl_login": "5/15m",
    "min_think_ms": "600",
    "min_fill_seconds": "3",
    "pow_difficulty": "",  # empty = use PLATS_POW_DIFFICULTY
    "anomaly_votes_per_hour": "200",
    "anomaly_visitors_per_ip": "15",
}

# Seed moderation list. "word" = whole word only (short or ambiguous terms, e.g. "pute" must not
# hit "député"), "substring" = anywhere in the text (long, unambiguous stems).
# "con" and "bâtard" are deliberately absent (chili con carne, pain bâtard).
# Stored normalised in the database; the admin can add/remove/import more.
BANNED_WORDS = [
    # French insults / slurs
    ("conne", "word"), ("connard", "substring"),
    ("connasse", "substring"), ("salaud", "substring"), ("salope", "substring"),
    ("pute", "word"), ("putes", "word"), ("putain", "substring"), ("enculé", "substring"),
    ("encule", "word"), ("enculer", "substring"),
    ("merde", "substring"), ("emmerde", "substring"), ("chier", "word"), ("chiasse", "substring"),
    ("couille", "substring"), ("bite", "word"), ("bites", "word"), ("teub", "word"),
    ("chatte", "word"), ("nique", "word"), ("niquer", "word"), ("ntm", "word"),
    ("fdp", "word"), ("tg", "word"), ("ta gueule", "word"), ("pd", "word"),
    ("pédé", "word"), ("tapette", "word"), ("tarlouze", "substring"), ("gouine", "substring"),
    ("negre", "word"), ("nègre", "word"), ("negro", "word"), ("bougnoul", "substring"),
    ("bougnoule", "substring"), ("youpin", "substring"), ("bicot", "substring"),
    ("raton", "word"), ("chinetoque", "substring"), ("niakoue", "substring"),
    ("feuj", "word"), ("rebeu", "word"), ("crouille", "substring"), ("sale arabe", "word"),
    ("sale juif", "word"), ("sale noir", "word"), ("mongol", "word"), ("trisomique", "word"),
    ("attardé", "word"), ("débile", "word"), ("abruti", "word"), ("crétin", "word"),
    ("pétasse", "substring"), ("pouffiasse", "substring"), ("grognasse", "substring"),
    ("branleur", "substring"), ("branlette", "substring"), ("sodomie", "substring"),
    ("pénis", "word"), ("vagin", "word"), ("porno", "substring"), ("viol", "word"),
    ("violer", "word"), ("nazi", "substring"), ("hitler", "substring"), ("sieg heil", "word"),
    # English insults / slurs
    ("fuck", "substring"), ("shit", "word"), ("bullshit", "substring"), ("bitch", "substring"),
    ("cunt", "substring"), ("pussy", "word"),
    ("asshole", "substring"), ("bastard", "substring"), ("motherfucker", "substring"),
    ("whore", "substring"), ("slut", "word"), ("nigger", "substring"), ("nigga", "substring"),
    ("faggot", "substring"), ("fag", "word"), ("retard", "word"), ("retarded", "word"),
    ("tranny", "substring"), ("chink", "word"), ("spic", "word"), ("kike", "word"),
    ("wetback", "substring"), ("dyke", "word"), ("twat", "word"), ("wanker", "substring"),
    ("porn", "word"), ("rape", "word"), ("rapist", "word"),
]
