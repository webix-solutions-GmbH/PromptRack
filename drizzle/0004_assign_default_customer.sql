-- Everything that existed before workspaces lands in one workspace named
-- "Default". `MIN(id)` rather than a hard-coded 1: a database that already has
-- customers (a re-run, or one created by hand between migrations) must not be
-- pointed at a row that does not exist. The `WHERE NOT EXISTS` makes the insert
-- idempotent.
INSERT INTO "customers" ("name", "description", "created_at", "updated_at")
SELECT 'Default', 'Everything that existed before customer workspaces were introduced.', now(), now()
WHERE NOT EXISTS (SELECT 1 FROM "customers");
--> statement-breakpoint
UPDATE "machines"       SET "customer_id" = (SELECT MIN("id") FROM "customers") WHERE "customer_id" IS NULL;--> statement-breakpoint
UPDATE "system_prompts" SET "customer_id" = (SELECT MIN("id") FROM "customers") WHERE "customer_id" IS NULL;--> statement-breakpoint
UPDATE "toolsets"       SET "customer_id" = (SELECT MIN("id") FROM "customers") WHERE "customer_id" IS NULL;--> statement-breakpoint
UPDATE "prompt_groups"  SET "customer_id" = (SELECT MIN("id") FROM "customers") WHERE "customer_id" IS NULL;--> statement-breakpoint
UPDATE "runs"           SET "customer_id" = (SELECT MIN("id") FROM "customers") WHERE "customer_id" IS NULL;--> statement-breakpoint
UPDATE "__app_seeds"    SET "customer_id" = (SELECT MIN("id") FROM "customers") WHERE "customer_id" IS NULL;
