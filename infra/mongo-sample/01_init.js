// one database per tenant (mcp-mongo supports database mode only)
const tenants = {
  acme_mg:   [{ name: "Acme Alice", email: "alice@acme.example", plan: "pro",  spend: 120.5 },
              { name: "Acme Bob",   email: "bob@acme.example",   plan: "free", spend: 0 }],
  globex_mg: [{ name: "Globex Gina", email: "gina@globex.example", plan: "pro", spend: 999 }],
};
for (const [dbName, docs] of Object.entries(tenants)) {
  const d = db.getSiblingDB(dbName);
  d.customers.insertMany(docs);
  d.customers.createIndex({ email: 1 }, { unique: true });
  d.events.insertMany([{ kind: "login", at: new Date("2026-09-01T10:00:00Z") }]);
}
