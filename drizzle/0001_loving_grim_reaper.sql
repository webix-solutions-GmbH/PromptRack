CREATE INDEX "run_results_run_id_idx" ON "run_results" USING btree ("run_id");--> statement-breakpoint
CREATE INDEX "run_results_prompt_id_idx" ON "run_results" USING btree ("prompt_id");