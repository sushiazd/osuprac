// Affiche le schéma d'une copie de client.realm (outil de mise au point).
// Usage : node schema.js <copie de client.realm>
const Realm = require("realm");

(async () => {
  const realm = await Realm.open({ path: process.argv[2] });
  for (const s of realm.schema) {
    const props = Object.entries(s.properties).map(([k, v]) => `${k}:${v.type}${v.objectType ? "<" + v.objectType + ">" : ""}`);
    console.log(`${s.name} (${s.embedded ? "embedded" : realm.objects(s.name).length}) -> ${props.join(", ")}`);
  }
  realm.close();
})().catch((e) => { console.error("ERREUR", e.message); process.exit(1); });
