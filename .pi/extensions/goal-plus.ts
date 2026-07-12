import type { ExtensionAPI, ExtensionContext, ToolCallEvent } from "@earendil-works/pi-coding-agent";
import { Box, Text } from "@earendil-works/pi-tui";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { type TSchema, Type } from "typebox";

const role = process.env.GOAL_PLUS_PI_ROLE || "main";
const runtimeRoot = process.env.GOAL_PLUS_ROOT || ".gp";
const sourcePath = process.env.GOAL_PLUS_SOURCE_PATH;
const exposeLowLevelWorker = process.env.GOAL_PLUS_PI_EXPOSE_LOW_LEVEL_WORKER === "1";
const modeArgIndex = process.argv.indexOf("--mode");
const isPrintLikeInvocation =
	process.argv.includes("-p") ||
	process.argv.includes("--print") ||
	process.argv.includes("--mode=json") ||
	(modeArgIndex >= 0 && process.argv[modeArgIndex + 1] === "json");
const STATE_ENTRY_TYPE = "goal-plus-native-state";
const GOAL_PLUS_STATS_ENTRY_TYPE = "goal-plus-stats";
let workspaceRoot: string | undefined;
let sawContext = false;
let activeGoalPlusId = process.env.GOAL_PLUS_ID;
let cachedGoalStatus: GoalPlusStatusPayload | undefined;
let continuationCount = 0;
let activeGoalStartedAt: string | undefined;
let activeGoalStartEntryCount = 0;

const INSTALL_HINT =
	'Install this project into the Python environment that launches Pi: python -m pip install -e ".[dev]".';
