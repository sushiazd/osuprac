// Exporte en JSON (sur la sortie standard) les scores osu!standard d'une COPIE de client.realm.
// Usage : node export.js <copie de client.realm>
// Ne jamais pointer vers le fichier d'origine : Realm peut modifier le fichier qu'il ouvre.
const Realm = require("realm");

// Énumérations de lazer (ScoreRank, BeatmapOnlineStatus).
const RANKS = { "-1": "F", 0: "D", 1: "C", 2: "B", 3: "A", 4: "S", 5: "SH", 6: "X", 7: "XH" };
const STATUSES = { "-2": "graveyard", "-1": "wip", 0: "pending", 1: "ranked", 2: "approved", 3: "qualified", 4: "loved" };

const parse = (text, fallback) => {
  try {
    return text ? JSON.parse(text) : fallback;
  } catch {
    return fallback;
  }
};

(async () => {
  const realm = await Realm.open({ path: process.argv[2] });
  const out = [];
  for (const s of realm.objects("Score")) {
    const b = s.BeatmapInfo;
    if (s.DeletePending || !b || !s.Ruleset || s.Ruleset.ShortName !== "osu") continue;
    const meta = b.Metadata || {};
    out.push({
      uuid: String(s.ID),
      online_id: s.OnlineID,
      user_id: s.User ? s.User.OnlineID : 0,
      beatmap_id: b.OnlineID,
      beatmapset_id: b.BeatmapSet ? b.BeatmapSet.OnlineID : 0,
      beatmap_hash: b.Hash,
      artist: meta.Artist || "",
      title: meta.Title || "",
      version: b.DifficultyName || "",
      creator: meta.Author ? meta.Author.Username : "",
      status: STATUSES[b.Status] || "unknown",
      accuracy: s.Accuracy,
      max_combo: s.MaxCombo,
      statistics: parse(s.Statistics, {}),
      mods: parse(s.Mods, []),
      rank: RANKS[s.Rank] || "?",
      passed: s.Rank !== -1,
      pp: s.PP,
      ended_at: s.Date.toISOString().replace(/\.\d+Z$/, "Z"),
    });
  }
  realm.close();
  // Realm garde des ressources ouvertes qui empêchent Node de se terminer seul.
  process.stdout.write(JSON.stringify(out), () => process.exit(0));
})().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
