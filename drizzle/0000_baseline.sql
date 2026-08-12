CREATE TABLE `__app_seeds` (
	`kind` text NOT NULL,
	`scope` text NOT NULL,
	`name` text NOT NULL,
	`seeded_at` integer NOT NULL,
	PRIMARY KEY(`kind`, `scope`, `name`)
);
--> statement-breakpoint
CREATE TABLE `machine_models` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`machine_id` integer NOT NULL,
	`model_id` text NOT NULL,
	`currently_loaded` integer DEFAULT false NOT NULL,
	`first_seen_at` integer NOT NULL,
	`last_seen_at` integer NOT NULL,
	`source` text NOT NULL,
	FOREIGN KEY (`machine_id`) REFERENCES `machines`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `machine_models_machine_id_model_id_unique` ON `machine_models` (`machine_id`,`model_id`);--> statement-breakpoint
CREATE TABLE `machines` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`name` text NOT NULL,
	`base_url` text NOT NULL,
	`api_key` text,
	`cpu` text,
	`ram` text,
	`gpu` text,
	`notes` text,
	`created_at` integer NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `prompt_groups` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`name` text NOT NULL,
	`description` text,
	`sort_order` integer DEFAULT 0 NOT NULL,
	`created_at` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `prompt_toolsets` (
	`prompt_id` integer NOT NULL,
	`toolset_id` integer NOT NULL,
	`sort_order` integer DEFAULT 0 NOT NULL,
	PRIMARY KEY(`prompt_id`, `toolset_id`),
	FOREIGN KEY (`prompt_id`) REFERENCES `prompts`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`toolset_id`) REFERENCES `toolsets`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE TABLE `prompts` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`group_id` integer NOT NULL,
	`title` text NOT NULL,
	`content` text NOT NULL,
	`expected_output` text,
	`system_prompt_id` integer,
	`system_prompt_mode` text DEFAULT 'append' NOT NULL,
	`custom_system_text` text,
	`tool_mode` text DEFAULT 'none' NOT NULL,
	`tool_choice` text,
	`max_turns` integer DEFAULT 6 NOT NULL,
	`sort_order` integer DEFAULT 0 NOT NULL,
	`created_at` integer NOT NULL,
	`updated_at` integer NOT NULL,
	FOREIGN KEY (`group_id`) REFERENCES `prompt_groups`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`system_prompt_id`) REFERENCES `system_prompts`(`id`) ON UPDATE no action ON DELETE set null
);
--> statement-breakpoint
CREATE TABLE `run_results` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`run_id` integer NOT NULL,
	`prompt_id` integer,
	`sort_order` integer DEFAULT 0 NOT NULL,
	`group_name` text NOT NULL,
	`prompt_title` text NOT NULL,
	`prompt_text` text NOT NULL,
	`expected_output` text,
	`system_prompt_text` text,
	`tools_snapshot` text,
	`tool_mode` text DEFAULT 'none' NOT NULL,
	`tool_choice` text,
	`max_turns` integer DEFAULT 6 NOT NULL,
	`status` text DEFAULT 'pending' NOT NULL,
	`response_text` text,
	`transcript_json` text,
	`turns_json` text,
	`turn_count` integer,
	`tool_call_count` integer,
	`stopped_reason` text,
	`error` text,
	`duration_ms` integer,
	`ttft_ms` integer,
	`prompt_tokens` integer,
	`completion_tokens` integer,
	`tokens_per_sec` real,
	`tokens_estimated` integer DEFAULT false NOT NULL,
	`rating` text,
	`rating_note` text,
	`started_at` integer,
	`finished_at` integer,
	FOREIGN KEY (`run_id`) REFERENCES `runs`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`prompt_id`) REFERENCES `prompts`(`id`) ON UPDATE no action ON DELETE set null
);
--> statement-breakpoint
CREATE TABLE `runs` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`machine_id` integer,
	`machine_snapshot` text NOT NULL,
	`model_id` text NOT NULL,
	`params` text,
	`comment` text,
	`group_names` text NOT NULL,
	`llm_info` text,
	`status` text DEFAULT 'pending' NOT NULL,
	`archived_at` integer,
	`created_at` integer NOT NULL,
	`started_at` integer,
	`finished_at` integer,
	FOREIGN KEY (`machine_id`) REFERENCES `machines`(`id`) ON UPDATE no action ON DELETE set null
);
--> statement-breakpoint
CREATE TABLE `system_prompts` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`name` text NOT NULL,
	`content` text NOT NULL,
	`created_at` integer NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `tools` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`toolset_id` integer NOT NULL,
	`name` text NOT NULL,
	`description` text,
	`parameters_json` text DEFAULT '{}' NOT NULL,
	`mock_response` text,
	`enabled` integer DEFAULT true NOT NULL,
	`source` text DEFAULT 'manual' NOT NULL,
	`first_seen_at` integer NOT NULL,
	`last_seen_at` integer NOT NULL,
	FOREIGN KEY (`toolset_id`) REFERENCES `toolsets`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `tools_toolset_id_name_unique` ON `tools` (`toolset_id`,`name`);--> statement-breakpoint
CREATE TABLE `toolsets` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`name` text NOT NULL,
	`description` text,
	`kind` text DEFAULT 'manual' NOT NULL,
	`mcp_url` text,
	`mcp_headers` text,
	`created_at` integer NOT NULL,
	`updated_at` integer NOT NULL
);