const LooseObject = Type.Object({}, { additionalProperties: true });
const GoalPlusConfidence = Type.Union([Type.Literal("high"), Type.Literal("medium"), Type.Literal("low")]);
const GoalPlusRecommendedPhase = Type.Union([
	Type.Literal("goal"),
	Type.Literal("spec_discovery"),
	Type.Literal("search"),
]);
const GoalPlusDiscoveryOrigin = Type.Union([Type.Literal("initial"), Type.Literal("in_progress")]);
const GoalPlusTriage = Type.Object(
	{
		is_optimization: Type.Boolean(),
		confidence: GoalPlusConfidence,
		recommended_phase: GoalPlusRecommendedPhase,
		identified_at: Type.Optional(GoalPlusDiscoveryOrigin),
		scenario: Type.Optional(Type.String()),
		reasons: Type.Optional(Type.Array(Type.String())),
		missing: Type.Optional(Type.Array(Type.String())),
	},
	{ additionalProperties: false },
);
const GoalPlusNextAction = Type.Object(
	{
		kind: Type.String(),
		description: Type.String(),
		required: Type.Optional(Type.Boolean()),
		metadata: Type.Optional(LooseObject),
	},
	{ additionalProperties: false },
);
const GoalPlusSpecDraft = Type.Object(
	{
		baseline: LooseObject,
		metric: LooseObject,
		correctness_gate: LooseObject,
		edit_surface: LooseObject,
		verifier_artifacts: Type.Optional(Type.Array(Type.String())),
		search_spec: LooseObject,
		promotion_rule: Type.String(),
		confidence: GoalPlusConfidence,
		origin: Type.Optional(GoalPlusDiscoveryOrigin),
		user_confirmed_frozen_verifier: Type.Optional(Type.Boolean()),
		open_questions: Type.Optional(Type.Array(Type.String())),
	},
	{ additionalProperties: false },
);
const RuntimeToolSchemas: Record<string, TSchema> = {
	goal_plus_create: Type.Object(
		{
			raw_goal: Type.String(),
			source_path: Type.Optional(Type.String()),
			policy: Type.Optional(LooseObject),
		},
		{ additionalProperties: false },
	),
	goal_plus_status: Type.Object({ goal_plus_id: Type.String() }, { additionalProperties: false }),
	goal_plus_monitor_snapshot: Type.Object(
		{
			goal_plus_id: Type.Optional(Type.String()),
			run_id: Type.Optional(Type.String()),
			stale_after_seconds: Type.Optional(Type.Number()),
		},
		{ additionalProperties: false },
	),
	goal_plus_record_triage: Type.Object(
		{
			goal_plus_id: Type.String(),
			triage: GoalPlusTriage,
		},
		{ additionalProperties: false },
	),
	goal_plus_save_spec_draft: Type.Object(
		{
			goal_plus_id: Type.String(),
			spec_draft: GoalPlusSpecDraft,
		},
		{ additionalProperties: false },
	),
	goal_plus_confirm_frozen_verifier: Type.Object(
		{
			goal_plus_id: Type.String(),
			confirmed_by: Type.Optional(Type.String()),
			evidence: Type.Optional(LooseObject),
		},
		{ additionalProperties: false },
	),
	goal_plus_link_search_run: Type.Object(
		{
			goal_plus_id: Type.String(),
			frozen_spec_id: Type.String(),
			run_id: Type.String(),
		},
		{ additionalProperties: false },
	),
	goal_plus_record_search_result: Type.Object(
		{
			goal_plus_id: Type.String(),
			run_id: Type.String(),
			selected_candidate_id: Type.Optional(Type.String()),
			report_path: Type.Optional(Type.String()),
			promotion_artifact_path: Type.Optional(Type.String()),
			summary: Type.Optional(Type.String()),
		},
		{ additionalProperties: false },
	),
	goal_plus_set_status: Type.Object(
		{
			goal_plus_id: Type.String(),
			status: Type.Union([
				Type.Literal("active"),
				Type.Literal("needs_user"),
				Type.Literal("blocked"),
				Type.Literal("complete"),
				Type.Literal("abandoned"),
			]),
			reason: Type.Optional(Type.String()),
			evidence: Type.Optional(Type.Array(LooseObject)),
			next_action: Type.Optional(GoalPlusNextAction),
		},
		{ additionalProperties: false },
	),
	goal_plus_gate: Type.Object(
		{
			goal_plus_id: Type.String(),
			event: Type.Union([
				Type.Literal("stop"),
				Type.Literal("subagent_stop"),
				Type.Literal("pre_tool_use"),
				Type.Literal("user_prompt_submit"),
			]),
			context: LooseObject,
		},
		{ additionalProperties: false },
	),
	search_freeze_spec: Type.Object(
		{
			spec: LooseObject,
			verifier_artifact_paths: Type.Array(Type.String()),
		},
		{ additionalProperties: false },
	),
	search_create: Type.Object({ frozen_spec_id: Type.String() }, { additionalProperties: false }),
	search_status: Type.Object({ run_id: Type.String() }, { additionalProperties: false }),
	search_list_history: Type.Object(
		{
			run_id: Type.String(),
			top_n: Type.Optional(Type.Number()),
			sort_by: Type.Optional(Type.String()),
		},
		{ additionalProperties: false },
	),
	search_plan_next: Type.Object(
		{
			run_id: Type.String(),
			requested_k: Type.Optional(Type.Number()),
		},
		{ additionalProperties: false },
	),
	search_start_batch: Type.Object(
		{
			run_id: Type.String(),
			plan_id: Type.String(),
			proposals: Type.Optional(Type.Array(LooseObject)),
		},
		{ additionalProperties: false },
	),
	search_start_agent_session: Type.Object(
		{
			run_id: Type.String(),
			candidate_id: Type.String(),
			directive: Type.Optional(Type.Union([Type.String(), LooseObject])),
		},
		{ additionalProperties: false },
	),
	search_redispatch_candidate: Type.Object(
		{
			run_id: Type.String(),
			candidate_id: Type.String(),
			directive: Type.Optional(Type.Union([Type.String(), LooseObject])),
			worker_agent_type: Type.Optional(Type.String()),
			worker_budget: Type.Optional(LooseObject),
		},
		{ additionalProperties: false },
	),
	search_bind_agent_handle: Type.Object(
		{
			agent_session_id: Type.String(),
			handle: LooseObject,
		},
		{ additionalProperties: false },
	),
	search_continue_agent_session: Type.Object(
		{
			agent_session_id: Type.String(),
			directive: Type.Optional(Type.Union([Type.String(), LooseObject])),
		},
		{ additionalProperties: false },
	),
	search_get_agent_context: Type.Object({ agent_session_id: Type.String() }, { additionalProperties: false }),
	search_run_verifier: Type.Object(
		{
			run_id: Type.String(),
			candidate_id: Type.String(),
			scope: Type.Optional(Type.Union([Type.Literal("process"), Type.Literal("promotion")])),
			agent_session_id: Type.Optional(Type.String()),
		},
		{ additionalProperties: false },
	),
	search_list_iterations: Type.Object(
		{
			run_id: Type.String(),
			candidate_id: Type.String(),
		},
		{ additionalProperties: false },
	),
	search_select: Type.Object(
		{
			run_id: Type.String(),
			strategy: Type.Optional(Type.String()),
		},
		{ additionalProperties: false },
	),
	search_report: Type.Object({ run_id: Type.String() }, { additionalProperties: false }),
	search_promote: Type.Object(
		{
			run_id: Type.String(),
			candidate_id: Type.String(),
		},
		{ additionalProperties: false },
	),
	pi_search_run_candidate: Type.Object(
		{
			run_id: Type.String(),
			candidate_id: Type.String(),
			directive: Type.Optional(Type.Union([Type.String(), LooseObject])),
			redispatch: Type.Optional(Type.Boolean()),
			runtime_multiplier: Type.Optional(
				Type.Number({ exclusiveMinimum: 1, maximum: 2 }),
			),
			final_verify: Type.Optional(Type.Boolean()),
		},
		{ additionalProperties: false },
	),
	pi_search_run_batch: Type.Object(
		{
			run_id: Type.String(),
			candidate_ids: Type.Array(Type.String()),
			directive: Type.Optional(Type.Union([Type.String(), LooseObject])),
			final_verify: Type.Optional(Type.Boolean()),
			max_parallel: Type.Optional(Type.Number()),
		},
		{ additionalProperties: false },
	),
};
const MAIN_GATED_TOOLS = new Set([
	"bash",
	"edit",
	"write",
	"pi_rpc_run_worker",
	"pi_search_run_candidate",
	"pi_search_run_batch",
]);

