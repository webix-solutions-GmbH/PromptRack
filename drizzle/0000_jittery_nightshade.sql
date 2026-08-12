CREATE TABLE "__app_seeds" (
	"kind" text NOT NULL,
	"scope" text NOT NULL,
	"name" text NOT NULL,
	"seeded_at" timestamp with time zone NOT NULL,
	CONSTRAINT "__app_seeds_kind_scope_name_pk" PRIMARY KEY("kind","scope","name")
);
--> statement-breakpoint
CREATE TABLE "machine_models" (
	"id" serial PRIMARY KEY NOT NULL,
	"machine_id" integer NOT NULL,
	"model_id" text NOT NULL,
	"currently_loaded" boolean DEFAULT false NOT NULL,
	"first_seen_at" timestamp with time zone NOT NULL,
	"last_seen_at" timestamp with time zone NOT NULL,
	"source" text NOT NULL,
	CONSTRAINT "machine_models_machine_id_model_id_unique" UNIQUE("machine_id","model_id")
);
--> statement-breakpoint
CREATE TABLE "machines" (
	"id" serial PRIMARY KEY NOT NULL,
	"name" text NOT NULL,
	"base_url" text NOT NULL,
	"api_key" text,
	"cpu" text,
	"ram" text,
	"gpu" text,
	"notes" text,
	"created_at" timestamp with time zone NOT NULL,
	"updated_at" timestamp with time zone NOT NULL
);
--> statement-breakpoint
CREATE TABLE "prompt_groups" (
	"id" serial PRIMARY KEY NOT NULL,
	"name" text NOT NULL,
	"description" text,
	"sort_order" integer DEFAULT 0 NOT NULL,
	"created_at" timestamp with time zone NOT NULL
);
--> statement-breakpoint
CREATE TABLE "prompt_toolsets" (
	"prompt_id" integer NOT NULL,
	"toolset_id" integer NOT NULL,
	"sort_order" integer DEFAULT 0 NOT NULL,
	CONSTRAINT "prompt_toolsets_prompt_id_toolset_id_pk" PRIMARY KEY("prompt_id","toolset_id")
);
--> statement-breakpoint
CREATE TABLE "prompts" (
	"id" serial PRIMARY KEY NOT NULL,
	"group_id" integer NOT NULL,
	"title" text NOT NULL,
	"content" text NOT NULL,
	"expected_output" text,
	"system_prompt_id" integer,
	"system_prompt_mode" text DEFAULT 'append' NOT NULL,
	"custom_system_text" text,
	"tool_mode" text DEFAULT 'none' NOT NULL,
	"tool_choice" text,
	"max_turns" integer DEFAULT 6 NOT NULL,
	"sort_order" integer DEFAULT 0 NOT NULL,
	"created_at" timestamp with time zone NOT NULL,
	"updated_at" timestamp with time zone NOT NULL
);
--> statement-breakpoint
CREATE TABLE "run_results" (
	"id" serial PRIMARY KEY NOT NULL,
	"run_id" integer NOT NULL,
	"prompt_id" integer,
	"sort_order" integer DEFAULT 0 NOT NULL,
	"group_name" text NOT NULL,
	"prompt_title" text NOT NULL,
	"prompt_text" text NOT NULL,
	"expected_output" text,
	"system_prompt_text" text,
	"tools_snapshot" text,
	"tool_mode" text DEFAULT 'none' NOT NULL,
	"tool_choice" text,
	"max_turns" integer DEFAULT 6 NOT NULL,
	"status" text DEFAULT 'pending' NOT NULL,
	"response_text" text,
	"transcript_json" text,
	"turns_json" text,
	"turn_count" integer,
	"tool_call_count" integer,
	"stopped_reason" text,
	"error" text,
	"duration_ms" integer,
	"ttft_ms" integer,
	"prompt_tokens" integer,
	"completion_tokens" integer,
	"tokens_per_sec" double precision,
	"tokens_estimated" boolean DEFAULT false NOT NULL,
	"rating" text,
	"rating_note" text,
	"started_at" timestamp with time zone,
	"finished_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "runs" (
	"id" serial PRIMARY KEY NOT NULL,
	"machine_id" integer,
	"machine_snapshot" text NOT NULL,
	"model_id" text NOT NULL,
	"params" text,
	"comment" text,
	"group_names" text NOT NULL,
	"llm_info" text,
	"status" text DEFAULT 'pending' NOT NULL,
	"archived_at" timestamp with time zone,
	"created_at" timestamp with time zone NOT NULL,
	"started_at" timestamp with time zone,
	"finished_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "system_prompts" (
	"id" serial PRIMARY KEY NOT NULL,
	"name" text NOT NULL,
	"content" text NOT NULL,
	"created_at" timestamp with time zone NOT NULL,
	"updated_at" timestamp with time zone NOT NULL
);
--> statement-breakpoint
CREATE TABLE "tools" (
	"id" serial PRIMARY KEY NOT NULL,
	"toolset_id" integer NOT NULL,
	"name" text NOT NULL,
	"description" text,
	"parameters_json" text DEFAULT '{}' NOT NULL,
	"mock_response" text,
	"enabled" boolean DEFAULT true NOT NULL,
	"source" text DEFAULT 'manual' NOT NULL,
	"first_seen_at" timestamp with time zone NOT NULL,
	"last_seen_at" timestamp with time zone NOT NULL,
	CONSTRAINT "tools_toolset_id_name_unique" UNIQUE("toolset_id","name")
);
--> statement-breakpoint
CREATE TABLE "toolsets" (
	"id" serial PRIMARY KEY NOT NULL,
	"name" text NOT NULL,
	"description" text,
	"kind" text DEFAULT 'manual' NOT NULL,
	"mcp_url" text,
	"mcp_headers" text,
	"created_at" timestamp with time zone NOT NULL,
	"updated_at" timestamp with time zone NOT NULL
);
--> statement-breakpoint
ALTER TABLE "machine_models" ADD CONSTRAINT "machine_models_machine_id_machines_id_fk" FOREIGN KEY ("machine_id") REFERENCES "public"."machines"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "prompt_toolsets" ADD CONSTRAINT "prompt_toolsets_prompt_id_prompts_id_fk" FOREIGN KEY ("prompt_id") REFERENCES "public"."prompts"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "prompt_toolsets" ADD CONSTRAINT "prompt_toolsets_toolset_id_toolsets_id_fk" FOREIGN KEY ("toolset_id") REFERENCES "public"."toolsets"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "prompts" ADD CONSTRAINT "prompts_group_id_prompt_groups_id_fk" FOREIGN KEY ("group_id") REFERENCES "public"."prompt_groups"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "prompts" ADD CONSTRAINT "prompts_system_prompt_id_system_prompts_id_fk" FOREIGN KEY ("system_prompt_id") REFERENCES "public"."system_prompts"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "run_results" ADD CONSTRAINT "run_results_run_id_runs_id_fk" FOREIGN KEY ("run_id") REFERENCES "public"."runs"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "run_results" ADD CONSTRAINT "run_results_prompt_id_prompts_id_fk" FOREIGN KEY ("prompt_id") REFERENCES "public"."prompts"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "runs" ADD CONSTRAINT "runs_machine_id_machines_id_fk" FOREIGN KEY ("machine_id") REFERENCES "public"."machines"("id") ON DELETE set null ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "tools" ADD CONSTRAINT "tools_toolset_id_toolsets_id_fk" FOREIGN KEY ("toolset_id") REFERENCES "public"."toolsets"("id") ON DELETE cascade ON UPDATE no action;