// One-off script: the "can_self_submit" entitlement used to default to
// true for every student created before today's fix (see
// app/api/students/route.ts). This patches any already-existing student
// rows in the real database so they match the corrected default —
// students can no longer type their own prompt, only submit against a
// trainer-assigned one. Safe to delete after running once.
//
// Run from C:\dev\stella-frontend with:  node fix-self-submit.js
const Database = require("better-sqlite3");
const path = require("path");

const db = new Database(path.join(__dirname, "data", "stella.db"));
const rows = db.prepare("SELECT id, email, entitlements FROM users WHERE role = 'student'").all();

let changed = 0;
for (const row of rows) {
  const ent = JSON.parse(row.entitlements);
  if (ent.can_self_submit) {
    ent.can_self_submit = false;
    db.prepare("UPDATE users SET entitlements = ? WHERE id = ?").run(JSON.stringify(ent), row.id);
    console.log(`Fixed ${row.email}`);
    changed++;
  }
}
console.log(`Done. ${changed} student(s) updated out of ${rows.length}.`);