interface GoalPlusNativeState {
	activeGoalPlusId?: string;
	continuationCount?: number;
	startedAt?: string;
	startEntryCount?: number;
	status?: string;
	phase?: string;
	updatedAt?: string;
}

interface GoalPlusNextActionPayload {
	kind?: string;
	description?: string;
	required?: boolean;
	metadata?: Record<string, unknown>;
}

interface GoalPlusStatusPayload {
	goal_plus_id?: string;
	raw_goal?: string;
	status?: string;
	phase?: string;
	next_action?: GoalPlusNextActionPayload | null;
	triage?: unknown;
	spec_draft?: unknown;
	search_tasks?: unknown[];
	search_tasks_total?: number;
	current_search_run_id?: string | null;
	linked_search?: unknown;
}

interface GoalPlusGatePayload {
	decision?: string;
	reason?: string;
	continuation_prompt?: string;
	status?: string;
	phase?: string;
}

interface GoalPlusUsageTotals {
	assistantMessages: number;
	toolCalls: number;
	input: number;
	output: number;
	cacheRead: number;
	cacheWrite: number;
	cost: number;
}

interface GoalPlusStatsEntry {
	goal_plus_id?: string;
	status?: string;
	startedAt?: string;
	endedAt: string;
	usage: GoalPlusUsageTotals;
	message: string;
}

interface CommandInvocation {
	command: string;
	argsPrefix: string[];
	label: string;
}

interface CommandRuntimeContext {
	cwd: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null;
}

