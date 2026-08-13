CREATE TABLE "customers" (
	"id" serial PRIMARY KEY NOT NULL,
	"name" text NOT NULL,
	"description" text,
	"archived_at" timestamp with time zone,
	"created_at" timestamp with time zone NOT NULL,
	"updated_at" timestamp with time zone NOT NULL
);
--> statement-breakpoint
ALTER TABLE "__app_seeds" ADD COLUMN "customer_id" integer;--> statement-breakpoint
ALTER TABLE "machines" ADD COLUMN "customer_id" integer;--> statement-breakpoint
ALTER TABLE "prompt_groups" ADD COLUMN "customer_id" integer;--> statement-breakpoint
ALTER TABLE "runs" ADD COLUMN "customer_id" integer;--> statement-breakpoint
ALTER TABLE "system_prompts" ADD COLUMN "customer_id" integer;--> statement-breakpoint
ALTER TABLE "toolsets" ADD COLUMN "customer_id" integer;--> statement-breakpoint
ALTER TABLE "user" ADD COLUMN "active_customer_id" integer;--> statement-breakpoint
CREATE UNIQUE INDEX "customers_name_lower_idx" ON "customers" USING btree (lower("name"));--> statement-breakpoint
ALTER TABLE "__app_seeds" ADD CONSTRAINT "__app_seeds_customer_id_customers_id_fk" FOREIGN KEY ("customer_id") REFERENCES "public"."customers"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "machines" ADD CONSTRAINT "machines_customer_id_customers_id_fk" FOREIGN KEY ("customer_id") REFERENCES "public"."customers"("id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "prompt_groups" ADD CONSTRAINT "prompt_groups_customer_id_customers_id_fk" FOREIGN KEY ("customer_id") REFERENCES "public"."customers"("id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "runs" ADD CONSTRAINT "runs_customer_id_customers_id_fk" FOREIGN KEY ("customer_id") REFERENCES "public"."customers"("id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "system_prompts" ADD CONSTRAINT "system_prompts_customer_id_customers_id_fk" FOREIGN KEY ("customer_id") REFERENCES "public"."customers"("id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "toolsets" ADD CONSTRAINT "toolsets_customer_id_customers_id_fk" FOREIGN KEY ("customer_id") REFERENCES "public"."customers"("id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "user" ADD CONSTRAINT "user_active_customer_id_customers_id_fk" FOREIGN KEY ("active_customer_id") REFERENCES "public"."customers"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "machines_customer_id_idx" ON "machines" USING btree ("customer_id");--> statement-breakpoint
CREATE INDEX "prompt_groups_customer_name_idx" ON "prompt_groups" USING btree ("customer_id","name");--> statement-breakpoint
CREATE INDEX "runs_customer_id_idx" ON "runs" USING btree ("customer_id");--> statement-breakpoint
CREATE INDEX "system_prompts_customer_name_idx" ON "system_prompts" USING btree ("customer_id","name");--> statement-breakpoint
CREATE INDEX "toolsets_customer_name_idx" ON "toolsets" USING btree ("customer_id","name");