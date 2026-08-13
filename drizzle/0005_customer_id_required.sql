ALTER TABLE "__app_seeds" DROP CONSTRAINT "__app_seeds_kind_scope_name_pk";--> statement-breakpoint
ALTER TABLE "__app_seeds" ALTER COLUMN "customer_id" SET NOT NULL;--> statement-breakpoint
ALTER TABLE "machines" ALTER COLUMN "customer_id" SET NOT NULL;--> statement-breakpoint
ALTER TABLE "prompt_groups" ALTER COLUMN "customer_id" SET NOT NULL;--> statement-breakpoint
ALTER TABLE "runs" ALTER COLUMN "customer_id" SET NOT NULL;--> statement-breakpoint
ALTER TABLE "system_prompts" ALTER COLUMN "customer_id" SET NOT NULL;--> statement-breakpoint
ALTER TABLE "toolsets" ALTER COLUMN "customer_id" SET NOT NULL;--> statement-breakpoint
ALTER TABLE "__app_seeds" ADD CONSTRAINT "__app_seeds_customer_id_kind_scope_name_pk" PRIMARY KEY("customer_id","kind","scope","name");