function numberFrom(value: unknown): number {
	return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function commandContextFrom(ctx: ExtensionContext): CommandRuntimeContext {
	return { cwd: ctx.cwd };
}

function sourceRoot(ctx: CommandRuntimeContext): string {
	return sourcePath || ctx.cwd;
}

function projectModuleInvocation(ctx: CommandRuntimeContext, command: string, moduleName: string): CommandInvocation {
	const root = sourceRoot(ctx);
	const src = join(root, "src");
	const packageDir = join(src, "goal_plus");
	if (existsSync(packageDir)) {
		const code = [
			"import sys",
			`sys.path.insert(0, ${JSON.stringify(src)})`,
			`from ${moduleName} import main`,
			"raise SystemExit(main())",
		].join("; ");
		return { command: "python", argsPrefix: ["-c", code], label: `python -c ${moduleName}` };
	}
	return { command, argsPrefix: [], label: command };
}

function parseJsonObject(text: string): Record<string, unknown> | undefined {
	const trimmed = text.trim();
	if (!trimmed) return undefined;
	try {
		const parsed = JSON.parse(trimmed);
		return isRecord(parsed) ? parsed : undefined;
	} catch {
		return undefined;
	}
}

function isEnvironmentFailure(text: string): boolean {
	const normalized = text.toLowerCase();
	return (
		text.includes("ModuleNotFoundError") ||
		normalized.includes("no module named") ||
		normalized.includes("cannot find module") ||
		normalized.includes("command not found") ||
		normalized.includes("enoent") ||
		normalized.includes("not found:")
	);
}

function commandFailure(
	tool: string,
	invocation: CommandInvocation,
	result: { stdout: string; stderr: string; code: number },
): { text: string; details: Record<string, unknown> } {
	const output = (result.stderr || result.stdout || `${invocation.label} failed with exit code ${result.code}`).trim();
	const parsed = parseJsonObject(output);
	const baseError = typeof parsed?.error === "string" ? parsed.error : output;
	const text = isEnvironmentFailure(baseError) ? `${baseError}\n\n${INSTALL_HINT}` : baseError;
	return {
		text,
		details: {
			...(parsed ?? {}),
			tool: typeof parsed?.tool === "string" ? parsed.tool : tool,
			ok: false,
			error: text,
		},
	};
}

function toolParameters(name: string): TSchema {
	return RuntimeToolSchemas[name] ?? LooseObject;
}

function goalPlusIdFrom(value: unknown): string | undefined {
	if (!isRecord(value)) return undefined;
	const id = value.goal_plus_id;
	return typeof id === "string" && id.length > 0 ? id : undefined;
}

function statusFrom(value: unknown): GoalPlusStatusPayload | undefined {
	if (!isRecord(value)) return undefined;
	return value as GoalPlusStatusPayload;
}

function gateFrom(value: unknown): GoalPlusGatePayload | undefined {
	if (!isRecord(value)) return undefined;
	return value as GoalPlusGatePayload;
}

async function runJsonCli(pi: ExtensionAPI, ctx: CommandRuntimeContext, tool: string, args: Record<string, unknown>) {
	const invocation = projectModuleInvocation(ctx, "goal-plus-pi-tool", "goal_plus.pi_tool");
	const result = await pi.exec(invocation.command, [
		...invocation.argsPrefix,
		"--root",
		runtimeRoot,
		"--args-json",
		JSON.stringify(args),
		tool,
	]);
	if (result.code !== 0) {
		const failure = commandFailure(tool, invocation, result);
		return {
			content: [{ type: "text" as const, text: failure.text }],
			details: failure.details,
		};
	}
	const parsed = JSON.parse(result.stdout || "null");
	return {
		content: [{ type: "text" as const, text: JSON.stringify(parsed, null, 2) }],
		details: parsed,
	};
}

function persistGoalState(pi: ExtensionAPI) {
	pi.appendEntry(STATE_ENTRY_TYPE, {
		activeGoalPlusId,
		continuationCount,
		startedAt: activeGoalStartedAt,
		startEntryCount: activeGoalStartEntryCount,
		status: cachedGoalStatus?.status,
		phase: cachedGoalStatus?.phase,
		updatedAt: new Date().toISOString(),
	} satisfies GoalPlusNativeState);
}

function canPersistGoalState(mode: string | undefined): boolean {
	return mode !== "print" && mode !== "json";
}

function restoreGoalState(ctx: ExtensionContext) {
	const entries = ctx.sessionManager.getEntries();
	const stateEntry = entries
		.filter((entry: { type: string; customType?: string }) => entry.type === "custom" && entry.customType === STATE_ENTRY_TYPE)
		.pop() as { data?: GoalPlusNativeState } | undefined;
	if (!stateEntry?.data) return;
	activeGoalPlusId = stateEntry.data.activeGoalPlusId ?? activeGoalPlusId;
	continuationCount = stateEntry.data.continuationCount ?? continuationCount;
	activeGoalStartedAt = stateEntry.data.startedAt ?? activeGoalStartedAt;
	activeGoalStartEntryCount = stateEntry.data.startEntryCount ?? activeGoalStartEntryCount;
}

function activateGoal(pi: ExtensionAPI, details: unknown, startEntryCount?: number, persist = true) {
	const id = goalPlusIdFrom(details);
	if (!id) return;
	if (id !== activeGoalPlusId || !activeGoalStartedAt) {
		activeGoalStartedAt = new Date().toISOString();
		activeGoalStartEntryCount = startEntryCount ?? activeGoalStartEntryCount;
		continuationCount = 0;
	}
	activeGoalPlusId = id;
	cachedGoalStatus = statusFrom(details);
	if (persist) persistGoalState(pi);
}

async function refreshActiveGoal(
	pi: ExtensionAPI,
	ctx: CommandRuntimeContext,
	persist = true,
): Promise<GoalPlusStatusPayload | undefined> {
	if (!activeGoalPlusId) return undefined;
	const result = await runJsonCli(pi, ctx, "goal_plus_status", { goal_plus_id: activeGoalPlusId });
	const status = statusFrom(result.details);
	if (!status?.goal_plus_id) return undefined;
	cachedGoalStatus = status;
	if (persist) persistGoalState(pi);
	return status;
}

function isTerminalStatus(status: string | undefined): boolean {
	return status === "blocked" || status === "complete" || status === "abandoned";
}

function formatDuration(ms: number): string {
	const seconds = Math.max(0, Math.floor(ms / 1000));
	const hours = Math.floor(seconds / 3600);
	const minutes = Math.floor((seconds % 3600) / 60);
	const remainingSeconds = seconds % 60;
	const parts: string[] = [];
	if (hours > 0) parts.push(`${hours}h`);
	if (minutes > 0 || hours > 0) parts.push(`${minutes}m`);
	parts.push(`${remainingSeconds}s`);
	return parts.join(" ");
}

function countToolCalls(content: unknown): number {
	if (!Array.isArray(content)) return 0;
	return content.filter((item) => isRecord(item) && item.type === "toolCall").length;
}

function collectGoalUsageFromEntries(entries: unknown[]): GoalPlusUsageTotals {
	const startIndex = Math.min(Math.max(0, activeGoalStartEntryCount), entries.length);
	const totals: GoalPlusUsageTotals = {
		assistantMessages: 0,
		toolCalls: 0,
		input: 0,
		output: 0,
		cacheRead: 0,
		cacheWrite: 0,
		cost: 0,
	};
	for (const entry of entries.slice(startIndex)) {
		if (!isRecord(entry) || entry.type !== "message" || !isRecord(entry.message)) continue;
		const message = entry.message;
		if (message.role !== "assistant") continue;
		const usage = isRecord(message.usage) ? message.usage : undefined;
		const cost = usage && isRecord(usage.cost) ? usage.cost : undefined;
		totals.assistantMessages += 1;
		totals.toolCalls += countToolCalls(message.content);
		totals.input += numberFrom(usage?.input);
		totals.output += numberFrom(usage?.output);
		totals.cacheRead += numberFrom(usage?.cacheRead);
		totals.cacheWrite += numberFrom(usage?.cacheWrite);
		totals.cost += numberFrom(cost?.total);
	}
	return totals;
}

function buildGoalStatsMessage(status: GoalPlusStatusPayload, usage: GoalPlusUsageTotals): string {
	const startedAtMs = activeGoalStartedAt ? Date.parse(activeGoalStartedAt) : NaN;
	const elapsedMs = Number.isFinite(startedAtMs) ? Date.now() - startedAtMs : 0;
	const totalTokens = usage.input + usage.output + usage.cacheRead + usage.cacheWrite;
	return [
		"Goal Plus stats",
		`goal_plus_id: ${status.goal_plus_id ?? activeGoalPlusId ?? "unknown"}`,
		`status: ${status.status ?? "unknown"}`,
		`search_tasks: ${status.search_tasks_total ?? status.search_tasks?.length ?? 0}`,
		`elapsed: ${formatDuration(elapsedMs)}`,
		`assistant_messages: ${usage.assistantMessages}`,
		`tool_calls: ${usage.toolCalls}`,
		`tokens: input=${usage.input.toLocaleString()} output=${usage.output.toLocaleString()} cache_read=${usage.cacheRead.toLocaleString()} cache_write=${usage.cacheWrite.toLocaleString()} total=${totalTokens.toLocaleString()}`,
		`estimated_cost: $${usage.cost.toFixed(4)}`,
	].join("\n");
}

function appendGoalStats(pi: ExtensionAPI, status: GoalPlusStatusPayload, usage: GoalPlusUsageTotals): string {
	const endedAt = new Date().toISOString();
	const message = buildGoalStatsMessage(status, usage);
	pi.appendEntry<GoalPlusStatsEntry>(GOAL_PLUS_STATS_ENTRY_TYPE, {
		goal_plus_id: status.goal_plus_id ?? activeGoalPlusId,
		status: status.status,
		startedAt: activeGoalStartedAt,
		endedAt,
		usage,
		message,
	});
	return message;
}

function buildGoalPlusContext(status: GoalPlusStatusPayload): string {
	const action = status.next_action;
	const lines = [
		"[GOAL PLUS ACTIVE]",
		`goal_plus_id: ${status.goal_plus_id ?? activeGoalPlusId ?? "unknown"}`,
		`status: ${status.status ?? "unknown"}`,
		`phase: ${status.phase ?? "unknown"}`,
		"",
		"Raw goal:",
		status.raw_goal ?? "",
		"",
		"Rules:",
		"- Keep the raw goal separate from implementation guesses.",
		"- Update goal-plus state after each phase change.",
		"- Search is an autonomous upgrade: once the spec draft is high-confidence with no open questions, proceed through the Search Mode gate without asking the user for approval.",
		"- Before claiming completion, audit the raw goal against current evidence and call goal_plus_set_status.",
	];
	if (action) {
		lines.push(
			"",
			"Current next_action:",
			`- kind: ${action.kind ?? "unknown"}`,
			`- required: ${action.required === false ? "false" : "true"}`,
			`- description: ${action.description ?? ""}`,
		);
	}
	return lines.join("\n");
}

function buildGoalStartPrompt(status: GoalPlusStatusPayload): string {
	return [
		"Continue this Goal Plus task.",
		"",
		`goal_plus_id: ${status.goal_plus_id ?? activeGoalPlusId ?? "unknown"}`,
		"",
		"Raw goal:",
		status.raw_goal ?? "",
		"",
		"Important:",
		"- The goal_plus_create tool has already created this record. Do not call goal_plus_create again for this goal.",
		"- Load and follow the goal-plus skill.",
		"- Except for loading the goal-plus skill, do not read or audit target files before goal_plus_record_triage.",
		"- Start by recording triage with goal_plus_record_triage.",
		"- If the raw goal explicitly requests verifier-guided Search Mode and supplies a measurable verifier or metric, do not downgrade it to ordinary Goal Mode.",
		"- If the task is search-ready, enter Search Mode autonomously through the frozen-spec and Search Mode gates; do not ask the user to approve the transition.",
		"- Never invent frozen_spec_id, run_id, plan_id, candidate_id, or agent_session_id values. Use only exact ids returned by the immediately preceding runtime tools; call search_create before goal_plus_link_search_run.",
		"- If it is not search-ready, continue in Goal Mode and update goal-plus state before stopping.",
	].join("\n");
}

function rawGoalFromSlashInput(text: string): string | undefined {
	const match = text.match(/^\/goal-plus(?:\s+([\s\S]*))?$/);
	return match ? (match[1] ?? "").trim() : undefined;
}

async function createGoalPlusStart(
	pi: ExtensionAPI,
	ctx: ExtensionContext,
	rawGoal: string,
): Promise<string | undefined> {
	const commandCtx = commandContextFrom(ctx);
	const startEntryCount = ctx.sessionManager.getEntries().length;
	const result = await runJsonCli(pi, commandCtx, "goal_plus_create", {
		raw_goal: rawGoal,
		source_path: ctx.cwd,
	});
	const status = statusFrom(result.details);
	if (!status?.goal_plus_id) {
		const details =
			isRecord(result.details) && typeof result.details.error === "string"
				? result.details.error
				: "goal_plus_create did not return a goal_plus_id";
		pi.sendMessage({
			customType: "goal-plus-error",
			content: details,
			display: true,
			details: { tool: "goal_plus_create" },
		});
		return undefined;
	}
	activateGoal(pi, status, startEntryCount, canPersistGoalState(ctx.mode));
	pi.sendMessage({
		customType: "goal-plus-created",
		content: `Goal Plus ${status.goal_plus_id} created`,
		display: true,
		details: { goal_plus_id: status.goal_plus_id },
	});
	return buildGoalStartPrompt(status);
}

function sendUserMessage(pi: ExtensionAPI, message: string, deliverAsFollowUp: boolean) {
	if (!deliverAsFollowUp) {
		pi.sendUserMessage(message);
		return;
	}
	pi.sendUserMessage(message, { deliverAs: "followUp" });
}

function registerRuntimeTool(pi: ExtensionAPI, name: string) {
	pi.registerTool({
		name,
		label: name,
		description: `Call goal-plus facade tool ${name}.`,
		parameters: toolParameters(name),
		executionMode: "sequential",
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			const commandCtx = commandContextFrom(ctx);
			const startEntryCount = ctx.sessionManager.getEntries().length;
			const canPersistPiState = canPersistGoalState(ctx.mode);
			const result = await runJsonCli(pi, commandCtx, name, params as Record<string, unknown>);
			if (name === "goal_plus_create") {
				activateGoal(pi, result.details, startEntryCount, canPersistPiState);
			}
			if (name === "search_get_agent_context") {
				const details = result.details as { workspace?: string } | undefined;
				workspaceRoot = details?.workspace;
				sawContext = true;
			}
			return result;
		},
	});
}

