// Visual use cases covered by the harness (ids from TESTING.md "Visual use cases").
// Each: { id, path, dark?, mask?, fixText?, steps? }.
// - mask: CSS selectors made invisible before the shot (layout kept): content that is random or
//   depends on the date. Listed in TESTING.md next to the case.
// - fixText: { selector: text } replaced (and shown, `hidden` removed) before the shot (the random dish of the play page: its
//   length would move the layout, so the text is pinned and the rest of the page is compared).
// - steps(page, base): browser actions before the shot (clicks, typing), Node built-ins only.
export const WIDTHS = [1400, 1024, 390];

const RANDOM_DISH = {
  "#dish-title": "Plat masqué",
  "#dish-desc": "Description masquée : le serveur choisit le plat au hasard.",
  "#dish-region": "Région",
};

export const CASES = [
  { id: "P1-play", path: "/", fixText: RANDOM_DISH, dark: true },
  { id: "B1-browse", path: "/plats" },
  { id: "B2-browse-page2", path: "/plats?page=2" },
  ...["az", "votes", "consensus", "dispute", "recent"].map((k) => ({ id: `B3-sort-${k}`, path: `/plats?tri=${k}` })),
  { id: "B4-search-hit", path: "/plats?q=GRATIN+DAUPHINOIS" },
  { id: "B5-search-none", path: "/plats?q=garbure" },
  { id: "D1-dish", path: "/plat/cassoulet" },
  { id: "D6-404", path: "/plat/nexiste-pas" },
  { id: "S1-stats-empty", path: "/stats", dark: true },
  { id: "F1-propose", path: "/proposer" },
  {
    id: "F3-propose-errors",
    path: "/proposer",
    // submit the empty form without the browser's own validation bubble (novalidate): server-side errors
    steps: async (p) => {
      await p.evaluate(`document.querySelector("#propose-submit").click(); true`);
      if (!(await p.waitFor(`!!document.querySelector(".alert") && document.readyState === "complete"`))) {
        throw new Error("no validation errors shown");
      }
    },
    mask: ["#pow-status"],
  },
  { id: "A1-about", path: "/a-propos" },
  { id: "M1-admin-login", path: "/admin/login" },
];
