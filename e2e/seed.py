"""Seed a fresh e2e container: ~45 dishes. Run via `docker exec -i <ctr> python - < e2e/seed.py`."""
from app import db, services

conn = db.connect("/data/plats.sqlite3")
DISHES = [
    ("Quiche lorraine", "Tarte salée à la pâte brisée garnie d'œufs, de crème et de lardons, servie en parts.", "Lorraine"),
    ("Cassoulet", "Ragoût de haricots blancs, saucisse et confit de canard, cuit longuement dans une cassole.", "Occitanie"),
    ("Croque-monsieur", "Deux tranches de pain de mie, jambon et fromage, grillées ou gratinées au four.", ""),
    ("Ratatouille", "Légumes d'été mijotés à l'huile d'olive : aubergine, courgette, poivron, tomate.", "Provence"),
    ("Gratin dauphinois", "Pommes de terre en fines rondelles cuites au four dans la crème.", "Dauphiné"),
    ("Soupe à l'oignon", "Bouillon aux oignons caramélisés, servi gratiné avec du pain et du fromage.", "Paris"),
    ("Pot-au-feu", "Viande de bœuf et légumes cuits longuement dans un bouillon, servis séparément.", ""),
    ("Brie de Meaux", "Fromage à pâte molle et croûte fleurie, servi en pointe sur le plateau.", "Île-de-France"),
    ("Hachis parmentier", "Viande hachée recouverte de purée de pommes de terre, gratinée au four.", ""),
    ("Salade niçoise", "Tomates, œufs durs, olives, anchois ou thon, servis froids en assiette.", "Nice"),
]
for i in range(35):
    DISHES.append((f"Spécialité test {i:02d}", f"Plat de test numéro {i}, décrit assez longuement pour la démonstration.", ""))
RECIPES = {"Cassoulet": "https://cuisine-facile.com/viandes/recette-cassoulet.php"}  # never fetched by the test
for title, desc, region in DISHES:
    services.create_dish(conn, title=title, description=desc, region=region, source="manual",
                         recipe_url=RECIPES.get(title, ""))
print("seeded", conn.execute("SELECT COUNT(*) FROM dishes").fetchone()[0])