function registerPiWorkerTool(pi: ExtensionAPI) {
	pi.registerTool({
		name: "pi_rpc_run_worker",
		label: "Pi RPC Worker",
		description: "Run a Pi RPC worker from a search_start_agent_session launch payload. Returns a handle for search_bind_agent_handle.",
		parameters: Type.Object({
			launch: Type.Object({}, { additionalProperties: true }),
		}),
		executionMode: "sequential",
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			const commandCtx = commandContextFrom(ctx);
			const invocation = projectModuleInvocation(commandCtx, "goal-plus-pi-worker", "goal_plus.pi_worker");
			const result = await pi.exec(invocation.command, [
				...invocation.argsPrefix,
				"run",
				"--launch-json",
				JSON.stringify(params.launch),
			]);
			if (result.code !== 0) {
				const failure = commandFailure("pi_rpc_run_worker", invocation, result);
				return {
					content: [{ type: "text" as const, text: failure.text }],
					details: failure.details,
				};
			}
			const handle = JSON.parse(result.stdout || "{}");
			return {
				content: [{ type: "text" as const, text: JSON.stringify(handle, null, 2) }],
				details: handle,
			};
		},
	});
}

function extractCandidatePath(event: ToolCallEvent): string | undefined {
	const input = event.input as Record<string, unknown>;
	if (event.toolName === "bash") return String(input.command || "");
	for (const key of ["path", "file_path", "filePath"]) {
		if (typeof input[key] === "string") return input[key] as string;
	}
	return undefined;
}

