const w = db.getSiblingDB("weird_mg");
w.getCollection("Mixed Case").insertMany([
  { dec: NumberDecimal("12345.678901"), big: NumberLong("9007199254740993"), bin: BinData(0, "3q2+7w=="), at: new Date("2026-09-01T10:00:00Z"),
    nested: { arr: [1, "two", { three: null }], "dotted.key": 1 }, re: /abc/i, ts: Timestamp(1, 1), uuid: UUID("0f8fad5b-d9cb-469f-a165-70867728950e") },
  { dec: NumberDecimal("0"), nothing: null },
]);
w.createView("V iew", "Mixed Case", [{ $project: { dec: 1 } }]);
w.getCollection("Empty").insertOne({ tmp: 1 });
w.getCollection("Empty").deleteMany({});
