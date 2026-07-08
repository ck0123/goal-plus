import type { ExtensionAPI, ExtensionContext, ToolCallEvent } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const role = process.env.AGENTIC_ANY_SEARCH_PI_ROLE || "main";
const runtimeRoot = process.env.AGENTIC_ANY_SEARCH_ROOT || ".search";
const STATE_ENTRY_TYPE = "goal-plus-native-state";
let workspaceRoot: string | undefined;
let sawContext = false;
let activeGoalPlusId = process.env.AGENTIC_ANY_SEARCH_GOAL_PLUS_ID;
let cachedGoalStatus: GoalPlusStatusPayload | undefined;
let continuationCount = 0;
let activeGoalStartedAt: string | undefined;
let activeGoalStartEntryCount = 0;

const JsonArgs = Type.Object({}, { additionalProperties: true });

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

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null;
}

function numberFrom(value: unknown): number {
	return typeof value === "number" && Number.isFinite(value) ? value : 0;
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

async function runJsonCli(pi: ExtensionAPI, _ctx: ExtensionContext, tool: string, args: Record<string, unknown>) {
	const result = await pi.exec("agentic-any-search-pi-tool", [
		"--root",
		runtimeRoot,
		"--args-json",
		JSON.stringify(args),
		tool,
	]);
	if (result.code !== 0) {
		return {
			content: [{ type: "text" as const, text: result.stderr || result.stdout || `${tool} failed` }],
			details: { tool, ok: false },
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

function activateGoal(pi: ExtensionAPI, ctx: ExtensionContext, details: unknown) {
	const id = goalPlusIdFrom(details);
	if (!id) return;
	if (id !== activeGoalPlusId || !activeGoalStartedAt) {
		activeGoalStartedAt = new Date().toISOString();
		activeGoalStartEntryCount = ctx.sessionManager.getEntries().length;
		continuationCount = 0;
	}
	activeGoalPlusId = id;
	cachedGoalStatus = statusFrom(details);
	persistGoalState(pi);
}

async function refreshActiveGoal(pi: ExtensionAPI, ctx: ExtensionContext): Promise<GoalPlusStatusPayload | undefined> {
	if (!activeGoalPlusId) return undefined;
	const result = await runJsonCli(pi, ctx, "goal_plus_status", { goal_plus_id: activeGoalPlusId });
	const status = statusFrom(result.details);
	if (!status?.goal_plus_id) return undefined;
	cachedGoalStatus = status;
	persistGoalState(pi);
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

function collectGoalUsage(ctx: ExtensionContext): GoalPlusUsageTotals {
	const entries = ctx.sessionManager.getEntries() as unknown[];
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
		`elapsed: ${formatDuration(elapsedMs)}`,
		`assistant_messages: ${usage.assistantMessages}`,
		`tool_calls: ${usage.toolCalls}`,
		`tokens: input=${usage.input.toLocaleString()} output=${usage.output.toLocaleString()} cache_read=${usage.cacheRead.toLocaleString()} cache_write=${usage.cacheWrite.toLocaleString()} total=${totalTokens.toLocaleString()}`,
		`estimated_cost: $${usage.cost.toFixed(4)}`,
	].join("\n");
}

function sendGoalStats(pi: ExtensionAPI, ctx: ExtensionContext, status: GoalPlusStatusPayload) {
	const usage = collectGoalUsage(ctx);
	pi.sendMessage({
		customType: "goal-plus-stats",
		content: buildGoalStatsMessage(status, usage),
		display: true,
		details: {
			goal_plus_id: status.goal_plus_id ?? activeGoalPlusId,
			status: status.status,
			startedAt: activeGoalStartedAt,
			endedAt: new Date().toISOString(),
			usage,
		},
	});
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
		"- Do not enter Search Mode until the spec draft is high-confidence and any required initial verifier confirmation is recorded.",
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
		"- Start by recording triage with goal_plus_record_triage.",
		"- If the task is search-ready, follow the frozen-verifier and Search Mode gates.",
		"- If it is not search-ready, continue in Goal Mode and update goal-plus state before stopping.",
	].join("\n");
}

function sendUserMessage(pi: ExtensionAPI, ctx: ExtensionContext, message: string) {
	if (ctx.isIdle()) {
		pi.sendUserMessage(message);
		return;
	}
	pi.sendUserMessage(message, { deliverAs: "followUp" });
}

function registerRuntimeTool(pi: ExtensionAPI, name: string) {
	pi.registerTool({
		name,
		label: name,
		description: `Call search-runtime facade tool ${name}.`,
		parameters: JsonArgs,
		executionMode: "sequential",
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			const result = await runJsonCli(pi, ctx, name, params as Record<string, unknown>);
			if (name === "goal_plus_create") {
				activateGoal(pi, ctx, result.details);
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
			const result = await pi.exec("agentic-any-search-pi-worker", [
				"run",
				"--launch-json",
				JSON.stringify(params.launch),
			]);
			if (result.code !== 0) {
				return {
					content: [{ type: "text" as const, text: result.stderr || result.stdout || "pi_rpc_run_worker failed" }],
					details: { ok: false },
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
	if (!event.toolName.startsWith("search_")) return undefined;
	const goalPlusId = activeGoalPlusId;
	if (!goalPlusId) return undefined;
	const gate = await runJsonCli(piForGate, ctx, "goal_plus_gate", {
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
	pi.registerCommand("goal-plus", {
		description: "Run native Pi Goal Plus",
		handler: async (args, ctx) => {
			const rawGoal = args.trim();
			if (!rawGoal) {
				ctx.ui.notify("Usage: /goal-plus <goal>", "error");
				return;
			}
			const result = await runJsonCli(pi, ctx, "goal_plus_create", {
				raw_goal: rawGoal,
				source_path: ctx.cwd,
			});
			const status = statusFrom(result.details);
			if (!status?.goal_plus_id) {
				ctx.ui.notify("goal_plus_create did not return a goal_plus_id", "error");
				return;
			}
			activateGoal(pi, ctx, status);
			ctx.ui.notify(`Goal Plus ${status.goal_plus_id} created`, "info");
			sendUserMessage(pi, ctx, buildGoalStartPrompt(status));
		},
	});

	const mainTools = [
		"goal_plus_create",
		"goal_plus_status",
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
		"search_start_agent_session",
		"search_redispatch_candidate",
		"search_bind_agent_handle",
		"search_continue_agent_session",
		"search_run_verifier",
		"search_select",
		"search_report",
		"search_promote",
	];
	const workerTools = ["search_get_agent_context", "search_run_verifier", "search_list_iterations"];
	for (const tool of role === "worker" ? workerTools : mainTools) {
		registerRuntimeTool(pi, tool);
	}
	if (role === "main") registerPiWorkerTool(pi);
	pi.on("tool_call", async (event, ctx) => {
		return workspaceGuard(event) || (await mainGate(event, ctx));
	});
	pi.on("session_start", async (_event, ctx) => {
		restoreGoalState(ctx);
		if (role !== "main" || !activeGoalPlusId) return;
		try {
			const status = await refreshActiveGoal(pi, ctx);
			if (isTerminalStatus(status?.status)) {
				activeGoalPlusId = undefined;
				activeGoalStartedAt = undefined;
				activeGoalStartEntryCount = 0;
				continuationCount = 0;
				persistGoalState(pi);
			}
		} catch {
			// Keep startup non-fatal; the next explicit tool call will surface runtime errors.
		}
	});
	pi.on("before_agent_start", async (_event, ctx) => {
		if (role !== "main" || !activeGoalPlusId) return;
		const status = await refreshActiveGoal(pi, ctx);
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
	pi.on("agent_end", async (_event, ctx) => {
		if (role !== "main" || !activeGoalPlusId) return;
		if (ctx.hasPendingMessages()) return;
		const gate = await runJsonCli(pi, ctx, "goal_plus_gate", {
			goal_plus_id: activeGoalPlusId,
			event: "stop",
			context: { mode: ctx.mode, continuationCount },
		});
		const details = gateFrom(gate.details);
		if (!details) return;
		if (details.decision === "block") {
			continuationCount += 1;
			persistGoalState(pi);
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
		const status = await refreshActiveGoal(pi, ctx);
		if (isTerminalStatus(status?.status)) {
			sendGoalStats(pi, ctx, status);
			activeGoalPlusId = undefined;
			activeGoalStartedAt = undefined;
			activeGoalStartEntryCount = 0;
			continuationCount = 0;
			persistGoalState(pi);
		}
	});
}