function workspaceGuard(event: ToolCallEvent) {
	if (role !== "worker") return undefined;
	if (event.toolName === "search_get_agent_context") return undefined;
	if (!sawContext) {
		const readOnly = new Set(["read", "grep", "find", "ls"]);
		if (readOnly.has(event.toolName)) return undefined;
		return { block: true, reason: "Call search_get_agent_context before mutating tools." };
	}
	if (!workspaceRoot) return undefined;
	if (!["edit", "write", "bash"].includes(event.toolName)) return undefined;
	const target = extractCandidatePath(event);
	if (target && target.includes("..")) {
		return { block: true, reason: "workspaceGuard blocked parent-directory path." };
	}
	if (target && target.startsWith("/") && !target.startsWith(workspaceRoot)) {
		return { block: true, reason: "workspaceGuard blocked access outside candidate workspace." };
	}
	return undefined;
}

async function mainGate(event: ToolCallEvent, ctx: ExtensionContext) {
	if (role !== "main") return undefined;
	if (!event.toolName.startsWith("search_") && !MAIN_GATED_TOOLS.has(event.toolName)) return undefined;
	const goalPlusId = activeGoalPlusId;
	if (!goalPlusId) return undefined;
	const commandCtx = commandContextFrom(ctx);
	const gate = await runJsonCli(piForGate, commandCtx, "goal_plus_gate", {
		goal_plus_id: goalPlusId,
		event: "pre_tool_use",
		context: { tool_name: event.toolName, input: event.input },
	});
	const details = gateFrom(gate.details);
	if (details?.decision === "block") {
		return { block: true, reason: details.reason || "goal_plus_gate blocked search tool use" };
	}
	return undefined;
}

let piForGate: ExtensionAPI;

export default function (pi: ExtensionAPI) {
	piForGate = pi;
	if (typeof pi.registerEntryRenderer === "function") {
		pi.registerEntryRenderer<GoalPlusStatsEntry>(GOAL_PLUS_STATS_ENTRY_TYPE, (entry, { expanded }, theme) => {
			const data = entry.data;
			const lines = (data?.message ?? "Goal Plus stats").split("\n");
			const visibleLines = expanded ? lines : lines.slice(0, 2);
			const box = new Box(1, visibleLines.length, (text) => theme.bg("customMessageBg", text));
			visibleLines.forEach((line, index) => {
				const rendered = index === 0 ? `${theme.fg("accent", "[goal-plus]")} ${line}` : theme.fg("dim", line);
				box.addChild(new Text(rendered, 0, index));
			});
			return box;
		});
	}
	if (!isPrintLikeInvocation) {
		pi.registerCommand("goal-plus", {
			description: "Run native Pi Goal Plus",
			handler: async (args, ctx) => {
				const rawGoal = args.trim();
				if (!rawGoal) {
					ctx.ui.notify("Usage: /goal-plus <goal>", "error");
					return;
				}
				const deliverAsFollowUp = !ctx.isIdle();
				const prompt = await createGoalPlusStart(pi, ctx, rawGoal);
				if (prompt) sendUserMessage(pi, prompt, deliverAsFollowUp);
			},
		});
	}

	const mainTools = [
		"goal_plus_create",
		"goal_plus_status",
		"goal_plus_monitor_snapshot",
		"goal_plus_record_triage",
		"goal_plus_save_spec_draft",
		"goal_plus_confirm_frozen_verifier",
		"goal_plus_link_search_run",
		"goal_plus_record_search_result",
		"goal_plus_set_status",
		"goal_plus_gate",
		"search_freeze_spec",
		"search_create",
		"search_status",
		"search_list_history",
		"search_plan_next",
		"search_start_batch",
		"search_run_verifier",
		"search_select",
		"search_report",
		"search_promote",
		"pi_search_run_candidate",
		"pi_search_run_batch",
	];
	const workerTools = ["search_get_agent_context", "search_run_verifier", "search_list_iterations"];
	for (const tool of role === "worker" ? workerTools : mainTools) {
		registerRuntimeTool(pi, tool);
	}
	if (role === "main" && exposeLowLevelWorker) registerPiWorkerTool(pi);
	pi.on("input", async (event, ctx) => {
		if (role !== "main" || (ctx.mode !== "print" && ctx.mode !== "json")) {
			return { action: "continue" };
		}
		const rawGoal = rawGoalFromSlashInput(event.text);
		if (rawGoal === undefined) return { action: "continue" };
		if (!rawGoal) {
			ctx.ui.notify("Usage: /goal-plus <goal>", "error");
			return { action: "handled" };
		}
		const prompt = await createGoalPlusStart(pi, ctx, rawGoal);
		return prompt
			? { action: "transform", text: prompt, images: event.images }
			: { action: "handled" };
	});
	pi.on("tool_call", async (event, ctx) => {
		return workspaceGuard(event) || (await mainGate(event, ctx));
	});
	pi.on("session_start", async (_event, ctx) => {
		restoreGoalState(ctx);
		if (role !== "main" || !activeGoalPlusId) return;
		const commandCtx = commandContextFrom(ctx);
		const persist = canPersistGoalState(ctx.mode);
		try {
			const status = await refreshActiveGoal(pi, commandCtx, persist);
			if (isTerminalStatus(status?.status)) {
				activeGoalPlusId = undefined;
				activeGoalStartedAt = undefined;
				activeGoalStartEntryCount = 0;
				continuationCount = 0;
				if (persist) persistGoalState(pi);
			}
		} catch {
			// Keep startup non-fatal; the next explicit tool call will surface runtime errors.
		}
	});
	pi.on("before_agent_start", async (_event, ctx) => {
		if (role !== "main" || !activeGoalPlusId) return;
		const commandCtx = commandContextFrom(ctx);
		const status = await refreshActiveGoal(pi, commandCtx, canPersistGoalState(ctx.mode));
		if (!status || isTerminalStatus(status.status)) return;
		return {
			message: {
				customType: "goal-plus-native-context",
				content: buildGoalPlusContext(status),
				display: false,
				details: { goal_plus_id: status.goal_plus_id, phase: status.phase, status: status.status },
			},
		};
	});
	pi.on("agent_end", async (event, ctx) => {
		if (role !== "main" || !activeGoalPlusId) return;
		const lastMessage = event.messages.at(-1);
		if (
			lastMessage?.role === "assistant" &&
			(lastMessage.stopReason === "error" || lastMessage.stopReason === "aborted")
		) {
			return;
		}
		if (ctx.hasPendingMessages()) return;
		const commandCtx = commandContextFrom(ctx);
		const mode = ctx.mode;
		const persist = canPersistGoalState(mode);
		const usage = collectGoalUsageFromEntries(ctx.sessionManager.getEntries() as unknown[]);
		const gate = await runJsonCli(pi, commandCtx, "goal_plus_gate", {
			goal_plus_id: activeGoalPlusId,
			event: "stop",
			context: { mode, continuationCount },
		});
		const details = gateFrom(gate.details);
		if (!details) return;
		if (details.decision === "block") {
			continuationCount += 1;
			if (persist) persistGoalState(pi);
			pi.sendMessage(
				{
					customType: "goal-plus-stop-continuation",
					content: details.continuation_prompt || details.reason || "Goal Plus is still active. Continue the next required action.",
					display: true,
					details: { goal_plus_id: activeGoalPlusId, continuationCount },
				},
				{ triggerTurn: true, deliverAs: "followUp" },
			);
			return;
		}
		const status = await refreshActiveGoal(pi, commandCtx, persist);
		if (isTerminalStatus(status?.status)) {
			const statsMessage = appendGoalStats(pi, status, usage);
			ctx.ui.notify(statsMessage, "info");
			activeGoalPlusId = undefined;
			activeGoalStartedAt = undefined;
			activeGoalStartEntryCount = 0;
			continuationCount = 0;
			cachedGoalStatus = undefined;
			if (persist) persistGoalState(pi);
		}
	});
}
